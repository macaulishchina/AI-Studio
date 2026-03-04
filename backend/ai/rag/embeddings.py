"""
RAG — Embedding 服务

EmbeddingService:
  - 优先使用 AI Provider 的 embedding endpoint
  - Fallback: 简易 TF-IDF 向量化 (零外部依赖)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import random
import re
from collections import Counter
from typing import Any, Dict, List, Optional

from backend.ai.providers.base import ProviderError

logger = logging.getLogger(__name__)

# embedding 维度 (TF-IDF fallback)
TFIDF_DIM = 256


class EmbeddingService:
    """嵌入向量服务 (含熔断器: 连续限流后自动跳过 Provider)"""

    # 熔断冷却时间 (秒) — 被限流后多久才重新尝试 Provider
    CIRCUIT_BREAKER_COOLDOWN = 300  # 5 分钟

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        provider_slug: str = "github",
        retry_max: int = 4,
        retry_base_seconds: float = 0.8,
    ):
        self._model = model
        self._provider_slug = provider_slug
        self._retry_max = max(0, int(retry_max))
        self._retry_base_seconds = max(0.1, float(retry_base_seconds))
        self._vocab: Dict[str, int] = {}  # TF-IDF 词汇表
        self._idf: Dict[str, float] = {}
        # 熔断器状态
        self._circuit_open_until: float = 0.0  # Unix timestamp, >0 表示熔断中
        self._consecutive_429s: int = 0

    def reset_circuit_breaker(self):
        """手动重置熔断器 (例如在新的索引周期开始时)"""
        self._circuit_open_until = 0.0
        self._consecutive_429s = 0

    @property
    def is_circuit_open(self) -> bool:
        """熔断器是否处于打开状态 (跳过 Provider)"""
        import time as _time
        return self._circuit_open_until > _time.time()

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """
        获取文本的嵌入向量

        优先使用 LLM Provider，失败时 fallback 到 TF-IDF。
        内置熔断器: 连续 429 失败后自动跳过 Provider，冷却后恢复。
        """
        import time as _time

        # 熔断器打开时直接走 TF-IDF，不浪费时间重试
        if self.is_circuit_open:
            return [self._tfidf_embed(text) for text in texts]

        try:
            from backend.ai.llm import get_llm_client
            client = get_llm_client()

            for attempt in range(self._retry_max + 1):
                try:
                    result = await client.embed(
                        texts,
                        self._model,
                        provider_slug=self._provider_slug,
                    )
                    if result and len(result) == len(texts):
                        # 成功: 重置熔断计数
                        self._consecutive_429s = 0
                        return result
                    break
                except ProviderError as e:
                    if e.status_code == 429 and attempt < self._retry_max:
                        delay = self._retry_base_seconds * (2 ** attempt)
                        jitter = delay * (0.1 + random.random() * 0.2)
                        wait_for = delay + jitter
                        logger.warning(
                            "Embedding 命中限流(429), %.2fs 后重试 (%d/%d)",
                            wait_for,
                            attempt + 1,
                            self._retry_max,
                        )
                        await asyncio.sleep(wait_for)
                        continue
                    if e.status_code == 429:
                        # 所有重试都 429: 触发熔断
                        self._consecutive_429s += 1
                        self._circuit_open_until = _time.time() + self.CIRCUIT_BREAKER_COOLDOWN
                        logger.warning(
                            "Embedding 连续限流, 熔断器已开启 — %ds 内跳过 Provider 直接使用 TF-IDF fallback",
                            self.CIRCUIT_BREAKER_COOLDOWN,
                        )
                    raise
        except Exception as e:
            logger.debug(f"Provider embedding 失败, 使用 TF-IDF fallback: {e}")

        # TF-IDF fallback
        return [self._tfidf_embed(text) for text in texts]

    async def embed_single(self, text: str) -> List[float]:
        """单文本嵌入"""
        results = await self.embed([text])
        return results[0] if results else [0.0] * TFIDF_DIM

    # 别名: retriever / indexer 统一调用 embed_text
    async def embed_text(self, text: str) -> List[float]:
        return await self.embed_single(text)

    def _tfidf_embed(self, text: str) -> List[float]:
        """简易 TF-IDF 向量化"""
        tokens = _tokenize(text)
        if not tokens:
            return [0.0] * TFIDF_DIM

        tf = Counter(tokens)
        max_tf = max(tf.values()) if tf else 1

        vec = [0.0] * TFIDF_DIM
        for token, count in tf.items():
            # Hash token to dimension
            idx = int(hashlib.md5(token.encode()).hexdigest(), 16) % TFIDF_DIM
            weight = (0.5 + 0.5 * count / max_tf)  # normalized TF
            vec[idx] += weight

        # L2 normalize
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


def _tokenize(text: str) -> List[str]:
    """简单分词 (英文 + 中文字符级)"""
    # 英文单词
    words = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', text.lower())
    # 中文字符
    chars = re.findall(r'[\u4e00-\u9fff]', text)
    return words + chars


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """余弦相似度"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# 全局实例
_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    global _embedding_service
    if _embedding_service is None:
        from backend.core.config import settings

        _embedding_service = EmbeddingService(
            model=settings.rag_embedding_model,
            provider_slug=settings.rag_embedding_provider,
            retry_max=settings.rag_embedding_retry_max,
            retry_base_seconds=settings.rag_embedding_retry_base_seconds,
        )
    return _embedding_service
