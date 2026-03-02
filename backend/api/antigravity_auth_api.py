"""
设计院 (Studio) - Anti-Gravity OAuth 认证 API

提供 Google OAuth 设备流端点，让用户在浏览器中用 Google 账号授权。
授权后即可使用 Anti-Gravity 的 Gemini, Claude 等模型。
"""
import logging
import time
from typing import Dict, Any

import httpx
from fastapi import APIRouter, HTTPException

from backend.services.antigravity_auth import antigravity_auth, ANTIGRAVITY_BASE_URL

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/antigravity-auth", tags=["Anti-Gravity Auth"])

# ==================== 用量缓存 ====================

_USAGE_CACHE_TTL = 120  # 2 分钟
_usage_cache: Dict[str, Any] = {}
_usage_cache_ts: float = 0


@router.get("/status")
async def get_auth_status():
    """获取 Anti-Gravity OAuth 认证状态"""
    return antigravity_auth.get_status()


@router.post("/device-flow/start")
async def start_device_flow():
    """
    发起 Google OAuth 设备流

    返回 user_code 和 verification_url，
    用户需要访问 verification_url 并输入 user_code 完成授权。
    """
    try:
        result = await antigravity_auth.start_device_flow()
        return result
    except Exception as e:
        logger.exception("启动 Anti-Gravity 设备流失败")
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
        result = await antigravity_auth.poll_for_token()
        return result
    except Exception as e:
        logger.exception("轮询 Anti-Gravity 设备流失败")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/logout")
async def logout():
    """注销 Anti-Gravity OAuth 授权"""
    antigravity_auth.logout()
    return {"success": True, "message": "已注销 Anti-Gravity 授权"}


@router.post("/test")
async def test_antigravity():
    """
    测试 Anti-Gravity API 连接

    尝试刷新 access_token 并发送简单请求,
    验证 Anti-Gravity 授权是否有效。
    """
    if not antigravity_auth.is_authenticated:
        return {"success": False, "message": "未授权 Anti-Gravity"}

    try:
        token = await antigravity_auth.ensure_token()

        # 尝试列出可用模型验证连接
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{ANTIGRAVITY_BASE_URL}/models",
                headers={
                    "Authorization": f"Bearer {token}",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                model_count = len(data.get("data", []))
                return {
                    "success": True,
                    "message": f"Anti-Gravity API 连接正常, 发现 {model_count} 个模型",
                    "token_valid": antigravity_auth.has_valid_token,
                }
            else:
                return {
                    "success": False,
                    "message": f"Anti-Gravity API 返回 {resp.status_code}: {resp.text[:200]}",
                }
    except Exception as e:
        return {
            "success": False,
            "message": f"Anti-Gravity API 连接失败: {str(e)}",
        }


@router.get("/usage")
async def get_antigravity_usage():
    """
    获取 Anti-Gravity 使用信息

    返回:
    - subscription: 订阅计划 (Individual / Developer / Team)
    - user_email: 授权的 Google 账号
    - rate_limits: 速率限制信息
    - token_valid: 当前 token 是否有效
    - token_expires_at: token 过期时间
    """
    global _usage_cache, _usage_cache_ts

    if not antigravity_auth.is_authenticated:
        raise HTTPException(status_code=401, detail="未授权 Anti-Gravity")

    # 使用缓存
    if _usage_cache and time.time() - _usage_cache_ts < _USAGE_CACHE_TTL:
        return _usage_cache

    try:
        token = await antigravity_auth.ensure_token()

        # 获取可用模型以推断订阅类型
        model_list = []
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{ANTIGRAVITY_BASE_URL}/models",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code == 200:
                    model_list = resp.json().get("data", [])
        except Exception:
            pass

        # 根据可用模型推断订阅计划
        model_names = [m.get("id", "") for m in model_list]
        has_premium_models = any(
            name for name in model_names
            if any(k in name.lower() for k in ("claude", "opus", "gpt"))
        )

        subscription = "Developer" if has_premium_models else "Individual"

        result = {
            "subscription": subscription,
            "user_email": antigravity_auth.user_email,
            "user_name": antigravity_auth.user_name,
            "token_valid": antigravity_auth.has_valid_token,
            "token_expires_at": antigravity_auth.token_expires_at,
            "available_models": len(model_list),
            "rate_limits": {
                "description": "根据订阅计划，速率限制每 5 小时刷新",
                "plan_details": {
                    "Individual": "免费层，Gemini 3 Pro 每周配额",
                    "Developer": "Google AI Pro/Ultra，更高速率限制",
                    "Team": "Google Workspace AI Ultra for Business",
                }.get(subscription, ""),
            },
        }

        _usage_cache = result
        _usage_cache_ts = time.time()
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("获取 Anti-Gravity 用量失败")
        raise HTTPException(status_code=500, detail=f"获取用量失败: {str(e)}")
