"""
系统级配置服务 — 统一管理 github_token / 模型设置 等系统凭据

核心原则:
  - 唯一写入入口: 所有修改 github_token / 模型设置 的操作都通过此服务
  - 持久化到 studio_config 表 (重启后仍有效)
  - 同步更新 settings 运行时对象
  - 同步更新 AIProvider.api_key (github slug) 保持一致
"""
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text, select

logger = logging.getLogger(__name__)

# ── 模型设置相关 config key ──
MODEL_CONFIG_KEYS = {
    "chat_default_model",       # str: 全局默认聊天模型 ID
    "chat_model_allowlist",     # JSON array: 聊天模型白名单
    "stt_default_model",        # str: 全局默认 STT 模型 ID
    "stt_model_allowlist",      # JSON array: STT 模型白名单
    "stt_provider",             # str: STT 提供商 (browser / openai_compat / ...)
    "stt_api_base",             # str: STT API base URL (OpenAI-compat)
    "stt_api_key",              # str: STT API key
}


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
    from backend.core.config import settings
    from backend.core.database import async_session_maker
    from backend.models import AIProvider

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
    from backend.core.config import settings
    token = settings.github_token or ""
    return {
        "configured": bool(token),
        "masked_token": _mask_token(token),
    }


# ── 模型设置管理 ────────────────────────────────────────────────

async def get_model_settings() -> Dict[str, Any]:
    """获取所有模型相关配置 (chat / stt)"""
    from backend.core.database import async_session_maker

    result: Dict[str, Any] = {}
    try:
        async with async_session_maker() as db:
            placeholders = ", ".join(f"'{k}'" for k in MODEL_CONFIG_KEYS)
            rows = (await db.execute(
                text(f"SELECT key, value FROM studio_config WHERE key IN ({placeholders})")
            )).all()
            for key, value in rows:
                if key.endswith("_allowlist"):
                    # JSON array
                    try:
                        result[key] = json.loads(value) if value else []
                    except (json.JSONDecodeError, TypeError):
                        result[key] = []
                else:
                    result[key] = value or ""
    except Exception as e:
        logger.debug(f"get_model_settings 跳过: {e}")

    # 确保所有 key 都有默认值
    for key in MODEL_CONFIG_KEYS:
        if key not in result:
            result[key] = [] if key.endswith("_allowlist") else ""
    return result


async def set_model_settings(updates: Dict[str, Any]) -> Dict[str, Any]:
    """批量更新模型相关配置

    Args:
        updates: { key: value } 字典, key 必须在 MODEL_CONFIG_KEYS 中

    Returns:
        更新后的完整模型配置
    """
    from backend.core.database import async_session_maker

    # 过滤只允许的 key
    valid_updates = {k: v for k, v in updates.items() if k in MODEL_CONFIG_KEYS}
    if not valid_updates:
        return await get_model_settings()

    async with async_session_maker() as db:
        for key, value in valid_updates.items():
            # JSON array 类型序列化
            if key.endswith("_allowlist"):
                if isinstance(value, list):
                    db_value = json.dumps(value, ensure_ascii=False)
                elif isinstance(value, str):
                    db_value = value  # 前端可能直接传 JSON string
                else:
                    db_value = "[]"
            elif key == "stt_api_key":
                db_value = (value or "").strip()
            else:
                db_value = str(value).strip() if value else ""

            await db.execute(text(
                "INSERT INTO studio_config (key, value, updated_at) VALUES (:k, :v, CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET value = :v, updated_at = CURRENT_TIMESTAMP"
            ), {"k": key, "v": db_value})

        await db.commit()

    logger.info(f"模型配置已更新: {list(valid_updates.keys())}")
    return await get_model_settings()


async def get_chat_default_model() -> str:
    """获取全局默认聊天模型 (快捷方法, 供 discussion / conversation fallback 用)"""
    from backend.core.database import async_session_maker
    try:
        async with async_session_maker() as db:
            row = (await db.execute(
                text("SELECT value FROM studio_config WHERE key = 'chat_default_model'")
            )).first()
            if row and row[0]:
                return row[0]
    except Exception:
        pass
    return ""  # 空 = 使用硬编码 fallback


