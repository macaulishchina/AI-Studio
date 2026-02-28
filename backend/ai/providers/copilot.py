"""
Copilot Provider — api.githubcopilot.com

通过 OAuth Device Flow 授权，模拟 VS Code 请求头实现 Copilot 计费归集。
同一 request_id 下的多次 API 调用 (工具调用轮次) 被 GitHub 后端归集为一次 premium request。
"""
from __future__ import annotations

import hashlib
import json
import logging
import platform
import time
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

# 应用实例级 session ID (启动时生成一次, 稳定用于计费关联)
_STUDIO_SESSION_ID = str(uuid.uuid4()) + str(int(time.time() * 1000))
_STUDIO_MACHINE_ID = hashlib.sha256(
    f"{platform.node()}-studio-ai".encode()
).hexdigest()


class CopilotProvider(BaseProvider):
    """GitHub Copilot API 提供商"""

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
        """获取 Copilot API 请求头 (含计费归集头)"""
        from studio.backend.services.copilot_auth import copilot_auth

        if not copilot_auth.is_authenticated:
            raise AuthenticationError("❌ 未授权 Copilot，请在设置页面完成 OAuth 授权")

        session_token = await copilot_auth.ensure_session()
        return {
            "Authorization": f"Bearer {session_token}",
            "Content-Type": "application/json",
            "editor-version": "vscode/1.96.0",
            "editor-plugin-version": "copilot-chat/0.24.0",
            "copilot-integration-id": "vscode-chat",
            "openai-intent": "conversation-panel",
            "user-agent": "Studio/1.0",
            "x-request-id": request_id or str(uuid.uuid4()),
            "vscode-sessionid": _STUDIO_SESSION_ID,
            "vscode-machineid": _STUDIO_MACHINE_ID,
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

        log_rid = f" (request_id: {request_id[:8]}...)" if request_id else ""
        logger.info(f"Using Copilot API for model: {model}{log_rid}")

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
                logger.error(f"Copilot API error {response.status_code}: {error_text}")
                yield ProviderEvent(
                    type=EventType.ERROR,
                    error=f"❌ Copilot 服务错误 ({response.status_code}): {error_text}",
                    error_meta=_parse_error_meta(response.status_code, error_text, model, "copilot"),
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
            logger.error(f"Copilot API error {response.status_code}: {error_text}")
            from .base import ProviderError
            raise ProviderError(
                f"Copilot 服务错误 ({response.status_code}): {error_text}",
                status_code=response.status_code,
                error_meta=_parse_error_meta(response.status_code, error_text, model, "copilot"),
            )

        return _parse_completion_response(response.json())

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
