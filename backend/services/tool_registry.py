"""
设计院 (Studio) - 工具注册表与执行引擎 (兼容层)

实际实现已迁至 backend/ai/tools/:
  - backend/ai/tools/registry.py      → 工具注册/发现/定义
  - backend/ai/tools/executor.py       → 工具执行/调度/权限
  - backend/ai/tools/builtin/          → 内置工具实现
  - backend/ai/tools/builtin/file_ops.py  → read_file, search_text, list_directory, get_file_tree
  - backend/ai/tools/builtin/commands.py  → run_command, 只读检测
  - backend/ai/tools/builtin/interaction.py → ask_user

本模块保留所有原有导出名以向后兼容。
"""

# === Re-exports from new modules ===

from studio.backend.ai.tools.registry import (
    TOOL_PERMISSIONS,
    DEFAULT_PERMISSIONS,
    TOOL_PERMISSION_MAP as _TOOL_PERMISSION_MAP,
    BUILTIN_TOOL_DEFINITIONS as TOOL_DEFINITIONS,
    load_tools_from_db,
    get_tool_definitions,
)

from studio.backend.ai.tools.executor import (
    execute_tool,
    execute_parallel,
    CommandApprovalCallback,
)

from studio.backend.ai.tools.builtin.file_ops import (
    validate_path as _validate_path,
    is_sensitive_file as _is_sensitive_file,
    MAX_READ_LINES,
    MAX_SEARCH_RESULTS,
    SEARCH_CONTEXT_LINES,
    TOOL_TIMEOUT_SECONDS,
    MAX_TREE_DEPTH,
    TREE_SKIP_DIRS,
    SENSITIVE_PATTERNS as _SENSITIVE_PATTERNS,
    SENSITIVE_EXTENSIONS as _SENSITIVE_EXTENSIONS,
    CONFIG_ALLOWLIST as _CONFIG_ALLOWLIST,
)

from studio.backend.ai.tools.builtin.commands import (
    is_readonly_command as _is_readonly_command,
    COMMAND_TIMEOUT_SECONDS,
    READONLY_COMMANDS as _READONLY_COMMANDS,
)

__all__ = [
    "TOOL_PERMISSIONS",
    "DEFAULT_PERMISSIONS",
    "TOOL_DEFINITIONS",
    "load_tools_from_db",
    "get_tool_definitions",
    "execute_tool",
    "execute_parallel",
    "CommandApprovalCallback",
    "MAX_READ_LINES",
    "MAX_SEARCH_RESULTS",
    "SEARCH_CONTEXT_LINES",
    "TOOL_TIMEOUT_SECONDS",
    "COMMAND_TIMEOUT_SECONDS",
    "MAX_TREE_DEPTH",
    "TREE_SKIP_DIRS",
]
