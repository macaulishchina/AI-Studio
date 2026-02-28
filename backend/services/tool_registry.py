"""
设计院 (Studio) - 工具注册表与执行引擎

为 AI 讨论提供代码感知能力:
  - read_file: 读取项目文件内容
  - search_text: 全文搜索 (支持正则)
  - list_directory: 列出目录内容
  - get_file_tree: 获取项目目录树

安全沙箱:
  - 路径限制在 workspace_path 内 (防止 ../ 逃逸和符号链接逃逸)
  - 敏感文件黑名单 (.env, *.key 等)
  - 文件读取行数上限 (默认 500 行)
  - 搜索结果数量上限 (默认 50 条)
  - 只读: 不提供任何写入/删除/执行工具
"""
import asyncio
import logging
import os
import re
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ==================== 权限定义 ====================

TOOL_PERMISSIONS = {
    "ask_user",      # 向用户提问澄清
    "read_source",   # 读取源码文件
    "read_config",   # 读取配置文件
    "search",        # 全文搜索
    "tree",          # 目录浏览
    "execute_readonly_command",  # 执行只读命令 (git log/diff, ls, cat, etc.)
    "execute_command",           # 执行任意命令 (需显式授权)
}

DEFAULT_PERMISSIONS = set(TOOL_PERMISSIONS) - {"execute_command"}  # 默认不开放写命令

# ==================== 安全限制 ====================

# 敏感文件/目录黑名单
_SENSITIVE_PATTERNS = {
    # 文件名精确匹配
    ".env", ".env.local", ".env.production",
    # 目录
    ".git/objects", ".git/refs", ".git/logs",
    "venv", ".venv", "node_modules", "__pycache__",
    # 安全相关
    "id_rsa", "id_ed25519",
}

_SENSITIVE_EXTENSIONS = {
    ".key", ".pem", ".p12", ".pfx", ".jks",
    ".secret", ".credentials",
}

# 允许读取的配置文件 (即使匹配了敏感模式)
_CONFIG_ALLOWLIST = {
    "package.json", "tsconfig.json", "vite.config.ts",
    "docker-compose.yml", "Dockerfile", "nginx.conf",
    "requirements.txt", "pyproject.toml", "setup.cfg",
    "CLAUDE.md", "README.md", "TODO.md",
}

# 文件读取限制
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


