"""
MCP Execution Adapter — 统一执行入口

这是 MCP 框架的核心编排组件:
  1. 判断工具是 MCP 工具还是本地内置工具
  2. MCP 工具: 解析凭据 → 连接 server → 权限检查 → 限流 → 调用 → 审计
  3. 支持 fallback: MCP 失败/不可用时回退到现有本地服务
  4. 与现有 tool_registry.execute_tool() 兼容, 作为包装层

调用链:
  ai_service.chat_stream() → tool_executor → MCPExecutionAdapter.execute()
    ├─ 本地工具 → tool_registry.execute_tool()
    └─ MCP 工具 → SecretResolver → ClientManager → tools/call → AuditLog
         └─ fallback → github_service.* (仅 GitHub 相关)
"""
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from studio.backend.services.mcp.tool_adapter import (
    is_mcp_tool,
    parse_studio_tool_name,
    mcp_result_to_text,
    openai_args_to_mcp,
    mcp_tools_to_openai,
)
from studio.backend.services.mcp.registry import MCPServerRegistry
from studio.backend.services.mcp.client_manager import MCPClientManager
from studio.backend.services.mcp.permission_bridge import check_mcp_permission
from studio.backend.services.mcp.secret_resolver import resolve_env_for_server
from studio.backend.services.mcp.audit import log_mcp_call, check_rate_limit

logger = logging.getLogger(__name__)


