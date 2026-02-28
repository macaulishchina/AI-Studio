"""
RAG — Embedding 服务

EmbeddingService:
  - 优先使用 AI Provider 的 embedding endpoint
  - Fallback: 简易 TF-IDF 向量化 (零外部依赖)
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from collections import Counter
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# embedding 维度 (TF-IDF fallback)
TFIDF_DIM = 256


class EmbeddingService:
    """嵌入向量服务"""

    def __init__(self, model: str = "text-embedding-3-small"):
        self._model = model
        self._vocab: Dict[str, int] = {}  # TF-IDF 词汇表
        self._idf: Dict[str, float] = {}

    async def embed(self, texts: List[str]) -> List[List[float]]:
        """
        获取文本的嵌入向量

        优先使用 LLM Provider，失败时 fallback 到 TF-IDF。
        """
        try:
            from studio.backend.ai.llm import get_llm_client
            client = get_llm_client()
            result = await client.embed(texts, self._model)
            if result and len(result) == len(texts):
                return result
        except Exception as e:
            logger.debug(f"Provider embedding 失败, 使用 TF-IDF fallback: {e}")

        # TF-IDF fallback
        return [self._tfidf_embed(text) for text in texts]

    async def embed_single(self, text: str) -> List[float]:
        """单文本嵌入"""
        results = await self.embed([text])
        return results[0] if results else [0.0] * TFIDF_DIM

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
        _embedding_service = EmbeddingService()
    return _embedding_service