# ==================== 工具定义 (OpenAI Function Calling Format) ====================

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取项目中的文件内容。支持指定起始行号来精确读取感兴趣的片段，"
                "不必每次从头读取整个文件。推荐策略：先用 search_text 定位行号，"
                "再用 start_line 跳转到目标位置读取。单次最多返回 200 行。"
                "小文件（<200行）直接一次读完，不要拆分。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于项目根目录的文件路径，例如 'backend/app/games/adventure.py'",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": (
                            "起始行号 (1-based)，默认从第 1 行开始。"
                            "配合 search_text 返回的行号，可直接跳到感兴趣的代码位置"
                        ),
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "结束行号 (1-based, inclusive)，不指定则从 start_line 开始读取最多 200 行",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_text",
            "description": (
                "在项目文件中搜索文本或正则表达式，返回匹配的文件路径、行号和上下文。"
                "这是最高效的代码定位工具——先搜索确定位置，再用 read_file 的 start_line 精确读取。"
                "务必指定 include_pattern 缩小搜索范围（如 '*.py', '*.vue'），"
                "否则结果可能过多。返回的行号可直接用于 read_file 的 start_line 参数。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索的文本或正则表达式",
                    },
                    "is_regex": {
                        "type": "boolean",
                        "description": "是否为正则表达式，默认 false (精确文本搜索)",
                        "default": False,
                    },
                    "include_pattern": {
                        "type": "string",
                        "description": (
                            "文件名 glob 过滤，如 '*.py'、'*.vue'、'*.ts'。"
                            "强烈建议始终指定，避免搜索全部文件类型"
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": (
                "列出目录下的文件和子目录。用于了解项目局部结构。"
                "建议先用 get_file_tree 获取整体概览，再用此工具查看特定目录的详细内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对于项目根目录的目录路径，例如 'backend/app/api'。空字符串表示项目根目录。",
                        "default": "",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_tree",
            "description": (
                "获取项目完整文件树（带缩进的树状结构）。"
                "适合在对话开始时调用一次，快速了解项目整体结构，"
                "再根据结构决定读取哪些文件。自动过滤 node_modules、.git 等无关目录。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "子目录路径 (相对于项目根目录)，空字符串表示整个项目",
                        "default": "",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "目录树最大深度，默认 3",
                        "default": 3,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "向用户提出需要澄清的问题。当描述模糊、有多种理解方式、"
                "或缺少关键信息时，主动调用此工具提问。可以一次提出多个问题。\n\n"
                "## 使用规范\n"
                "- 每个问题通过 type 指定 'single'(单选) 或 'multi'(多选)\n"
                "- options 数组中的选项按推荐程度从高到低排列\n"
                "- 为最推荐的 1-2 个选项设置 recommended: true\n"
                "- 单选题最后一个选项通常是'其他（请说明）'之类的自定义选项，除非是严格几选一\n"
                "- 用 context 字段简要说明为什么需要明确这个问题\n"
                "- 调用此工具后你必须停止，等待用户回答后再继续\n"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "description": "问题列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {
                                    "type": "string",
                                    "description": "问题文本",
                                },
                                "type": {
                                    "type": "string",
                                    "enum": ["single", "multi"],
                                    "description": "单选 single 或多选 multi，默认 single",
                                },
                                "options": {
                                    "type": "array",
                                    "description": "选项列表，按推荐程度从高到低排列",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {
                                                "type": "string",
                                                "description": "选项文本",
                                            },
                                            "description": {
                                                "type": "string",
                                                "description": "选项的补充说明（可选）",
                                            },
                                            "recommended": {
                                                "type": "boolean",
                                                "description": "是否为推荐选项",
                                            },
                                        },
                                        "required": ["label"],
                                    },
                                },
                                "context": {
                                    "type": "string",
                                    "description": "为什么需要明确这个问题（简要说明对需求的影响）",
                                },
                            },
                            "required": ["question"],
                        },
                    },
                },
                "required": ["questions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "在项目工作目录中执行 shell 命令。⚠️ 当用户要求你执行命令时，"
                "你必须调用此工具，禁止在文本中编造执行结果。\n\n"
                "支持常用的只读命令如 "
                "git (log, diff, show, status, blame), ls, cat, head, tail, find, "
                "grep, wc, diff, python3 -c 等。非只读命令需要额外授权。\n\n"
                "常用场景：\n"
                "- `git log --oneline -20` 查看近 20 条提交\n"
                "- `git diff origin/main...HEAD -- path/to/file` 查看单文件变更\n"
                "- `git diff --stat origin/main...HEAD` 查看变更统计\n"
                "- `git blame path/to/file` 查看文件逐行负责人\n"
                "- `find . -name '*.py' -newer some_file` 查找新修改的文件\n"
                "- `python3 -c \"import json; ...\"` 执行简单脚本\n"
                "- `docker ps` 查看运行中的容器\n"
                "- `rm file` 删除文件 (需授权)\n"
                "- `touch file` 创建文件 (需授权)\n"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令 (单行)",
                    },
                },
                "required": ["command"],
            },
        },
    },
]

# ==================== DB 工具缓存 ====================

# 从 DB 加载的工具定义缓存 (启动时 + API 更新时刷新)
_db_tool_cache: Optional[List[Dict[str, Any]]] = None
_db_perm_map_cache: Optional[Dict[str, Set[str]]] = None


