"""
设计院 (Studio) - AI 服务提供商管理 API

CRUD + 连接测试 + 模型发现
/studio-api/providers/
"""
import logging
from typing import Optional, List, Dict, Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.core.database import async_session_maker
from backend.core.config import settings
from backend.models import AIProvider
from backend.api.provider_presets import ALL_SEED_PROVIDERS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/providers", tags=["AI Providers"])


# ==================== Schemas ====================

class ProviderOut(BaseModel):
    id: int
    slug: str
    name: str
    provider_type: str
    base_url: str
    api_key_set: bool = Field(description="是否已设置 API Key (不暴露真实值)")
    api_key_hint: str = Field("", description="API Key 脱敏后四位")
    enabled: bool
    is_builtin: bool
    is_preset: bool
    icon: str
    description: str
    default_models: list


class ProviderCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = ""
    icon: str = "🔌"
    description: str = ""
    default_models: list = []


class ProviderUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None  # 空字符串 "" = 清除, None = 不变
    enabled: Optional[bool] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    default_models: Optional[list] = None


# ==================== Helpers ====================

def _mask_key(key: str) -> str:
    """脱敏 API Key"""
    if not key or len(key) < 8:
        return ""
    return "****" + key[-4:]


def _provider_to_out(p: AIProvider) -> dict:
    effective_key = (p.api_key or "").strip()
    if p.slug == "github" and not effective_key:
        # 兼容历史配置: 允许从环境变量回退
        effective_key = (settings.github_token or "").strip()

    return {
        "id": p.id,
        "slug": p.slug,
        "name": p.name,
        "provider_type": p.provider_type,
        "base_url": p.base_url,
        "api_key_set": bool(effective_key),
        "api_key_hint": _mask_key(effective_key),
        "enabled": p.enabled,
        "is_builtin": p.is_builtin,
        "is_preset": p.is_preset,
        "icon": p.icon,
        "description": p.description,
        "default_models": p.default_models or [],
    }


# ==================== 种子初始化 ====================

async def seed_providers():
    """首次启动时初始化提供商数据 (不覆盖已有配置)"""
    async with async_session_maker() as db:
        result = await db.execute(select(AIProvider))
        existing_slugs = {p.slug for p in result.scalars().all()}

        added = 0
        for seed in ALL_SEED_PROVIDERS:
            if seed["slug"] not in existing_slugs:
                db.add(AIProvider(**seed))
                added += 1

        if added:
            await db.commit()
            logger.info(f"✅ 种子化 {added} 个 AI 服务提供商")
        else:
            logger.info("AI 服务提供商已存在, 跳过种子化")


# ==================== 公共查询 ====================

async def get_provider_by_slug(slug: str) -> Optional[AIProvider]:
    """按 slug 查找提供商 (供其他模块调用)"""
    async with async_session_maker() as db:
        result = await db.execute(
            select(AIProvider).where(AIProvider.slug == slug)
        )
        return result.scalar_one_or_none()


async def get_enabled_providers() -> List[AIProvider]:
    """获取所有已启用的提供商"""
    async with async_session_maker() as db:
        result = await db.execute(
            select(AIProvider).where(AIProvider.enabled == True)
        )
        return list(result.scalars().all())


# ==================== Routes ====================

@router.get("")
async def list_providers():
    """列出所有 AI 服务提供商 (API Key 脱敏)"""
    async with async_session_maker() as db:
        result = await db.execute(
            select(AIProvider).order_by(AIProvider.is_builtin.desc(), AIProvider.is_preset.desc(), AIProvider.id)
        )
        providers = result.scalars().all()
        return [_provider_to_out(p) for p in providers]


