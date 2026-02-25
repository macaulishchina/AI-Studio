"""
设计院 (Studio) - 模型配置管理 API

管理自定义模型列表和模型能力覆盖:
  - 自定义模型: 替代硬编码的 _COPILOT_PRO_EXTRA_MODELS / _COPILOT_EXCLUSIVE_MODELS
  - 能力覆盖: 手动修正模型的 token 限制、vision/tools/reasoning 能力
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, delete as sa_delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.database import get_db
from studio.backend.models import CustomModel, ModelCapabilityOverride
from studio.backend.core.model_capabilities import capability_cache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/model-config", tags=["Model Config"])


# ==================== Schemas ====================

class CustomModelOut(BaseModel):
    id: int
    name: str
    friendly_name: str
    model_family: str
    task: str
    tags: list
    summary: str
    api_backend: str
    enabled: bool
    is_seed: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class CustomModelCreate(BaseModel):
    name: str = Field(..., max_length=200, description="模型名 (用于 API 调用)")
    friendly_name: str = Field("", max_length=200, description="显示名")
    model_family: str = Field("", max_length=100, description="厂商: openai, anthropic, google, ...")
    task: str = Field("chat-completion", max_length=100)
    tags: list = Field(default_factory=list, description="能力标签: reasoning, agents, multimodal, ...")
    summary: str = Field("", max_length=500)
    api_backend: str = Field("models", description="API 后端: models / copilot")
    enabled: bool = True


class CustomModelUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    friendly_name: Optional[str] = Field(None, max_length=200)
    model_family: Optional[str] = Field(None, max_length=100)
    task: Optional[str] = Field(None, max_length=100)
    tags: Optional[list] = None
    summary: Optional[str] = Field(None, max_length=500)
    api_backend: Optional[str] = None
    enabled: Optional[bool] = None


class CapabilityOverrideOut(BaseModel):
    id: int
    model_name: str
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    supports_vision: Optional[bool] = None
    supports_tools: Optional[bool] = None
    is_reasoning: Optional[bool] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class CapabilityOverrideUpdate(BaseModel):
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    supports_vision: Optional[bool] = None
    supports_tools: Optional[bool] = None
    is_reasoning: Optional[bool] = None


# ==================== 种子数据 ====================

# Copilot Pro+ 自定义模型 (backend="models")
# 这些模型已通过实际 API 调用验证可用，但 /models 列表端点不返回
_SEED_EXTRA_MODELS = [
    # OpenAI
    {"name": "o1", "friendly_name": "OpenAI o1", "model_family": "openai", "tags": ["reasoning"], "summary": "OpenAI 推理模型，擅长复杂逻辑和数学"},
    {"name": "o3", "friendly_name": "OpenAI o3", "model_family": "openai", "tags": ["reasoning"], "summary": "OpenAI o3 推理模型"},
    {"name": "o3-mini", "friendly_name": "OpenAI o3-mini", "model_family": "openai", "tags": ["reasoning"], "summary": "OpenAI o3 轻量推理模型"},
    {"name": "o4-mini", "friendly_name": "OpenAI o4-mini", "model_family": "openai", "tags": ["reasoning", "agents"], "summary": "OpenAI 最新推理模型"},
    {"name": "gpt-4.1", "friendly_name": "GPT-4.1", "model_family": "openai", "tags": ["multipurpose", "multimodal", "agents"], "summary": "OpenAI GPT-4.1，全面升级版"},
    {"name": "gpt-4.1-mini", "friendly_name": "GPT-4.1 Mini", "model_family": "openai", "tags": ["multipurpose", "multimodal", "agents"], "summary": "GPT-4.1 轻量版，速度快成本低"},
    {"name": "gpt-4.1-nano", "friendly_name": "GPT-4.1 Nano", "model_family": "openai", "tags": ["multipurpose", "multimodal"], "summary": "GPT-4.1 超轻量版，极致速度"},
    # DeepSeek
    {"name": "DeepSeek-R1", "friendly_name": "DeepSeek R1", "model_family": "deepseek", "tags": ["reasoning"], "summary": "DeepSeek 推理模型，中文能力出色"},
    # Mistral
    {"name": "Codestral-2501", "friendly_name": "Codestral (Mistral)", "model_family": "mistralai", "tags": ["agents"], "summary": "Mistral 专注代码的模型"},
    {"name": "Mistral-Small-2503", "friendly_name": "Mistral Small", "model_family": "mistralai", "tags": ["multipurpose"], "summary": "Mistral AI 轻量模型"},
    # Meta
    {"name": "Llama-4-Scout-17B-16E-Instruct", "friendly_name": "Llama 4 Scout", "model_family": "meta", "tags": ["conversation"], "summary": "Meta Llama 4 Scout 模型"},
    {"name": "Llama-4-Maverick-17B-128E-Instruct-FP8", "friendly_name": "Llama 4 Maverick", "model_family": "meta", "tags": ["conversation"], "summary": "Meta Llama 4 Maverick 大模型"},
    # Microsoft
    {"name": "Phi-4", "friendly_name": "Phi-4", "model_family": "microsoft", "tags": ["reasoning"], "summary": "Microsoft Phi-4 小模型"},
    {"name": "Phi-4-multimodal-instruct", "friendly_name": "Phi-4 Multimodal", "model_family": "microsoft", "tags": ["multimodal"], "summary": "Microsoft Phi-4 多模态版"},
    {"name": "Phi-4-mini-instruct", "friendly_name": "Phi-4 Mini", "model_family": "microsoft", "tags": ["multipurpose"], "summary": "Microsoft Phi-4 超轻量版"},
    {"name": "MAI-DS-R1", "friendly_name": "MAI DeepSeek R1", "model_family": "microsoft", "tags": ["reasoning"], "summary": "Microsoft 基于 DeepSeek 的推理蒸馏模型"},
    # Cohere
    {"name": "cohere-command-a", "friendly_name": "Cohere Command A", "model_family": "cohere", "tags": ["agents"], "summary": "Cohere 最新 Agent 模型"},
    {"name": "Cohere-command-r-plus-08-2024", "friendly_name": "Cohere Command R+", "model_family": "cohere", "tags": ["rag", "agents"], "summary": "Cohere RAG 增强模型"},
]

# Copilot 专属模型 (backend="copilot")
_SEED_COPILOT_MODELS = [
    # Anthropic
    {"name": "claude-opus-4-20250514", "friendly_name": "Claude Opus 4", "model_family": "anthropic", "tags": ["multimodal", "agents", "reasoning"], "summary": "Anthropic 旗舰模型，最强综合能力"},
    {"name": "claude-sonnet-4-20250514", "friendly_name": "Claude Sonnet 4", "model_family": "anthropic", "tags": ["multimodal", "agents", "reasoning"], "summary": "Anthropic 最强编码模型，支持图片输入"},
    {"name": "claude-3.5-sonnet", "friendly_name": "Claude 3.5 Sonnet", "model_family": "anthropic", "tags": ["multimodal", "agents"], "summary": "Anthropic 高性价比模型，综合能力强"},
    {"name": "claude-3.7-sonnet", "friendly_name": "Claude 3.7 Sonnet", "model_family": "anthropic", "tags": ["multimodal", "agents", "reasoning"], "summary": "Anthropic 扩展思考模型"},
    # Google
    {"name": "gemini-2.0-flash", "friendly_name": "Gemini 2.0 Flash", "model_family": "google", "tags": ["multimodal", "agents"], "summary": "Google 快速多模态模型"},
    {"name": "gemini-2.5-pro", "friendly_name": "Gemini 2.5 Pro", "model_family": "google", "tags": ["multimodal", "agents", "reasoning"], "summary": "Google 旗舰推理模型"},
    # xAI
    {"name": "grok-3", "friendly_name": "Grok 3", "model_family": "xai", "tags": ["multipurpose"], "summary": "xAI Grok 3 大模型"},
    # OpenAI (Copilot 版, 可能与 Models API 重复, 两路保留)
    {"name": "gpt-4o", "friendly_name": "GPT-4o", "model_family": "openai", "tags": ["multimodal", "agents"], "summary": "OpenAI 旗舰多模态模型 (via Copilot)"},
    {"name": "o4-mini", "friendly_name": "o4-mini", "model_family": "openai", "tags": ["reasoning", "agents"], "summary": "OpenAI 最新推理模型 (via Copilot)"},
    {"name": "o3", "friendly_name": "o3", "model_family": "openai", "tags": ["reasoning"], "summary": "OpenAI o3 推理模型 (via Copilot)"},
]


async def seed_custom_models(db: AsyncSession):
    """
    历史兼容函数: 自定义模型种子已废弃，不再自动插入。
    """
    logger.info("自定义模型种子已禁用，跳过初始化")


async def get_custom_models_from_db(db: AsyncSession, api_backend: Optional[str] = None) -> list:
    """从数据库加载自定义模型列表, 返回与原硬编码格式兼容的 dict 列表"""
    stmt = select(CustomModel).where(CustomModel.enabled == True)
    if api_backend:
        stmt = stmt.where(CustomModel.api_backend == api_backend)
    result = await db.execute(stmt)
    rows = result.scalars().all()

    return [
        {
            "name": r.name,
            "friendly_name": r.friendly_name,
            "model_family": r.model_family,
            "task": r.task,
            "tags": r.tags or [],
            "summary": r.summary,
        }
        for r in rows
    ]


async def get_capability_overrides_map(db: AsyncSession) -> dict:
    """从数据库加载所有能力覆盖, 返回 {model_name: ModelCapabilityOverride} 映射"""
    result = await db.execute(select(ModelCapabilityOverride))
    rows = result.scalars().all()
    return {r.model_name: r for r in rows}


# ==================== 自定义模型 CRUD ====================

@router.get("/models", response_model=List[CustomModelOut])
async def list_custom_models(
    api_backend: Optional[str] = Query(None, description="筛选: models / copilot"),
    search: Optional[str] = Query(None, description="名称模糊搜索"),
    enabled_only: bool = Query(False, description="仅启用的"),
    db: AsyncSession = Depends(get_db),
):
    """获取自定义模型列表 (支持筛选和搜索)"""
    stmt = select(CustomModel).order_by(CustomModel.api_backend, CustomModel.model_family, CustomModel.name)

    if api_backend:
        stmt = stmt.where(CustomModel.api_backend == api_backend)
    if enabled_only:
        stmt = stmt.where(CustomModel.enabled == True)
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(
            CustomModel.name.ilike(pattern) | CustomModel.friendly_name.ilike(pattern) | CustomModel.model_family.ilike(pattern)
        )

    result = await db.execute(stmt)
    rows = result.scalars().all()

    out = []
    for r in rows:
        out.append(CustomModelOut(
            id=r.id,
            name=r.name,
            friendly_name=r.friendly_name,
            model_family=r.model_family,
            task=r.task,
            tags=r.tags or [],
            summary=r.summary,
            api_backend=r.api_backend,
            enabled=r.enabled,
            is_seed=r.is_seed,
            created_at=r.created_at.isoformat() if r.created_at else None,
            updated_at=r.updated_at.isoformat() if r.updated_at else None,
        ))
    return out


@router.post("/models", response_model=CustomModelOut)
async def create_custom_model(
    data: CustomModelCreate,
    db: AsyncSession = Depends(get_db),
):
    """添加自定义模型"""
    # 检查重复
    existing = await db.execute(
        select(CustomModel).where(CustomModel.name == data.name, CustomModel.api_backend == data.api_backend)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, f"模型 {data.name} (backend={data.api_backend}) 已存在")

    model = CustomModel(
        name=data.name,
        friendly_name=data.friendly_name or data.name,
        model_family=data.model_family,
        task=data.task,
        tags=data.tags,
        summary=data.summary,
        api_backend=data.api_backend,
        enabled=data.enabled,
        is_seed=False,  # 用户创建的
    )
    db.add(model)
    await db.flush()

    # 刷新模型缓存
    await _invalidate_model_cache()

    return CustomModelOut(
        id=model.id,
        name=model.name,
        friendly_name=model.friendly_name,
        model_family=model.model_family,
        task=model.task,
        tags=model.tags or [],
        summary=model.summary,
        api_backend=model.api_backend,
        enabled=model.enabled,
        is_seed=model.is_seed,
        created_at=model.created_at.isoformat() if model.created_at else None,
        updated_at=model.updated_at.isoformat() if model.updated_at else None,
    )


@router.put("/models/{model_id}", response_model=CustomModelOut)
async def update_custom_model(
    model_id: int,
    data: CustomModelUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新自定义模型"""
    model = await db.get(CustomModel, model_id)
    if not model:
        raise HTTPException(404, f"模型 ID {model_id} 不存在")

    update_fields = data.model_dump(exclude_unset=True)
    for key, val in update_fields.items():
        setattr(model, key, val)
    model.updated_at = datetime.utcnow()

    await db.flush()
    await _invalidate_model_cache()

    return CustomModelOut(
        id=model.id,
        name=model.name,
        friendly_name=model.friendly_name,
        model_family=model.model_family,
        task=model.task,
        tags=model.tags or [],
        summary=model.summary,
        api_backend=model.api_backend,
        enabled=model.enabled,
        is_seed=model.is_seed,
        created_at=model.created_at.isoformat() if model.created_at else None,
        updated_at=model.updated_at.isoformat() if model.updated_at else None,
    )


