"""
上下文源 — 工作区源

从 workspace 发现项目结构、关键文件、目录树，
注入 AI 系统提示符以提供项目感知能力。

提取自 context_service.py 的 discover_key_files/dirs + build_project_context。
"""
from __future__ import annotations

import os
import logging
from typing import Any, List

from ..builder import BaseContextSource, ContextSection

logger = logging.getLogger(__name__)

# 优先级排序的候选关键文件
_CANDIDATE_KEY_FILES = [
    "CLAUDE.md", "README.md", "package.json", "requirements.txt",
    "pyproject.toml", "setup.cfg", "Cargo.toml", "go.mod",
    "pom.xml", "build.gradle", "CMakeLists.txt",
    "docker-compose.yml", "Dockerfile", "Makefile",
    "tsconfig.json", "vite.config.ts", "webpack.config.js",
    ".env.example", "TODO.md", "CHANGELOG.md",
]

# 候选关键目录
_CANDIDATE_KEY_DIRS = [
    "app/api", "app/models", "app/services", "app/core",
    "src", "src/views", "src/components", "src/api",
    "frontend/src/views", "frontend/src/components",
    "backend/api", "backend/services", "backend/core",
    "cmd", "internal", "pkg",
    "lib", "tests", "test",
]

# 目录树跳过
_TREE_SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", ".venv", "venv",
    "dist", ".claude", "studio-data", "data", ".idea", ".vscode",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "htmlcov",
    ".next", ".nuxt", "build", "target",
}


class WorkspaceContextSource(BaseContextSource):
    """工作区上下文源 — 提供项目结构和关键文件"""
    name = "workspace"
    priority = 30  # 中等优先级

    async def gather(self, budget_tokens: int, **kwargs) -> List[ContextSection]:
        from studio.backend.core.config import settings
        workspace = kwargs.get("workspace") or getattr(settings, "WORKSPACE_PATH", "")
        if not workspace or not os.path.isdir(workspace):
            return []

        sections = []

        # 项目树
        tree = _get_tree(workspace, max_depth=3)
        if tree:
            sections.append(ContextSection(
                name="项目结构",
                content=f"## 项目目录结构\n```\n{tree}\n```",
                priority=30,
                trimmable=True,
            ))

        # 关键文件内容
        key_files = _discover_key_files(workspace)
        if key_files:
            file_contents = []
            for rel_path in key_files[:6]:
                content = _read_file_safe(os.path.join(workspace, rel_path))
                if content:
                    file_contents.append(f"### {rel_path}\n```\n{content}\n```")
            if file_contents:
                sections.append(ContextSection(
                    name="关键文件",
                    content="## 项目关键文件\n" + "\n\n".join(file_contents),
                    priority=35,
                    trimmable=True,
                ))

        # 关键目录
        key_dirs = _discover_key_dirs(workspace)
        if key_dirs:
            dir_infos = []
            for rel_dir in key_dirs[:4]:
                files = _list_dir_files(os.path.join(workspace, rel_dir))
                if files:
                    dir_infos.append(f"- `{rel_dir}/`: {files}")
            if dir_infos:
                sections.append(ContextSection(
                    name="关键目录",
                    content="## 关键目录概览\n" + "\n".join(dir_infos),
                    priority=40,
                    trimmable=True,
                ))

        return sections


def _discover_key_files(workspace: str) -> List[str]:
    """发现工作区中的关键文件"""
    found = []
    for name in _CANDIDATE_KEY_FILES:
        if os.path.isfile(os.path.join(workspace, name)):
            found.append(name)
            if len(found) >= 8:
                break
    return found


def _discover_key_dirs(workspace: str) -> List[str]:
    """发现工作区中的关键目录"""
    found = []
    for name in _CANDIDATE_KEY_DIRS:
        if os.path.isdir(os.path.join(workspace, name)):
            found.append(name)
            if len(found) >= 8:
                break
    return found


def _get_tree(path: str, max_depth: int = 3, prefix: str = "", depth: int = 0) -> str:
    """递归构建目录树"""
    if depth >= max_depth:
        return ""
    try:
        entries = sorted(os.listdir(path))
    except PermissionError:
        return ""

    entries = [e for e in entries if e not in _TREE_SKIP_DIRS and not e.startswith(".")]
    lines = []
    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        full = os.path.join(path, entry)
        if os.path.isdir(full):
            lines.append(f"{prefix}{connector}{entry}/")
            ext = "    " if is_last else "│   "
            subtree = _get_tree(full, max_depth, prefix + ext, depth + 1)
            if subtree:
                lines.append(subtree)
        else:
            lines.append(f"{prefix}{connector}{entry}")
    return "\n".join(lines)


def _read_file_safe(filepath: str, max_lines: int = 200) -> str:
    """安全读取文件"""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        if len(lines) > max_lines:
            return "".join(lines[:max_lines]) + f"\n... (截断, 共 {len(lines)} 行)"
        return "".join(lines)
    except Exception:
        return ""


def _list_dir_files(dirpath: str) -> str:
    """列出目录下的文件名"""
    try:
        entries = [e for e in sorted(os.listdir(dirpath)) if not e.startswith("__")]
        return ", ".join(entries[:20])
    except Exception:
        return ""
