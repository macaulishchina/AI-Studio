"""
BaseProvider ABC — LLM 提供商抽象基类

所有提供商 (GitHub Models / Copilot / OpenAI Compatible) 实现此接口。
Provider 仅负责协议差异 (认证、header、SSE 解析)，不含工具循环或消息管理。
"""
from __future__ import annotations

import enum
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import (
    Any,
    AsyncGenerator,
    Dict,
    List,
    Literal,
    Optional,
    Set,
)


# ── 事件协议 ────────────────────────────────────────────────

class EventType(str, enum.Enum):
    """Provider 发射的流式事件类型"""
    CONTENT_DELTA = "content_delta"
    THINKING_DELTA = "thinking_delta"
    TOOL_CALL_DELTA = "tool_call_delta"
    USAGE = "usage"
    FINISH = "finish"
    ERROR = "error"


@dataclass
class ProviderEvent:
    """Provider 流式输出的一个事件

    根据 type 携带不同 payload:
      - content_delta:     text (str)
      - thinking_delta:    text (str)
      - tool_call_delta:   tool_call_index (int), tool_call_id (str), name (str), arguments_delta (str)
      - usage:             usage (dict)
      - finish:            finish_reason (str)
      - error:             error (str), error_meta (dict)
    """
    type: EventType

    # content / thinking
    text: str = ""

    # tool_call_delta
    tool_call_index: int = -1
    tool_call_id: str = ""
    name: str = ""
    arguments_delta: str = ""

    # usage
    usage: Dict[str, Any] = field(default_factory=dict)

    # finish
    finish_reason: str = ""

    # error
    error: str = ""
    error_meta: Dict[str, Any] = field(default_factory=dict)


# ── 能力声明 ────────────────────────────────────────────────

class ProviderCapability(str, enum.Enum):
    """提供商支持的能力"""
    STREAMING = "streaming"
    TOOLS = "tools"
    VISION = "vision"
    REASONING = "reasoning"
    EMBEDDINGS = "embeddings"
    JSON_MODE = "json_mode"


# ── 返回类型 ────────────────────────────────────────────────

@dataclass
class CompletionResult:
    """非流式完成结果"""
    content: str = ""
    thinking: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""
    raw_response: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EmbeddingResult:
    """Embedding 结果"""
    embeddings: List[List[float]] = field(default_factory=list)
    model: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)


# ── 提供商元信息 ──────────────────────────────────────────

@dataclass
class ProviderInfo:
    """提供商解析后的元信息"""
    provider_type: Literal["github_models", "copilot", "openai_compatible"]
    slug: str
    actual_model: str
    base_url: str
    api_key: str = ""
    icon: str = ""
    name: str = ""


# ── 错误 ─────────────────────────────────────────────────

class ProviderError(Exception):
    """提供商级别错误"""
    def __init__(self, message: str, status_code: int = 0, error_meta: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_meta = error_meta or {}


class AuthenticationError(ProviderError):
    """认证失败"""
    pass


class RateLimitError(ProviderError):
    """速率限制"""
    def __init__(self, message: str, retry_after: float = 0, **kwargs):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ContextOverflowError(ProviderError):
    """上下文窗口溢出"""
    def __init__(self, message: str, max_tokens: int = 0, requested_tokens: int = 0, **kwargs):
        super().__init__(message, **kwargs)
        self.max_tokens = max_tokens
        self.requested_tokens = requested_tokens


# ── 基类 ─────────────────────────────────────────────────

class BaseProvider(ABC):
    """LLM 提供商基类

    子类实现:
      - stream()   — 流式生成
      - complete() — 非流式生成
      - embed()    — embedding (可选)

    Provider 不管理消息历史、工具循环、上下文窗口。
    它只关心: 输入消息 → provider-specific 请求 → 输出事件流。
    """

    def __init__(self, info: ProviderInfo):
        self.info = info
        self._capabilities: Set[ProviderCapability] = set()

    @property
    def slug(self) -> str:
        return self.info.slug

    @property
    def name(self) -> str:
        return self.info.name

    @property
    def capabilities(self) -> Set[ProviderCapability]:
        return self._capabilities

    def supports(self, cap: ProviderCapability) -> bool:
        return cap in self._capabilities

    # ── 核心接口 ──

    @abstractmethod
    async def stream(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        request_id: str = "",
        **kwargs,
    ) -> AsyncGenerator[ProviderEvent, None]:
        """流式生成 — yield ProviderEvent 序列"""
        ...  # pragma: no cover

    @abstractmethod
    async def complete(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        request_id: str = "",
        **kwargs,
    ) -> CompletionResult:
        """非流式生成 — 返回完整结果"""
        ...  # pragma: no cover

    async def embed(
        self,
        texts: List[str],
        model: str = "",
        **kwargs,
    ) -> EmbeddingResult:
        """Embedding 生成 — 默认不支持, 子类可选覆写"""
        raise NotImplementedError(f"{self.name} 不支持 embedding")

    # ── 生命周期 ──

    async def close(self):
        """清理资源 (http client 等)"""
        pass

    def __repr__(self):
        return f"<{self.__class__.__name__} slug={self.slug} name={self.name}>"
