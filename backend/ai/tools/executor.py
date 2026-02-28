"""
工具执行引擎

统一的工具调度与执行:
  - 权限检查
  - 路径沙箱
  - MCP 路由
  - 命令审批流
  - 超时控制
  - 并行执行支持
"""
import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Set

from .registry import TOOL_PERMISSION_MAP, DEFAULT_PERMISSIONS
from .builtin.file_ops import (
    tool_read_file,
    tool_search_text,
    tool_list_directory,
    tool_get_file_tree,
    TOOL_TIMEOUT_SECONDS,
)
from .builtin.commands import (
    tool_run_command,
    tool_run_command_unrestricted,
    is_readonly_command,
    COMMAND_TIMEOUT_SECONDS,
)
from .builtin.interaction import tool_ask_user

logger = logging.getLogger(__name__)

# 类型: 命令审批回调
CommandApprovalCallback = Optional[Any]

# 内置工具执行器映射
_TOOL_EXECUTORS: Dict[str, Callable] = {
    "read_file": tool_read_file,
    "search_text": tool_search_text,
    "list_directory": tool_list_directory,
    "get_file_tree": tool_get_file_tree,
    "ask_user": tool_ask_user,
    "run_command": tool_run_command,
}


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
        project_id: 项目 ID (MCP 调用时使用)
        workspace_dir: 工作目录路径 (MCP 调用时使用)

    Returns:
        工具执行结果 (纯文本)
    """
    # MCP 工具路由
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
    required_perm = TOOL_PERMISSION_MAP.get(name)
    if required_perm and not required_perm.issubset(perms):
        return f"⚠️ 工具 '{name}' 已被项目管理员禁用"

    # run_command 特殊处理
    if name == "run_command":
        return await _handle_run_command(arguments, workspace, perms, command_approval_fn)

    # 通用执行
    executor = _TOOL_EXECUTORS.get(name)
    if not executor:
        return f"⚠️ 未知工具: '{name}'"

    timeout = COMMAND_TIMEOUT_SECONDS if name == "run_command" else TOOL_TIMEOUT_SECONDS
    try:
        result = await asyncio.wait_for(executor(arguments, workspace), timeout=timeout)
        return result
    except asyncio.TimeoutError:
        return f"⚠️ 工具 '{name}' 执行超时 ({timeout}s)"
    except Exception as e:
        logger.exception(f"工具 {name} 执行失败")
        return f"⚠️ 工具执行失败: {str(e)}"


async def _handle_run_command(
    arguments: Dict[str, Any],
    workspace: str,
    perms: Set[str],
    command_approval_fn: CommandApprovalCallback,
) -> str:
    """run_command 路由: 只读 vs 写命令 (审批流)"""
    command = arguments.get("command", "")

    if is_readonly_command(command):
        # 只读命令: 直接走白名单执行器
        try:
            result = await asyncio.wait_for(
                tool_run_command(arguments, workspace),
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
            return result
        except asyncio.TimeoutError:
            return f"⚠️ 命令执行超时 ({COMMAND_TIMEOUT_SECONDS}s)"
        except Exception as e:
            logger.exception("命令执行失败")
            return f"⚠️ 命令执行失败: {str(e)}"

    # 非只读命令
    if "execute_command" not in perms:
        return (
            f"⚠️ 此命令不在只读白名单中，且项目未开启「执行写入命令」权限。\n"
            f"命令: {command}\n\n"
            f"只读命令示例: git log, git diff, ls, cat, grep, find, python3 -c 等\n"
            f"如需执行此命令，请让用户在工具面板中开启「⚠️ 执行写入命令」权限。"
        )

    # 需要审批
    if command_approval_fn:
        approval = await command_approval_fn(command, "")
        if approval.get("approved"):
            try:
                result = await asyncio.wait_for(
                    tool_run_command_unrestricted(arguments, workspace),
                    timeout=COMMAND_TIMEOUT_SECONDS * 2,
                )
                scope_label = {
                    "once": "本次", "session": "本会话",
                    "project": "本项目", "permanent": "永久", "rule": "规则匹配",
                }.get(approval.get("scope", ""), "")
                if scope_label:
                    return f"✅ 用户已授权执行 ({scope_label})\n\n{result}"
                return result
            except asyncio.TimeoutError:
                return f"⚠️ 命令执行超时"
            except Exception as e:
                logger.exception("命令执行失败")
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
        # 无审批回调 — 直接执行
        try:
            result = await asyncio.wait_for(
                tool_run_command_unrestricted(arguments, workspace),
                timeout=COMMAND_TIMEOUT_SECONDS * 2,
            )
            return result
        except asyncio.TimeoutError:
            return f"⚠️ 命令执行超时"
        except Exception as e:
            logger.exception("命令执行失败")
            return f"⚠️ 命令执行失败: {str(e)}"


async def execute_parallel(
    calls: List[Dict[str, Any]],
    workspace: str,
    permissions: Optional[Set[str]] = None,
    command_approval_fn: CommandApprovalCallback = None,
    project_id: Optional[int] = None,
    workspace_dir: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    并行执行多个工具调用

    Args:
        calls: [{"name": str, "arguments": dict, "id": str}, ...]

    Returns:
        [{"id": str, "name": str, "result": str, "duration_ms": int}, ...]
    """
    import time

    async def _exec_one(call: Dict[str, Any]) -> Dict[str, Any]:
        start = time.monotonic()
        try:
            result = await execute_tool(
                call["name"], call["arguments"], workspace,
                permissions, command_approval_fn, project_id, workspace_dir,
            )
        except Exception as e:
            result = f"⚠️ 工具执行失败: {str(e)}"
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            "id": call.get("id", ""),
            "name": call["name"],
            "result": result,
            "duration_ms": duration_ms,
        }

    results = await asyncio.gather(*[_exec_one(c) for c in calls])
    return list(results)
