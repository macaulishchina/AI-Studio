"""
OpenAI Compatible Provider — 通用第三方 API

支持所有兼容 OpenAI Chat Completions 协议的提供商:
  - DeepSeek, Qwen, Moonshot, 零一万物, Minimax 等
  - 自部署 vLLM, Ollama, LocalAI 等

通过 AIProvider 数据库表配置 base_url / api_key。
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
from .github_models import _parse_sse_chunk, _parse_completion_response, _parse_error_meta

logger = logging.getLogger(__name__)


class OpenAICompatProvider(BaseProvider):
    """通用 OpenAI 兼容提供商"""

    def __init__(self, info: ProviderInfo):
        super().__init__(info)
        self._capabilities = {
            ProviderCapability.STREAMING,
            ProviderCapability.TOOLS,
            ProviderCapability.VISION,
        }
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=300)
        return self._client

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.info.api_key:
            h["Authorization"] = f"Bearer {self.info.api_key}"
        return h

    def _build_url(self, path: str = "/chat/completions") -> str:
        base = self.info.base_url.rstrip("/")
        return f"{base}{path}"

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

        logger.info(f"Using {self.info.name} ({self.info.slug}) for model: {model}")

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
                logger.error(f"{self.info.name} API error {response.status_code}: {error_text}")
                yield ProviderEvent(
                    type=EventType.ERROR,
                    error=f"❌ {self.info.name} 服务错误 ({response.status_code}): {error_text}",
                    error_meta=_parse_error_meta(response.status_code, error_text, model, "openai_compatible"),
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
            logger.error(f"{self.info.name} API error {response.status_code}: {error_text}")
            raise ProviderError(
                f"{self.info.name} 服务错误 ({response.status_code}): {error_text}",
                status_code=response.status_code,
                error_meta=_parse_error_meta(response.status_code, error_text, model, "openai_compatible"),
            )

        return _parse_completion_response(response.json())

    async def embed(
        self,
        texts: List[str],
        model: str = "",
        **kwargs,
    ) -> EmbeddingResult:
        """部分第三方提供商支持 embedding"""
        if not model:
            raise ProviderError(f"{self.info.name}: 需要指定 embedding 模型名")

        payload = {"model": model, "input": texts}
        client = self._get_client()
        response = await client.post(
            self._build_url("/embeddings"),
            headers=self._headers(),
            json=payload,
        )
        if response.status_code != 200:
            raise ProviderError(
                f"{self.info.name} Embedding 错误 ({response.status_code}): {response.text}",
                status_code=response.status_code,
            )
        result = response.json()
        embeddings = [item["embedding"] for item in result.get("data", [])]
        return EmbeddingResult(embeddings=embeddings, model=model, usage=result.get("usage", {}))

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
