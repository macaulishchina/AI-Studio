"""
设计院 (Studio) - AI 模型管理 API

从 GitHub Models API 动态获取可用模型列表，带本地缓存。
你的 GitHub PAT (Copilot Pro+) 决定了你能用哪些模型。

GitHub Models API 端点:
  GET https://models.inference.ai.azure.com/models
  Authorization: Bearer <GITHUB_TOKEN>
"""
import asyncio
import logging
import re
import time
from typing import List, Optional, Dict, Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from studio.backend.core.config import settings
from studio.backend.core.model_capabilities import capability_cache
from studio.backend.models import ModelCapabilityOverride
from studio.backend.services.copilot_auth import copilot_auth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/models", tags=["AI Models"])


# ==================== 模型定义 ====================

class ModelInfo(BaseModel):
    """模型信息 (从 GitHub Models API 映射)"""
    id: str = Field(description="模型 ID，用于 API 调用 (Copilot 模型以 'copilot:' 前缀)")
    name: str = Field(description="模型显示名称")
    publisher: str = Field("", description="发布者/厂商 (如 OpenAI, Anthropic)")
    registry: str = Field("", description="注册源 (如 azure-openai, github)")
    description: str = ""
    summary: str = ""
    category: str = Field("discussion", description="discussion / implementation / both")
    max_input_tokens: int = 0
    max_output_tokens: int = 4096
    supports_vision: bool = False
    supports_tools: bool = False
    supports_json_output: bool = False
    is_reasoning: bool = Field(False, description="推理模型需要 max_completion_tokens 替代 max_tokens")
    api_backend: str = Field("models", description="API 后端: models (GitHub Models) / copilot (Copilot API)")
    pricing_tier: str = Field("free", description="定价: free(免费) / premium(消耗高级请求)")
    premium_multiplier: float = Field(0, description="付费用户每次消耗高级请求数 (0=免费, 1=1次, 0.33=⅓次)")
    free_multiplier: Optional[float] = Field(None, description="免费用户每次消耗高级请求数 (None=需订阅, 1=可用)")
    is_deprecated: bool = Field(False, description="是否即将弃用")
    pricing_note: str = Field("", description="定价/弃用说明")
    task: str = Field("", description="模型任务类型 (chat-completion, etc)")
    is_custom: bool = Field(False, description="是否来自 DB 自定义模型 (用于全局开关过滤)")
    model_family: str = Field("", description="二级分类/厂商族 (如 OpenAI, DeepSeek, MiniMax 等)")
    provider_slug: str = Field("", description="提供商标识 (github/copilot/deepseek 等)")
    provider_icon: str = Field("", description="提供商图标")
    # 原始数据保留
    raw_capabilities: Dict[str, Any] = Field(default_factory=dict)


# ==================== 模型缓存 ====================

class ModelCache:
    """模型列表缓存，避免每次请求都调 GitHub API"""

    def __init__(self, ttl_seconds: int = 600):
        self.ttl = ttl_seconds  # 默认缓存 10 分钟
        self._models: List[ModelInfo] = []
        self._last_fetch: float = 0
        self._lock = asyncio.Lock()
        self._fetch_error: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        return time.time() - self._last_fetch > self.ttl

    @property
    def models(self) -> List[ModelInfo]:
        return self._models

    @property
    def last_error(self) -> Optional[str]:
        return self._fetch_error

    async def get_models(self, force_refresh: bool = False) -> List[ModelInfo]:
        """获取模型列表，过期则自动刷新"""
        if not force_refresh and not self.is_expired and self._models:
            return self._models

        async with self._lock:
            # 双检锁 — 其他协程可能已刷新
            if not force_refresh and not self.is_expired and self._models:
                return self._models

            try:
                self._models = await _fetch_github_models()
                self._last_fetch = time.time()
                self._fetch_error = None
                logger.info(f"✅ 从 GitHub Models API 获取到 {len(self._models)} 个模型")
            except Exception as e:
                self._fetch_error = str(e)
                logger.error(f"❌ 获取 GitHub 模型列表失败: {e}")
                # 如果有旧缓存就继续用，不置空
                if not self._models:
                    raise

        return self._models


# 全局单例
_model_cache = ModelCache(ttl_seconds=600)  # 缓存 10 分钟, 手动刷新按钮触发 force_refresh


# ==================== GitHub Models API 调用 ====================

# 已知的强代码实现能力的模型关键字 (用于自动分类)
_STRONG_CODE_KEYWORDS = {
    "gpt-4o", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
    "o1", "o1-mini", "o3", "o3-mini", "o4-mini",
    "claude-sonnet-4", "claude-3.5-sonnet", "claude-3-opus",
    "deepseek-chat", "deepseek-v3", "deepseek-r1",
    "codestral",
    "mistral-large",
}

# model_family → 显示名映射
_FAMILY_DISPLAY = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "meta": "Meta",
    "mistralai": "Mistral AI",
    "mistral-ai": "Mistral AI",
    "mistral": "Mistral AI",
    "deepseek": "DeepSeek",
    "google": "Google",
    "microsoft": "Microsoft",
    "cohere": "Cohere",
    "ai21 labs": "AI21 Labs",
    "xai": "xAI",
    "unknown": "Other",
}

# tags → 能力映射
_VISION_TAGS = {"multimodal", "vision", "image"}
_TOOLS_TAGS = {"agents", "tools", "function-calling"}
_REASONING_TAGS = {"reasoning"}

# 已知的推理模型 (需要 max_completion_tokens 替代 max_tokens, 不支持 system prompt)
_REASONING_MODEL_PATTERNS = {"o1", "o3", "o3-mini", "o4-mini"}


# ==================== 定价信息 ====================

# Copilot API 模型定价倍率表 (基于 GitHub Copilot 官方定价)
# 两列: paid = 付费用户倍率 (0=计划内免费, >0=消耗高级请求)
#        free = 免费用户倍率 (1=可用消耗1次高级请求, None=需订阅)
# 来源: https://docs.github.com/en/copilot/concepts/billing/copilot-requests#model-multipliers
# 最后更新: 2026-02-16 (可通过设置页「刷新定价」按钮从官方文档同步)
_COPILOT_PREMIUM_COST: Dict[str, Dict[str, Any]] = {
    # OpenAI — 免费包含模型 (paid plans)
    "gpt-4o": {"paid": 0, "free": 1},
    "gpt-4o-mini": {"paid": 0, "free": 1},
    "gpt-4": {"paid": 0, "free": 1},
    "gpt-4.1": {"paid": 0, "free": 1},
    "gpt-5-mini": {"paid": 0, "free": 1},
    "raptor-mini": {"paid": 0, "free": 1},
    # OpenAI — 高级模型
    "gpt-4.1-mini": {"paid": 0.25, "free": 1},
    "gpt-4.1-nano": {"paid": 0, "free": 1},
    "gpt-5": {"paid": 1.0, "free": None},
    "gpt-5-codex": {"paid": 1.0, "free": None},
    "gpt-5.1": {"paid": 1.0, "free": None},
    "gpt-5.1-codex": {"paid": 1.0, "free": None},
    "gpt-5.1-codex-max": {"paid": 1.0, "free": None},
    "gpt-5.1-codex-mini": {"paid": 0.33, "free": 1},
    "gpt-5.2": {"paid": 1.0, "free": None},
    "gpt-5.2-codex": {"paid": 1.0, "free": None},
    "gpt-5.3-codex": {"paid": 1.0, "free": None},
    "o1": {"paid": 1.0, "free": None},
    "o1-mini": {"paid": 0.5, "free": 1},
    "o3": {"paid": 1.0, "free": None},
    "o3-mini": {"paid": 0.33, "free": 1},
    "o4-mini": {"paid": 0.33, "free": 1},
    # Anthropic
    "claude-sonnet-4": {"paid": 1.0, "free": 1},
    "claude-sonnet-4-20250514": {"paid": 1.0, "free": 1},
    "claude-sonnet-4.5": {"paid": 1.0, "free": None},
    "claude-opus-41": {"paid": 10.0, "free": None},
    "claude-opus-4.5": {"paid": 3.0, "free": None},
    "claude-opus-4.6": {"paid": 3.0, "free": None},
    "claude-opus-4.6-fast": {"paid": 9.0, "free": None},
    "claude-haiku-4.5": {"paid": 0.33, "free": 1},
    "claude-3.5-sonnet": {"paid": 0, "free": 1},
    "claude-3.7-sonnet": {"paid": 1.0, "free": 1},
    # Google
    "gemini-2.0-flash": {"paid": 0.25, "free": 1},
    "gemini-2.5-pro": {"paid": 1.0, "free": None},
    "gemini-3-flash-preview": {"paid": 0.33, "free": 1},
    "gemini-3-pro-preview": {"paid": 1.0, "free": None},
    # xAI
    "grok-3": {"paid": 1.0, "free": None},
    "grok-code-fast-1": {"paid": 0.25, "free": 1},
}

