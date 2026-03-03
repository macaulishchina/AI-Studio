"""
Memory — 长期记忆存储 (v2)

完全基于 ORM (MemoryItemModel) + 向量/关键词混合检索。
embedding 生成复用 backend.ai.rag.embeddings 模块。
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import List, Optional

from sqlalchemy import select, update, delete, func

from backend.core.database import async_session_maker
from backend.models import MemoryItemModel, MemoryType

logger = logging.getLogger(__name__)


class MemoryStore:
    """基于 ORM 的记忆存储 — 向量 + 关键词混合检索"""

    # ── 写入 ──

    async def add(
        self,
        content: str,
        memory_type: str | MemoryType,
        user_id: str,
        project_id: Optional[str] = None,
        conversation_id: Optional[int] = None,
        importance: float = 0.5,
        tags: Optional[list] = None,
        source: str = "",
        metadata: Optional[dict] = None,
    ) -> str:
        """添加一条记忆, 自动生成 embedding。返回 id。"""
        mem_id = uuid.uuid4().hex[:16]
        now = time.time()
        mtype = memory_type.value if isinstance(memory_type, MemoryType) else memory_type

        # 生成 embedding (graceful degradation)
        embedding = None
        try:
            from backend.ai.rag.embeddings import get_embedding_service
            svc = get_embedding_service()
            embedding = await svc.embed_text(content)
        except Exception as e:
            logger.debug(f"embedding 生成失败 (退化到纯关键词): {e}")

        item = MemoryItemModel(
            id=mem_id,
            content=content,
            memory_type=mtype,
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            importance=importance,
            embedding=embedding,
            tags=tags or [],
            source=source,
            access_count=0,
            last_accessed=now,
            created_at=now,
            updated_at=now,
            metadata_json=metadata or {},
        )

        async with async_session_maker() as db:
            db.add(item)
            await db.commit()

        return mem_id

    async def get(self, memory_id: str) -> Optional[MemoryItemModel]:
        async with async_session_maker() as db:
            result = await db.execute(
                select(MemoryItemModel).where(MemoryItemModel.id == memory_id)
            )
            return result.scalar_one_or_none()

    # ── 混合检索 ──

    async def search(
        self,
        query: str,
        user_id: str,
        memory_type: Optional[str | MemoryType] = None,
        project_id: Optional[str] = None,
        top_k: int = 10,
    ) -> List[MemoryItemModel]:
        """
        向量 + 关键词混合检索。

        排序公式:
          score = 0.4 * vector_sim + 0.3 * keyword_hit + 0.2 * importance + 0.1 * recency
        """
        mtype = memory_type.value if isinstance(memory_type, MemoryType) else memory_type

        # 1) 拉候选集 (按 user_id 过滤, 上限 200)
        async with async_session_maker() as db:
            stmt = select(MemoryItemModel).where(MemoryItemModel.user_id == user_id)
            if mtype:
                stmt = stmt.where(MemoryItemModel.memory_type == mtype)
            if project_id:
                stmt = stmt.where(
                    (MemoryItemModel.project_id == project_id)
                    | (MemoryItemModel.project_id.is_(None))
                )
            stmt = stmt.order_by(MemoryItemModel.importance.desc()).limit(200)
            rows = list((await db.execute(stmt)).scalars().all())

            if not rows:
                return []

            # 2) 生成查询 embedding
            query_emb = None
            if query:
                try:
                    from backend.ai.rag.embeddings import get_embedding_service
                    svc = get_embedding_service()
                    query_emb = await svc.embed_text(query)
                except Exception:
                    pass

            # 3) 计算混合分数
            keywords = [w.lower() for w in query.split() if len(w) > 1] if query else []
            now = time.time()

            scored: list[tuple[float, MemoryItemModel]] = []
            for item in rows:
                # 向量相似度
                vec_score = 0.0
                if query_emb and item.embedding:
                    from backend.ai.rag.embeddings import cosine_similarity
                    vec_score = max(0.0, cosine_similarity(query_emb, item.embedding))

                # 关键词命中率
                kw_score = 0.0
                if keywords:
                    content_lower = item.content.lower()
                    hits = sum(1 for kw in keywords if kw in content_lower)
                    kw_score = hits / len(keywords)

                # 重要性 (已经 0~1)
                imp_score = item.importance or 0.5

                # 时效性 (最近访问): 30 天内线性衰减
                age_seconds = now - (item.last_accessed or item.created_at or now)
                recency = max(0.0, 1.0 - age_seconds / (30 * 86400))

                score = 0.4 * vec_score + 0.3 * kw_score + 0.2 * imp_score + 0.1 * recency
                scored.append((score, item))

            # 排序取 top_k
            scored.sort(key=lambda x: x[0], reverse=True)
            results = [item for _, item in scored[:top_k]]

            # 更新访问计数
            if results:
                ids = [it.id for it in results]
                await db.execute(
                    update(MemoryItemModel)
                    .where(MemoryItemModel.id.in_(ids))
                    .values(access_count=MemoryItemModel.access_count + 1, last_accessed=now)
                )
                await db.commit()

            return results

    # ── 列表查询 ──

    async def list_recent(
        self,
        user_id: str,
        memory_type: Optional[str | MemoryType] = None,
        project_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[MemoryItemModel]:
        mtype = memory_type.value if isinstance(memory_type, MemoryType) else memory_type
        async with async_session_maker() as db:
            stmt = select(MemoryItemModel).where(MemoryItemModel.user_id == user_id)
            if mtype:
                stmt = stmt.where(MemoryItemModel.memory_type == mtype)
            if project_id:
                stmt = stmt.where(
                    (MemoryItemModel.project_id == project_id)
                    | (MemoryItemModel.project_id.is_(None))
                )
            stmt = stmt.order_by(MemoryItemModel.updated_at.desc()).limit(limit)
            return list((await db.execute(stmt)).scalars().all())

    async def list_by_conversation(self, conversation_id: int, limit: int = 50) -> List[MemoryItemModel]:
        async with async_session_maker() as db:
            stmt = (
                select(MemoryItemModel)
                .where(MemoryItemModel.conversation_id == conversation_id)
                .order_by(MemoryItemModel.created_at.desc())
                .limit(limit)
            )
            return list((await db.execute(stmt)).scalars().all())

    async def list_by_user(
        self, user_id: str,
        memory_type: Optional[str | MemoryType] = None,
        limit: int = 50,
    ) -> List[MemoryItemModel]:
        mtype = memory_type.value if isinstance(memory_type, MemoryType) else memory_type
        async with async_session_maker() as db:
            stmt = select(MemoryItemModel).where(MemoryItemModel.user_id == user_id)
            if mtype:
                stmt = stmt.where(MemoryItemModel.memory_type == mtype)
            stmt = stmt.order_by(
                MemoryItemModel.importance.desc(),
                MemoryItemModel.updated_at.desc(),
            ).limit(limit)
            return list((await db.execute(stmt)).scalars().all())

    # ── 更新/删除 ──

    async def remove(self, memory_id: str) -> bool:
        async with async_session_maker() as db:
            result = await db.execute(
                delete(MemoryItemModel).where(MemoryItemModel.id == memory_id)
            )
            await db.commit()
            return result.rowcount > 0

    async def update_importance(self, memory_id: str, importance: float):
        async with async_session_maker() as db:
            await db.execute(
                update(MemoryItemModel)
                .where(MemoryItemModel.id == memory_id)
                .values(importance=importance, updated_at=time.time())
            )
            await db.commit()

    async def update_content(self, memory_id: str, content: str):
        """更新内容 + 重新生成 embedding"""
        embedding = None
        try:
            from backend.ai.rag.embeddings import get_embedding_service
            svc = get_embedding_service()
            embedding = await svc.embed_text(content)
        except Exception:
            pass

        async with async_session_maker() as db:
            values: dict = {"content": content, "updated_at": time.time()}
            if embedding is not None:
                values["embedding"] = embedding
            await db.execute(
                update(MemoryItemModel)
                .where(MemoryItemModel.id == memory_id)
                .values(**values)
            )
            await db.commit()

    # ── 维护 ──

    async def decay_old_memories(self, threshold_days: int = 30, decay_factor: float = 0.9):
        """衰减长期未访问记忆的 importance"""
        cutoff = time.time() - threshold_days * 86400
        async with async_session_maker() as db:
            stmt = (
                select(MemoryItemModel)
                .where(
                    MemoryItemModel.last_accessed < cutoff,
                    MemoryItemModel.last_accessed > 0,
                    MemoryItemModel.importance > 0.1,
                )
            )
            items = list((await db.execute(stmt)).scalars().all())
            now = time.time()
            for item in items:
                item.importance = max(item.importance * decay_factor, 0.1)
                item.updated_at = now
            await db.commit()

    async def trim_by_user(self, user_id: str, max_count: int = 500) -> int:
        """裁剪超出上限的低重要性记忆"""
        async with async_session_maker() as db:
            count_result = await db.execute(
                select(func.count()).select_from(MemoryItemModel)
                .where(MemoryItemModel.user_id == user_id)
            )
            total = count_result.scalar() or 0
            if total <= max_count:
                return 0

            excess = total - max_count
            stmt = (
                select(MemoryItemModel.id)
                .where(MemoryItemModel.user_id == user_id)
                .order_by(MemoryItemModel.importance.asc(), MemoryItemModel.updated_at.asc())
                .limit(excess)
            )
            ids_to_remove = [row[0] for row in (await db.execute(stmt)).all()]
            if ids_to_remove:
                await db.execute(
                    delete(MemoryItemModel).where(MemoryItemModel.id.in_(ids_to_remove))
                )
                await db.commit()
            return len(ids_to_remove)

    async def count(
        self,
        user_id: Optional[str] = None,
        memory_type: Optional[str | MemoryType] = None,
    ) -> int:
        mtype = memory_type.value if isinstance(memory_type, MemoryType) else memory_type
        async with async_session_maker() as db:
            stmt = select(func.count()).select_from(MemoryItemModel)
            if user_id:
                stmt = stmt.where(MemoryItemModel.user_id == user_id)
            if mtype:
                stmt = stmt.where(MemoryItemModel.memory_type == mtype)
            return (await db.execute(stmt)).scalar() or 0

    async def get_all_user_ids(self) -> List[str]:
        """获取所有有记忆的用户 ID (用于批量维护)"""
        async with async_session_maker() as db:
            stmt = select(MemoryItemModel.user_id).distinct()
            rows = (await db.execute(stmt)).all()
            return [r[0] for r in rows if r[0]]

    async def clear_user(self, user_id: str) -> int:
        """清空某用户所有记忆"""
        async with async_session_maker() as db:
            result = await db.execute(
                delete(MemoryItemModel).where(MemoryItemModel.user_id == user_id)
            )
            await db.commit()
            return result.rowcount


# ── 全局单例 ──

_memory_store: Optional[MemoryStore] = None


def get_memory_store() -> MemoryStore:
    global _memory_store
    if _memory_store is None:
        _memory_store = MemoryStore()
    return _memory_store
