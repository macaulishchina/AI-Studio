"""
GitHub Models Provider — models.inference.ai.azure.com

使用 GitHub PAT (Personal Access Token) 直接调用 GitHub Models API。
支持流式 SSE + 非流式 + embedding。
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from .base import (
    BaseProvider,
    CompletionResult,
    EmbeddingResult,
    EventType,
    ProviderCapability,
    ProviderError,
    ProviderEvent,
    ProviderInfo,
)

logger = logging.getLogger(__name__)


class GitHubModelsProvider(BaseProvider):
    """GitHub Models API 提供商"""

    def __init__(self, info: ProviderInfo):
        super().__init__(info)
        self._capabilities = {
            ProviderCapability.STREAMING,
            ProviderCapability.TOOLS,
            ProviderCapability.VISION,
            ProviderCapability.EMBEDDINGS,
        }
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=300)
        return self._client

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.info.api_key}",
            "Content-Type": "application/json",
        }

    def _build_url(self, path: str = "/chat/completions") -> str:
        return f"{self.info.base_url}{path}"

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
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        client = self._get_client()
        async with client.stream(
            "POST",
            self._build_url(),
            headers=self._headers(),
            json=payload,
        ) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                error_text = error_body.decode()
                logger.error(f"GitHub Models API error {response.status_code}: {error_text}")
                yield ProviderEvent(
                    type=EventType.ERROR,
                    error=f"❌ AI 服务错误 ({response.status_code}): {error_text}",
                    error_meta=_parse_error_meta(response.status_code, error_text, model, "github_models"),
                )
                return

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except Exception:
                    continue

                for event in _parse_sse_chunk(chunk):
                    yield event

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
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        client = self._get_client()
        response = await client.post(
            self._build_url(),
            headers=self._headers(),
            json=payload,
        )
        if response.status_code != 200:
            error_text = response.text
            logger.error(f"GitHub Models API error {response.status_code}: {error_text}")
            raise ProviderError(
                f"AI 服务错误 ({response.status_code}): {error_text}",
                status_code=response.status_code,
                error_meta=_parse_error_meta(response.status_code, error_text, model, "github_models"),
            )

        return _parse_completion_response(response.json())

    async def embed(
        self,
        texts: List[str],
        model: str = "text-embedding-3-small",
        **kwargs,
    ) -> EmbeddingResult:
        payload = {
            "model": model,
            "input": texts,
        }
        client = self._get_client()
        response = await client.post(
            self._build_url("/embeddings"),
            headers=self._headers(),
            json=payload,
        )
        if response.status_code != 200:
            raise ProviderError(
                f"Embedding 错误 ({response.status_code}): {response.text}",
                status_code=response.status_code,
            )
        result = response.json()
        embeddings = [item["embedding"] for item in result.get("data", [])]
        usage = result.get("usage", {})
        return EmbeddingResult(embeddings=embeddings, model=model, usage=usage)

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# ── 共享工具函数 (其他 Provider 也复用) ──────────────────


def _parse_sse_chunk(chunk: Dict[str, Any]) -> List[ProviderEvent]:
    """解析一个 SSE chunk → 零或多个 ProviderEvent"""
    events: List[ProviderEvent] = []
    choice = chunk.get("choices", [{}])[0] if chunk.get("choices") else {}
    delta = choice.get("delta", {})

    # finish_reason
    fr = choice.get("finish_reason")
    if fr:
        events.append(ProviderEvent(type=EventType.FINISH, finish_reason=fr))

    # thinking (reasoning models)
    thinking = delta.get("reasoning_content") or delta.get("thinking") or ""
    if thinking:
        events.append(ProviderEvent(type=EventType.THINKING_DELTA, text=thinking))

    # content
    content = delta.get("content")
    if content:
        events.append(ProviderEvent(type=EventType.CONTENT_DELTA, text=content))

    # tool_calls
    if "tool_calls" in delta:
        for tc in delta["tool_calls"]:
            idx = tc.get("index", 0)
            func = tc.get("function", {})
            events.append(ProviderEvent(
                type=EventType.TOOL_CALL_DELTA,
                tool_call_index=idx,
                tool_call_id=tc.get("id", ""),
                name=func.get("name", ""),
                arguments_delta=func.get("arguments", ""),
            ))

    # usage (有些 provider 在流中返回 usage)
    usage = chunk.get("usage")
    if usage:
        events.append(ProviderEvent(type=EventType.USAGE, usage=usage))

    return events


def _parse_completion_response(result: Dict[str, Any]) -> CompletionResult:
    """解析非流式完成响应"""
    choice = result.get("choices", [{}])[0]
    message = choice.get("message", {})

    tool_calls = []
    if "tool_calls" in message:
        for tc in message["tool_calls"]:
            func = tc.get("function", {})
            try:
                args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {"_raw": func.get("arguments", "")}
            tool_calls.append({
                "id": tc.get("id", ""),
                "name": func.get("name", ""),
                "arguments": args,
            })

    return CompletionResult(
        content=message.get("content", "") or "",
        thinking=message.get("reasoning_content") or message.get("thinking") or "",
        tool_calls=tool_calls,
        usage=result.get("usage", {}),
        finish_reason=choice.get("finish_reason", ""),
        raw_response=result,
    )


def _parse_error_meta(
    status_code: int,
    error_text: str,
    model: str,
    provider_type: str = "",
) -> Dict[str, Any]:
    """从 API 错误响应中提取结构化元数据"""
    import re
    meta: Dict[str, Any] = {"status_code": status_code, "model": model}
    if provider_type:
        meta["provider_type"] = provider_type

    lower = error_text.lower()

    if status_code == 429 or "rate limit" in lower:
        meta["error_type"] = "rate_limit"
        m = re.search(r'Rate limit of (\d+) per (\d+)s', error_text, re.I)
        if m:
            meta["rate_limit"] = f"{m.group(1)} per {m.group(2)}s"
            meta["rate_limit_count"] = int(m.group(1))
            meta["rate_limit_seconds"] = int(m.group(2))
        m = re.search(r'(\d+) per (\d+) (second|minute|hour)', error_text, re.I)
        if m and "rate_limit" not in meta:
            unit_map = {"second": 1, "minute": 60, "hour": 3600}
            secs = int(m.group(2)) * unit_map.get(m.group(3).lower(), 1)
            meta["rate_limit"] = f"{m.group(1)} per {secs}s"
            meta["rate_limit_count"] = int(m.group(1))
            meta["rate_limit_seconds"] = secs
        m = re.search(r'wait\s+(\d+)\s*seconds?', error_text, re.I)
        if m:
            meta["wait_seconds"] = int(m.group(1))
    elif "context length" in lower or "too large" in lower or "max_tokens" in lower:
        meta["error_type"] = "context_overflow"
        m = re.search(r'maximum context length.*?(\d{3,})', error_text, re.I)
        if m:
            meta["max_context_tokens"] = int(m.group(1))
        m = re.search(r'Max size:\s*(\d+)\s*tokens', error_text, re.I)
        if m:
            meta["max_context_tokens"] = int(m.group(1))
        m = re.search(r'requested\s+(\d+)\s*tokens', error_text, re.I)
        if m:
            meta["requested_tokens"] = int(m.group(1))
    elif status_code in (401, 403):
        meta["error_type"] = "auth_error"
    else:
        meta["error_type"] = "unknown"

    return meta