# 硬编码默认值副本 (用于 DB diff 比较)
_COPILOT_PREMIUM_COST_DEFAULTS: Dict[str, Dict[str, Any]] = dict(_COPILOT_PREMIUM_COST)

# 已知即将弃用 / 已有更新版本的模型
_DEPRECATED_MODELS: Dict[str, str] = {
    "claude-3.5-sonnet": "建议升级到 Claude Sonnet 4",
}

# 官方 token 上限来源 (GitHub Models API)
_OFFICIAL_MODELS_SOURCE_URL = f"{settings.github_models_endpoint}/models"
# 官方 Copilot 模型来源 (授权后可用)
_OFFICIAL_COPILOT_MODELS_URL = "https://api.githubcopilot.com/models"


async def load_pricing_overrides_from_db():
    """启动时从 DB 加载定价覆盖; 若有记录则完整替换运行时定价表"""
    global _COPILOT_PREMIUM_COST
    from sqlalchemy import select
    from studio.backend.core.database import async_session_maker
    try:
        async with async_session_maker() as db:
            result = await db.execute(
                select(ModelCapabilityOverride).where(
                    ModelCapabilityOverride.premium_paid.isnot(None)
                )
            )
            rows = result.scalars().all()
            if not rows:
                return
            # DB 有定价数据 → 合并到硬编码上 (只更新/新增, 不删除)
            db_pricing: Dict[str, Dict[str, Any]] = {}
            for r in rows:
                free_val = r.premium_free if r.premium_free != -1 else None
                db_pricing[r.model_name] = {
                    "paid": r.premium_paid,
                    "free": free_val,
                }
            _COPILOT_PREMIUM_COST.update(db_pricing)
            logger.info(f"✅ 从 DB 加载了 {len(rows)} 条定价覆盖, 运行时定价表共 {len(_COPILOT_PREMIUM_COST)} 条")
    except Exception as e:
        logger.warning(f"加载定价覆盖失败: {e}")


def _normalize_model_key(name: str) -> str:
    key = (name or "").strip().lower()
    key = key.replace("**", "")
    key = key.replace("`", "")
    key = re.sub(r"\s+", "-", key)
    key = key.replace(":", "-")
    key = key.replace("_", "-")
    return key


def _parse_token_value(raw: str) -> int:
    if not raw:
        return 0
    s = raw.strip().lower()
    if "unknown" in s or "unclear" in s or s == "-":
        return 0
    s = s.replace("tokens", "").replace("token", "").strip()
    m = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(k|m)?", s)
    if not m:
        return 0
    value = float(m.group(1).replace(",", ""))
    unit = m.group(2)
    if unit == "k":
        value *= 1000
    elif unit == "m":
        value *= 1000000
    return int(value)


def _parse_online_context_limits(markdown_text: str) -> Dict[str, tuple[int, int]]:
    """从 llm-context-limits README 的 Markdown 表格解析 token 上限"""
    limits: Dict[str, tuple[int, int]] = {}
    lines = markdown_text.splitlines()
    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        if set(line.replace("|", "").strip()) <= {":", "-", " "}:
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 3:
            continue

        model_name = parts[0]
        if model_name.lower() in {"model", "model name", "endpoint"}:
            continue

        ctx = _parse_token_value(parts[1])
        out = _parse_token_value(parts[2])
        if ctx <= 0 and out <= 0:
            continue

        key = _normalize_model_key(model_name)
        if ctx <= 0:
            ctx = 128000
        if out <= 0:
            out = 4096
        limits[key] = (ctx, out)
    return limits


async def _fetch_online_context_limits() -> Dict[str, tuple[int, int]]:
    # 兼容旧调用名: 统一走官方 GitHub Models 来源
    return await _fetch_official_context_limits()


async def _fetch_official_copilot_context_limits() -> Dict[str, tuple[int, int]]:
    """从 Copilot 官方 /models 接口提取 token 上限（需要 Copilot 授权）。"""
    limits: Dict[str, tuple[int, int]] = {}
    raw_list = await _fetch_copilot_api_models()
    for raw in raw_list:
        model_name = (raw.get("id") or raw.get("name") or "").strip()
        if not model_name:
            continue
        max_in, max_out = _extract_limits_from_raw(raw)
        if max_in <= 0 and max_out <= 0:
            continue
        key = _normalize_model_key(model_name)
        if max_in <= 0:
            max_in = 128000
        if max_out <= 0:
            max_out = 4096
        limits[key] = (max_in, max_out)
    return limits


def _extract_limits_from_raw(raw: Dict[str, Any]) -> tuple[int, int]:
    """从官方 models 元数据提取输入/输出 token 上限。"""
    max_input = (
        raw.get("max_input_tokens")
        or raw.get("context_window")
        or raw.get("max_prompt_tokens")
        or (raw.get("capabilities", {}) or {}).get("limits", {}).get("max_prompt_tokens")
        or (raw.get("model_limits", {}) or {}).get("input_tokens")
        or (raw.get("limits", {}) or {}).get("max_input_tokens")
        or 0
    )
    max_output = (
        raw.get("max_output_tokens")
        or raw.get("max_completion_tokens")
        or (raw.get("capabilities", {}) or {}).get("limits", {}).get("max_output_tokens")
        or (raw.get("model_limits", {}) or {}).get("output_tokens")
        or (raw.get("limits", {}) or {}).get("max_output_tokens")
        or 0
    )

    try:
        max_input = int(max_input or 0)
    except Exception:
        max_input = 0
    try:
        max_output = int(max_output or 0)
    except Exception:
        max_output = 0
    return max_input, max_output


