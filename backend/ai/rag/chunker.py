"""
RAG — 文档分块器

代码感知分块:
  - CodeChunker: 按函数/类分割 (Python/JS/TS)
  - TextChunker: 按段落/句子分割 (通用文本)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Chunk:
    """文档块"""
    content: str
    source: str  # 文件路径
    start_line: int = 0
    end_line: int = 0
    chunk_type: str = "text"  # text / function / class / module
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class CodeChunker:
    """
    代码感知分块器

    按函数/类边界分割代码，保持逻辑完整性。
    """

    def __init__(self, max_chunk_tokens: int = 512, overlap_lines: int = 2):
        self.max_chunk_tokens = max_chunk_tokens
        self.overlap_lines = overlap_lines
        # 简易估算: 1 token ≈ 4 chars
        self.max_chunk_chars = max_chunk_tokens * 4

    def chunk_file(self, content: str, source: str) -> List[Chunk]:
        """将代码文件分块"""
        lines = content.split("\n")
        if not lines:
            return []

        # 尝试按函数/类边界分割
        boundaries = self._detect_boundaries(lines, source)
        if boundaries:
            return self._split_by_boundaries(lines, boundaries, source)

        # 回退: 按固定行数分割
        return self._split_by_lines(lines, source)

    def _detect_boundaries(self, lines: List[str], source: str) -> List[int]:
        """检测函数/类定义的行号"""
        boundaries = [0]
        ext = source.rsplit(".", 1)[-1].lower() if "." in source else ""

        if ext in ("py",):
            pattern = re.compile(r'^(class |def |async def )\w')
        elif ext in ("js", "ts", "jsx", "tsx", "vue"):
            pattern = re.compile(r'^(export |)(function |class |const \w+ = |interface |type )')
        elif ext in ("go",):
            pattern = re.compile(r'^(func |type )\w')
        elif ext in ("java", "kt", "scala"):
            pattern = re.compile(r'^\s*(public |private |protected |)(static |)(class |interface |void |.* \w+\()')
        else:
            return []

        for i, line in enumerate(lines):
            if pattern.match(line.strip()) and i > 0:
                boundaries.append(i)

        return boundaries if len(boundaries) > 1 else []

    def _split_by_boundaries(self, lines: List[str], boundaries: List[int], source: str) -> List[Chunk]:
        """按边界分割"""
        chunks = []
        for i in range(len(boundaries)):
            start = boundaries[i]
            end = boundaries[i + 1] if i + 1 < len(boundaries) else len(lines)
            content = "\n".join(lines[start:end])

            # 如果块太大，进一步分割
            if len(content) > self.max_chunk_chars:
                sub_chunks = self._split_by_lines(lines[start:end], source, base_line=start)
                chunks.extend(sub_chunks)
            else:
                chunks.append(Chunk(
                    content=content,
                    source=source,
                    start_line=start + 1,
                    end_line=end,
                    chunk_type="function",
                ))
        return chunks

    def _split_by_lines(self, lines: List[str], source: str, base_line: int = 0) -> List[Chunk]:
        """按固定行数分割"""
        chunks = []
        max_lines = max(10, self.max_chunk_chars // 80)  # 估算行数
        i = 0
        while i < len(lines):
            end = min(i + max_lines, len(lines))
            content = "\n".join(lines[i:end])
            chunks.append(Chunk(
                content=content,
                source=source,
                start_line=base_line + i + 1,
                end_line=base_line + end,
                chunk_type="text",
            ))
            i = end - self.overlap_lines if end < len(lines) else end
        return chunks


class TextChunker:
    """通用文本分块器 — 按段落/句子分割"""

    def __init__(self, max_chunk_tokens: int = 512, overlap_chars: int = 100):
        self.max_chunk_chars = max_chunk_tokens * 4
        self.overlap_chars = overlap_chars

    def chunk_text(self, content: str, source: str = "") -> List[Chunk]:
        """将文本分块"""
        if len(content) <= self.max_chunk_chars:
            return [Chunk(content=content, source=source, chunk_type="text")]

        # 按段落分割
        paragraphs = content.split("\n\n")
        chunks = []
        current = ""

        for para in paragraphs:
            if len(current) + len(para) > self.max_chunk_chars:
                if current:
                    chunks.append(Chunk(content=current.strip(), source=source, chunk_type="text"))
                current = para
            else:
                current += "\n\n" + para if current else para

        if current:
            chunks.append(Chunk(content=current.strip(), source=source, chunk_type="text"))

        return chunks
