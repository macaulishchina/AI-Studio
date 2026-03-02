"""
设计院 (Studio) - Google Anti-Gravity OAuth 认证服务

通过 Google OAuth 设备流获取 Anti-Gravity API 访问权限。
使用 Anti-Gravity 的 OpenAI 兼容端点访问 Gemini, Claude 等模型。

认证流程:
  1. Studio 请求 Google 设备代码 (device_code + user_code)
  2. 用户访问 verification_url 在浏览器中用 Google 账号授权
  3. Studio 轮询 Google 获取 OAuth access_token + refresh_token
  4. 用 access_token 调用 Anti-Gravity API (/v1beta/openai/chat/completions)
  5. access_token 过期后用 refresh_token 自动续期

OAuth access_token 约 1 小时过期，使用 refresh_token 自动续期。
"""
import asyncio
import json
import logging
import os
import time
import base64
import hashlib
from urllib.parse import urlencode, parse_qs
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

import httpx

from backend.core.config import settings

logger = logging.getLogger(__name__)

# Anti-Gravity 使用的公开 Google OAuth client_id 和 secret
ANTIGRAVITY_CLIENT_ID = os.getenv("ANTIGRAVITY_CLIENT_ID", "")
ANTIGRAVITY_CLIENT_SECRET = os.getenv("ANTIGRAVITY_CLIENT_SECRET", "")
ANTIGRAVITY_REDIRECT_URI = "http://localhost:51121/oauth-callback"

# Google OAuth endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo"

# Anti-Gravity API 端点 (OpenAI 兼容)
ANTIGRAVITY_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"

# Token 文件路径
TOKEN_FILE = os.path.join(settings.data_path, "antigravity_auth.json")

# OAuth scope
OAUTH_SCOPE = "openid email profile https://www.googleapis.com/auth/generative-language"