async def load_tools_from_db():
    """从 DB 加载工具定义到内存缓存 (启动时调用)"""
    global _db_tool_cache, _db_perm_map_cache
    try:
        from studio.backend.core.database import async_session_maker
        from studio.backend.models import ToolDefinition
        from sqlalchemy import select

        async with async_session_maker() as db:
            result = await db.execute(
                select(ToolDefinition)
                .where(ToolDefinition.is_enabled.is_(True))
                .order_by(ToolDefinition.sort_order, ToolDefinition.id)
            )
            tools = result.scalars().all()

        _db_tool_cache = []
        _db_perm_map_cache = {}
        for t in tools:
            func_def = t.function_def or {}
            tool_name = func_def.get("name", t.name)
            _db_tool_cache.append({
                "type": "function",
                "function": func_def,
            })
            _db_perm_map_cache[tool_name] = {t.permission_key}

        logger.info(f"✅ 从 DB 加载了 {len(_db_tool_cache)} 个工具定义到缓存")
    except Exception as e:
        logger.warning(f"⚠️ 从 DB 加载工具定义失败, 使用硬编码 fallback: {e}")
        _db_tool_cache = None
        _db_perm_map_cache = None


def get_tool_definitions(permissions: Optional[Set[str]] = None) -> list:
    """
    获取当前可用的工具定义列表 (根据权限过滤)

    优先使用 DB 缓存, 回退到硬编码 TOOL_DEFINITIONS
    同时包含已启用的 MCP Server 提供的工具

    Args:
        permissions: 允许的权限集合，None 表示使用默认权限 (全部开启)

    Returns:
        OpenAI tools format 列表
    """
    perms = permissions or DEFAULT_PERMISSIONS

    # 使用 DB 缓存
    tool_defs = _db_tool_cache if _db_tool_cache is not None else TOOL_DEFINITIONS
    perm_map = _db_perm_map_cache if _db_perm_map_cache is not None else _TOOL_PERMISSION_MAP

    tools = []
    for tool_def in tool_defs:
        name = tool_def["function"]["name"]
        required_perm = perm_map.get(name)
        if required_perm and required_perm.issubset(perms):
            tools.append(tool_def)

    # 追加 MCP 工具 (如果有)
    try:
        from studio.backend.services.mcp.execution_adapter import MCPExecutionAdapter
        mcp_tools = MCPExecutionAdapter.get_mcp_tool_definitions(perms)
        tools.extend(mcp_tools)
    except Exception as e:
        logger.debug(f"MCP 工具加载跳过: {e}")

    return tools


# 工具名 → 所需权限映射
_TOOL_PERMISSION_MAP: Dict[str, Set[str]] = {
    "ask_user": {"ask_user"},
    "read_file": {"read_source"},
    "search_text": {"search"},
    "list_directory": {"tree"},
    "get_file_tree": {"tree"},
    "run_command": {"execute_readonly_command"},
}


# ==================== 路径安全检查 ====================

def _validate_path(workspace: str, rel_path: str) -> Tuple[bool, str, str]:
    """
    验证路径安全性

    Returns:
        (is_safe, absolute_path, error_message)
    """
    # 规范化路径
    rel_path = rel_path.strip().lstrip("/")

    # 阻止空路径用于文件操作
    abs_path = os.path.realpath(os.path.join(workspace, rel_path))

    # 沙箱检查: 必须在 workspace 内
    workspace_real = os.path.realpath(workspace)
    if not abs_path.startswith(workspace_real + os.sep) and abs_path != workspace_real:
        return False, abs_path, f"⚠️ 路径越界: '{rel_path}' 不在项目目录内"

    return True, abs_path, ""


def _is_sensitive_file(rel_path: str) -> bool:
    """检查文件是否在敏感黑名单中"""
    basename = os.path.basename(rel_path)
    _, ext = os.path.splitext(basename)

    # 允许列表优先
    if basename in _CONFIG_ALLOWLIST:
        return False

    # 精确文件名匹配
    if basename in _SENSITIVE_PATTERNS:
        return True

    # 扩展名匹配
    if ext.lower() in _SENSITIVE_EXTENSIONS:
        return True

    # 路径中包含敏感目录
    path_parts = rel_path.replace("\\", "/").split("/")
    for part in path_parts:
        if part in _SENSITIVE_PATTERNS:
            return True

    return False