async def _fetch_official_context_limits() -> Dict[str, tuple[int, int]]:
    """从 GitHub Models 官方 /models 接口提取 token 上限。"""
    from studio.backend.api.provider_api import get_provider_by_slug

    github_provider = await get_provider_by_slug("github")
    token = ((github_provider.api_key if github_provider else "") or settings.github_token or "").strip()
    if not token:
        raise RuntimeError("未配置 GitHub Models 全局 Token")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(_OFFICIAL_MODELS_SOURCE_URL, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"官方 models 接口返回 HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()

    raw_list = data if isinstance(data, list) else (
        data.get("data") or data.get("models") or data.get("value") or []
    )

    limits: Dict[str, tuple[int, int]] = {}
    for raw in raw_list:
        model_name = (raw.get("name") or raw.get("id") or "").strip()
        if not model_name:
            continue
        max_in, max_out = _extract_limits_from_raw(raw)
        if max_in <= 0 and max_out <= 0:
            continue
        key = _normalize_model_key(model_name)
        if max_in <= 0:
            max_in = 128000
        if max_out <= 0:
            max_out = 4096
        limits[key] = (max_in, max_out)

    return limits


def _classify_model(model_name: str, task: str, supports_tools: bool) -> str:
    """根据模型名称和能力自动分类: discussion / implementation / both"""
    name_lower = model_name.lower()

    # 非 chat 类 → 不参与
    if task and "chat" not in task and "completion" not in task:
        return "discussion"

    # 已知强代码模型 → both
    for keyword in _STRONG_CODE_KEYWORDS:
        if keyword in name_lower:
            return "both"

    # 支持 tool calling / agents 标签的通常也能写代码
    if supports_tools:
        return "both"

    return "discussion"


def _parse_model(raw: Dict[str, Any], api_backend: str = "models") -> ModelInfo:
    """
    将 GitHub Models API 返回的原始数据解析为 ModelInfo

    api_backend: "models" (GitHub Models API) 或 "copilot" (Copilot API)

    GitHub Models API 响应字段:
      id:             azureml://registries/.../gpt-4o/versions/2 (长路径)
      name:           gpt-4o (短名称, 用于 chat API 调用!)
      friendly_name:  OpenAI GPT-4o (显示名)
      model_family:   openai (厂商)
      model_registry: azure-openai
      task:           chat-completion
      tags:           ["multipurpose", "multilingual", "multimodal"]
      description:    ...
      summary:        ...
    """
    # 关键: name 才是 chat/completions API 用的 model 参数
    model_name = raw.get("name") or raw.get("id", "unknown")
    friendly_name = raw.get("friendly_name") or model_name
    model_family = (raw.get("model_family") or "").lower()
    registry = raw.get("model_registry") or raw.get("registry", "")
    description = raw.get("description") or ""
    summary = raw.get("summary") or ""
    task = raw.get("task") or ""
    tags = set(t.lower() for t in (raw.get("tags") or []))

    # 通过 tags 检测能力
    supports_vision = bool(tags & _VISION_TAGS)
    supports_tools = bool(tags & _TOOLS_TAGS)

    # 额外: 基于模型名的 vision 推断 (描述中明确说支持 image)
    if not supports_vision:
        desc_lower = (description + summary).lower()
        if "image" in desc_lower and "input" in desc_lower:
            supports_vision = True

    # 推理模型检测
    is_reasoning = bool(tags & _REASONING_TAGS)
    # 更精确：已知推理模型
    name_lower = model_name.lower()
    for pattern in _REASONING_MODEL_PATTERNS:
        if name_lower == pattern or name_lower.startswith(pattern + "-"):
            is_reasoning = True
            break

    # publisher 显示名
    publisher = _FAMILY_DISPLAY.get(model_family, None)
    if not publisher:
        # 尝试从模型名猜测厂商再查表
        guessed = _guess_family(model_name)
        publisher = _FAMILY_DISPLAY.get(guessed, raw.get("publisher", model_family or model_name))

    # 分类
    category = _classify_model(model_name, task, supports_tools)

    # Copilot API 模型 ID 添加前缀
    model_id = f"copilot:{model_name}" if api_backend == "copilot" else model_name
    # 显示名不添加后端标识 (前端的分组标题已有 ☁️)
    display_name = friendly_name

    # 定价信息
    # 定价信息 (两列: paid=付费用户倍率, free=免费用户倍率)
    free_multiplier: Optional[float] = None
    if api_backend == "copilot":
        pricing_tier = "premium"
        # 精确匹配 → 前缀匹配 (处理带日期后缀的模型如 gpt-4o-2024-08-06)
        pricing_entry = _COPILOT_PREMIUM_COST.get(model_name, None)
        if pricing_entry is None:
            # 尝试前缀匹配 (去掉日期后缀)
            for known_name, entry in _COPILOT_PREMIUM_COST.items():
                if model_name.startswith(known_name):
                    pricing_entry = entry
                    break
        if pricing_entry:
            premium_multiplier = pricing_entry["paid"]
            free_multiplier = pricing_entry.get("free")
        else:
            premium_multiplier = 1.0  # 未知模型默认 1x (可能不准)
            free_multiplier = None
        if premium_multiplier == 0:
            pricing_tier = "free"
            pricing_note = "x0"
        else:
            pricing_note = f"x{premium_multiplier:g}"
    else:
        # GitHub Models API 模型始终免费
        pricing_tier = "free"
        premium_multiplier = 0.0
        free_multiplier = None  # GitHub Models API 不区分
        pricing_note = "x0"

    model_is_deprecated = model_name in _DEPRECATED_MODELS
    if model_is_deprecated:
        pricing_note += f" | {_DEPRECATED_MODELS[model_name]}"

    # Token 限制: 优先从 API 原始数据学习, 否则查能力缓存
    capability_cache.learn_from_api(model_id, raw)
    cap_input, cap_output = capability_cache.get_context_window(model_id)

    # provider 信息
    provider_slug = "copilot" if api_backend == "copilot" else "github"
    provider_icon = "☁️" if api_backend == "copilot" else "🐙"

    return ModelInfo(
        id=model_id,             # copilot:gpt-4o 或 gpt-4o
        name=display_name,       # 显示名
        publisher=publisher,
        registry=registry,
        description=description,
        summary=summary,
        category=category,
        max_input_tokens=cap_input,
        max_output_tokens=cap_output,
        supports_vision=supports_vision,
        supports_tools=supports_tools,
        supports_json_output=False,  # API 未返回此字段
        is_reasoning=is_reasoning,
        api_backend=api_backend,
        pricing_tier=pricing_tier,
        premium_multiplier=premium_multiplier,
        free_multiplier=free_multiplier,
        is_deprecated=model_is_deprecated,
        pricing_note=pricing_note,
        task=task,
        model_family=publisher,
        provider_slug=provider_slug,
        provider_icon=provider_icon,
        raw_capabilities={"tags": list(tags)},
    )


# ==================== Copilot 动态模型发现 ====================
# 旧硬编码模型列表已迁移到数据库 (custom_models 表)
# 种子数据见 studio/backend/api/model_config.py
# 用户可通过设置页面增删改补充模型

def _guess_family(model_name: str) -> str:
    """根据模型名称猜测 model_family"""
    n = model_name.lower()
    if "claude" in n or "anthropic" in n:
        return "anthropic"
    if "gpt" in n or n.startswith("o1") or n.startswith("o3") or n.startswith("o4"):
        return "openai"
    if "gemini" in n or "google" in n:
        return "google"
    if "grok" in n or "xai" in n:
        return "xai"
    if "deepseek" in n:
        return "deepseek"
    if "mistral" in n or "codestral" in n:
        return "mistral"
    if "llama" in n or "meta" in n:
        return "meta"
    return "unknown"


def _guess_tags(model_name: str) -> list:
    """根据模型名称猜测 tags"""
    n = model_name.lower()
    tags = []
    if any(k in n for k in ("opus", "pro", "sonnet-4", "gpt-4o", "o3", "grok-3")):
        tags.append("multimodal")
    if any(k in n for k in ("o1", "o3", "o4", "3.7", "reasoning", "think")):
        tags.append("reasoning")
    if "flash" in n or "mini" in n or "haiku" in n:
        tags.append("fast")
    tags.append("agents")
    return tags


async def _fetch_copilot_api_models() -> List[Dict[str, Any]]:
    """
    尝试从 Copilot API 动态获取可用模型列表。
    VS Code Copilot 使用 GET https://api.githubcopilot.com/models 获取模型。
    返回原始模型信息列表，失败返回空列表。
    """
    if not copilot_auth.is_authenticated:
        return []

    try:
        session_token = await copilot_auth.ensure_session()
        headers = {
            "Authorization": f"Bearer {session_token}",
            "Accept": "application/json",
            "editor-version": "vscode/1.96.0",
            "editor-plugin-version": "copilot-chat/0.24.0",
            "copilot-integration-id": "vscode-chat",
            "user-agent": "Studio/1.0",
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.githubcopilot.com/models",
                headers=headers,
            )

            if resp.status_code == 200:
                data = resp.json()
                # 兼容多种返回格式
                if isinstance(data, list):
                    raw_list = data
                elif isinstance(data, dict):
                    raw_list = data.get("data") or data.get("models") or data.get("value") or []
                else:
                    raw_list = []

                logger.info(f"Copilot API /models 返回 {len(raw_list)} 个模型")
                return raw_list
            else:
                logger.warning(f"Copilot /models 返回 {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"获取 Copilot 模型列表失败: {e}")

    return []


async def _fetch_github_models() -> List[ModelInfo]:
    """
    获取可用模型列表:
    1. 从 GitHub Models API 动态获取 (backend="models")
    2. 合并 DB 中的 backend="models" 自定义模型 (去重)
    3. 如果 Copilot 已授权:
       a. 先尝试 Copilot API 动态获取 (优先)
       b. 失败时回退到 DB 中的 backend="copilot" 模型
    4. 应用 DB 能力覆盖
    """
    from studio.backend.api.provider_api import get_provider_by_slug

    github_provider = await get_provider_by_slug("github")
    token = ((github_provider.api_key if github_provider else "") or settings.github_token or "").strip()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    models: List[ModelInfo] = []
    seen_names: set = set()

    if token:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{settings.github_models_endpoint}/models",
                headers=headers,
            )

            if resp.status_code == 200:
                data = resp.json()
                raw_list = data if isinstance(data, list) else (
                    data.get("data") or data.get("models") or data.get("value") or []
                )

                for raw in raw_list:
                    try:
                        model = _parse_model(raw)
                        # 只保留 chat 相关模型
                        if model.task and "chat" not in model.task and "completion" not in model.task:
                            continue
                        models.append(model)
                        seen_names.add(model.id.lower())
                    except Exception as e:
                        logger.warning(f"解析模型数据失败: {e}, raw={raw.get('name', 'unknown')}")
                        continue

                logger.info(f"GitHub Models API 返回 {len(raw_list)} 个原始模型, 解析出 {len(models)} 个 chat 模型")
            else:
                error_text = resp.text[:500]
                logger.warning(f"GitHub Models API 返回 {resp.status_code}: {error_text}")
    else:
        logger.warning("未配置 GitHub Models 全局 Token，跳过 GitHub Models API；将仅返回已配置的自定义/Copilot/第三方模型")

    # 历史包袱清理: 不再注入 DB 自定义模型到运行时列表

    # 添加 Copilot API 专属模型 (如果已授权)
    if copilot_auth.is_authenticated:
        copilot_count = 0
        copilot_seen = set()

        # 先尝试从 Copilot API 动态获取模型列表
        dynamic_models = await _fetch_copilot_api_models()
        _SKIP_PATTERNS = {"embedding", "text-embedding", "oswe-"}
        if dynamic_models:
            for cp_raw in dynamic_models:
                cp_name = (cp_raw.get("id") or cp_raw.get("name") or "").strip()
                if not cp_name:
                    continue
                cp_lower = cp_name.lower()
                if any(pat in cp_lower for pat in _SKIP_PATTERNS):
                    continue
                copilot_id = f"copilot:{cp_lower}"
                if copilot_id in copilot_seen:
                    continue

                # 从 Copilot API 的 capabilities 字段提取丰富能力信息
                caps = cp_raw.get("capabilities", {})
                caps_supports = caps.get("supports", {})
                caps_limits = caps.get("limits", {})

                # 构建 tags: 先用 API 能力数据，再用猜测兜底
                api_tags = set()
                if caps_supports.get("vision"):
                    api_tags.add("multimodal")
                if caps_supports.get("tool_calls"):
                    api_tags.add("agents")
                if caps_supports.get("adaptive_thinking"):
                    api_tags.add("reasoning")
                merged_tags = list(api_tags) if api_tags else _guess_tags(cp_name)

                parsed_raw = {
                    "name": cp_name,
                    "friendly_name": cp_raw.get("name") or cp_raw.get("friendly_name") or cp_name,
                    "model_family": caps.get("family") or cp_raw.get("vendor") or _guess_family(cp_name),
                    "task": caps.get("type", "chat") + "-completion",
                    "tags": merged_tags,
                    "summary": cp_raw.get("summary") or cp_raw.get("description") or "",
                    # 传递 API 原始能力数据，供 learn_from_api 使用
                    "max_input_tokens": caps_limits.get("max_prompt_tokens") or caps_limits.get("max_context_window_tokens") or 0,
                    "max_output_tokens": caps_limits.get("max_output_tokens") or 0,
                }
                try:
                    model = _parse_model(parsed_raw, api_backend="copilot")
                    models.append(model)
                    copilot_seen.add(copilot_id)
                    copilot_count += 1
                except Exception as e:
                    logger.warning(f"解析 Copilot 动态模型失败: {e}")
            logger.info(f"Copilot API 动态发现 {copilot_count} 个模型")
        else:
            logger.info("Copilot API 动态模型获取失败，已跳过历史 DB 自定义回退")

        if copilot_count:
            logger.info(f"添加了 {copilot_count} 个 Copilot API 专属模型")
    else:
        logger.info("Copilot 未授权，跳过 Copilot 专属模型")

    # 应用 DB 能力覆盖 (supports_vision, supports_tools, is_reasoning)
    await _apply_db_capability_overrides(models)

    # 加载第三方提供商 (openai_compatible) 的模型
    await _append_third_party_models(models, seen_names)

    # 按 publisher 排序，常用模型靠前
    publisher_order = {"OpenAI": 0, "Anthropic": 1, "Google": 2, "DeepSeek": 3, "Mistral AI": 4, "Meta": 5, "xAI": 6, "AI21 Labs": 7}
    models.sort(key=lambda m: (publisher_order.get(m.publisher, 99), m.api_backend, m.name))

    return models


async def _load_db_custom_models(api_backend: str) -> list:
    """从 DB 加载指定后端的自定义模型 (返回与 _parse_model 兼容的 dict 列表)"""
    from studio.backend.core.database import async_session_maker
    try:
        async with async_session_maker() as db:
            from studio.backend.api.model_config import get_custom_models_from_db
            return await get_custom_models_from_db(db, api_backend=api_backend)
    except Exception as e:
        logger.warning(f"从 DB 加载 {api_backend} 自定义模型失败: {e}")
        return []


async def _apply_db_capability_overrides(models: List[ModelInfo]):
    """应用 DB 中的能力覆盖到模型列表 (boolean 能力: vision, tools, reasoning)"""
    from studio.backend.core.database import async_session_maker
    try:
        async with async_session_maker() as db:
            from studio.backend.api.model_config import get_capability_overrides_map
            overrides = await get_capability_overrides_map(db)
            if not overrides:
                return

            for m in models:
                clean = m.id.removeprefix("copilot:").lower()
                ov = overrides.get(clean)
                if not ov:
                    continue
                if ov.supports_vision is not None:
                    m.supports_vision = ov.supports_vision
                if ov.supports_tools is not None:
                    m.supports_tools = ov.supports_tools
                if ov.is_reasoning is not None:
                    m.is_reasoning = ov.is_reasoning
                # token 限制已通过 capability_cache 的 DB override 层处理
                # 这里同步更新 ModelInfo 的显示值
                if ov.max_input_tokens is not None:
                    m.max_input_tokens = ov.max_input_tokens
                if ov.max_output_tokens is not None:
                    m.max_output_tokens = ov.max_output_tokens

            logger.debug(f"应用了 {len(overrides)} 条 DB 能力覆盖")
    except Exception as e:
        logger.warning(f"应用 DB 能力覆盖失败: {e}")


async def _append_third_party_models(models: List[ModelInfo], seen_names: set):
    """从已启用的第三方 (openai_compatible) 提供商加载模型 — 优先 API 动态发现, 回退到预设"""
    try:
        from studio.backend.api.provider_api import get_enabled_providers
        providers = await get_enabled_providers()
    except Exception as e:
        logger.warning(f"加载第三方提供商失败: {e}")
        return

    for prov in providers:
        if prov.provider_type != "openai_compatible":
            continue
        if not prov.api_key:
            continue  # 无 API Key 的提供商跳过

        # 优先尝试 API 动态发现模型
        discovered = await _discover_provider_models(prov)
        model_list = discovered if discovered else (prov.default_models or [])
        source = "API发现" if discovered else "预设"

        count = 0
        for dm in model_list:
            model_name = dm.get("name", "")
            if not model_name:
                continue
            full_id = f"{prov.slug}:{model_name}"
            if full_id.lower() in seen_names:
                continue

            friendly = dm.get("friendly_name") or dm.get("name", model_name)
            family = dm.get("model_family") or prov.name
            tags = dm.get("tags", [])
            summary = dm.get("summary", "")

            models.append(ModelInfo(
                id=full_id,
                name=friendly,
                publisher=prov.name,
                registry=prov.slug,
                description=summary,
                summary=summary,
                category="both",
                max_input_tokens=dm.get("max_input_tokens", 0),
                max_output_tokens=dm.get("max_output_tokens", 4096),
                supports_vision="multimodal" in tags or "vision" in tags,
                supports_tools="agents" in tags or "tools" in tags,
                supports_json_output="json" in tags,
                is_reasoning="reasoning" in tags,
                api_backend=prov.slug,
                pricing_tier="paid",
                premium_multiplier=0,
                free_multiplier=None,
                task="chat-completion",
                is_custom=False,
                model_family=family,
                provider_slug=prov.slug,
                provider_icon=prov.icon,
            ))
            seen_names.add(full_id.lower())
            count += 1

        logger.info(f"第三方提供商 {prov.name} ({prov.slug}): 添加 {count} 个模型 ({source})")


async def _discover_provider_models(prov) -> list:
    """尝试从提供商的 /models 端点动态发现可用模型"""
    base_url = prov.base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {prov.api_key}", "Accept": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                raw_list = data.get("data", data) if isinstance(data, dict) else data
                if not isinstance(raw_list, list):
                    return []
                _SKIP = {"embed", "tts", "whisper", "audio", "image", "dall", "moderation",
                         "rerank", "ocr", "asr", "s2s", "mt-", "livetranslate",
                         "gui-", "tongyi-xiaomi", "z-image", "deep-search"}
                models = []
                for m in raw_list:
                    mid = m.get("id") or m.get("name", "")
                    if not mid:
                        continue
                    mid_lower = mid.lower()
                    if any(pat in mid_lower for pat in _SKIP):
                        continue
                    family, clean_name = _parse_model_family(mid, prov.name)
                    token_info = _guess_token_limits(mid)
                    models.append({
                        "name": mid,
                        "friendly_name": clean_name,
                        "model_family": family,
                        "tags": _guess_tags_from_name(mid),
                        "summary": "",
                        "max_input_tokens": token_info[0],
                        "max_output_tokens": token_info[1],
                    })
                logger.info(f"从 {prov.slug} API 动态发现 {len(models)} 个模型")
                return models
    except Exception as e:
        logger.debug(f"从 {prov.slug} API 发现模型失败 (回退到预设): {e}")
    return []


def _parse_model_family(model_id: str, provider_name: str) -> tuple:
    """
    从模型 ID 解析二级分类 (model_family) 和干净的显示名。
    如 'MiniMax/MiniMax-M2.1' → ('MiniMax', 'MiniMax-M2.1')
    如 'siliconflow/deepseek-v3.2' → ('SiliconFlow', 'deepseek-v3.2')
    如 'deepseek-r1' → ('DeepSeek', 'deepseek-r1')
    如 'qwen3-max' → (provider_name, 'qwen3-max')  # 属于提供商自己
    """
    # 1. 带斜杠的前缀: 'Vendor/model-name'
    if "/" in model_id:
        prefix, rest = model_id.split("/", 1)
        family = _KNOWN_FAMILIES.get(prefix.lower(), prefix)
        return (family, rest)

    # 2. 根据模型名称前缀猜测厂商
    n = model_id.lower()
    for key, display in _KNOWN_FAMILIES.items():
        if n.startswith(key):
            return (display, model_id)

    # 3. 默认归属提供商自身
    return (provider_name, model_id)


# 已知模型族 → 显示名 (小写 key)
_KNOWN_FAMILIES: Dict[str, str] = {
    "deepseek": "DeepSeek",
    "glm": "Zhipu GLM",
    "kimi": "Kimi",
    "minimax": "MiniMax",
    "siliconflow": "SiliconFlow",
    "claude": "Anthropic",
    "gpt": "OpenAI",
    "o1": "OpenAI",
    "o3": "OpenAI",
    "o4": "OpenAI",
    "gemini": "Google",
    "llama": "Meta",
    "mistral": "Mistral AI",
    "codestral": "Mistral AI",
    "qwq": "Qwen",
    "qvq": "Qwen",
    "qwen": "Qwen",
    "codeqwen": "Qwen",
}


def _guess_token_limits(model_id: str) -> tuple:
    """
    根据模型名称猜测 (max_input_tokens, max_output_tokens)。
    返回 (0, 4096) 表示未知。
    """
    n = model_id.lower()
    # 长上下文模型
    if "1m" in n:
        return (1048576, 8192)
    # DeepSeek
    if "deepseek-v3" in n or "deepseek-r1" in n:
        return (65536, 8192)
    if "deepseek" in n:
        return (32768, 4096)
    # GLM
    if "glm-4" in n:
        return (128000, 4096)
    # Kimi
    if "kimi" in n:
        return (131072, 8192)
    # MiniMax
    if "minimax" in n:
        return (1048576, 16384)
    # Qwen 3.5
    if "qwen3.5" in n:
        return (131072, 16384)
    # Qwen 3 max/plus
    if "qwen3-max" in n or "qwen3-coder" in n:
        return (131072, 16384)
    # Qwen 3 base/open
    if "qwen3" in n:
        return (32768, 8192)
    # QwQ / QvQ reasoning
    if "qwq" in n or "qvq" in n:
        return (131072, 16384)
    # Qwen 2.5 variants
    if "qwen2.5" in n:
        return (131072, 8192)
    # Qwen general (plus/max/turbo)
    if "qwen-max" in n or "qwen-plus" in n:
        return (131072, 8192)
    if "qwen-turbo" in n or "qwen-flash" in n:
        return (131072, 8192)
    # Older qwen
    if "qwen" in n:
        return (32768, 4096)
    return (0, 4096)


# 已知支持 function calling 的模型前缀 (小写)
_KNOWN_TOOL_MODELS = {
    # Qwen 系列 (通义千问) — qwen3 及以上全系支持 tools
    "qwen3", "qwen2.5", "qwen2", "qwen-max", "qwen-plus", "qwen-turbo", "qwen-long",
    "qwq",  # QwQ 推理模型也支持 tools
    # DeepSeek
    "deepseek-v3", "deepseek-v2", "deepseek-chat", "deepseek-coder",
    # GLM (智谱)
    "glm-4", "glm-3",
    # Kimi (Moonshot)
    "kimi", "moonshot",
    # MiniMax
    "minimax",
    # Mistral
    "mistral-large", "mistral-medium", "mistral-small", "codestral",
    # Meta Llama 3+
    "llama-3", "llama-4",
    # Cohere
    "command-r", "command-a",
    # xAI
    "grok",
}

# 已知支持视觉的模型前缀 (非名称含 vl/vision 的)
_KNOWN_VISION_MODELS = {
    "qwen2.5-vl", "qwen2-vl", "qvq",
    "glm-4v",
    "kimi-vision",
}


def _guess_tags_from_name(model_name: str) -> list:
    """根据模型名称猜测能力 tags"""
    n = model_name.lower()
    tags = []
    # 多模态 (视觉)
    if any(k in n for k in ("vl", "vision", "multimodal", "omni")):
        tags.append("multimodal")
    elif any(n.startswith(p) for p in _KNOWN_VISION_MODELS):
        tags.append("multimodal")
    # 推理
    if any(k in n for k in ("think", "r1", "qwq", "qvq")):
        tags.append("reasoning")
    # 工具调用: 名称关键词 + 已知模型族
    if any(k in n for k in ("tool", "agent", "function")):
        tags.append("agents")
    elif any(n.startswith(p) or n == p for p in _KNOWN_TOOL_MODELS):
        tags.append("agents")
    # 编码增强
    if any(k in n for k in ("coder", "codestral", "codeqwen")):
        tags.append("coding")
    return tags


# ==================== 模型能力知识库 (内置校准数据) ====================
# 格式: {model_name_prefix: {supports_vision, supports_tools, is_reasoning}}
# 用于校准端点批量写入 DB, 前缀匹配 (gpt-4o 匹配 gpt-4o-2024-08-06)
# None 表示不覆盖该字段 (保留原值)
_STATIC_MODEL_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    # ===== OpenAI =====
    "gpt-4o": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "gpt-4o-mini": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "gpt-4": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "gpt-4-turbo": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "gpt-4.1": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "gpt-4.1-mini": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "gpt-4.1-nano": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "gpt-5": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "gpt-5-mini": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "gpt-5-codex": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "gpt-5.1": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "gpt-5.1-codex": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "gpt-5.2": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "o1": {"supports_vision": True, "supports_tools": True, "is_reasoning": True},
    "o1-mini": {"supports_vision": False, "supports_tools": True, "is_reasoning": True},
    "o3": {"supports_vision": True, "supports_tools": True, "is_reasoning": True},
    "o3-mini": {"supports_vision": False, "supports_tools": True, "is_reasoning": True},
    "o4-mini": {"supports_vision": True, "supports_tools": True, "is_reasoning": True},
    # ===== Anthropic =====
    "claude-3.5-sonnet": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "claude-3.7-sonnet": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "claude-sonnet-4": {"supports_vision": True, "supports_tools": True, "is_reasoning": True},
    "claude-sonnet-4.5": {"supports_vision": True, "supports_tools": True, "is_reasoning": True},
    "claude-opus-4": {"supports_vision": True, "supports_tools": True, "is_reasoning": True},
    "claude-opus-4.5": {"supports_vision": True, "supports_tools": True, "is_reasoning": True},
    "claude-opus-4.6": {"supports_vision": True, "supports_tools": True, "is_reasoning": True},
    "claude-haiku-4.5": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    # ===== Google =====
    "gemini-2.0-flash": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "gemini-2.5-pro": {"supports_vision": True, "supports_tools": True, "is_reasoning": True},
    "gemini-3-flash": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "gemini-3-pro": {"supports_vision": True, "supports_tools": True, "is_reasoning": True},
    # ===== xAI =====
    "grok-3": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "grok-code-fast": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    # ===== DeepSeek =====
    "deepseek-r1": {"supports_vision": False, "supports_tools": True, "is_reasoning": True},
    "deepseek-v3": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "deepseek-chat": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "deepseek-coder": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    # ===== Qwen (通义千问) =====
    "qwen3": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "qwen3-max": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "qwen3-plus": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "qwen3-coder": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "qwen3.5": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "qwen2.5": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "qwen2.5-vl": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "qwen-max": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "qwen-plus": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "qwen-turbo": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "qwen-long": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "qwq": {"supports_vision": False, "supports_tools": True, "is_reasoning": True},
    "qvq": {"supports_vision": True, "supports_tools": False, "is_reasoning": True},
    # ===== GLM (智谱) =====
    "glm-4": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "glm-4v": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    # ===== Kimi (Moonshot) =====
    "kimi": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "moonshot": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    # ===== MiniMax =====
    "minimax": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    # ===== Mistral =====
    "mistral-large": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "codestral": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    # ===== Meta =====
    "llama-3": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "llama-4-scout": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    "llama-4-maverick": {"supports_vision": True, "supports_tools": True, "is_reasoning": False},
    # ===== Microsoft =====
    "phi-4": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    # ===== Cohere =====
    "command-r": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
    "command-a": {"supports_vision": False, "supports_tools": True, "is_reasoning": False},
}


