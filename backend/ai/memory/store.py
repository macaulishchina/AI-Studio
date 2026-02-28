"""
Memory — 长期记忆存储

提供会话级和项目级长期记忆:
  - 事实 (Facts): 用户告知的确定性信息
  - 决策 (Decisions): 已做出的技术/设计决策
  - 偏好 (Preferences): 用户偏好 (风格/框架/命名等)

存储后端: SQLite (memory_items 表)
"""
from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)


class MemoryType(str, Enum):
    FACT = "fact"
    DECISION = "decision"
    PREFERENCE = "preference"
    CONTEXT = "context"  # 会话上下文摘要


@dataclass
class MemoryItem:
    """一条记忆"""
    id: str
    content: str
    memory_type: MemoryType
    project_id: Optional[str] = None  # None = 全局
    importance: float = 0.5  # 0~1
    tags: List[str] = field(default_factory=list)
    source: str = ""  # 来源 (conversation / extraction / manual)
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: dict = field(default_factory=dict)


class BaseMemoryStore(ABC):
    """记忆存储抽象基类"""

    @abstractmethod
    async def add(self, item: MemoryItem) -> str:
        """添加记忆, 返回 ID"""

    @abstractmethod
    async def get(self, memory_id: str) -> Optional[MemoryItem]:
        """获取单条记忆"""

    @abstractmethod
    async def search(
        self,
        query: str,
        project_id: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        top_k: int = 10,
    ) -> List[MemoryItem]:
        """搜索相关记忆"""

    @abstractmethod
    async def list_recent(
        self,
        project_id: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        limit: int = 20,
    ) -> List[MemoryItem]:
        """获取最近的记忆"""

    @abstractmethod
    async def remove(self, memory_id: str) -> bool:
        """删除记忆"""

    @abstractmethod
    async def update_importance(self, memory_id: str, importance: float):
        """更新重要性"""


class SQLiteMemoryStore(BaseMemoryStore):
    """基于 SQLite 的记忆存储"""

    def __init__(self):
        self._initialized = False

    async def _ensure_table(self, session):
        """确保表存在"""
        if self._initialized:
            return
        from sqlalchemy import text
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS memory_items (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                project_id TEXT,
                importance REAL DEFAULT 0.5,
                tags TEXT DEFAULT '[]',
                source TEXT DEFAULT '',
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0,
                metadata TEXT DEFAULT '{}'
            )
        """))
        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_memory_project
            ON memory_items(project_id, memory_type)
        """))
        await session.commit()
        self._initialized = True

    async def _get_session(self):
        from studio.backend.core.database import async_session_maker
        return async_session_maker()

    async def add(self, item: MemoryItem) -> str:
        import uuid
        if not item.id:
            item.id = uuid.uuid4().hex[:16]
        now = time.time()
        item.created_at = item.created_at or now
        item.updated_at = now

        async with await self._get_session() as session:
            await self._ensure_table(session)
            from sqlalchemy import text
            await session.execute(
                text("""INSERT OR REPLACE INTO memory_items
                        (id, content, memory_type, project_id, importance, tags,
                         source, created_at, updated_at, metadata)
                        VALUES (:id, :content, :type, :pid, :imp, :tags,
                                :src, :ca, :ua, :meta)"""),
                {
                    "id": item.id,
                    "content": item.content,
                    "type": item.memory_type.value,
                    "pid": item.project_id,
                    "imp": item.importance,
                    "tags": json.dumps(item.tags),
                    "src": item.source,
                    "ca": item.created_at,
                    "ua": item.updated_at,
                    "meta": json.dumps(item.metadata),
                },
            )
            await session.commit()
        return item.id

    async def get(self, memory_id: str) -> Optional[MemoryItem]:
        async with await self._get_session() as session:
            await self._ensure_table(session)
            from sqlalchemy import text
            row = await session.execute(
                text("SELECT * FROM memory_items WHERE id = :id"),
                {"id": memory_id},
            )
            row = row.fetchone()
            if not row:
                return None
            return self._row_to_item(row)

    async def search(
        self,
        query: str,
        project_id: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        top_k: int = 10,
    ) -> List[MemoryItem]:
        """简易关键词搜索 (后续可升级为向量检索)"""
        async with await self._get_session() as session:
            await self._ensure_table(session)
            from sqlalchemy import text
            sql = "SELECT * FROM memory_items WHERE 1=1"
            params: dict = {}
            if project_id:
                sql += " AND (project_id = :pid OR project_id IS NULL)"
                params["pid"] = project_id
            if memory_type:
                sql += " AND memory_type = :mtype"
                params["mtype"] = memory_type.value

            # 关键词过滤
            keywords = [w for w in query.lower().split() if len(w) > 1]
            if keywords:
                conditions = []
                for i, kw in enumerate(keywords[:5]):
                    key = f"kw{i}"
                    conditions.append(f"LOWER(content) LIKE :{key}")
                    params[key] = f"%{kw}%"
                sql += f" AND ({' OR '.join(conditions)})"

            sql += " ORDER BY importance DESC, updated_at DESC LIMIT :limit"
            params["limit"] = top_k

            rows = await session.execute(text(sql), params)
            return [self._row_to_item(r) for r in rows.fetchall()]

    async def list_recent(
        self,
        project_id: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        limit: int = 20,
    ) -> List[MemoryItem]:
        async with await self._get_session() as session:
            await self._ensure_table(session)
            from sqlalchemy import text
            sql = "SELECT * FROM memory_items WHERE 1=1"
            params: dict = {}
            if project_id:
                sql += " AND (project_id = :pid OR project_id IS NULL)"
                params["pid"] = project_id
            if memory_type:
                sql += " AND memory_type = :mtype"
                params["mtype"] = memory_type.value
            sql += " ORDER BY updated_at DESC LIMIT :limit"
            params["limit"] = limit
            rows = await session.execute(text(sql), params)
            return [self._row_to_item(r) for r in rows.fetchall()]

    async def remove(self, memory_id: str) -> bool:
        async with await self._get_session() as session:
            await self._ensure_table(session)
            from sqlalchemy import text
            result = await session.execute(
                text("DELETE FROM memory_items WHERE id = :id"),
                {"id": memory_id},
            )
            await session.commit()
            return result.rowcount > 0

    async def update_importance(self, memory_id: str, importance: float):
        async with await self._get_session() as session:
            await self._ensure_table(session)
            from sqlalchemy import text
            await session.execute(
                text("UPDATE memory_items SET importance = :imp, updated_at = :ua WHERE id = :id"),
                {"imp": importance, "ua": time.time(), "id": memory_id},
            )
            await session.commit()

    @staticmethod
    def _row_to_item(row) -> MemoryItem:
        return MemoryItem(
            id=row[0],
            content=row[1],
            memory_type=MemoryType(row[2]),
            project_id=row[3],
            importance=row[4],
            tags=json.loads(row[5]) if row[5] else [],
            source=row[6],
            created_at=row[7],
            updated_at=row[8],
            metadata=json.loads(row[9]) if row[9] else {},
        )


# ── 全局单例 ──

_memory_store: Optional[BaseMemoryStore] = None


def get_memory_store() -> BaseMemoryStore:
    """获取全局记忆存储实例"""
    global _memory_store
    if _memory_store is None:
        _memory_store = SQLiteMemoryStore()
    return _memory_store
