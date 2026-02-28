"""
RAG — 检索器

Hybrid Retriever: 向量检索 + 关键词匹配, 支持 rerank。
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from studio.backend.ai.rag.index import VectorIndex, IndexEntry, get_vector_index

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """检索结果"""
    content: str
    source: str
    score: float
    start_line: int = 0
    end_line: int = 0
    chunk_type: str = "text"
    match_type: str = "vector"  # vector / keyword / hybrid


class RAGRetriever:
    """
    RAG 检索器

    支持:
    - 向量相似度检索
    - 关键词匹配检索
    - 混合模式 (两者合并 + rerank)
    """

    def __init__(
        self,
        vector_index: Optional[VectorIndex] = None,
        embedding_service=None,
        top_k: int = 5,
        keyword_weight: float = 0.3,
        vector_weight: float = 0.7,
        min_score: float = 0.1,
    ):
        self._index = vector_index or get_vector_index()
        self._embedder = embedding_service
        self.top_k = top_k
        self.keyword_weight = keyword_weight
        self.vector_weight = vector_weight
        self.min_score = min_score

    async def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        source_filter: Optional[str] = None,
        mode: str = "hybrid",  # vector / keyword / hybrid
    ) -> List[RetrievalResult]:
        """
        执行检索

        Args:
            query: 查询文本
            top_k: 返回结果数 (覆盖默认)
            source_filter: 文件路径前缀过滤
            mode: 检索模式

        Returns:
            按相关性降序排列的结果
        """
        k = top_k or self.top_k
        results: dict[str, RetrievalResult] = {}  # id → result

        # 1) 向量检索
        if mode in ("vector", "hybrid") and self._index.size > 0:
            vec_results = await self._vector_search(query, k * 2, source_filter)
            for r in vec_results:
                rid = self._result_id(r)
                if rid not in results:
                    results[rid] = r
                else:
                    results[rid].score = max(results[rid].score, r.score)

        # 2) 关键词检索
        if mode in ("keyword", "hybrid") and self._index.size > 0:
            kw_results = self._keyword_search(query, k * 2, source_filter)
            for r in kw_results:
                rid = self._result_id(r)
                if rid not in results:
                    results[rid] = r
                else:
                    # 混合: 加权合并
                    existing = results[rid]
                    if r.match_type == "keyword" and existing.match_type == "vector":
                        existing.score = (
                            existing.score * self.vector_weight
                            + r.score * self.keyword_weight
                        )
                        existing.match_type = "hybrid"

        # 3) 过滤 + 排序
        final = [r for r in results.values() if r.score >= self.min_score]
        final.sort(key=lambda x: -x.score)
        return final[:k]

    async def _vector_search(
        self, query: str, top_k: int, source_filter: Optional[str]
    ) -> List[RetrievalResult]:
        """向量检索"""
        if self._embedder is None:
            from studio.backend.ai.rag.embeddings import get_embedding_service
            self._embedder = get_embedding_service()

        try:
            query_vec = await self._embedder.embed_text(query)
        except Exception as e:
            logger.warning("向量化查询失败: %s", e)
            return []

        matches = self._index.search(query_vec, top_k, source_filter)
        return [
            RetrievalResult(
                content=entry.content,
                source=entry.source,
                score=score,
                start_line=entry.start_line,
                end_line=entry.end_line,
                chunk_type=entry.chunk_type,
                match_type="vector",
            )
            for entry, score in matches
        ]

    def _keyword_search(
        self, query: str, top_k: int, source_filter: Optional[str]
    ) -> List[RetrievalResult]:
        """关键词检索 (BM25-like 简易实现)"""
        tokens = self._tokenize(query)
        if not tokens:
            return []

        scored: List[Tuple[IndexEntry, float]] = []
        for entry in self._index._entries:
            if source_filter and not entry.source.startswith(source_filter):
                continue
            content_lower = entry.content.lower()
            score = 0.0
            for token in tokens:
                count = content_lower.count(token)
                if count > 0:
                    # TF 部分 (简化)
                    tf = count / (len(content_lower.split()) + 1)
                    score += tf
            if score > 0:
                scored.append((entry, score))

        # 归一化
        if scored:
            max_score = max(s for _, s in scored)
            if max_score > 0:
                scored = [(e, s / max_score) for e, s in scored]

        scored.sort(key=lambda x: -x[1])
        return [
            RetrievalResult(
                content=entry.content,
                source=entry.source,
                score=score,
                start_line=entry.start_line,
                end_line=entry.end_line,
                chunk_type=entry.chunk_type,
                match_type="keyword",
            )
            for entry, score in scored[:top_k]
        ]

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """简易分词"""
        text = text.lower()
        # 英文单词 + 中文字符
        words = re.findall(r'[a-z_][a-z0-9_]*|[\u4e00-\u9fff]+', text)
        return [w for w in words if len(w) > 1]

    @staticmethod
    def _result_id(r: RetrievalResult) -> str:
        """生成结果去重 ID"""
        return hashlib.md5(f"{r.source}:{r.start_line}:{r.end_line}".encode()).hexdigest()


# ── 全局单例 ──

_retriever: Optional[RAGRetriever] = None


def get_retriever() -> RAGRetriever:
    """获取全局 RAG 检索器"""
    global _retriever
    if _retriever is None:
        _retriever = RAGRetriever()
    return _retriever