class MCPExecutionAdapter:
    """MCP 执行适配器 — 工具调用的统一入口"""

    @staticmethod
    async def execute(
        tool_name: str,
        arguments: Dict[str, Any],
        workspace: str,
        permissions: Optional[Set[str]] = None,
        project_id: Optional[int] = None,
        workspace_dir: Optional[str] = None,
        command_approval_fn=None,
    ) -> str:
        """执行工具 (自动路由 MCP / 本地)

        Args:
            tool_name: 工具名 (mcp_github__create_issue 或 read_file)
            arguments: 工具参数
            workspace: 工作区路径
            permissions: 项目权限集合
            project_id: 项目 ID (用于凭据解析和审计)
            workspace_dir: 工作目录路径 (用于凭据解析)
            command_approval_fn: 命令审批回调

        Returns:
            工具执行结果 (纯文本)
        """
        # 判断是否为 MCP 工具
        parsed = parse_studio_tool_name(tool_name)
        if parsed is not None:
            server_slug, mcp_tool_name = parsed
            return await MCPExecutionAdapter._execute_mcp(
                server_slug=server_slug,
                mcp_tool_name=mcp_tool_name,
                arguments=arguments,
                permissions=permissions,
                project_id=project_id,
                workspace_dir=workspace_dir,
            )
        else:
            # 本地内置工具 → 委托给 tool_registry
            from studio.backend.services.tool_registry import execute_tool
            return await execute_tool(
                name=tool_name,
                arguments=arguments,
                workspace=workspace,
                permissions=permissions,
                command_approval_fn=command_approval_fn,
            )

    @staticmethod
    async def _execute_mcp(
        server_slug: str,
        mcp_tool_name: str,
        arguments: Dict[str, Any],
        permissions: Optional[Set[str]] = None,
        project_id: Optional[int] = None,
        workspace_dir: Optional[str] = None,
    ) -> str:
        """执行 MCP 工具调用"""
        start_time = time.monotonic()

        # 1. 获取 server 配置
        registry = MCPServerRegistry.get_instance()
        server_config = registry.get_server(server_slug)
        if not server_config:
            return f"⚠️ MCP 服务 '{server_slug}' 未注册"
        if not server_config.enabled:
            return f"⚠️ MCP 服务 '{server_slug}' 已禁用"

        # 2. 权限检查
        if permissions is not None:
            perm_list = list(permissions)
            allowed = check_mcp_permission(
                server_slug, mcp_tool_name, perm_list,
                server_config.permission_map,
            )
            if not allowed:
                return (
                    f"⚠️ 项目未授权使用 MCP 工具 '{mcp_tool_name}' (服务: {server_slug})。\n"
                    f"请在项目设置中启用 'mcp_{server_slug}' 权限。"
                )

        # 3. 限流检查
        if not check_rate_limit(server_slug, project_id):
            return f"⚠️ MCP 服务 '{server_slug}' 调用频率超限, 请稍后重试"

        # 4. 解析凭据
        try:
            env_override = await resolve_env_for_server(
                server_slug,
                server_config.env,
                workspace_dir=workspace_dir,
                project_id=project_id,
            )
        except Exception as e:
            logger.error(f"MCP 凭据解析失败 ({server_slug}): {e}")
            return f"⚠️ MCP 凭据解析失败: {e}"

        # 5. 获取/建立连接
        client_manager = MCPClientManager.get_instance()
        conn = await client_manager.get_or_connect(server_config, env_override)
        if not conn:
            error_msg = f"MCP 服务 '{server_slug}' 连接失败"
            duration_ms = int((time.monotonic() - start_time) * 1000)
            await log_mcp_call(
                server_slug, mcp_tool_name, arguments, "",
                duration_ms, False, project_id, error_msg,
            )
            # 尝试 fallback
            fallback_result = await MCPExecutionAdapter._try_fallback(
                server_slug, mcp_tool_name, arguments,
                workspace_dir=workspace_dir, project_id=project_id,
            )
            if fallback_result is not None:
                return f"⚠️ MCP 不可用, 使用本地服务:\n{fallback_result}"
            return f"⚠️ {error_msg}"

        # 6. 执行 MCP 工具调用
        try:
            mcp_args = openai_args_to_mcp(arguments)
            mcp_result = await conn.call_tool(mcp_tool_name, mcp_args, timeout=120)
            result_text = mcp_result_to_text(mcp_result)
            duration_ms = int((time.monotonic() - start_time) * 1000)

            is_error = mcp_result.get("isError", False) or "error" in mcp_result
            await log_mcp_call(
                server_slug, mcp_tool_name, arguments, result_text,
                duration_ms, not is_error, project_id,
                mcp_result.get("error", "") if is_error else "",
            )

            return result_text

        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            error_msg = str(e)
            logger.error(f"MCP 工具调用异常 ({server_slug}.{mcp_tool_name}): {e}")

            await log_mcp_call(
                server_slug, mcp_tool_name, arguments, "",
                duration_ms, False, project_id, error_msg,
            )

            # 尝试 fallback
            fallback_result = await MCPExecutionAdapter._try_fallback(
                server_slug, mcp_tool_name, arguments,
                workspace_dir=workspace_dir, project_id=project_id,
            )
            if fallback_result is not None:
                return f"⚠️ MCP 调用失败, 使用本地服务:\n{fallback_result}"
            return f"⚠️ MCP 工具调用失败: {error_msg}"

    @staticmethod
    async def _try_fallback(
        server_slug: str,
        tool_name: str,
        arguments: Dict[str, Any],
        workspace_dir: Optional[str] = None,
        project_id: Optional[int] = None,
    ) -> Optional[str]:
        """MCP 不可用时尝试 fallback 到本地服务

        目前仅支持 GitHub 服务的 fallback (github_service.py)
        """
        if server_slug != "github":
            return None

        try:
            return await _github_fallback(tool_name, arguments, workspace_dir, project_id)
        except Exception as e:
            logger.warning(f"GitHub fallback 也失败了 ({tool_name}): {e}")
            return None

    @staticmethod
    def get_mcp_tool_definitions(
        permissions: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """获取所有已启用 MCP Server 的工具定义 (OpenAI 格式)

        根据项目权限过滤: 只返回 project 授权的 MCP server 的工具
        """
        registry = MCPServerRegistry.get_instance()
        all_tools = []
        perm_list = list(permissions) if permissions else []

        for server in registry.get_enabled_servers():
            # 检查 server 级权限
            if permissions is not None:
                server_key = f"mcp_{server.slug}"
                if server_key not in perm_list:
                    continue

            # 转换工具定义
            openai_tools = mcp_tools_to_openai(
                server.discovered_tools,
                server.slug,
                server.name,
            )
            all_tools.extend(openai_tools)

        return all_tools


# ==================== GitHub Fallback ====================

async def _github_fallback(
    tool_name: str,
    arguments: Dict[str, Any],
    workspace_dir: Optional[str] = None,
    project_id: Optional[int] = None,
) -> Optional[str]:
    """GitHub MCP 工具 → github_service.py fallback 映射"""
    import json
    from studio.backend.services import github_service

    # 解析 token/repo (复用现有逻辑)
    token, repo = await _resolve_github_creds(workspace_dir, project_id)

    # 工具名映射
    if tool_name in ("get_issue", "get-issue"):
        issue_number = arguments.get("issue_number") or arguments.get("number")
        if issue_number:
            result = await github_service.get_issue(int(issue_number), repo=repo, token=token)
            return json.dumps(result, ensure_ascii=False, indent=2)

    elif tool_name in ("create_issue", "create-issue"):
        result = await github_service.create_issue(
            title=arguments.get("title", ""),
            body=arguments.get("body", ""),
            labels=arguments.get("labels"),
            repo=repo, token=token,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)

    elif tool_name in ("list_pull_requests", "list_pulls", "list-pull-requests"):
        state = arguments.get("state", "open")
        result = await github_service.list_pulls(state=state, repo=repo, token=token)
        return json.dumps(result, ensure_ascii=False, indent=2)

    elif tool_name in ("get_pull_request", "get_pull", "get-pull-request"):
        pr_number = arguments.get("pull_number") or arguments.get("number")
        if pr_number:
            result = await github_service.get_pull(int(pr_number), repo=repo, token=token)
            return json.dumps(result, ensure_ascii=False, indent=2)

    elif tool_name in ("merge_pull_request", "merge_pull", "merge-pull-request"):
        pr_number = arguments.get("pull_number") or arguments.get("number")
        if pr_number:
            result = await github_service.merge_pull(
                int(pr_number),
                merge_method=arguments.get("merge_method", "squash"),
                repo=repo, token=token,
            )
            return json.dumps(result, ensure_ascii=False, indent=2)

    elif tool_name in ("get_repo", "get_repository"):
        result = await github_service.get_repo_info(repo=repo, token=token)
        return json.dumps(result, ensure_ascii=False, indent=2)

    elif tool_name in ("list_branches",):
        result = await github_service.list_branches(repo=repo, token=token)
        return json.dumps(result, ensure_ascii=False, indent=2)

    return None  # 无对应 fallback


async def _resolve_github_creds(
    workspace_dir: Optional[str] = None,
    project_id: Optional[int] = None,
) -> Tuple[str, str]:
    """解析 GitHub token 和 repo"""
    try:
        from studio.backend.core.database import async_session_maker
        from studio.backend.models import WorkspaceDir, Project
        from sqlalchemy import select

        async with async_session_maker() as db:
            ws = None
            if project_id:
                project = (await db.execute(
                    select(Project).where(Project.id == project_id).limit(1)
                )).scalar_one_or_none()
                if project and project.workspace_dir:
                    ws = (await db.execute(
                        select(WorkspaceDir).where(
                            WorkspaceDir.path == project.workspace_dir
                        ).limit(1)
                    )).scalar_one_or_none()

            if ws is None and workspace_dir:
                ws = (await db.execute(
                    select(WorkspaceDir).where(
                        WorkspaceDir.path == workspace_dir
                    ).limit(1)
                )).scalar_one_or_none()

            if ws is None:
                ws = (await db.execute(
                    select(WorkspaceDir).where(
                        WorkspaceDir.is_active == True
                    ).limit(1)
                )).scalar_one_or_none()

            if ws:
                return (ws.github_token or "").strip(), (ws.github_repo or "").strip()

    except Exception:
        pass

    from studio.backend.core.config import settings
    return (settings.github_token or "").strip(), (settings.github_repo or "").strip()
