"""
设计院 (Studio) - GitHub Copilot OAuth 认证服务

通过 OAuth 设备流 (Device Flow) 获取 GitHub Copilot API 访问权限。
这使得 Studio 可以使用 Copilot 订阅中的所有模型 (包括 Claude Opus/Sonnet 等高级模型)。

认证流程:
  1. Studio 请求 GitHub 设备代码 (device_code + user_code)
  2. 用户访问 https://github.com/login/device 输入 user_code 授权
  3. Studio 轮询 GitHub 获取 OAuth access_token
  4. 用 access_token 换取短期 Copilot session token (~30min)
  5. 用 session token 调用 api.githubcopilot.com/chat/completions

Copilot session token 约 30 分钟过期，自动续期。
OAuth access_token 长期有效（除非用户撤销授权）。
"""
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

import httpx

from studio.backend.core.config import settings

logger = logging.getLogger(__name__)

# VS Code Copilot Chat 的公开 OAuth client_id
# 这是公开信息，不是秘密
COPILOT_CLIENT_ID = "01ab8ac9400c4e429b23"

# GitHub OAuth endpoints
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"

# Copilot 端点
COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
COPILOT_CHAT_URL = "https://api.githubcopilot.com"

# Token 文件路径
TOKEN_FILE = os.path.join(settings.data_path, "copilot_oauth.json")


@dataclass
class CopilotSession:
    """Copilot session token (短期, ~30min)"""
    token: str = ""
    expires_at: int = 0  # unix timestamp

    @property
    def is_valid(self) -> bool:
        return bool(self.token) and time.time() < self.expires_at - 60  # 提前 1 分钟失效