# ==================== 工具执行器 ====================

# 类型: 命令审批回调 (command_str, tool_call_id) -> {"approved": bool, "scope": str}
CommandApprovalCallback = Optional[Any]  # asyncio coroutine

async def execute_tool(
    name: str,
    arguments: Dict[str, Any],
    workspace: str,
    permissions: Optional[Set[str]] = None,
    command_approval_fn: CommandApprovalCallback = None,
    project_id: Optional[int] = None,
    workspace_dir: Optional[str] = None,
) -> str:
    """
    执行指定工具并返回结果文本

    Args:
        name: 工具名称 (支持 mcp_{slug}__{tool} 格式的 MCP 工具)
        arguments: 工具参数
        workspace: 工作区根路径
        permissions: 允许的权限集合
        command_approval_fn: 异步回调, 用于请求用户批准写命令
                            签名: async (command: str, tool_call_id: str) -> {"approved": bool, "scope": str}
        project_id: 项目 ID (MCP 调用时用于凭据解析和审计)
        workspace_dir: 工作目录路径 (MCP 调用时用于凭据解析)

    Returns:
        工具执行结果 (纯文本)
    """
    # MCP 工具路由: 以 mcp_ 前缀判断
    from studio.backend.services.mcp.tool_adapter import is_mcp_tool
    if is_mcp_tool(name):
        from studio.backend.services.mcp.execution_adapter import MCPExecutionAdapter
        return await MCPExecutionAdapter.execute(
            tool_name=name,
            arguments=arguments,
            workspace=workspace,
            permissions=permissions,
            project_id=project_id,
            workspace_dir=workspace_dir,
            command_approval_fn=command_approval_fn,
        )

    perms = permissions or DEFAULT_PERMISSIONS

    # 权限检查
    required_perm = _TOOL_PERMISSION_MAP.get(name)
    if required_perm and not required_perm.issubset(perms):
        return f"⚠️ 工具 '{name}' 已被项目管理员禁用"

    # run_command 特殊处理: 非只读命令需要 execute_command 权限 + 用户审批
    if name == "run_command":
        command = arguments.get("command", "")
        if not _is_readonly_command(command):
            if "execute_command" not in perms:
                # 写命令权限未开启 — 完全阻止
                return (
                    f"⚠️ 此命令不在只读白名单中，且项目未开启「执行写入命令」权限。\n"
                    f"命令: {command}\n\n"
                    f"只读命令示例: git log, git diff, ls, cat, grep, find, python3 -c 等\n"
                    f"如需执行此命令，请让用户在工具面板中开启「⚠️ 执行写入命令」权限。"
                )
            # 写命令权限已开启 — 仍需通过审批流程
            if command_approval_fn:
                # 请求用户实时审批 (回调内部处理 session/project 级缓存)
                approval = await command_approval_fn(command, "")
                if approval.get("approved"):
                    try:
                        result = await asyncio.wait_for(
                            _tool_run_command_unrestricted(arguments, workspace),
                            timeout=COMMAND_TIMEOUT_SECONDS * 2,
                        )
                        scope_label = {"once": "本次", "session": "本会话", "project": "本项目", "permanent": "永久", "rule": "规则匹配"}.get(approval.get("scope", ""), "")
                        if scope_label:
                            return f"✅ 用户已授权执行 ({scope_label})\n\n{result}"
                        return result
                    except asyncio.TimeoutError:
                        return f"⚠️ 命令执行超时"
                    except Exception as e:
                        logger.exception(f"命令执行失败")
                        return f"⚠️ 命令执行失败: {str(e)}"
                else:
                    reason = approval.get("reason", "用户拒绝")
                    return (
                        f"⚠️ 用户拒绝执行此命令。\n"
                        f"命令: {command}\n"
                        f"原因: {reason}\n\n"
                        f"请改用只读命令获取信息，或向用户解释为什么需要执行此命令后再次尝试。"
                    )
            else:
                # 无审批回调 (非任务上下文, 如直接 API 调用) — 直接执行
                try:
                    result = await asyncio.wait_for(
                        _tool_run_command_unrestricted(arguments, workspace),
                        timeout=COMMAND_TIMEOUT_SECONDS * 2,
                    )
                    return result
                except asyncio.TimeoutError:
                    return f"⚠️ 命令执行超时"
                except Exception as e:
                    logger.exception(f"命令执行失败")
                    return f"⚠️ 命令执行失败: {str(e)}"

    # 执行工具 (带超时)
    executor = _TOOL_EXECUTORS.get(name)
    if not executor:
        return f"⚠️ 未知工具: '{name}'"

    timeout = COMMAND_TIMEOUT_SECONDS if name == "run_command" else TOOL_TIMEOUT_SECONDS
    try:
        result = await asyncio.wait_for(
            executor(arguments, workspace),
            timeout=timeout,
        )
        return result
    except asyncio.TimeoutError:
        return f"⚠️ 工具 '{name}' 执行超时 ({timeout}s)"
    except Exception as e:
        logger.exception(f"工具 {name} 执行失败")
        return f"⚠️ 工具执行失败: {str(e)}"


