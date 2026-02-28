"""
内置工具 — 文件操作

read_file, search_text, list_directory, get_file_tree
"""
import asyncio
import fnmatch
import os
import re
from typing import Any, Dict, List, Tuple

import logging

logger = logging.getLogger(__name__)

# ==================== 安全限制常量 ====================

# 敏感文件/目录黑名单
SENSITIVE_PATTERNS = {
    ".env", ".env.local", ".env.production",
    ".git/objects", ".git/refs", ".git/logs",
    "venv", ".venv", "node_modules", "__pycache__",
    "id_rsa", "id_ed25519",
}

SENSITIVE_EXTENSIONS = {
    ".key", ".pem", ".p12", ".pfx", ".jks",
    ".secret", ".credentials",
}

# 允许读取的配置文件
CONFIG_ALLOWLIST = {
    "package.json", "tsconfig.json", "vite.config.ts",
    "docker-compose.yml", "Dockerfile", "nginx.conf",
    "requirements.txt", "pyproject.toml", "setup.cfg",
    "CLAUDE.md", "README.md", "TODO.md",
}

# 读取/搜索限制
MAX_READ_LINES = 200
MAX_SEARCH_RESULTS = 30
SEARCH_CONTEXT_LINES = 1
TOOL_TIMEOUT_SECONDS = 10

# 目录树限制
MAX_TREE_DEPTH = 4
TREE_SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", ".venv", "venv",
    "dist", ".claude", "studio-data", "data", ".idea", ".vscode",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "htmlcov",
    ".next", ".nuxt", "build", "target",
}


# ==================== 路径安全检查 ====================

def validate_path(workspace: str, rel_path: str) -> Tuple[bool, str, str]:
    """
    验证路径安全性

    Returns:
        (is_safe, absolute_path, error_message)
    """
    rel_path = rel_path.strip().lstrip("/")
    abs_path = os.path.realpath(os.path.join(workspace, rel_path))
    workspace_real = os.path.realpath(workspace)
    if not abs_path.startswith(workspace_real + os.sep) and abs_path != workspace_real:
        return False, abs_path, f"⚠️ 路径越界: '{rel_path}' 不在项目目录内"
    return True, abs_path, ""


def is_sensitive_file(rel_path: str) -> bool:
    """检查文件是否在敏感黑名单中"""
    basename = os.path.basename(rel_path)
    _, ext = os.path.splitext(basename)

    if basename in CONFIG_ALLOWLIST:
        return False
    if basename in SENSITIVE_PATTERNS:
        return True
    if ext.lower() in SENSITIVE_EXTENSIONS:
        return True

    path_parts = rel_path.replace("\\", "/").split("/")
    for part in path_parts:
        if part in SENSITIVE_PATTERNS:
            return True
    return False


# ==================== read_file ====================

async def tool_read_file(args: Dict[str, Any], workspace: str) -> str:
    """读取文件内容"""
    path = args.get("path", "")
    start_line = args.get("start_line", 1)
    end_line = args.get("end_line")

    if not path:
        return "⚠️ 请指定文件路径"

    is_safe, abs_path, error = validate_path(workspace, path)
    if not is_safe:
        return error

    if is_sensitive_file(path):
        return f"⚠️ 无法读取敏感文件: '{path}'"

    if not os.path.exists(abs_path):
        return f"⚠️ 文件不存在: '{path}'"

    if not os.path.isfile(abs_path):
        return f"⚠️ '{path}' 不是文件 (可能是目录，请使用 list_directory)"

    file_size = os.path.getsize(abs_path)
    if file_size > 1024 * 1024:
        return f"⚠️ 文件过大 ({file_size / 1024:.0f}KB)，请指定行范围读取"

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        return f"⚠️ '{path}' 是二进制文件，无法读取"

    total_lines = len(lines)
    start = max(1, start_line or 1)
    end = min(total_lines, end_line or (start + MAX_READ_LINES - 1))

    if end - start + 1 > MAX_READ_LINES:
        end = start + MAX_READ_LINES - 1

    selected = lines[start - 1:end]
    content = "".join(selected)

    header = f"📄 {path} (行 {start}-{end}, 共 {total_lines} 行)"
    if end < total_lines:
        header += f" [截断: 使用 start_line/end_line 查看更多]"

    return f"{header}\n```\n{content}```"


