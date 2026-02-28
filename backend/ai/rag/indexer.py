"""
RAG — 后台索引器

自动扫描工作区文件, 分块 → 向量化 → 写入索引。
支持增量更新 (基于文件修改时间)。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Optional, Set

from studio.backend.ai.rag.chunker import CodeChunker, TextChunker, Chunk
from studio.backend.ai.rag.embeddings import EmbeddingService, get_embedding_service
from studio.backend.ai.rag.index import VectorIndex, IndexEntry, get_vector_index

logger = logging.getLogger(__name__)

# 索引文件扩展名白名单
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".vue", ".go",
    ".java", ".kt", ".scala", ".rs", ".c", ".cpp", ".h",
    ".rb", ".php", ".swift", ".sh", ".bash", ".zsh",
    ".sql", ".r", ".lua", ".dart",
}

TEXT_EXTENSIONS = {
    ".md", ".txt", ".rst", ".adoc", ".csv",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".xml", ".html", ".css", ".scss",
}

# 跳过的路径模式
SKIP_DIRS = {
    "node_modules", ".git", ".svn", "__pycache__", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".next", ".nuxt",
    "venv", ".venv", "env", ".tox", "htmlcov",
    ".idea", ".vscode", "vendor",
}

MAX_FILE_SIZE = 512 * 1024  # 512 KB
BATCH_SIZE = 20  # 每批次处理的文件数


class BackgroundIndexer:
    """
    工作区后台索引器

    扫描 → 分块 → 向量化 → 索引, 支持增量更新。
    """

    def __init__(
        self,
        workspace_path: str = "",
        vector_index: Optional[VectorIndex] = None,
        embedding_service: Optional[EmbeddingService] = None,
        max_chunk_tokens: int = 512,
    ):
        from studio.backend.core.config import settings
        self.workspace_path = workspace_path or settings.WORKSPACE_PATH
        self._index = vector_index or get_vector_index()
        self._embedder = embedding_service or get_embedding_service()
        self._code_chunker = CodeChunker(max_chunk_tokens=max_chunk_tokens)
        self._text_chunker = TextChunker(max_chunk_tokens=max_chunk_tokens)

        self._indexed_files: dict[str, float] = {}  # path → last_modified
        self._running = False
        self._task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def indexed_count(self) -> int:
        return len(self._indexed_files)

    async def start(self, interval_seconds: int = 300):
        """启动后台索引循环"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(interval_seconds))
        logger.info("后台索引器已启动 (间隔 %ds)", interval_seconds)

    async def stop(self):
        """停止后台索引器"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("后台索引器已停止")

    async def index_once(self) -> dict:
        """执行一次全量/增量索引"""
        start = time.time()
        stats = {"scanned": 0, "indexed": 0, "skipped": 0, "errors": 0}

        files = self._scan_files()
        stats["scanned"] = len(files)

        # 过滤需更新的文件
        to_index = []
        for fpath in files:
            try:
                mtime = os.path.getmtime(fpath)
                if fpath in self._indexed_files and self._indexed_files[fpath] >= mtime:
                    stats["skipped"] += 1
                    continue
                to_index.append((fpath, mtime))
            except OSError:
                stats["skipped"] += 1

        # 批量处理
        for i in range(0, len(to_index), BATCH_SIZE):
            batch = to_index[i:i + BATCH_SIZE]
            for fpath, mtime in batch:
                try:
                    await self._index_file(fpath)
                    self._indexed_files[fpath] = mtime
                    stats["indexed"] += 1
                except Exception as e:
                    logger.warning("索引文件失败 %s: %s", fpath, e)
                    stats["errors"] += 1

            # 让出控制权, 避免阻塞事件循环
            await asyncio.sleep(0)

        elapsed = time.time() - start
        logger.info(
            "索引完成: scanned=%d indexed=%d skipped=%d errors=%d (%.2fs)",
            stats["scanned"], stats["indexed"], stats["skipped"], stats["errors"], elapsed,
        )
        return stats

    # ── 内部方法 ──

    async def _loop(self, interval: int):
        """后台循环"""
        while self._running:
            try:
                await self.index_once()
            except Exception as e:
                logger.error("索引循环异常: %s", e)
            await asyncio.sleep(interval)

    def _scan_files(self) -> list[str]:
        """扫描工作区文件"""
        result = []
        allowed_exts = CODE_EXTENSIONS | TEXT_EXTENSIONS
        base = Path(self.workspace_path)

        if not base.exists():
            return result

        for root, dirs, files in os.walk(base):
            # 跳过不需要的目录
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]

            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in allowed_exts:
                    continue
                fpath = os.path.join(root, fname)
                try:
                    if os.path.getsize(fpath) > MAX_FILE_SIZE:
                        continue
                except OSError:
                    continue
                result.append(fpath)

        return result

    async def _index_file(self, fpath: str):
        """索引单个文件"""
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            return

        if not content.strip():
            return

        # 先删除该文件的旧条目
        old_ids = [eid for eid, idx in self._index._id_map.items()
                   if self._index._entries[idx].source == fpath]
        for oid in old_ids:
            self._index.remove(oid)

        # 分块
        ext = os.path.splitext(fpath)[1].lower()
        if ext in CODE_EXTENSIONS:
            # 取相对路径作为 source
            rel = os.path.relpath(fpath, self.workspace_path)
            chunks = self._code_chunker.chunk_file(content, rel)
        else:
            rel = os.path.relpath(fpath, self.workspace_path)
            chunks = self._text_chunker.chunk_text(content, rel)

        # 向量化 + 入库
        for chunk in chunks:
            if not chunk.content.strip():
                continue
            try:
                embedding = await self._embedder.embed_text(chunk.content)
            except Exception:
                continue

            entry_id = hashlib.md5(
                f"{chunk.source}:{chunk.start_line}:{chunk.end_line}:{chunk.content[:50]}".encode()
            ).hexdigest()

            self._index.upsert(IndexEntry(
                id=entry_id,
                content=chunk.content,
                embedding=embedding,
                source=chunk.source,
                chunk_type=chunk.chunk_type,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                metadata=chunk.metadata or {},
            ))


# ── 全局单例 ──

_indexer: Optional[BackgroundIndexer] = None


def get_indexer() -> BackgroundIndexer:
    """获取全局索引器实例"""
    global _indexer
    if _indexer is None:
        _indexer = BackgroundIndexer()
    return _indexer
