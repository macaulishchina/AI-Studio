"""
上下文源 — 记忆源

从长期记忆 (项目事实 + 决策记录) 注入相关上下文。
"""
from __future__ import annotations

import logging
from typing import Any, List

from ..builder import BaseContextSource, ContextSection

logger = logging.getLogger(__name__)


class MemoryContextSource(BaseContextSource):
    """长期记忆上下文源"""
    name = "memory"
    priority = 50  # 较低优先级

    async def gather(self, budget_tokens: int, **kwargs) -> List[ContextSection]:
        """
        从记忆存储加载项目相关事实和决策

        需要 kwargs 中传入:
          - project_id: 项目 ID
          - memory_enabled: 是否启用记忆
        """
        if not kwargs.get("memory_enabled", False):
            return []

        project_id = kwargs.get("project_id")
        if not project_id:
            return []

        try:
            from studio.backend.ai.memory.store import get_memory_store
            store = get_memory_store()
            if not store:
                return []

            sections = []

            # 加载事实
            facts = await store.query_facts(project_id, limit=10)
            if facts:
                fact_lines = []
                for f in facts:
                    fact_lines.append(f"- {f.get('subject', '')} {f.get('predicate', '')} {f.get('object', '')}")
                sections.append(ContextSection(
                    name="项目记忆",
                    content="## 项目记忆 (长期)\n" + "\n".join(fact_lines),
                    priority=50,
                    trimmable=True,
                ))

            # 加载决策
            decisions = await store.query_decisions(project_id, limit=5)
            if decisions:
                decision_lines = []
                for d in decisions:
                    decision_lines.append(f"- **{d.get('title', '')}**: {d.get('chosen', '')} ({d.get('reason', '')})")
                sections.append(ContextSection(
                    name="决策记录",
                    content="## 关键决策\n" + "\n".join(decision_lines),
                    priority=55,
                    trimmable=True,
                ))

            return sections

        except Exception as e:
            logger.debug(f"记忆加载跳过: {e}")
            return []