# ==================== Routes ====================

@router.get("", response_model=List[ModelInfo])
async def list_models(
    category: Optional[str] = Query(None, description="筛选: discussion / implementation / both"),
    vision_only: bool = Query(False, description="只返回支持图片的模型"),
    api_backend: Optional[str] = Query(None, description="筛选 API 后端: models / copilot"),
    refresh: bool = Query(False, description="强制刷新缓存"),
    custom_models: bool = Query(True, description="是否包含自定义模型 (全局开关)"),
):
    """
    获取可用的 AI 模型列表

    模型列表从 GitHub Models API 动态获取，使用你的 GITHUB_TOKEN 鉴权。
    如果已完成 Copilot OAuth 授权，还会包含 Copilot 专属模型（Claude, Gemini 等）。
    Copilot 模型的 id 以 "copilot:" 前缀标识，用 ☁️ 图标在前端展示。

    缓存 10 分钟，可通过 refresh=true 强制刷新。
    """
    try:
        models = await _model_cache.get_models(force_refresh=refresh)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"获取模型列表失败: {str(e)}")

    # 始终重新应用 DB 能力覆盖, 确保用户修改立即生效
    # (缓存中的模型可能带有过期的覆盖值)
    await _apply_db_capability_overrides(models)

    # 筛选
    result = models
    if not custom_models:
        result = [m for m in result if not m.is_custom]
    if category:
        result = [m for m in result if m.category in (category, "both")]
    if vision_only:
        result = [m for m in result if m.supports_vision]
    if api_backend:
        result = [m for m in result if m.api_backend == api_backend]

    return result