async def get_stt_config() -> Dict[str, Any]:
    """获取 STT 相关配置 (provider / api_base / api_key / model)"""
    from backend.core.database import async_session_maker
    stt_keys = {"stt_default_model", "stt_model_allowlist", "stt_provider", "stt_api_base", "stt_api_key"}
    result: Dict[str, Any] = {}
    try:
        async with async_session_maker() as db:
            placeholders = ", ".join(f"'{k}'" for k in stt_keys)
            rows = (await db.execute(
                text(f"SELECT key, value FROM studio_config WHERE key IN ({placeholders})")
            )).all()
            for key, value in rows:
                if key.endswith("_allowlist"):
                    try:
                        result[key] = json.loads(value) if value else []
                    except (json.JSONDecodeError, TypeError):
                        result[key] = []
                elif key == "stt_api_key":
                    result[key] = value or ""
                else:
                    result[key] = value or ""
    except Exception as e:
        logger.debug(f"get_stt_config 跳过: {e}")

    for key in stt_keys:
        if key not in result:
            result[key] = [] if key.endswith("_allowlist") else ""
    return result


# ── 记忆系统配置管理 ────────────────────────────────────────────────

MEMORY_CONFIG_KEYS = {
    "memory_enabled",               # bool (str "true"/"false")
    "memory_extraction_model",      # str: 空 = 用聊天默认模型
    "memory_consolidation_model",   # str: 空 = 同上
    "memory_auto_extract",          # bool: 每次对话后自动提取
    "memory_extract_assistant",     # bool: 是否从助手消息提取
    "memory_max_per_user",          # int: 每用户记忆上限
    "memory_decay_days",            # int: 未访问衰减天数
    "memory_auto_consolidate_hours",  # int: 自动合并周期 (0=关闭)
}

_MEMORY_DEFAULTS = {
    "memory_enabled": "true",
    "memory_extraction_model": "",
    "memory_consolidation_model": "",
    "memory_auto_extract": "true",
    "memory_extract_assistant": "true",
    "memory_max_per_user": "500",
    "memory_decay_days": "30",
    "memory_auto_consolidate_hours": "24",
}

_MEMORY_BOOL_KEYS = {"memory_enabled", "memory_auto_extract", "memory_extract_assistant"}
_MEMORY_INT_KEYS = {"memory_max_per_user", "memory_decay_days", "memory_auto_consolidate_hours"}


async def get_memory_config() -> dict:
    """获取记忆系统配置 (返回 Python 原生类型)"""
    from backend.core.database import async_session_maker

    raw: dict = {}
    try:
        async with async_session_maker() as db:
            placeholders = ", ".join(f"'{k}'" for k in MEMORY_CONFIG_KEYS)
            rows = (await db.execute(
                text(f"SELECT key, value FROM studio_config WHERE key IN ({placeholders})")
            )).all()
            for key, value in rows:
                raw[key] = value or ""
    except Exception as e:
        logger.debug(f"get_memory_config 跳过: {e}")

    # 填充默认值 + 类型转换
    result: dict = {}
    for key in MEMORY_CONFIG_KEYS:
        val = raw.get(key, _MEMORY_DEFAULTS.get(key, ""))
        if key in _MEMORY_BOOL_KEYS:
            result[key] = val.lower() in ("true", "1", "yes") if isinstance(val, str) else bool(val)
        elif key in _MEMORY_INT_KEYS:
            try:
                result[key] = int(val) if val else int(_MEMORY_DEFAULTS[key])
            except (ValueError, TypeError):
                result[key] = int(_MEMORY_DEFAULTS[key])
        else:
            result[key] = val
    return result


async def set_memory_config(updates: dict) -> dict:
    """批量更新记忆配置"""
    from backend.core.database import async_session_maker

    valid_updates = {k: v for k, v in updates.items() if k in MEMORY_CONFIG_KEYS}
    if not valid_updates:
        return await get_memory_config()

    async with async_session_maker() as db:
        for key, value in valid_updates.items():
            if key in _MEMORY_BOOL_KEYS:
                db_value = "true" if value in (True, "true", "1", "yes") else "false"
            elif key in _MEMORY_INT_KEYS:
                db_value = str(int(value)) if value else _MEMORY_DEFAULTS[key]
            else:
                db_value = str(value).strip() if value else ""

            await db.execute(text(
                "INSERT INTO studio_config (key, value, updated_at) VALUES (:k, :v, CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET value = :v, updated_at = CURRENT_TIMESTAMP"
            ), {"k": key, "v": db_value})

        await db.commit()

    logger.info(f"记忆配置已更新: {list(valid_updates.keys())}")
    return await get_memory_config()