# ==================== search_text ====================

async def tool_search_text(args: Dict[str, Any], workspace: str) -> str:
    """全文搜索"""
    query = args.get("query", "")
    is_regex = args.get("is_regex", False)
    include_pattern = args.get("include_pattern", "")

    if not query:
        return "⚠️ 请指定搜索内容"

    cmd = ["grep", "-rn", "--color=never"]
    if is_regex:
        cmd.append("-E")
    else:
        cmd.append("-F")

    cmd.extend(["-B", str(SEARCH_CONTEXT_LINES), "-A", str(SEARCH_CONTEXT_LINES)])
    cmd.extend(["-m", str(MAX_SEARCH_RESULTS)])

    for skip_dir in TREE_SKIP_DIRS:
        cmd.extend(["--exclude-dir", skip_dir])
    for ext in SENSITIVE_EXTENSIONS:
        cmd.extend(["--exclude", f"*{ext}"])
    cmd.extend(["--exclude", ".env*"])

    if include_pattern:
        clean_pattern = include_pattern
        if '/' in clean_pattern:
            clean_pattern = clean_pattern.rsplit('/', 1)[-1]
        if not clean_pattern or clean_pattern == '**':
            clean_pattern = '*'
        cmd.extend(["--include", clean_pattern])

    cmd.append(query)
    cmd.append(".")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TOOL_TIMEOUT_SECONDS)
        output = stdout.decode("utf-8", errors="replace").strip()

        if not output:
            return f"🔍 未找到匹配: '{query}'"

        output = output.replace("\n./", "\n").lstrip("./")

        MAX_OUTPUT_LINES = 120
        MAX_OUTPUT_CHARS = 6000
        lines = output.split("\n")
        if len(lines) > MAX_OUTPUT_LINES:
            output = "\n".join(lines[:MAX_OUTPUT_LINES])
            output += f"\n\n... (结果过多，已截断至 {MAX_OUTPUT_LINES} 行。请使用 include_pattern 缩小范围)"
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS]
            output += f"\n\n... (输出过长，已截断至 {MAX_OUTPUT_CHARS} 字符。请缩小搜索范围或指定 include_pattern)"

        pattern_desc = f"正则 '{query}'" if is_regex else f"'{query}'"
        scope = f" (范围: {include_pattern})" if include_pattern else ""
        return f"🔍 搜索 {pattern_desc}{scope}:\n\n{output}"

    except FileNotFoundError:
        return await _python_search(query, is_regex, include_pattern, workspace)


async def _python_search(
    query: str, is_regex: bool, include_pattern: str, workspace: str,
) -> str:
    """Python 备用搜索实现 (grep 不可用时)"""
    if is_regex:
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error as e:
            return f"⚠️ 无效的正则表达式: {e}"
    else:
        pattern = None

    results: List[str] = []
    count = 0

    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in TREE_SKIP_DIRS]
        for fname in files:
            if count >= MAX_SEARCH_RESULTS:
                break
            rel_path = os.path.relpath(os.path.join(root, fname), workspace)
            if is_sensitive_file(rel_path):
                continue
            if include_pattern and not fnmatch.fnmatch(fname, include_pattern):
                continue

            try:
                with open(os.path.join(root, fname), "r", encoding="utf-8", errors="replace") as f:
                    file_lines = f.readlines()
            except Exception:
                continue

            for i, line in enumerate(file_lines):
                if count >= MAX_SEARCH_RESULTS:
                    break
                matched = bool(pattern.search(line)) if pattern else (query.lower() in line.lower())
                if matched:
                    count += 1
                    ctx_start = max(0, i - SEARCH_CONTEXT_LINES)
                    ctx_end = min(len(file_lines), i + SEARCH_CONTEXT_LINES + 1)
                    ctx = ""
                    for j in range(ctx_start, ctx_end):
                        prefix = ">" if j == i else " "
                        ctx += f"{prefix} {j+1}: {file_lines[j]}"
                    results.append(f"{rel_path}:{i+1}\n{ctx}")

    if not results:
        return f"🔍 未找到匹配: '{query}'"

    output = "\n---\n".join(results)
    truncated = f"\n\n... (已达到 {MAX_SEARCH_RESULTS} 条上限)" if count >= MAX_SEARCH_RESULTS else ""
    return f"🔍 搜索 '{query}' 找到 {count} 个匹配:\n\n{output}{truncated}"