@router.get("/cache-status")
async def get_cache_status():
    """获取模型缓存状态"""
    return {
        "cached_count": len(_model_cache.models),
        "is_expired": _model_cache.is_expired,
        "last_error": _model_cache.last_error,
        "ttl_seconds": _model_cache.ttl,
        "seconds_since_fetch": int(time.time() - _model_cache._last_fetch) if _model_cache._last_fetch else None,
        "copilot_authenticated": copilot_auth.is_authenticated,
    }


@router.post("/refresh")
async def refresh_models():
    """强制刷新模型缓存"""
    try:
        models = await _model_cache.get_models(force_refresh=True)
        return {"success": True, "count": len(models)}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"刷新失败: {str(e)}")


@router.post("/token-limits/refresh")
async def refresh_capabilities_online():
    """模型能力校准: 联网抓取 token 上限 + 内置知识库校准视觉/工具/推理能力"""
    source_used: list[str] = []
    warnings: list[str] = []
    online_limits: Dict[str, tuple[int, int]] = {}

    # 源 1: GitHub Models 官方接口
    try:
        gh_limits = await _fetch_official_context_limits()
        if gh_limits:
            online_limits.update(gh_limits)
            source_used.append(_OFFICIAL_MODELS_SOURCE_URL)
    except Exception as e:
        warnings.append(f"GitHub Models 来源不可用: {str(e)}")

    # 源 2: Copilot 官方接口（若已授权）
    if copilot_auth.is_authenticated:
        try:
            cp_limits = await _fetch_official_copilot_context_limits()
            if cp_limits:
                online_limits.update(cp_limits)
                source_used.append(_OFFICIAL_COPILOT_MODELS_URL)
            else:
                warnings.append("Copilot 来源未返回可解析的 token 上限")
        except Exception as e:
            warnings.append(f"Copilot 来源不可用: {str(e)}")
    else:
        warnings.append("Copilot 未授权，已跳过 Copilot 官方来源")

    if not online_limits:
        raise HTTPException(status_code=503, detail="官方来源未解析到可用 token 上限数据")

    models = await _model_cache.get_models(force_refresh=False)
    updated = 0
    matched: list[str] = []

    from sqlalchemy import select
    from studio.backend.core.database import async_session_maker

    async with async_session_maker() as db:
        for m in models:
            if not online_limits:
                break
            candidates = [
                _normalize_model_key(m.id.removeprefix("copilot:")),
                _normalize_model_key(m.name),
            ]

            found = None
            for key in candidates:
                if key in online_limits:
                    found = online_limits[key]
                    break
                # 前缀匹配兜底（处理日期后缀, 如 gpt-4o-2024-08-06）
                # 要求前缀后面紧跟分隔符 (-) 或到结尾, 避免 gpt-4 匹配 gpt-4o
                import re as _re
                for remote_key, val in online_limits.items():
                    if key.startswith(remote_key) and (len(key) == len(remote_key) or key[len(remote_key)] == '-'):
                        found = val
                        break
                    if remote_key.startswith(key) and (len(remote_key) == len(key) or remote_key[len(key)] == '-'):
                        found = val
                        break
                if found:
                    break

            if not found:
                continue

            max_in, max_out = found
            clean = m.id.removeprefix("copilot:").lower()

            # 检查是否真正有变化
            old_in, old_out = capability_cache.get_context_window(clean)
            if old_in == max_in and old_out == max_out:
                matched.append(clean)
                continue  # 值相同,跳过写入

            capability_cache._learned[clean] = (max_in, max_out)

            result = await db.execute(
                select(ModelCapabilityOverride).where(ModelCapabilityOverride.model_name == clean)
            )
            override = result.scalar_one_or_none()
            if override:
                override.max_input_tokens = max_in
                override.max_output_tokens = max_out
            else:
                db.add(ModelCapabilityOverride(
                    model_name=clean,
                    max_input_tokens=max_in,
                    max_output_tokens=max_out,
                ))

            capability_cache.set_db_override(clean, max_input=max_in, max_output=max_out)
            updated += 1
            matched.append(clean)

        # === 第二步: 校准能力 (vision/tools/reasoning) ===
        cap_updated = 0
        for m in models:
            clean = m.id.removeprefix("copilot:").lower()
            caps = _STATIC_MODEL_CAPABILITIES.get(clean)
            if not caps:
                # 前缀匹配
                for known_key, known_caps in _STATIC_MODEL_CAPABILITIES.items():
                    if clean.startswith(known_key) and (len(clean) == len(known_key) or clean[len(known_key)] == '-'):
                        caps = known_caps
                        break
            if not caps:
                continue

            # 检查是否需要更新
            need_update = False
            kw: Dict[str, Any] = {}
            for field in ("supports_vision", "supports_tools", "is_reasoning"):
                new_val = caps.get(field)
                if new_val is None:
                    continue
                old_val = getattr(m, field, None)
                if old_val != new_val:
                    need_update = True
                    kw[field] = new_val

            if not need_update:
                continue

            result = await db.execute(
                select(ModelCapabilityOverride).where(ModelCapabilityOverride.model_name == clean)
            )
            override = result.scalar_one_or_none()
            if override:
                for k, v in kw.items():
                    setattr(override, k, v)
            else:
                db.add(ModelCapabilityOverride(model_name=clean, **kw))
            cap_updated += 1

        await db.commit()

    return {
        "success": True,
        "source": source_used,
        "warning": "；".join(warnings) if warnings else "",
        "online_count": len(online_limits),
        "updated_count": updated,
        "cap_updated": cap_updated,
        "matched_models": matched,
    }