# ==================== 具体工具实现 ====================

async def _tool_read_file(args: Dict[str, Any], workspace: str) -> str:
    """读取文件内容"""
    path = args.get("path", "")
    start_line = args.get("start_line", 1)
    end_line = args.get("end_line")

    if not path:
        return "⚠️ 请指定文件路径"

    # 路径安全检查
    is_safe, abs_path, error = _validate_path(workspace, path)
    if not is_safe:
        return error

    # 敏感文件检查
    if _is_sensitive_file(path):
        return f"⚠️ 无法读取敏感文件: '{path}'"

    if not os.path.exists(abs_path):
        return f"⚠️ 文件不存在: '{path}'"

    if not os.path.isfile(abs_path):
        return f"⚠️ '{path}' 不是文件 (可能是目录，请使用 list_directory)"

    # 检查文件大小 (跳过过大的二进制文件)
    file_size = os.path.getsize(abs_path)
    if file_size > 1024 * 1024:  # 1MB
        return f"⚠️ 文件过大 ({file_size / 1024:.0f}KB)，请指定行范围读取"

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        return f"⚠️ '{path}' 是二进制文件，无法读取"

    total_lines = len(lines)

    # 处理行范围
    start = max(1, start_line or 1)
    end = min(total_lines, end_line or (start + MAX_READ_LINES - 1))

    # 最多读取 MAX_READ_LINES 行
    if end - start + 1 > MAX_READ_LINES:
        end = start + MAX_READ_LINES - 1

    selected = lines[start - 1:end]
    content = "".join(selected)

    # 构建结果头信息
    header = f"📄 {path} (行 {start}-{end}, 共 {total_lines} 行)"
    if end < total_lines:
        header += f" [截断: 使用 start_line/end_line 查看更多]"

    return f"{header}\n```\n{content}```"


