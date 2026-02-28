"""
RAG — 向量索引

基于 numpy (可选) 的向量索引, 带 SQLite 持久化。
如果 numpy 不可用, 使用纯 Python 数组运算。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 向量数学 ────────────────────────────────
try:
    import numpy as np

    def _cosine_batch(query: List[float], matrix: list, top_k: int) -> List[Tuple[int, float]]:
        """批量余弦相似度 (numpy 加速)"""
        q = np.array(query, dtype=np.float32)
        m = np.array(matrix, dtype=np.float32)
        norm_q = np.linalg.norm(q)
        if norm_q == 0:
            return []
        norms = np.linalg.norm(m, axis=1)
        norms[norms == 0] = 1e-10
        scores = m @ q / (norms * norm_q)
        topk_idx = np.argsort(scores)[-top_k:][::-1]
        return [(int(i), float(scores[i])) for i in topk_idx if scores[i] > 0]

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

    def _cosine_batch(query: List[float], matrix: list, top_k: int) -> List[Tuple[int, float]]:
        """纯 Python 余弦相似度"""
        import math
        norm_q = math.sqrt(sum(x * x for x in query))
        if norm_q == 0:
            return []
        results = []
        for idx, row in enumerate(matrix):
            dot = sum(a * b for a, b in zip(query, row))
            norm_r = math.sqrt(sum(x * x for x in row))
            if norm_r == 0:
                continue
            score = dot / (norm_q * norm_r)
            if score > 0:
                results.append((idx, score))
        results.sort(key=lambda x: -x[1])
        return results[:top_k]


# ── 索引条目 ─────────────────────────────────
@dataclass
class IndexEntry:
    """索引中的单条记录"""
    id: str
    content: str
    embedding: List[float]
    source: str = ""
    chunk_type: str = "text"
    start_line: int = 0
    end_line: int = 0
    metadata: dict = field(default_factory=dict)
    updated_at: float = 0.0


class VectorIndex:
    """
    内存 + SQLite 持久化向量索引

    在内存中维护向量矩阵用于快速检索;
    变更持久化到 SQLite 的 rag_index 表。
    """

    def __init__(self):
        self._entries: List[IndexEntry] = []
        self._matrix: List[List[float]] = []
        self._id_map: dict[str, int] = {}  # id → index position
        self._dirty = False

    @property
    def size(self) -> int:
        return len(self._entries)

    # ── 写入 ──

    def upsert(self, entry: IndexEntry):
        """添加或更新条目"""
        entry.updated_at = time.time()
        if entry.id in self._id_map:
            idx = self._id_map[entry.id]
            self._entries[idx] = entry
            self._matrix[idx] = entry.embedding
        else:
            self._id_map[entry.id] = len(self._entries)
            self._entries.append(entry)
            self._matrix.append(entry.embedding)
        self._dirty = True

    def remove(self, entry_id: str):
        """删除条目"""
        if entry_id not in self._id_map:
            return
        idx = self._id_map.pop(entry_id)
        self._entries.pop(idx)
        self._matrix.pop(idx)
        # 重建 id_map
        self._id_map = {e.id: i for i, e in enumerate(self._entries)}
        self._dirty = True

    def clear(self):
        """清空索引"""
        self._entries.clear()
        self._matrix.clear()
        self._id_map.clear()
        self._dirty = True

    # ── 检索 ──

    def search(self, query_embedding: List[float], top_k: int = 5,
               source_filter: Optional[str] = None) -> List[Tuple[IndexEntry, float]]:
        """
        向量相似度搜索

        Args:
            query_embedding: 查询向量
            top_k: 返回前 K 个结果
            source_filter: 可选的来源路径前缀过滤

        Returns:
            [(IndexEntry, score), ...] 按相似度降序
        """
        if not self._matrix:
            return []

        # 过滤
        if source_filter:
            indices = [i for i, e in enumerate(self._entries) if e.source.startswith(source_filter)]
            matrix = [self._matrix[i] for i in indices]
        else:
            indices = list(range(len(self._entries)))
            matrix = self._matrix

        if not matrix:
            return []

        matches = _cosine_batch(query_embedding, matrix, top_k)
        return [(self._entries[indices[idx]], score) for idx, score in matches]

    # ── 持久化 ──

    async def save_to_db(self, db_session):
        """将索引持久化到 SQLite"""
        if not self._dirty:
            return

        from sqlalchemy import text
        await db_session.execute(text("""
            CREATE TABLE IF NOT EXISTS rag_index (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                embedding TEXT NOT NULL,
                source TEXT DEFAULT '',
                chunk_type TEXT DEFAULT 'text',
                start_line INTEGER DEFAULT 0,
                end_line INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}',
                updated_at REAL DEFAULT 0
            )
        """))
        # 全量替换 (简单实现)
        await db_session.execute(text("DELETE FROM rag_index"))
        for entry in self._entries:
            await db_session.execute(
                text("""INSERT INTO rag_index (id, content, embedding, source, chunk_type,
                        start_line, end_line, metadata, updated_at)
                        VALUES (:id, :content, :embedding, :source, :chunk_type,
                                :start_line, :end_line, :metadata, :updated_at)"""),
                {
                    "id": entry.id,
                    "content": entry.content,
                    "embedding": json.dumps(entry.embedding),
                    "source": entry.source,
                    "chunk_type": entry.chunk_type,
                    "start_line": entry.start_line,
                    "end_line": entry.end_line,
                    "metadata": json.dumps(entry.metadata),
                    "updated_at": entry.updated_at,
                },
            )
        await db_session.commit()
        self._dirty = False
        logger.info("RAG 索引已保存 (%d 条)", self.size)

    async def load_from_db(self, db_session):
        """从 SQLite 加载索引"""
        from sqlalchemy import text
        try:
            rows = await db_session.execute(text("SELECT * FROM rag_index"))
            rows = rows.fetchall()
        except Exception:
            return  # 表不存在

        self.clear()
        for row in rows:
            entry = IndexEntry(
                id=row[0],
                content=row[1],
                embedding=json.loads(row[2]),
                source=row[3],
                chunk_type=row[4],
                start_line=row[5],
                end_line=row[6],
                metadata=json.loads(row[7]) if row[7] else {},
                updated_at=row[8],
            )
            self._id_map[entry.id] = len(self._entries)
            self._entries.append(entry)
            self._matrix.append(entry.embedding)

        self._dirty = False
        logger.info("RAG 索引已加载 (%d 条)", self.size)


# ── 全局单例 ──

_vector_index: Optional[VectorIndex] = None


def get_vector_index() -> VectorIndex:
    """获取全局向量索引实例"""
    global _vector_index
    if _vector_index is None:
        _vector_index = VectorIndex()
    return _vector_index