@router.get("/capabilities/all")
async def get_all_capabilities():
    """获取所有已知模型的能力数据 (含 API 学习 + 硬编码)"""
    known = capability_cache.get_all_known()
    result = {}
    for model_name, (max_input, max_output) in known.items():
        result[model_name] = {
            "max_input_tokens": max_input,
            "max_output_tokens": max_output,
        }
    return result


class CapabilityUpdate(BaseModel):
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None


@router.patch("/capabilities/{model_id:path}")
async def update_model_capability(model_id: str, data: CapabilityUpdate):
    """手动更新模型能力数据 (前端设置页面调用, 同时持久化到 DB)"""
    current_in, current_out = capability_cache.get_context_window(model_id)
    clean = model_id.removeprefix("copilot:").lower()
    new_in = data.max_input_tokens or current_in
    new_out = data.max_output_tokens or current_out
    capability_cache._learned[clean] = (new_in, new_out)

    # 也持久化到 DB
    try:
        from studio.backend.core.database import async_session_maker
        from sqlalchemy import select
        async with async_session_maker() as db:
            result = await db.execute(
                select(ModelCapabilityOverride).where(ModelCapabilityOverride.model_name == clean)
            )
            override = result.scalar_one_or_none()
            if override:
                if data.max_input_tokens is not None:
                    override.max_input_tokens = data.max_input_tokens
                if data.max_output_tokens is not None:
                    override.max_output_tokens = data.max_output_tokens
            else:
                override = ModelCapabilityOverride(
                    model_name=clean,
                    max_input_tokens=data.max_input_tokens,
                    max_output_tokens=data.max_output_tokens,
                )
                db.add(override)
            await db.commit()
            capability_cache.set_db_override(clean, max_input=override.max_input_tokens, max_output=override.max_output_tokens)
    except Exception as e:
        logger.warning(f"持久化能力覆盖失败: {e}")

    return {
        "ok": True,
        "model": model_id,
        "max_input_tokens": new_in,
        "max_output_tokens": new_out,
    }