@router.delete("/models/{model_id}")
async def delete_custom_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
):
    """删除自定义模型"""
    model = await db.get(CustomModel, model_id)
    if not model:
        raise HTTPException(404, f"模型 ID {model_id} 不存在")

    await db.delete(model)
    await db.flush()
    await _invalidate_model_cache()

    return {"ok": True, "deleted": model.name}


@router.post("/models/reset")
async def reset_custom_models(
    db: AsyncSession = Depends(get_db),
):
    """清空历史自定义模型数据（不再重新插入种子）"""
    await db.execute(sa_delete(CustomModel))
    await db.flush()
    await _invalidate_model_cache()

    count = await db.scalar(select(func.count(CustomModel.id)))
    return {"ok": True, "message": f"已清空历史自定义模型，共 {count} 个模型"}


# ==================== 能力覆盖 CRUD ====================

@router.get("/capabilities", response_model=List[CapabilityOverrideOut])
async def list_capability_overrides(
    search: Optional[str] = Query(None, description="模型名模糊搜索"),
    db: AsyncSession = Depends(get_db),
):
    """获取所有能力覆盖记录"""
    stmt = select(ModelCapabilityOverride).order_by(ModelCapabilityOverride.model_name)
    if search:
        stmt = stmt.where(ModelCapabilityOverride.model_name.ilike(f"%{search}%"))

    result = await db.execute(stmt)
    rows = result.scalars().all()

    return [
        CapabilityOverrideOut(
            id=r.id,
            model_name=r.model_name,
            max_input_tokens=r.max_input_tokens,
            max_output_tokens=r.max_output_tokens,
            supports_vision=r.supports_vision,
            supports_tools=r.supports_tools,
            is_reasoning=r.is_reasoning,
            updated_at=r.updated_at.isoformat() if r.updated_at else None,
        )
        for r in rows
    ]


