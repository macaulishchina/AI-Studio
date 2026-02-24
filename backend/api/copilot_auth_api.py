"""
设计院 (Studio) - Copilot OAuth 认证 API

提供 OAuth 设备流端点，让用户在浏览器中授权 Copilot 访问。
授权后即可使用 Claude、Gemini 等 Copilot 专属模型。
"""
import logging
import time
from typing import Dict, Any

import httpx
from fastapi import APIRouter, HTTPException

from studio.backend.services.copilot_auth import copilot_auth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/copilot-auth", tags=["Copilot Auth"])

# ==================== 用量缓存 ====================

_USAGE_CACHE_TTL = 120  # 2 分钟
_usage_cache: Dict[str, Any] = {}
_usage_cache_ts: float = 0


@router.get("/status")
async def get_auth_status():
    """获取 Copilot OAuth 认证状态"""
    return copilot_auth.get_status()


@router.post("/device-flow/start")
async def start_device_flow():
    """
    发起 OAuth 设备流

    返回 user_code 和 verification_uri，
    用户需要访问 verification_uri 并输入 user_code 完成授权。
    """
    try:
        result = await copilot_auth.start_device_flow()
        return result
    except Exception as e:
        logger.exception("启动设备流失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/device-flow/poll")
async def poll_device_flow():
    """
    轮询设备流授权状态

    返回:
    - {"status": "pending"} — 用户尚未授权
    - {"status": "success"} — 授权成功! 可以刷新模型列表了
    - {"status": "expired"} — 设备码已过期，需要重新开始
    """
    try:
        result = await copilot_auth.poll_for_token()
        return result
    except Exception as e:
        logger.exception("轮询设备流失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/logout")
async def logout():
    """注销 Copilot OAuth 授权"""
    copilot_auth.logout()
    return {"success": True, "message": "已注销 Copilot 授权"}


@router.post("/test")
async def test_copilot():
    """
    测试 Copilot API 连接

    尝试获取 session token 并发送简单请求,
    验证 Copilot 授权是否有效。
    """
    if not copilot_auth.is_authenticated:
        return {"success": False, "message": "未授权 Copilot"}

    try:
        token = await copilot_auth.ensure_session()
        return {
            "success": True,
            "message": "Copilot API 连接正常",
            "session_valid": copilot_auth.has_valid_session,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Copilot API 连接失败: {str(e)}",
        }


@router.get("/usage")
async def get_copilot_usage():
    """
    获取 Copilot 高级请求使用情况

    调用 GitHub API /copilot_internal/user 获取配额快照，包括:
    - premium_interactions: 高级请求配额/剩余/已用
    - chat: 聊天配额
    - completions: 补全配额
    - quota_reset_date: 配额重置日期
    - copilot_plan: 订阅计划
    - sku: 订阅类型

    结果缓存 2 分钟。
    """
    global _usage_cache, _usage_cache_ts

    if not copilot_auth.is_authenticated:
        raise HTTPException(status_code=401, detail="未授权 Copilot")

    # 使用缓存
    if _usage_cache and time.time() - _usage_cache_ts < _USAGE_CACHE_TTL:
        return _usage_cache

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.github.com/copilot_internal/user",
                headers={
                    "Authorization": f"token {copilot_auth.oauth_token}",
                    "Accept": "application/json",
                    "editor-version": "vscode/1.96.0",
                    "editor-plugin-version": "copilot-chat/0.24.0",
                    "user-agent": "Studio/1.0",
                },
            )

            if resp.status_code != 200:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"GitHub API 返回 {resp.status_code}",
                )

            data = resp.json()

            # 提取配额快照
            quota_snapshots = data.get("quota_snapshots", {})
            premium = quota_snapshots.get("premium_interactions", {})

            result = {
                "copilot_plan": data.get("copilot_plan", "unknown"),
                "sku": data.get("access_type_sku", "unknown"),
                "quota_reset_date": data.get("quota_reset_date"),
                "quota_reset_date_utc": data.get("quota_reset_date_utc"),
                "premium_requests": {
                    "entitlement": premium.get("entitlement", 0),
                    "remaining": premium.get("remaining", 0),
                    "used": premium.get("entitlement", 0) - premium.get("remaining", 0),
                    "percent_remaining": premium.get("percent_remaining", 0),
                    "unlimited": premium.get("unlimited", False),
                    "overage_count": premium.get("overage_count", 0),
                    "overage_permitted": premium.get("overage_permitted", False),
                },
                "chat": {
                    "unlimited": quota_snapshots.get("chat", {}).get("unlimited", False),
                    "remaining": quota_snapshots.get("chat", {}).get("remaining", 0),
                },
                "completions": {
                    "unlimited": quota_snapshots.get("completions", {}).get("unlimited", False),
                    "remaining": quota_snapshots.get("completions", {}).get("remaining", 0),
                },
            }

            _usage_cache = result
            _usage_cache_ts = time.time()
            return result

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("获取 Copilot 用量失败")
        raise HTTPException(status_code=500, detail=f"获取用量失败: {str(e)}")
