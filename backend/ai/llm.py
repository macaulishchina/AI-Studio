"""
统一 LLM 客户端

LLMClient 是 AI 骨干层对外的主要 LLM 接口:
  - stream()   → 流式生成 (yield LLMEvent)
  - complete() → 非流式生成
  - embed()    → Embedding

它负责:
  1. Provider 路由: 根据 model_id 前缀解析出正确的 Provider
  2. 推理模型检测: auto 切换 reasoning model 参数
  3. 消息格式转换: 内部格式 → API 格式
  4. 重试策略: auth error → 重新获取 token, context overflow → 抛出可恢复异常
  5. Provider 池管理: 复用 httpx.AsyncClient

**不含 tool-calling loop** — 纯 LLM 通信层。工具循环由 Agent 层管理。
"""
from __future__ import annotations

import base64
import hashlib
import logging
import mimetypes
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from .providers.base import (
    BaseProvider,
    CompletionResult,
    ContextOverflowError,
    EmbeddingResult,
    EventType,
    ProviderCapability,
    ProviderError,
    ProviderEvent,
    ProviderInfo,
)
from .providers.github_models import GitHubModelsProvider, _parse_error_meta
from .providers.copilot import CopilotProvider
from .providers.openai_compat import OpenAICompatProvider
from .providers.antigravity import AntigravityProvider

logger = logging.getLogger(__name__)

# 推理模型前缀
_REASONING_MODEL_PREFIXES = ("o1", "o3", "o4")

# Copilot 前缀
COPILOT_PREFIX = "copilot:"

# Anti-Gravity 前缀
ANTIGRAVITY_PREFIX = "antigravity:"


def _is_reasoning_model(model: str) -> bool:
    """检测是否为推理模型"""
    name = model.lower().removeprefix(COPILOT_PREFIX.lower())
    for prefix in _REASONING_MODEL_PREFIXES:
        if name == prefix or name.startswith(prefix + "-"):
            return True
    return False


def new_request_id() -> str:
    """每次用户消息生成新的 request_id (计费归集)"""
    rid = str(uuid.uuid4())
    logger.info(f"新消息 request_id: {rid[:8]}...")
    return rid


# ── LLM 事件 (对 ProviderEvent 的上层封装) ──────────────────

class LLMEvent:
    """统一 LLM 事件 — 直接复用 ProviderEvent 结构但更语义化"""
    __slots__ = ("type", "data")

    def __init__(self, type: str, **data):
        self.type = type
        self.data = data

    def __repr__(self):
        return f"LLMEvent({self.type}, {self.data})"

    def to_dict(self) -> Dict[str, Any]:
        """转为向后兼容的 SSE dict (与旧 ai_service 事件协议一致)"""
        d = {"type": self.type}
        d.update(self.data)
        return d


# ── LLMClient ──────────────────────────────────────────