@router.post("")
async def create_provider(data: ProviderCreate):
    """新增自定义提供商"""
    async with async_session_maker() as db:
        # 检查 slug 唯一性
        existing = await db.execute(
            select(AIProvider).where(AIProvider.slug == data.slug)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(400, f"提供商标识 '{data.slug}' 已存在")

        provider = AIProvider(
            slug=data.slug,
            name=data.name,
            provider_type="openai_compatible",
            base_url=data.base_url,
            api_key=data.api_key,
            enabled=bool(data.api_key),  # 有 key 则自动启用
            is_builtin=False,
            is_preset=False,
            icon=data.icon,
            description=data.description,
            default_models=data.default_models,
        )
        db.add(provider)
        await db.commit()
        await db.refresh(provider)
        return _provider_to_out(provider)


@router.patch("/{slug}")
async def update_provider(slug: str, data: ProviderUpdate):
    """更新提供商配置 (API Key, enabled 等)"""
    async with async_session_maker() as db:
        result = await db.execute(
            select(AIProvider).where(AIProvider.slug == slug)
        )
        provider = result.scalar_one_or_none()
        if not provider:
            raise HTTPException(404, f"提供商 '{slug}' 不存在")

        if data.name is not None:
            provider.name = data.name
        if data.base_url is not None:
            # 内置提供商不允许改 base_url
            if not provider.is_builtin:
                provider.base_url = data.base_url
        if data.api_key is not None:
            provider.api_key = data.api_key
            # GitHub Models 作为“全局 AI Token”来源，通过统一配置服务同步
            if provider.slug == "github":
                from backend.services.config_service import set_github_token
                await set_github_token(data.api_key)
        if data.enabled is not None:
            provider.enabled = data.enabled
        if data.icon is not None:
            provider.icon = data.icon
        if data.description is not None:
            provider.description = data.description
        if data.default_models is not None:
            provider.default_models = data.default_models

        await db.commit()
        await db.refresh(provider)
        return _provider_to_out(provider)


@router.delete("/{slug}")
async def delete_provider(slug: str):
    """删除自定义提供商 (内置和预设不可删)"""
    async with async_session_maker() as db:
        result = await db.execute(
            select(AIProvider).where(AIProvider.slug == slug)
        )
        provider = result.scalar_one_or_none()
        if not provider:
            raise HTTPException(404, f"提供商 '{slug}' 不存在")
        if provider.is_builtin:
            raise HTTPException(400, "内置提供商不可删除")
        if provider.is_preset:
            raise HTTPException(400, "预设提供商不可删除，可以禁用")

        await db.delete(provider)
        await db.commit()
        return {"ok": True, "deleted": slug}


@router.post("/{slug}/test")
async def test_provider(slug: str):
    """测试提供商连接 (发送一个最小请求)"""
    async with async_session_maker() as db:
        result = await db.execute(
            select(AIProvider).where(AIProvider.slug == slug)
        )
        provider = result.scalar_one_or_none()
        if not provider:
            raise HTTPException(404, f"提供商 '{slug}' 不存在")

    # 内置提供商特殊处理
    if provider.provider_type == "github_models":
        return await _test_github_models(provider)
    elif provider.provider_type == "copilot":
        return await _test_copilot(provider)
    elif provider.provider_type == "antigravity":
        return await _test_antigravity(provider)
    else:
        return await _test_openai_compatible(provider)


@router.get("/{slug}/models")
async def fetch_provider_models(slug: str):
    """从提供商 API 获取可用模型列表"""
    async with async_session_maker() as db:
        result = await db.execute(
            select(AIProvider).where(AIProvider.slug == slug)
        )
        provider = result.scalar_one_or_none()
        if not provider:
            raise HTTPException(404, f"提供商 '{slug}' 不存在")

    if provider.provider_type == "openai_compatible":
        return await _fetch_openai_compatible_models(provider)
    else:
        # 内置提供商的模型由 models_api 管理
        return {"models": [], "message": "内置提供商模型由系统自动管理"}


@router.post("/seed-reset")
async def reset_providers():
    """重置提供商列表 (恢复预设, 保留 API Key)"""
    async with async_session_maker() as db:
        # 获取现有 provider 的 API Key (保留用户配置)
        result = await db.execute(select(AIProvider))
        existing = {p.slug: p.api_key for p in result.scalars().all()}

        # 删除非自定义的提供商，重新种子化
        for seed in ALL_SEED_PROVIDERS:
            slug = seed["slug"]
            result = await db.execute(
                select(AIProvider).where(AIProvider.slug == slug)
            )
            provider = result.scalar_one_or_none()

            if provider:
                # 更新 (保留 api_key 和 enabled)
                provider.name = seed["name"]
                provider.base_url = seed["base_url"]
                provider.icon = seed["icon"]
                provider.description = seed["description"]
                provider.default_models = seed["default_models"]
            else:
                new_p = AIProvider(**seed)
                # 恢复之前保存的 API Key
                if slug in existing and existing[slug]:
                    new_p.api_key = existing[slug]
                    new_p.enabled = True
                db.add(new_p)

        await db.commit()
    return {"ok": True, "message": "提供商列表已重置"}


# ==================== 测试函数 ====================

async def _test_github_models(provider: AIProvider) -> dict:
    """测试 GitHub Models API"""
    token = (provider.api_key or settings.github_token or "").strip()
    if not token:
        return {"success": False, "message": "未配置 GitHub Models 全局 Token"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{provider.base_url}/models",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                count = len(data) if isinstance(data, list) else len(data.get("data", data.get("models", [])))
                return {"success": True, "message": f"连接正常, 获取到 {count} 个模型"}
            else:
                return {"success": False, "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "message": f"连接失败: {str(e)}"}


async def _test_copilot(provider: AIProvider) -> dict:
    """测试 Copilot API"""
    from backend.services.copilot_auth import copilot_auth
    if not copilot_auth.is_authenticated:
        return {"success": False, "message": "Copilot 未授权，请先完成 OAuth 认证"}

    try:
        session_token = await copilot_auth.ensure_session()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{provider.base_url}/models",
                headers={
                    "Authorization": f"Bearer {session_token}",
                    "Accept": "application/json",
                    "editor-version": "vscode/1.96.0",
                    "editor-plugin-version": "copilot-chat/0.24.0",
                    "copilot-integration-id": "vscode-chat",
                },
            )
            if resp.status_code == 200:
                return {"success": True, "message": "Copilot 连接正常"}
            else:
                return {"success": False, "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "message": f"连接失败: {str(e)}"}


async def _test_antigravity(provider: AIProvider) -> dict:
    """测试 Anti-Gravity API"""
    from backend.services.antigravity_auth import antigravity_auth
    if not antigravity_auth.is_authenticated:
        return {"success": False, "message": "Anti-Gravity 未授权，请先完成 Google 账号授权"}

    try:
        access_token = await antigravity_auth.ensure_token()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{provider.base_url}/models",
                headers={
                    "Authorization": f"Bearer {access_token}",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                model_count = len(data.get("data", []))
                return {"success": True, "message": f"Anti-Gravity 连接正常, 发现 {model_count} 个模型"}
            else:
                return {"success": False, "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "message": f"连接失败: {str(e)}"}


async def _test_openai_compatible(provider: AIProvider) -> dict:
    """测试 OpenAI 兼容 API (发送最小 chat 请求)"""
    if not provider.api_key:
        return {"success": False, "message": "请先配置 API Key"}

    # 优先用预设模型列表中的第一个模型做测试
    test_model = "gpt-3.5-turbo"
    if provider.default_models:
        test_model = provider.default_models[0].get("name", test_model)

    base_url = provider.base_url.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {provider.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": test_model,
                    "messages": [{"role": "user", "content": "Say OK"}],
                    "max_tokens": 5,
                    "stream": False,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                return {
                    "success": True,
                    "message": f"连接正常 (模型: {test_model}, 回复: {reply[:50]})",
                    "model_tested": test_model,
                }
            elif resp.status_code == 401:
                return {"success": False, "message": "API Key 无效或已过期 (401)"}
            elif resp.status_code == 403:
                return {"success": False, "message": "权限不足 (403), 请检查 API Key 权限"}
            else:
                return {"success": False, "message": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except httpx.ConnectError as e:
        return {"success": False, "message": f"无法连接到 {base_url}: {str(e)}"}
    except httpx.TimeoutException:
        return {"success": False, "message": f"连接超时 (20s)"}
    except Exception as e:
        return {"success": False, "message": f"测试失败: {str(e)}"}


async def _fetch_openai_compatible_models(provider: AIProvider) -> dict:
    """从 OpenAI 兼容 API 获取模型列表"""
    if not provider.api_key:
        return {"models": provider.default_models or [], "source": "presets", "message": "未配置 API Key, 显示预设模型"}

    base_url = provider.base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{base_url}/models",
                headers={
                    "Authorization": f"Bearer {provider.api_key}",
                    "Accept": "application/json",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                model_list = data.get("data", data) if isinstance(data, dict) else data
                models = []
                for m in (model_list if isinstance(model_list, list) else []):
                    mid = m.get("id") or m.get("name", "")
                    if mid:
                        models.append({
                            "name": mid,
                            "friendly_name": m.get("name", mid),
                            "owned_by": m.get("owned_by", ""),
                        })
                return {"models": models, "source": "api", "count": len(models)}
            else:
                return {"models": provider.default_models or [], "source": "presets",
                        "message": f"API 返回 {resp.status_code}, 使用预设模型列表"}
    except Exception as e:
        return {"models": provider.default_models or [], "source": "presets",
                "message": f"获取模型列表失败: {str(e)}, 使用预设模型列表"}