@router.put("/capabilities/{model_name:path}", response_model=CapabilityOverrideOut)
async def upsert_capability_override(
    model_name: str,
    data: CapabilityOverrideUpdate,
    db: AsyncSession = Depends(get_db),
):
    """创建或更新模型能力覆盖"""
    clean = model_name.removeprefix("copilot:").lower()

    result = await db.execute(
        select(ModelCapabilityOverride).where(ModelCapabilityOverride.model_name == clean)
    )
    override = result.scalar_one_or_none()

    if override:
        update_fields = data.model_dump(exclude_unset=True)
        for key, val in update_fields.items():
            setattr(override, key, val)
        override.updated_at = datetime.utcnow()
    else:
        override = ModelCapabilityOverride(
            model_name=clean,
            **data.model_dump(exclude_unset=True),
        )
        db.add(override)

    await db.flush()

    # 同步更新内存缓存
    _sync_override_to_cache(clean, override)
    await _invalidate_model_cache()

    return CapabilityOverrideOut(
        id=override.id,
        model_name=override.model_name,
        max_input_tokens=override.max_input_tokens,
        max_output_tokens=override.max_output_tokens,
        supports_vision=override.supports_vision,
        supports_tools=override.supports_tools,
        is_reasoning=override.is_reasoning,
        updated_at=override.updated_at.isoformat() if override.updated_at else None,
    )