class LLMClient:
    """统一 LLM 客户端

    使用:
      client = LLMClient.get_instance()
      async for event in client.stream(messages, model, system_prompt=...):
          ...
    """

    _instance: Optional["LLMClient"] = None

    def __init__(self):
        self._providers: Dict[str, BaseProvider] = {}  # cache_key → Provider
        self._provider_cache_ts: float = 0
        self._CACHE_TTL = 60

    @classmethod
    def get_instance(cls) -> "LLMClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Provider 路由 ──

    async def _resolve_provider(self, model_id: str) -> tuple[BaseProvider, str]:
        """解析 model_id → (Provider 实例, actual_model_name)

        model_id 格式:
          - "gpt-4o"                → GitHub Models
          - "copilot:gpt-4o"        → Copilot API
          - "deepseek:deepseek-chat" → 第三方 (DB 查询)
        """
        from backend.core.config import settings
        from backend.services.copilot_auth import COPILOT_CHAT_URL

        now = time.time()
        cache_stale = (now - self._provider_cache_ts) > self._CACHE_TTL

        # Copilot
        if model_id.startswith(COPILOT_PREFIX):
            actual = model_id[len(COPILOT_PREFIX):]
            cache_key = "copilot"
            if cache_key not in self._providers or cache_stale:
                info = ProviderInfo(
                    provider_type="copilot", slug="copilot",
                    actual_model=actual, base_url=COPILOT_CHAT_URL,
                    icon="☁️", name="Copilot",
                )
                self._providers[cache_key] = CopilotProvider(info)
                self._provider_cache_ts = now
            return self._providers[cache_key], actual

        # Anti-Gravity
        if model_id.startswith(ANTIGRAVITY_PREFIX):
            actual = model_id[len(ANTIGRAVITY_PREFIX):]
            cache_key = "antigravity"
            if cache_key not in self._providers or cache_stale:
                from backend.services.antigravity_auth import ANTIGRAVITY_BASE_URL
                info = ProviderInfo(
                    provider_type="antigravity", slug="antigravity",
                    actual_model=actual, base_url=ANTIGRAVITY_BASE_URL,
                    icon="🚀", name="Anti-Gravity",
                )
                self._providers[cache_key] = AntigravityProvider(info)
                self._provider_cache_ts = now
            return self._providers[cache_key], actual

        # 第三方: slug:model 格式
        if ":" in model_id:
            slug, actual = model_id.split(":", 1)
            cache_key = slug

            if cache_key in self._providers and not cache_stale:
                return self._providers[cache_key], actual

            from backend.api.provider_api import get_provider_by_slug
            provider_row = await get_provider_by_slug(slug)
            if provider_row and provider_row.enabled:
                info = ProviderInfo(
                    provider_type=provider_row.provider_type,
                    slug=provider_row.slug,
                    actual_model=actual,
                    base_url=provider_row.base_url,
                    api_key=provider_row.api_key or "",
                    icon=provider_row.icon,
                    name=provider_row.name,
                )
                self._providers[cache_key] = OpenAICompatProvider(info)
                self._provider_cache_ts = now
                return self._providers[cache_key], actual
            else:
                logger.warning(f"提供商 '{slug}' 不存在或未启用, 回退到 GitHub Models")

        # 默认: GitHub Models
        cache_key = "github"
        if cache_key not in self._providers or cache_stale:
            from backend.api.provider_api import get_provider_by_slug
            provider_row = await get_provider_by_slug("github")
            api_key = ((provider_row.api_key if provider_row else "") or settings.github_token or "").strip()
            info = ProviderInfo(
                provider_type="github_models", slug="github",
                actual_model=model_id,
                base_url=settings.github_models_endpoint,
                api_key=api_key,
                icon="🐙", name="GitHub Models",
            )
            self._providers[cache_key] = GitHubModelsProvider(info)
            self._provider_cache_ts = now
        return self._providers[cache_key], model_id

    def invalidate_cache(self):
        """清除 provider 缓存 (配置变更后调用)"""
        self._providers.clear()
        self._provider_cache_ts = 0

    # ── 消息构建 ──

    @staticmethod
    def build_api_messages(
        messages: List[Dict[str, Any]],
        system_prompt: str = "",
        is_reasoning: bool = False,
    ) -> List[Dict[str, Any]]:
        """将内部消息格式转为 OpenAI API 格式"""
        api_messages: List[Dict[str, Any]] = []

        if system_prompt:
            if is_reasoning:
                api_messages.append({
                    "role": "user",
                    "content": f"[System Instructions]\n{system_prompt}",
                })
            else:
                api_messages.append({"role": "system", "content": system_prompt})

        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")
            images = msg.get("images", [])

            if role == "tool":
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "content": msg.get("content", ""),
                })
                continue

            if role == "assistant" and "tool_calls" in msg:
                entry: Dict[str, Any] = {"role": "assistant"}
                entry["content"] = content if content else None
                entry["tool_calls"] = msg["tool_calls"]
                api_messages.append(entry)
                continue

            if images and role == "user":
                content_parts: List[Dict[str, Any]] = []
                if content:
                    content_parts.append({"type": "text", "text": content})
                for img in images:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img['mime_type']};base64,{img['base64']}"
                        },
                    })
                api_messages.append({"role": role, "content": content_parts})
            else:
                api_messages.append({"role": role, "content": content})

        return api_messages

    # ── 核心接口 ──

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        model: str = "gpt-4o",
        *,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 8192,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        request_id: str = "",
    ) -> AsyncGenerator[LLMEvent, None]:
        """流式 LLM 调用 — yield LLMEvent

        不含 tool-calling loop。单次 LLM 调用，返回内容 + tool_calls。
        工具循环由 Agent 层管理。
        """
        provider, actual_model = await self._resolve_provider(model)
        is_reasoning = _is_reasoning_model(actual_model)

        # 认证检查
        auth_error = self._check_auth(provider, model)
        if auth_error:
            yield LLMEvent("error", error=auth_error)
            return

        # 推理模型: 禁用 tools, 使用 complete() 非流式
        if is_reasoning:
            if tools:
                logger.info(f"推理模型 {actual_model} 不支持 tools, 跳过工具注入")
                tools = None
            async for ev in self._stream_reasoning(provider, messages, actual_model, system_prompt, max_tokens, request_id):
                yield ev
            return

        # 构建 API 消息
        api_messages = self.build_api_messages(messages, system_prompt, False)

        # 流式调用
        async for pev in provider.stream(
            api_messages, actual_model,
            temperature=temperature, max_tokens=max_tokens,
            tools=tools, tool_choice=tool_choice,
            request_id=request_id,
        ):
            yield self._convert_provider_event(pev)

    async def _stream_reasoning(
        self,
        provider: BaseProvider,
        messages: List[Dict[str, Any]],
        actual_model: str,
        system_prompt: str,
        max_tokens: int,
        request_id: str,
    ) -> AsyncGenerator[LLMEvent, None]:
        """推理模型: 非流式调用, 包装为 LLMEvent 序列"""
        api_messages = self.build_api_messages(messages, system_prompt, True)
        try:
            result = await provider.complete(
                api_messages, actual_model,
                max_tokens=max_tokens,
                request_id=request_id,
            )
        except ProviderError as e:
            yield LLMEvent("error", error=str(e), error_meta=e.error_meta)
            return

        if result.thinking:
            yield LLMEvent("thinking", content=result.thinking)
        if result.content:
            yield LLMEvent("content", content=result.content)
        if result.usage:
            yield LLMEvent("usage", usage={
                "prompt_tokens": result.usage.get("prompt_tokens", 0),
                "completion_tokens": result.usage.get("completion_tokens", 0),
                "total_tokens": result.usage.get("total_tokens", 0),
                "reasoning_tokens": result.usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0),
            })

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        model: str = "gpt-4o",
        *,
        system_prompt: str = "",
        temperature: float = 0.7,
        max_tokens: int = 8192,
    ) -> str:
        """非流式 LLM 调用 — 返回纯文本内容"""
        result_parts: List[str] = []
        async for event in self.stream(
            messages, model,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            if event.type == "content":
                result_parts.append(event.data.get("content", ""))
            elif event.type == "error":
                result_parts.append(event.data.get("error", ""))
        return "".join(result_parts)

    async def embed(
        self,
        texts: List[str],
        model: str = "text-embedding-3-small",
        *,
        provider_slug: str = "github",
    ) -> EmbeddingResult:
        """Embedding 调用

        Args:
            texts: 待 embed 的文本列表
            model: embedding 模型名
            provider_slug: 使用哪个提供商 (默认 github)
        """
        # 构造一个临时 model_id 来解析 provider
        model_id = f"{provider_slug}:{model}" if provider_slug != "github" else model
        provider, _ = await self._resolve_provider(model_id)
        return await provider.embed(texts, model)

    # ── 内部工具 ──

    def _check_auth(self, provider: BaseProvider, model: str) -> str:
        """检查认证状态, 返回错误消息或空字符串"""
        if isinstance(provider, CopilotProvider):
            from backend.services.copilot_auth import copilot_auth
            if not copilot_auth.is_authenticated:
                return "❌ 未授权 Copilot，请在设置页面完成 OAuth 授权"
        elif isinstance(provider, GitHubModelsProvider):
            if not provider.info.api_key:
                return "❌ 未配置 GitHub Models 全局 Token，请在 AI 服务设置中配置"
        elif isinstance(provider, OpenAICompatProvider):
            if not provider.info.api_key:
                return f"❌ {provider.info.name} 未配置 API Key，请在 AI 服务设置中配置"
        return ""

    @staticmethod
    def _convert_provider_event(pev: ProviderEvent) -> LLMEvent:
        """将 ProviderEvent 转为 LLMEvent"""
        if pev.type == EventType.CONTENT_DELTA:
            return LLMEvent("content", content=pev.text)
        elif pev.type == EventType.THINKING_DELTA:
            return LLMEvent("thinking", content=pev.text)
        elif pev.type == EventType.TOOL_CALL_DELTA:
            return LLMEvent("tool_call_delta",
                            tool_call_index=pev.tool_call_index,
                            tool_call_id=pev.tool_call_id,
                            name=pev.name,
                            arguments_delta=pev.arguments_delta)
        elif pev.type == EventType.USAGE:
            return LLMEvent("usage", usage=pev.usage)
        elif pev.type == EventType.FINISH:
            return LLMEvent("finish", finish_reason=pev.finish_reason)
        elif pev.type == EventType.ERROR:
            return LLMEvent("error", error=pev.error, error_meta=pev.error_meta)
        else:
            return LLMEvent("unknown", raw=str(pev))

    # ── 生命周期 ──

    async def close(self):
        """关闭所有 Provider 连接"""
        for p in self._providers.values():
            await p.close()
        self._providers.clear()


# ── 便捷函数 (模块级) ──

def get_llm_client() -> LLMClient:
    """获取全局 LLMClient 单例"""
    return LLMClient.get_instance()


# ── 图片工具 (从旧 ai_service 迁入) ──

def encode_image_to_base64(file_bytes: bytes) -> str:
    """将图片字节编码为 base64"""
    return base64.b64encode(file_bytes).decode("utf-8")


def get_mime_type(filename: str) -> str:
    """根据文件名获取 MIME 类型"""
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or "image/png"