async def _tool_search_text(args: Dict[str, Any], workspace: str) -> str:
    """全文搜索"""
    query = args.get("query", "")
    is_regex = args.get("is_regex", False)
    include_pattern = args.get("include_pattern", "")

    if not query:
        return "⚠️ 请指定搜索内容"

    # 构建 grep 命令
    cmd = ["grep", "-rn", "--color=never"]

    if is_regex:
        cmd.append("-E")
    else:
        cmd.append("-F")

    # 上下文行
    cmd.extend(["-B", str(SEARCH_CONTEXT_LINES), "-A", str(SEARCH_CONTEXT_LINES)])

    # 最大结果数
    cmd.extend(["-m", str(MAX_SEARCH_RESULTS)])

    # 排除目录
    for skip_dir in TREE_SKIP_DIRS:
        cmd.extend(["--exclude-dir", skip_dir])

    # 排除敏感文件
    for ext in _SENSITIVE_EXTENSIONS:
        cmd.extend(["--exclude", f"*{ext}"])
    cmd.extend(["--exclude", ".env*"])

    # 包含模式 (修正: grep --include 不支持 ** 或路径, 只支持文件名 glob)
    if include_pattern:
        # 去掉路径前缀 (如 **/*.py → *.py, src/**/*.ts → *.ts)
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
            *cmd,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TOOL_TIMEOUT_SECONDS)
        output = stdout.decode("utf-8", errors="replace").strip()

        if not output:
            return f"🔍 未找到匹配: '{query}'"

        # 清理路径 (去掉 ./ 前缀)
        output = output.replace("\n./", "\n").lstrip("./")

        # 限制输出长度 (行数 + 字符数双重限制)
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
        # grep 不可用，退回到 Python 实现
        return await _python_search(query, is_regex, include_pattern, workspace)


async def _python_search(
    query: str, is_regex: bool, include_pattern: str, workspace: str,
) -> str:
    """Python 备用搜索实现 (grep 不可用时)"""
    import fnmatch

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
        # 跳过排除目录
        dirs[:] = [d for d in dirs if d not in TREE_SKIP_DIRS]

        for fname in files:
            if count >= MAX_SEARCH_RESULTS:
                break

            # 敏感文件检查
            rel_path = os.path.relpath(os.path.join(root, fname), workspace)
            if _is_sensitive_file(rel_path):
                continue

            # include_pattern 过滤
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

                matched = False
                if pattern:
                    matched = bool(pattern.search(line))
                else:
                    matched = query.lower() in line.lower()

                if matched:
                    count += 1
                    line_num = i + 1
                    # 上下文
                    ctx_start = max(0, i - SEARCH_CONTEXT_LINES)
                    ctx_end = min(len(file_lines), i + SEARCH_CONTEXT_LINES + 1)
                    ctx = ""
                    for j in range(ctx_start, ctx_end):
                        prefix = ">" if j == i else " "
                        ctx += f"{prefix} {j+1}: {file_lines[j]}"
                    results.append(f"{rel_path}:{line_num}\n{ctx}")

    if not results:
        return f"🔍 未找到匹配: '{query}'"

    output = "\n---\n".join(results)
    truncated = f"\n\n... (已达到 {MAX_SEARCH_RESULTS} 条上限)" if count >= MAX_SEARCH_RESULTS else ""
    return f"🔍 搜索 '{query}' 找到 {count} 个匹配:\n\n{output}{truncated}"


async def _tool_list_directory(args: Dict[str, Any], workspace: str) -> str:
    """列出目录内容"""
    path = args.get("path", "")

    # 路径安全检查
    is_safe, abs_path, error = _validate_path(workspace, path or ".")
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

    # 过滤隐藏和忽略的目录
    entries = [e for e in entries if e not in TREE_SKIP_DIRS and not e.startswith("__pycache__")]

    dirs = []
    files = []
    for entry in entries:
        full = os.path.join(abs_path, entry)
        if os.path.isdir(full):
            # 计算子项数量
            try:
                sub_count = len(os.listdir(full))
            except Exception:
                sub_count = 0
            dirs.append(f"📁 {entry}/ ({sub_count} items)")
        else:
            size = os.path.getsize(full)
            size_str = f"{size}B" if size < 1024 else f"{size / 1024:.1f}KB" if size < 1048576 else f"{size / 1048576:.1f}MB"
            files.append(f"📄 {entry} ({size_str})")

    display_path = path or "."
    result = f"📂 {display_path}/\n"
    result += "\n".join(dirs + files)

    if not dirs and not files:
        result += "(空目录)"

    return result


