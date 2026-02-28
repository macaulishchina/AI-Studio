"""
MCP Permission Bridge — 权限桥接

将 MCP 工具权限映射到 Studio 现有的 Project.tool_permissions 体系:
  - 每个 MCP Server 有一个总权限键: mcp_{slug} (如 mcp_github)
  - 每个 MCP 工具有独立权限键: mcp_{slug}_{tool_name} (如 mcp_github_create_issue)
  - 项目的 tool_permissions 列表中包含这些键即为授权

权限层级:
  1. mcp_{slug} — 整个 MCP Server 的开关 (必须)
  2. mcp_{slug}_{tool_name} — 单工具精细控制 (可选, 默认跟随 server 开关)
  3. command_auth 规则 — 高危操作二次确认 (如 merge_pull_request)
"""
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


def get_mcp_server_permission_key(server_slug: str) -> str:
    """生成 MCP Server 总权限键"""
    return f"mcp_{server_slug}"


def get_mcp_tool_permission_key(server_slug: str, tool_name: str) -> str:
    """生成 MCP 工具精细权限键"""
    return f"mcp_{server_slug}_{tool_name}"


def check_mcp_permission(
    server_slug: str,
    tool_name: str,
    project_permissions: List[str],
    server_permission_map: Optional[Dict[str, str]] = None,
) -> bool:
    """检查项目是否授权使用指定 MCP 工具

    权限检查逻辑:
      1. 项目的 tool_permissions 必须包含 mcp_{slug} (server 级开关)
      2. 如果 server 有自定义 permission_map, 检查映射后的权限键
      3. 否则默认允许 (只要 server 级开关打开)

    Args:
        server_slug: MCP Server 标识
        tool_name: MCP 工具名
        project_permissions: 项目的 tool_permissions 列表
        server_permission_map: Server 配置的权限映射 (tool_name → permission_key)

    Returns:
        True = 允许调用
    """
    perms_set = set(project_permissions)

    # 检查 server 级开关
    server_key = get_mcp_server_permission_key(server_slug)
    if server_key not in perms_set:
        return False

    # 检查自定义权限映射
    if server_permission_map:
        mapped_key = server_permission_map.get(tool_name)
        if mapped_key and mapped_key not in perms_set:
            return False

    # 检查工具级精细权限 (如果存在)
    tool_key = get_mcp_tool_permission_key(server_slug, tool_name)
    # 工具级权限是可选的 — 只有当它被显式列入 deny list 时才拒绝
    # 这里采用"默认允许"策略: server 开关开 + 无精细拒绝 = 允许
    # 未来可扩展为 deny list 机制

    return True


def get_available_mcp_permission_keys(
    server_slug: str,
    discovered_tools: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """生成某个 MCP Server 的所有权限键定义 (供前端展示)

    Returns:
        [{"key": "mcp_github", "label": "GitHub MCP", "type": "server"},
         {"key": "mcp_github_create_issue", "label": "创建 Issue", "type": "tool"}, ...]
    """
    result = [
        {
            "key": get_mcp_server_permission_key(server_slug),
            "label": f"MCP: {server_slug}",
            "type": "server",
        }
    ]

    for tool in discovered_tools:
        tool_name = tool.get("name", "")
        if tool_name:
            result.append({
                "key": get_mcp_tool_permission_key(server_slug, tool_name),
                "label": tool.get("description", tool_name)[:50],
                "type": "tool",
            })

    return result


# ==================== 危险操作检测 ====================

# MCP 工具中被视为"写操作"的模式 (需要额外确认)
_WRITE_TOOL_PATTERNS = {
    "create", "update", "delete", "merge", "close", "assign",
    "push", "write", "edit", "remove", "add_comment",
}


def is_write_operation(tool_name: str) -> bool:
    """检测 MCP 工具是否为写操作"""
    name_lower = tool_name.lower()
    return any(pattern in name_lower for pattern in _WRITE_TOOL_PATTERNS)
