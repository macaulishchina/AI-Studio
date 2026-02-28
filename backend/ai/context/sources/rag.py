"""
上下文源 — RAG 检索源

从向量索引检索与当前对话语义相关的代码片段，注入系统提示符。
"""
from __future__ import annotations

import logging
from typing import Any, List

from ..builder import BaseContextSource, ContextSection

logger = logging.getLogger(__name__)


class RAGContextSource(BaseContextSource):
    """RAG 检索上下文源"""
    name = "rag"
    priority = 45  # 低于工作区，高于记忆

    async def gather(self, budget_tokens: int, **kwargs) -> List[ContextSection]:
        """
        从 RAG 索引检索相关代码片段

        需要 kwargs 中传入:
          - query: 当前用户消息 (用于语义检索)
          - project_id: 项目 ID (筛选索引范围)
          - rag_enabled: 是否启用 RAG
        """
        if not kwargs.get("rag_enabled", False):
            return []

        query = kwargs.get("query", "")
        if not query:
            return []

        try:
            from studio.backend.ai.rag.retriever import get_retriever
            retriever = get_retriever()
            if not retriever:
                return []

            project_id = kwargs.get("project_id")
            chunks = await retriever.retrieve(
                query=query,
                top_k=5,
                project_id=project_id,
            )

            if not chunks:
                return []

            # 组装检索结果
            result_parts = []
            for chunk in chunks:
                source = chunk.get("source", "unknown")
                content = chunk.get("content", "")
                score = chunk.get("score", 0)
                result_parts.append(
                    f"### {source} (相关度: {score:.2f})\n```\n{content}\n```"
                )

            return [ContextSection(
                name="RAG 检索",
                content="## 相关代码片段 (自动检索)\n" + "\n\n".join(result_parts),
                priority=45,
                trimmable=True,
            )]

        except Exception as e:
            logger.debug(f"RAG 检索跳过: {e}")
            return []