async def _tool_get_file_tree(args: Dict[str, Any], workspace: str) -> str:
    """获取目录树"""
    path = args.get("path", "")
    max_depth = min(args.get("max_depth", 3), MAX_TREE_DEPTH)

    # 路径安全检查
    target = os.path.join(workspace, path) if path else workspace
    is_safe, abs_path, error = _validate_path(workspace, path or ".")
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

    # 过滤
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


async def _tool_ask_user(args: Dict[str, Any], workspace: str) -> str:
    """向用户提出需求澄清问题 (结果直接透传给前端渲染)"""
    questions = args.get("questions", [])
    if not questions:
        return "⚠️ 请至少提出一个问题"
    count = len(questions)
    return f"✅ 已向用户展示 {count} 个问题，请等待用户回答后再继续讨论。不要自行假设答案。"


# ==================== 命令执行工具 ====================

# 只读命令白名单: {命令: 允许的子命令集合 (None=全部允许)}
_READONLY_COMMANDS = {
    "git": {"log", "diff", "show", "status", "branch", "tag", "describe",
            "rev-parse", "ls-files", "blame", "shortlog", "remote", "stash"},
    "ls": None, "cat": None, "head": None, "tail": None,
    "find": None, "grep": None, "wc": None, "file": None,
    "diff": None, "pwd": None, "echo": None, "which": None,
    "du": None, "stat": None, "realpath": None, "dirname": None,
    "basename": None, "env": None, "uname": None, "whoami": None,
    "date": None, "tree": None, "less": None, "more": None,
    "sort": None, "uniq": None, "awk": None, "sed": None,
    "cut": None, "tr": None, "xargs": None,
    "python3": {"-c", "--version", "-V"},
    "python": {"-c", "--version", "-V"},
    "node": {"-e", "--version", "-v"},
    "docker": {"ps", "images", "logs", "inspect", "stats", "top", "version", "info"},
    "docker-compose": {"ps", "logs", "config", "images"},
}

# Shell 写操作符 — 出现在命令中则视为非只读
import re as _re
_WRITE_OPERATORS_PATTERN = _re.compile(
    r'(?:^|\s)'
    r'(?:'
    r'>|>>|'           # 输出重定向
    r'\|\s*tee\b|'     # tee 写文件
    r'&&|;'            # 链式命令 (可能后接写命令)
    r')'
)

COMMAND_TIMEOUT_SECONDS = 30


def _is_readonly_command(command_str: str) -> bool:
    """检查命令是否为只读命令

    检查层级:
    1. 全局写操作符检测: >, >>, &&, ;, |tee 等
    2. 管道链: 每个子命令都必须在白名单中
    3. 白名单匹配: 命令 + 子命令检查
    """
    stripped = command_str.strip()
    if not stripped:
        return False

    # 1) 检测写操作符 (>, >>, &&, ;, tee)
    # 允许管道 | 但不允许 | tee
    if _re.search(r'>{1,2}', stripped):  # > or >>
        return False
    if '&&' in stripped or ';' in stripped:
        return False
    if _re.search(r'\|\s*tee\b', stripped):
        return False
    # 检测反引号/子 shell 执行
    if '`' in stripped or '$(' in stripped:
        return False

    # 2) 管道链: 每个子命令都必须在白名单中
    pipe_segments = [s.strip() for s in stripped.split('|') if s.strip()]
    for seg in pipe_segments:
        parts = seg.split()
        if not parts:
            return False
        cmd = os.path.basename(parts[0])

        allowed_subs = _READONLY_COMMANDS.get(cmd)
        if allowed_subs is None and cmd in _READONLY_COMMANDS:
            continue  # 该命令任何参数都允许
        if allowed_subs is not None:
            if len(parts) >= 2 and parts[1] in allowed_subs:
                continue
            elif len(parts) < 2:
                continue  # 无参数, 视为安全
            else:
                return False  # 子命令不在允许列表
        else:
            return False  # 命令不在白名单

    return True


