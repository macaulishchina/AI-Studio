"""
统一工具注册表

ToolRegistry 管理所有工具源 (builtin / DB / MCP):
  - 注册/发现/加载工具定义
  - 权限过滤
  - 输出 OpenAI Function Calling 格式

单例模式，通过 get_tool_registry() 获取。
"""
import logging
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ==================== 权限定义 ====================

TOOL_PERMISSIONS = {
    "ask_user",
    "read_source",
    "read_config",
    "search",
    "tree",
    "execute_readonly_command",
    "execute_command",
}

DEFAULT_PERMISSIONS = set(TOOL_PERMISSIONS) - {"execute_command"}

# 工具名 → 所需权限映射 (builtin tools)
TOOL_PERMISSION_MAP: Dict[str, Set[str]] = {
    "ask_user": {"ask_user"},
    "read_file": {"read_source"},
    "search_text": {"search"},
    "list_directory": {"tree"},
    "get_file_tree": {"tree"},
    "run_command": {"execute_readonly_command"},
}


# ==================== 工具定义 (OpenAI Function Calling Format) ====================

BUILTIN_TOOL_DEFINITIONS: List[Dict[str, Any]] = [
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
                        "description": "起始行号 (1-based)，默认从第 1 行开始。配合 search_text 返回的行号，可直接跳到感兴趣的代码位置",
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
                        "description": "文件名 glob 过滤，如 '*.py'、'*.vue'、'*.ts'。强烈建议始终指定，避免搜索全部文件类型",
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
                                "question": {"type": "string", "description": "问题文本"},
                                "type": {"type": "string", "enum": ["single", "multi"], "description": "单选/多选"},
                                "options": {
                                    "type": "array",
                                    "description": "选项列表",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {"type": "string", "description": "选项文本"},
                                            "description": {"type": "string", "description": "补充说明"},
                                            "recommended": {"type": "boolean", "description": "是否推荐"},
                                        },
                                        "required": ["label"],
                                    },
                                },
                                "context": {"type": "string", "description": "为什么需要明确这个问题"},
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

    优先使用 DB 缓存, 回退到硬编码 BUILTIN_TOOL_DEFINITIONS
    同时包含已启用的 MCP Server 提供的工具
    """
    perms = permissions or DEFAULT_PERMISSIONS

    tool_defs = _db_tool_cache if _db_tool_cache is not None else BUILTIN_TOOL_DEFINITIONS
    perm_map = _db_perm_map_cache if _db_perm_map_cache is not None else TOOL_PERMISSION_MAP

    tools = []
    for tool_def in tool_defs:
        name = tool_def["function"]["name"]
        required_perm = perm_map.get(name)
        if required_perm and required_perm.issubset(perms):
            tools.append(tool_def)

    # 追加 MCP 工具
    try:
        from studio.backend.services.mcp.execution_adapter import MCPExecutionAdapter
        mcp_tools = MCPExecutionAdapter.get_mcp_tool_definitions(perms)
        tools.extend(mcp_tools)
    except Exception as e:
        logger.debug(f"MCP 工具加载跳过: {e}")

    return tools