@router.delete("/capabilities/{model_name:path}")
async def delete_capability_override(
    model_name: str,
    db: AsyncSession = Depends(get_db),
):
    """删除单个模型的能力覆盖 (恢复自动检测)"""
    clean = model_name.removeprefix("copilot:").lower()

    result = await db.execute(
        select(ModelCapabilityOverride).where(ModelCapabilityOverride.model_name == clean)
    )
    override = result.scalar_one_or_none()
    if not override:
        raise HTTPException(404, f"模型 {clean} 没有能力覆盖记录")

    await db.delete(override)
    await db.flush()

    # 从内存缓存中移除
    capability_cache.remove_db_override(clean)
    await _invalidate_model_cache()

    return {"ok": True, "deleted": clean}


@router.post("/capabilities/reset-all")
async def reset_all_capability_overrides(
    db: AsyncSession = Depends(get_db),
):
    """清除所有能力覆盖 (全部恢复自动检测)"""
    await db.execute(sa_delete(ModelCapabilityOverride))
    await db.flush()
    capability_cache.clear_db_overrides()
    await _invalidate_model_cache()
    return {"ok": True, "message": "所有能力覆盖已清除"}


# ==================== 合并视图: 模型+能力 ====================

@router.get("/merged")
async def get_merged_model_capabilities(
    search: Optional[str] = Query(None, description="名称模糊搜索"),
    db: AsyncSession = Depends(get_db),
):
    """
    获取所有模型的合并能力视图 (模型列表 + 能力覆盖 + 自动检测值)。
    前端管理界面使用此接口展示完整数据。
    """
    from studio.backend.api.models_api import _model_cache

    try:
        models = await _model_cache.get_models()
    except Exception:
        models = []

    # 加载 DB 覆盖
    overrides = await get_capability_overrides_map(db)

    result = []
    for m in models:
        clean = m.id.removeprefix("copilot:").lower()
        ov = overrides.get(clean)

        if search:
            pattern = search.lower()
            if pattern not in m.name.lower() and pattern not in m.id.lower() and pattern not in m.publisher.lower():
                continue

        result.append({
            "id": m.id,
            "name": m.name,
            "publisher": m.publisher,
            "api_backend": m.api_backend,
            "pricing_note": m.pricing_note,
            "premium_multiplier": m.premium_multiplier,
            "free_multiplier": m.free_multiplier,
            "is_custom": m.is_custom,
            "provider_slug": m.provider_slug,
            # 自动检测值
            "auto_max_input": m.max_input_tokens,
            "auto_max_output": m.max_output_tokens,
            "auto_supports_vision": m.supports_vision,
            "auto_supports_tools": m.supports_tools,
            "auto_is_reasoning": m.is_reasoning,
            # 覆盖值 (null = 未覆盖)
            "override_max_input": ov.max_input_tokens if ov else None,
            "override_max_output": ov.max_output_tokens if ov else None,
            "override_supports_vision": ov.supports_vision if ov else None,
            "override_supports_tools": ov.supports_tools if ov else None,
            "override_is_reasoning": ov.is_reasoning if ov else None,
            # 生效值 (覆盖优先)
            "eff_max_input": (ov.max_input_tokens if ov and ov.max_input_tokens is not None else m.max_input_tokens),
            "eff_max_output": (ov.max_output_tokens if ov and ov.max_output_tokens is not None else m.max_output_tokens),
            "eff_supports_vision": (ov.supports_vision if ov and ov.supports_vision is not None else m.supports_vision),
            "eff_supports_tools": (ov.supports_tools if ov and ov.supports_tools is not None else m.supports_tools),
            "eff_is_reasoning": (ov.is_reasoning if ov and ov.is_reasoning is not None else m.is_reasoning),
            "has_override": ov is not None,
        })

    return result