async def _tool_run_command(args: Dict[str, Any], workspace: str) -> str:
    """执行 shell 命令"""
    command = args.get("command", "").strip()
    if not command:
        return "⚠️ 请指定要执行的命令"

    # 安全检查: 阻止危险模式
    dangerous_patterns = ["rm -rf /", "mkfs", "dd if=", "> /dev/", ":(){ :|:& };:", "shutdown", "reboot"]
    for pattern in dangerous_patterns:
        if pattern in command:
            return f"⚠️ 命令包含危险模式: '{pattern}'，已阻止执行"

    # 管道/链式命令: 检查每个子命令
    # 注: 简化检查，只检查第一个命令的只读性
    is_readonly = _is_readonly_command(command)

    if not is_readonly:
        # 非只读命令提示
        return (
            f"⚠️ 此命令不在只读白名单中，需要 '执行任意命令' 权限。\n"
            f"命令: {command}\n\n"
            f"只读命令示例: git log, git diff, ls, cat, grep, find, python3 -c 等\n"
            f"如需执行此命令，请让项目管理员开启 'execute_command' 权限。"
        )

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=COMMAND_TIMEOUT_SECONDS
        )
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()

        # 限制输出长度
        MAX_CMD_OUTPUT = 8000
        if len(out) > MAX_CMD_OUTPUT:
            out = out[:MAX_CMD_OUTPUT] + f"\n\n... (输出已截断至 {MAX_CMD_OUTPUT} 字符)"

        result = f"$ {command}\n"
        if out:
            result += f"\n{out}"
        if err:
            result += f"\n(stderr) {err}"
        if proc.returncode != 0:
            result += f"\n(exit code: {proc.returncode})"

        return result

    except asyncio.TimeoutError:
        return f"⚠️ 命令执行超时 ({COMMAND_TIMEOUT_SECONDS}s): {command}"
    except Exception as e:
        return f"⚠️ 命令执行失败: {str(e)}"


async def _tool_run_command_unrestricted(args: Dict[str, Any], workspace: str) -> str:
    """执行任意命令 (需要 execute_command 权限)"""
    command = args.get("command", "").strip()
    if not command:
        return "⚠️ 请指定要执行的命令"

    # 仍然阻止极端危险的命令
    lethal = ["rm -rf /", "mkfs", "> /dev/", ":(){ :|:& };:", "shutdown", "reboot"]
    for pattern in lethal:
        if pattern in command:
            return f"⚠️ 命令包含极端危险模式: '{pattern}'，已阻止执行"

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=COMMAND_TIMEOUT_SECONDS * 2  # 写命令给更多时间
        )
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()

        MAX_CMD_OUTPUT = 8000
        if len(out) > MAX_CMD_OUTPUT:
            out = out[:MAX_CMD_OUTPUT] + f"\n\n... (输出已截断至 {MAX_CMD_OUTPUT} 字符)"

        result = f"$ {command}\n"
        if out:
            result += f"\n{out}"
        if err:
            result += f"\n(stderr) {err}"
        if proc.returncode != 0:
            result += f"\n(exit code: {proc.returncode})"

        return result

    except asyncio.TimeoutError:
        return f"⚠️ 命令执行超时 ({COMMAND_TIMEOUT_SECONDS * 2}s): {command}"
    except Exception as e:
        return f"⚠️ 命令执行失败: {str(e)}"


# 工具执行器映射
_TOOL_EXECUTORS: Dict[str, Callable] = {
    "read_file": _tool_read_file,
    "search_text": _tool_search_text,
    "list_directory": _tool_list_directory,
    "get_file_tree": _tool_get_file_tree,
    "ask_user": _tool_ask_user,
    "run_command": _tool_run_command,
}
