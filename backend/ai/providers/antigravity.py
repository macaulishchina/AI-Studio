"""
Anti-Gravity Provider — Google Antigravity OpenAI 兼容 API

通过 Google OAuth 授权，使用 Anti-Gravity 的 OpenAI 兼容端点访问
Gemini, Claude 等模型。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from .base import (
    AuthenticationError,
    BaseProvider,
    CompletionResult,
    EmbeddingResult,
    EventType,
    ProviderCapability,
    ProviderEvent,
    ProviderInfo,
)
from .github_models import _parse_sse_chunk, _parse_completion_response, _parse_error_meta

logger = logging.getLogger(__name__)


class AntigravityProvider(BaseProvider):
    """Google Anti-Gravity API 提供商"""

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

    async def _get_headers(self, request_id: str = "") -> Dict[str, str]:
        """获取 Anti-Gravity API 请求头"""
        from backend.services.antigravity_auth import antigravity_auth

        if not antigravity_auth.is_authenticated:
            raise AuthenticationError("❌ 未授权 Anti-Gravity，请在设置页面完成 Google 账号授权")

        access_token = await antigravity_auth.ensure_token()
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": "Studio/1.0",
            "X-Request-Id": request_id or str(uuid.uuid4()),
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
        try:
            headers = await self._get_headers(request_id)
        except AuthenticationError as e:
            yield ProviderEvent(type=EventType.ERROR, error=str(e))
            return

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

        logger.info(f"Using Anti-Gravity API for model: {model}")

        client = self._get_client()
        async with client.stream(
            "POST",
            self._build_url(),
            headers=headers,
            json=payload,
        ) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                error_text = error_body.decode()
                logger.error(f"Anti-Gravity API error {response.status_code}: {error_text}")
                yield ProviderEvent(
                    type=EventType.ERROR,
                    error=f"❌ Anti-Gravity 服务错误 ({response.status_code}): {error_text}",
                    error_meta=_parse_error_meta(response.status_code, error_text, model, "antigravity"),
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
        try:
            headers = await self._get_headers(request_id)
        except AuthenticationError as e:
            raise

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
            headers=headers,
            json=payload,
        )
        if response.status_code != 200:
            error_text = response.text
            logger.error(f"Anti-Gravity API error {response.status_code}: {error_text}")
            from .base import ProviderError
            raise ProviderError(
                f"Anti-Gravity 服务错误 ({response.status_code}): {error_text}",
                status_code=response.status_code,
                error_meta=_parse_error_meta(response.status_code, error_text, model, "antigravity"),
            )

        return _parse_completion_response(response.json())

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