# ==================== 辅助函数 ====================

def _sync_override_to_cache(clean_name: str, override: ModelCapabilityOverride):
    """将 DB 覆盖同步到内存缓存"""
    if override.max_input_tokens is not None or override.max_output_tokens is not None:
        capability_cache.set_db_override(
            clean_name,
            max_input=override.max_input_tokens,
            max_output=override.max_output_tokens,
        )


async def _invalidate_model_cache():
    """使模型缓存过期, 下次请求时重新拉取"""
    try:
        from studio.backend.api.models_api import _model_cache
        _model_cache._last_fetch = 0  # 强制过期
    except Exception:
        pass


async def load_capability_overrides_to_cache():
    """启动时从 DB 加载所有能力覆盖到内存缓存 (在 lifespan 中调用)"""
    from studio.backend.core.database import async_session_maker
    try:
        async with async_session_maker() as db:
            result = await db.execute(select(ModelCapabilityOverride))
            rows = result.scalars().all()
            count = 0
            for r in rows:
                if r.max_input_tokens is not None or r.max_output_tokens is not None:
                    capability_cache.set_db_override(
                        r.model_name,
                        max_input=r.max_input_tokens,
                        max_output=r.max_output_tokens,
                    )
                    count += 1
            if count:
                logger.info(f"✅ 从 DB 加载了 {count} 条能力覆盖到内存缓存")
    except Exception as e:
        logger.warning(f"加载能力覆盖失败: {e}")