@dataclass
class AntigravityAuth:
    """Anti-Gravity 认证状态管理"""
    # OAuth tokens
    access_token: str = ""
    refresh_token: str = ""
    token_expires_at: int = 0  # unix timestamp
    # 用户信息
    user_email: str = ""
    user_name: str = ""
    # Localhost PKCE flow 状态
    _pkce_verifier: str = ""
    _oauth_code: str = ""
    _verification_url: str = ""
    _device_expires_at: float = 0
    _poll_interval: int = 2
    _callback_server_task: Optional[asyncio.Task] = None
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
                    self.access_token = data.get("access_token", "")
                    self.refresh_token = data.get("refresh_token", "")
                    self.token_expires_at = data.get("token_expires_at", 0)
                    self.user_email = data.get("user_email", "")
                    self.user_name = data.get("user_name", "")
                    if self.refresh_token:
                        logger.info(f"✅ 已从文件加载 Anti-Gravity OAuth token ({self.user_email})")
        except Exception as e:
            logger.warning(f"加载 Anti-Gravity token 文件失败: {e}")

    def _save_token(self):
        """持久化 OAuth token"""
        try:
            os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
            with open(TOKEN_FILE, "w") as f:
                json.dump({
                    "access_token": self.access_token,
                    "refresh_token": self.refresh_token,
                    "token_expires_at": self.token_expires_at,
                    "user_email": self.user_email,
                    "user_name": self.user_name,
                }, f)
            logger.info("✅ Anti-Gravity OAuth token 已保存")
        except Exception as e:
            logger.warning(f"保存 Anti-Gravity token 文件失败: {e}")

    @property
    def is_authenticated(self) -> bool:
        return bool(self.refresh_token)

    @property
    def has_valid_token(self) -> bool:
        return bool(self.access_token) and time.time() < self.token_expires_at - 60

    async def _handle_oauth_callback(self, reader, writer):
        try:
            request = await reader.read(4096)
            request = request.decode('utf-8', errors='ignore')
            lines = request.split("\n", 1)
            if lines and lines[0].startswith("GET"):
                path_part = lines[0].split(" ")[1]
                if "?" in path_part:
                    qs = path_part.split("?")[1]
                    params = parse_qs(qs)
                    if "code" in params:
                        self._oauth_code = params["code"][0]
            
            response = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
            response += "<html><head><title>授权完成</title></head><body><h1>授权完成</h1><p>您可以关闭此窗口返回 Studio。</p><script>window.close()</script></body></html>"
            writer.write(response.encode('utf-8'))
            await writer.drain()
        except Exception as e:
            logger.error(f"处理 OAuth 回调失败: {e}")
        finally:
            writer.close()

    async def start_device_flow(self) -> Dict[str, Any]:
        """
        开启 Local PKCE Flow 并建立监听 51121 端口的本地服务
        """
        # 生成 PKCE
        verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=').decode('ascii')
        digest = hashlib.sha256(verifier.encode('ascii')).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')
        
        self._pkce_verifier = verifier
        self._oauth_code = ""
        self._device_expires_at = time.time() + 600  # 10 分钟

        # 停止上一个可能存在的服务
        if self._callback_server_task and not self._callback_server_task.done():
            self._callback_server_task.cancel()
        
        async def run_server():
            try:
                server = await asyncio.start_server(self._handle_oauth_callback, '127.0.0.1', 51121)
                async with server:
                    await server.serve_forever()
            except Exception as e:
                logger.error(f"Anti-Gravity 授权回调服务停止: {e}")

        # 启动端口监听任务
        loop = asyncio.get_event_loop()
        self._callback_server_task = loop.create_task(run_server())

        params = {
            "client_id": ANTIGRAVITY_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": ANTIGRAVITY_REDIRECT_URI,
            "scope": OAUTH_SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
        
        self._verification_url = GOOGLE_AUTH_URL + "?" + urlencode(params)
        logger.info(f"🔑 Anti-Gravity Localhost Flow 已启动, 请访问浏览器")

        return {
            "user_code": "",  # PKCE
            "verification_url": self._verification_url,
            "expires_in": 600,
        }

    async def poll_for_token(self) -> Dict[str, Any]:
        """
        轮询查看本地 51121 端口是否接收到 code 回调
        并换取 token
        """
        if not self._pkce_verifier:
            return {"status": "error", "message": "请先调用 start_device_flow"}

        if time.time() >= self._device_expires_at:
            if self._callback_server_task and not self._callback_server_task.done():
                self._callback_server_task.cancel()
                self._callback_server_task = None
            self._pkce_verifier = ""
            return {"status": "expired", "message": "超过 10 分钟未授权，请重新开始"}

        if not self._oauth_code:
            return {"status": "pending", "message": "等待用户在浏览器中授权..."}

        # 此时有了 code，开始 exchange
        if self._callback_server_task and not self._callback_server_task.done():
            self._callback_server_task.cancel()
            self._callback_server_task = None

        code = self._oauth_code
        self._oauth_code = ""

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                GOOGLE_TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "client_id": ANTIGRAVITY_CLIENT_ID,
                    "client_secret": ANTIGRAVITY_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": ANTIGRAVITY_REDIRECT_URI,
                    "code_verifier": self._pkce_verifier,
                },
            )

            if resp.status_code != 200:
                logger.error(f"换取 Token 失败: {resp.status_code} {resp.text}")
                error_msg = resp.json().get("error_description", resp.text[:200])
                return {"status": "error", "message": f"回调 Token 失败: {error_msg}"}

            data = resp.json()

            if "access_token" in data:
                self.access_token = data["access_token"]
                self.refresh_token = data.get("refresh_token", "")
                self.token_expires_at = int(time.time()) + data.get("expires_in", 3600)
                self._pkce_verifier = ""  # 清理

                # 获取用户信息
                await self._fetch_user_info()

                self._save_token()
                logger.info(f"✅ Anti-Gravity OAuth 授权成功! ({self.user_email})")
                return {"status": "success"}

            return {"status": "error", "message": data.get("error_description", "未知错误")}

    async def _fetch_user_info(self):
        """获取 Google 用户信息"""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    GOOGLE_USERINFO_URL,
                    headers={"Authorization": f"Bearer {self.access_token}"},
                )
                if resp.status_code == 200:
                    info = resp.json()
                    self.user_email = info.get("email", "")
                    self.user_name = info.get("name", "")
        except Exception as e:
            logger.warning(f"获取 Google 用户信息失败: {e}")

    async def ensure_token(self) -> str:
        """
        确保有有效的 access_token，必要时用 refresh_token 自动续期

        返回 access_token，如果无法获取则抛出异常
        """
        if not self.refresh_token:
            raise RuntimeError("未授权 Anti-Gravity，请先完成 OAuth 认证")

        if self.has_valid_token:
            return self.access_token

        async with self._lock:
            # 双检锁
            if self.has_valid_token:
                return self.access_token

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    GOOGLE_TOKEN_URL,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "client_id": ANTIGRAVITY_CLIENT_ID,
                        "client_secret": ANTIGRAVITY_CLIENT_SECRET,
                        "refresh_token": self.refresh_token,
                        "grant_type": "refresh_token",
                    },
                )

                if resp.status_code == 200:
                    data = resp.json()
                    self.access_token = data["access_token"]
                    self.token_expires_at = int(time.time()) + data.get("expires_in", 3600)
                    self._save_token()
                    logger.info(f"✅ Anti-Gravity access_token 已刷新, "
                                f"有效期至 {time.strftime('%H:%M:%S', time.localtime(self.token_expires_at))}")
                    return self.access_token
                elif resp.status_code == 400:
                    error_data = resp.json()
                    error = error_data.get("error", "")
                    if error in ("invalid_grant", "invalid_client"):
                        logger.error("Anti-Gravity refresh_token 无效或已撤销")
                        self.access_token = ""
                        self.refresh_token = ""
                        self._save_token()
                        raise RuntimeError("Anti-Gravity 授权已失效，请重新授权")
                    raise RuntimeError(f"刷新 Anti-Gravity token 失败: {error}")
                else:
                    raise RuntimeError(
                        f"刷新 Anti-Gravity token 失败: {resp.status_code} {resp.text[:200]}"
                    )

    def logout(self):
        """清除所有认证信息"""
        self.access_token = ""
        self.refresh_token = ""
        self.token_expires_at = 0
        self.user_email = ""
        self.user_name = ""
        self._pkce_verifier = ""
        self._oauth_code = ""
        if self._callback_server_task and not self._callback_server_task.done():
            self._callback_server_task.cancel()
            self._callback_server_task = None
        try:
            if os.path.exists(TOKEN_FILE):
                os.remove(TOKEN_FILE)
        except Exception:
            pass
        logger.info("🔓 Anti-Gravity 已登出")

    def get_status(self) -> Dict[str, Any]:
        """获取当前认证状态"""
        return {
            "authenticated": self.is_authenticated,
            "has_valid_token": self.has_valid_token,
            "token_expires_at": self.token_expires_at if self.access_token else None,
            "user_email": self.user_email,
            "user_name": self.user_name,
            "device_flow_active": bool(self._pkce_verifier) and time.time() < self._device_expires_at,
            "user_code": "",
            "verification_url": self._verification_url if self._pkce_verifier else None,
        }


# 全局单例
antigravity_auth = AntigravityAuth()
