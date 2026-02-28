"""
系统级配置服务 — 统一管理 github_token 等系统凭据

核心原则:
  - 唯一写入入口: 所有修改 github_token 的操作都通过此服务
  - 持久化到 studio_config 表 (重启后仍有效)
  - 同步更新 settings 运行时对象
  - 同步更新 AIProvider.api_key (github slug) 保持一致
"""
import logging
from typing import Optional

from sqlalchemy import text, select

logger = logging.getLogger(__name__)


def _mask_token(t: str) -> str:
    if not t:
        return ""
    if len(t) > 16:
        return t[:8] + "•" * 12 + t[-4:]
    return "•" * len(t)


async def set_github_token(token: str) -> dict:
    """统一设置系统级 GitHub Token

    同时写入:
      1. studio_config 表 (持久化, 重启后有效)
      2. settings.github_token (运行时)
      3. AIProvider(slug='github').api_key (保持 provider 列表一致)
    """
    from studio.backend.core.config import settings
    from studio.backend.core.database import async_session_maker
    from studio.backend.models import AIProvider

    token = token.strip()
    settings.github_token = token

    async with async_session_maker() as db:
        # 1. 持久化到 studio_config
        await db.execute(text(
            "INSERT INTO studio_config (key, value, updated_at) VALUES (:k, :v, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = :v, updated_at = CURRENT_TIMESTAMP"
        ), {"k": "github_token", "v": token})

        # 2. 同步到 AIProvider(slug='github')
        provider = (await db.execute(
            select(AIProvider).where(AIProvider.slug == "github").limit(1)
        )).scalar_one_or_none()
        if provider:
            provider.api_key = token

        await db.commit()

    logger.info(f"GitHub Token {'已设置' if token else '已清空'} (统一配置)")
    return {
        "ok": True,
        "configured": bool(token),
        "masked_token": _mask_token(token),
    }


async def clear_github_token() -> dict:
    """统一清空系统级 GitHub Token"""
    return await set_github_token("")


async def get_github_token_status() -> dict:
    """获取系统级 GitHub Token 配置状态"""
    from studio.backend.core.config import settings
    token = settings.github_token or ""
    return {
        "configured": bool(token),
        "masked_token": _mask_token(token),
    }