@dataclass
class CopilotAuth:
    """Copilot 认证状态管理"""
    # OAuth token (长期)
    oauth_token: str = ""
    # Session token (短期, 自动续期)
    _session: CopilotSession = field(default_factory=CopilotSession)
    # Device flow 状态
    _device_code: str = ""
    _user_code: str = ""
    _verification_uri: str = ""
    _device_expires_at: float = 0
    _poll_interval: int = 5
    _polling: bool = False
    # 锁
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self):
        self._load_token()

    def _load_token(self):
        """从文件加载持久化的 OAuth token"""
        try:
            if os.path.exists(TOKEN_FILE):
                with open(TOKEN_FILE, "r") as f:
                    data = json.load(f)
                    self.oauth_token = data.get("oauth_token", "")
                    if self.oauth_token:
                        logger.info("✅ 已从文件加载 Copilot OAuth token")
        except Exception as e:
            logger.warning(f"加载 Copilot token 文件失败: {e}")

    def _save_token(self):
        """持久化 OAuth token"""
        try:
            os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
            with open(TOKEN_FILE, "w") as f:
                json.dump({"oauth_token": self.oauth_token}, f)
            logger.info("✅ Copilot OAuth token 已保存")
        except Exception as e:
            logger.warning(f"保存 Copilot token 文件失败: {e}")

    @property
    def is_authenticated(self) -> bool:
        return bool(self.oauth_token)

    @property
    def has_valid_session(self) -> bool:
        return self._session.is_valid

    @property
    def session_token(self) -> str:
        return self._session.token

    async def start_device_flow(self) -> Dict[str, Any]:
        """
        发起 OAuth 设备流，返回 user_code 和 verification_uri

        用户需要访问 verification_uri 并输入 user_code 完成授权
        """
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                GITHUB_DEVICE_CODE_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": COPILOT_CLIENT_ID,
                    "scope": "copilot",
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(f"GitHub 设备流启动失败: {resp.status_code} {resp.text}")

            data = resp.json()
            self._device_code = data["device_code"]
            self._user_code = data["user_code"]
            self._verification_uri = data.get("verification_uri", "https://github.com/login/device")
            self._device_expires_at = time.time() + data.get("expires_in", 900)
            self._poll_interval = data.get("interval", 5)

            logger.info(f"🔑 设备流已启动, user_code={self._user_code}")
            return {
                "user_code": self._user_code,
                "verification_uri": self._verification_uri,
                "expires_in": data.get("expires_in", 900),
            }

    async def poll_for_token(self) -> Dict[str, Any]:
        """
        轮询等待用户授权，获取 OAuth token

        返回:
        - {"status": "pending"} — 用户尚未授权
        - {"status": "success"} — 授权成功
        - {"status": "expired"} — 设备码已过期
        - {"status": "error", "message": ...} — 其他错误
        """
        if not self._device_code:
            return {"status": "error", "message": "请先调用 start_device_flow"}

        if time.time() >= self._device_expires_at:
            self._device_code = ""
            return {"status": "expired", "message": "设备码已过期，请重新开始"}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                GITHUB_OAUTH_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": COPILOT_CLIENT_ID,
                    "device_code": self._device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )

            data = resp.json()

            if "access_token" in data:
                self.oauth_token = data["access_token"]
                self._device_code = ""  # 清理
                self._save_token()
                logger.info("✅ Copilot OAuth 授权成功!")
                return {"status": "success"}

            error = data.get("error", "")
            if error == "authorization_pending":
                return {"status": "pending", "message": "等待用户授权..."}
            elif error == "slow_down":
                self._poll_interval = min(self._poll_interval + 5, 30)
                return {"status": "pending", "message": "请稍候..."}
            elif error == "expired_token":
                self._device_code = ""
                return {"status": "expired", "message": "设备码已过期，请重新开始"}
            else:
                return {"status": "error", "message": data.get("error_description", error)}

    async def ensure_session(self) -> str:
        """
        确保有有效的 Copilot session token，必要时自动续期

        返回 session token，如果无法获取则抛出异常
        """
        if not self.oauth_token:
            raise RuntimeError("未授权 Copilot，请先完成 OAuth 设备流")

        if self._session.is_valid:
            return self._session.token

        async with self._lock:
            # 双检锁
            if self._session.is_valid:
                return self._session.token

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    COPILOT_TOKEN_URL,
                    headers={
                        "Authorization": f"token {self.oauth_token}",
                        "editor-version": "vscode/1.96.0",
                        "editor-plugin-version": "copilot-chat/0.24.0",
                        "user-agent": "Studio/1.0",
                    },
                )

                if resp.status_code == 200:
                    data = resp.json()
                    self._session = CopilotSession(
                        token=data.get("token", ""),
                        expires_at=data.get("expires_at", 0),
                    )
                    logger.info(f"✅ Copilot session token 已刷新, "
                                f"有效期至 {time.strftime('%H:%M:%S', time.localtime(self._session.expires_at))}")
                    return self._session.token
                elif resp.status_code == 401:
                    # OAuth token 可能已被撤销
                    logger.error("Copilot OAuth token 无效或已撤销")
                    self.oauth_token = ""
                    self._save_token()  # 清除保存的无效 token
                    raise RuntimeError("Copilot OAuth token 无效，请重新授权")
                else:
                    raise RuntimeError(
                        f"获取 Copilot session token 失败: {resp.status_code} {resp.text[:200]}"
                    )

    def logout(self):
        """清除所有认证信息"""
        self.oauth_token = ""
        self._session = CopilotSession()
        self._device_code = ""
        try:
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)
        except Exception:
            pass
        logger.info("🔓 Copilot 已登出")

    def get_status(self) -> Dict[str, Any]:
        """获取当前认证状态"""
        return {
            "authenticated": self.is_authenticated,
            "has_valid_session": self.has_valid_session,
            "session_expires_at": self._session.expires_at if self._session.token else None,
            "device_flow_active": bool(self._device_code) and time.time() < self._device_expires_at,
            "user_code": self._user_code if self._device_code else None,
            "verification_uri": self._verification_uri if self._device_code else None,
        }


# 全局单例
copilot_auth = CopilotAuth()