@router.get("/{model_id}", response_model=ModelInfo)
async def get_model(model_id: str):
    """获取模型详情"""
    try:
        models = await _model_cache.get_models()
    except Exception:
        raise HTTPException(status_code=503, detail="模型列表不可用")

    for m in models:
        if m.id == model_id:
            return m
    raise HTTPException(status_code=404, detail=f"模型 {model_id} 不存在或不在你的可用范围内")


# ==================== 定价刷新 ====================

_PRICING_DOC_URL = "https://docs.github.com/en/copilot/concepts/billing/copilot-requests"


async def _scrape_github_pricing() -> Dict[str, Dict[str, Any]]:
    """
    从 GitHub 官方文档抓取模型定价倍率表。
    页面包含一个 HTML table，列: Model | Multiplier for paid plans | Multiplier for Copilot Free
    返回 {model_name_lower: {"paid": float, "free": float|None}}
    """
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(_PRICING_DOC_URL, headers={
            "Accept": "text/html",
            "User-Agent": "Studio/1.0",
        })
        if resp.status_code != 200:
            raise RuntimeError(f"获取文档失败: HTTP {resp.status_code}")
        html = resp.text

    # 解析 HTML 中 "Model multipliers" section 的 table
    parsed: Dict[str, Dict[str, Any]] = {}

    # 找到 model-multipliers 部分
    anchor = html.find('id="model-multipliers"')
    if anchor == -1:
        raise RuntimeError("页面中未找到 #model-multipliers 锚点")

    table_section = html[anchor:]
    # 找第一个 <table>
    table_start = table_section.find("<table")
    if table_start == -1:
        raise RuntimeError("未找到定价表格")

    table_end = table_section.find("</table>", table_start)
    table_html = table_section[table_start:table_end]

    # 解析行: <tr><th scope="row">Model</th><td>Paid multiplier</td><td>Free multiplier</td></tr>
    row_pattern = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL)
    cell_pattern = re.compile(r"<(?:td|th)[^>]*>(.*?)</(?:td|th)>", re.DOTALL)

    for row_match in row_pattern.finditer(table_html):
        row_html = row_match.group(1)
        cells = cell_pattern.findall(row_html)
        if len(cells) < 2:
            continue

        model_name_raw = re.sub(r"<[^>]+>", "", cells[0]).strip()
        paid_raw = re.sub(r"<[^>]+>", "", cells[1]).strip().lower()

        # 跳过表头行
        if not model_name_raw or model_name_raw.lower() == "model":
            continue

        # 解析付费倍率 (左列: Multiplier for paid plans)
        paid_mult = None
        if "not applicable" not in paid_raw:
            num_match = re.match(r"([\d.]+)", paid_raw)
            if num_match:
                paid_mult = float(num_match.group(1))

        if paid_mult is None:
            continue  # 无法解析付费倍率，跳过

        # 解析免费倍率 (右列: Multiplier for Copilot Free)
        free_mult = None
        if len(cells) >= 3:
            free_raw = re.sub(r"<[^>]+>", "", cells[2]).strip().lower()
            if "not applicable" not in free_raw:
                num_match_f = re.match(r"([\d.]+)", free_raw)
                if num_match_f:
                    free_mult = float(num_match_f.group(1))

        # 标准化模型名（转小写，去掉括号注释但保留核心名）
        clean_name = _normalize_model_name(model_name_raw)
        if clean_name:
            parsed[clean_name] = {"paid": paid_mult, "free": free_mult}

    if not parsed:
        raise RuntimeError("解析定价表失败: 未提取到任何模型数据")

    return parsed