# ==================== list_directory ====================

async def tool_list_directory(args: Dict[str, Any], workspace: str) -> str:
    """列出目录内容"""
    path = args.get("path", "")

    is_safe, abs_path, error = validate_path(workspace, path or ".")
    if not is_safe:
        return error

    if not os.path.exists(abs_path):
        return f"⚠️ 目录不存在: '{path}'"
    if not os.path.isdir(abs_path):
        return f"⚠️ '{path}' 不是目录 (请使用 read_file 读取文件)"

    try:
        entries = sorted(os.listdir(abs_path))
    except PermissionError:
        return f"⚠️ 无权访问: '{path}'"

    entries = [e for e in entries if e not in TREE_SKIP_DIRS and not e.startswith("__pycache__")]

    dirs_list = []
    files_list = []
    for entry in entries:
        full = os.path.join(abs_path, entry)
        if os.path.isdir(full):
            try:
                sub_count = len(os.listdir(full))
            except Exception:
                sub_count = 0
            dirs_list.append(f"📁 {entry}/ ({sub_count} items)")
        else:
            size = os.path.getsize(full)
            size_str = f"{size}B" if size < 1024 else f"{size / 1024:.1f}KB" if size < 1048576 else f"{size / 1048576:.1f}MB"
            files_list.append(f"📄 {entry} ({size_str})")

    display_path = path or "."
    result = f"📂 {display_path}/\n"
    result += "\n".join(dirs_list + files_list)
    if not dirs_list and not files_list:
        result += "(空目录)"
    return result


# ==================== get_file_tree ====================

async def tool_get_file_tree(args: Dict[str, Any], workspace: str) -> str:
    """获取目录树"""
    path = args.get("path", "")
    max_depth = min(args.get("max_depth", 3), MAX_TREE_DEPTH)

    is_safe, abs_path, error = validate_path(workspace, path or ".")
    if not is_safe:
        return error

    if not os.path.exists(abs_path):
        return f"⚠️ 路径不存在: '{path}'"
    if not os.path.isdir(abs_path):
        return f"⚠️ '{path}' 不是目录"

    tree = _build_tree(abs_path, max_depth)
    display_path = path or "."
    return f"🌳 {display_path}/ 目录树 (深度: {max_depth}):\n\n{tree}"


def _build_tree(path: str, max_depth: int, prefix: str = "", depth: int = 0) -> str:
    """递归构建目录树"""
    if depth >= max_depth:
        return ""

    try:
        entries = sorted(os.listdir(path))
    except PermissionError:
        return f"{prefix}(无权限访问)\n"

    entries = [e for e in entries if e not in TREE_SKIP_DIRS and not e.startswith(".")]

    lines = []
    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        full_path = os.path.join(path, entry)

        if os.path.isdir(full_path):
            lines.append(f"{prefix}{connector}{entry}/")
            extension = "    " if is_last else "│   "
            subtree = _build_tree(full_path, max_depth, prefix + extension, depth + 1)
            if subtree:
                lines.append(subtree)
        else:
            lines.append(f"{prefix}{connector}{entry}")

    return "\n".join(lines)