def _normalize_model_name(display_name: str) -> str:
    """
    将 GitHub 文档中的模型显示名标准化为 API 模型 ID。

    如: "Claude Opus 4.6 (fast mode) (preview)" → "claude-opus-4.6-fast"
         "GPT-5.1-Codex-Mini" → "gpt-5.1-codex-mini"
         "Gemini 3 Flash" → "gemini-3-flash-preview" (尝试映射)
    """
    name = display_name.strip()

    # 提取快速模式标记
    is_fast = "(fast mode)" in name.lower() or "(fast)" in name.lower()
    # 去掉括号注释
    name = re.sub(r'\(.*?\)', '', name).strip()

    # 标准化: 空格 → 连字符, 小写
    name = re.sub(r'\s+', '-', name).strip('-').lower()

    if is_fast:
        name += "-fast"

    # 特殊映射 (文档显示名 → API ID)
    _DOC_TO_API = {
        "grok-code-fast-1": "grok-code-fast-1",
        "raptor-mini": "raptor-mini",
        "gemini-3-flash": "gemini-3-flash-preview",
        "gemini-3-pro": "gemini-3-pro-preview",
        "gemini-2.5-pro": "gemini-2.5-pro",
        "claude-opus-4.6-fast": "claude-opus-4.6-fast",
    }
    return _DOC_TO_API.get(name, name)


def _format_pricing_change(old_entry: Dict, new_entry: Dict) -> str:
    """格式化定价变更说明"""
    parts = []
    if old_entry["paid"] != new_entry["paid"]:
        parts.append(f"付费: x{old_entry['paid']:g}→x{new_entry['paid']:g}")
    old_free = old_entry.get("free")
    new_free = new_entry.get("free")
    if old_free != new_free:
        old_f = f"x{old_free:g}" if old_free is not None else "需订阅"
        new_f = f"x{new_free:g}" if new_free is not None else "需订阅"
        parts.append(f"免费: {old_f}→{new_f}")
    return ", ".join(parts) if parts else "数据变更"


@router.post("/pricing/refresh")
async def refresh_pricing():
    """
    从 GitHub 官方文档抓取最新的模型定价倍率表，与当前硬编码表对比。
    返回差异列表 (含付费/免费两列)，供前端二次确认后应用。
    """
    global _COPILOT_PREMIUM_COST

    try:
        scraped = await _scrape_github_pricing()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"抓取官方定价失败: {e}")

    # 对比差异: 只检查变更和新增, 不报告删除 (未匹配的保留原样)
    changes: List[Dict[str, Any]] = []

    for model in sorted(scraped.keys()):
        old_entry = _COPILOT_PREMIUM_COST.get(model)
        new_entry = scraped[model]

        if old_entry is not None:
            if old_entry != new_entry:
                changes.append({
                    "model": model,
                    "type": "changed",
                    "old_paid": old_entry["paid"],
                    "new_paid": new_entry["paid"],
                    "old_free": old_entry.get("free"),
                    "new_free": new_entry.get("free"),
                    "note": _format_pricing_change(old_entry, new_entry),
                })
        else:
            free_note = f"免费x{new_entry['free']:g}" if new_entry.get("free") is not None else "需订阅"
            changes.append({
                "model": model,
                "type": "added",
                "old_paid": None,
                "new_paid": new_entry["paid"],
                "old_free": None,
                "new_free": new_entry.get("free"),
                "note": f"新模型: 付费x{new_entry['paid']:g}, {free_note}",
            })

    return {
        "has_changes": len(changes) > 0,
        "changes": changes,
        "scraped_count": len(scraped),
        "current_count": len(_COPILOT_PREMIUM_COST),
        "scraped": scraped,  # 完整新表，用于应用
    }


class PricingApplyRequest(BaseModel):
    scraped: Dict[str, Any]  # Dict[str, {"paid": float, "free": float|None}]


@router.post("/pricing/apply")
async def apply_pricing(data: PricingApplyRequest):
    """
    应用新的定价表并持久化到数据库。
    合并硬编码默认值 + DB 覆盖, 重启后自动加载。
    同时触发模型缓存刷新以使新定价生效。
    """
    global _COPILOT_PREMIUM_COST
    old_count = len(_COPILOT_PREMIUM_COST)

    # 兼容处理: 新格式 {"paid": x, "free": y} 或旧格式 float
    new_pricing: Dict[str, Dict[str, Any]] = {}
    for model, entry in data.scraped.items():
        if isinstance(entry, dict):
            new_pricing[model] = entry
        else:
            new_pricing[model] = {"paid": float(entry), "free": None}

    # 持久化到 DB: 保存所有条目 (重启后完整替换硬编码)
    from sqlalchemy import select
    from studio.backend.core.database import async_session_maker
    db_saved = 0
    async with async_session_maker() as db:
        for model_name, entry in new_pricing.items():
            clean = model_name.lower()
            result = await db.execute(
                select(ModelCapabilityOverride).where(ModelCapabilityOverride.model_name == clean)
            )
            override = result.scalar_one_or_none()
            paid_val = entry.get("paid")
            free_val = entry.get("free")
            # 用 -1 表示 free=None (需订阅), 因为 DB Column 无法区分 null 和 "未设"
            free_db = free_val if free_val is not None else -1
            if override:
                override.premium_paid = paid_val
                override.premium_free = free_db
            else:
                db.add(ModelCapabilityOverride(
                    model_name=clean,
                    premium_paid=paid_val,
                    premium_free=free_db,
                ))
            db_saved += 1
        await db.commit()

    _COPILOT_PREMIUM_COST.update(new_pricing)  # 合并: 只更新/新增, 不删除未匹配的
    # 刷新模型缓存使新定价生效
    try:
        await _model_cache.get_models(force_refresh=True)
    except Exception:
        pass

    return {
        "ok": True,
        "old_count": old_count,
        "new_count": len(_COPILOT_PREMIUM_COST),
        "db_saved": db_saved,
        "message": "定价表已更新并持久化到数据库",
    }


@router.get("/pricing/current")
async def get_current_pricing():
    """获取当前使用的定价表 (两列: paid + free)"""
    return {
        "pricing": _COPILOT_PREMIUM_COST,
        "count": len(_COPILOT_PREMIUM_COST),
        "source": "hardcoded + runtime overrides",
    }


@router.post("/overrides/reset-all")
async def reset_all_model_overrides():
    """一键恢复所有覆盖数据: 清空 DB 覆盖并恢复运行时默认定价/能力缓存。"""
    from sqlalchemy import delete as sa_delete
    from studio.backend.core.database import async_session_maker

    # 1) 清空 DB 覆盖 (能力 + 定价覆盖都在同一表)
    async with async_session_maker() as db:
        await db.execute(sa_delete(ModelCapabilityOverride))
        await db.commit()

    # 2) 清空能力缓存覆盖/学习值
    capability_cache.clear_db_overrides()
    capability_cache._learned.clear()

    # 3) 恢复运行时 Copilot 定价到默认值
    global _COPILOT_PREMIUM_COST
    _COPILOT_PREMIUM_COST = {k: dict(v) for k, v in _COPILOT_PREMIUM_COST_DEFAULTS.items()}

    # 4) 标记缓存过期（由后续读取异步刷新，避免接口阻塞）
    _model_cache._last_fetch = 0
    _model_cache._fetch_error = None

    return {
        "ok": True,
        "message": "已恢复默认：清空全部覆盖并回到接口/内置默认值",
        "pricing_count": len(_COPILOT_PREMIUM_COST),
    }
