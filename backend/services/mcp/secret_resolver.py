"""
MCP Secret Resolver — 凭据解析与安全注入

职责:
  - 从 WorkspaceDir / Project / 系统配置中解析 MCP Server 所需的凭据
  - 将凭据注入为 MCP 进程环境变量 (仅内存, 不落盘)
  - 支持凭据模板变量替换 (如 env_template 中的 {github_token})

安全原则:
  - Token 不落盘 (不写日志, 不返回前端)
  - 按 workspace 隔离 (不同工作目录可绑定不同 token)
  - 最小权限: 只注入当前 server 需要的变量
"""
import logging
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def resolve_env_for_server(
    server_slug: str,
    env_template: Dict[str, str],
    workspace_dir: Optional[str] = None,
    project_id: Optional[int] = None,
) -> Dict[str, str]:
    """解析 MCP Server 所需的环境变量

    env_template 中支持模板变量:
      {github_token}  → 从 WorkspaceDir.github_token 或全局配置获取
      {github_repo}   → 从 WorkspaceDir.github_repo 获取
      {gitlab_token}  → 从 WorkspaceDir.gitlab_token 获取

    Returns:
        已替换模板变量的环境变量字典
    """
    # 收集可用的变量值
    variables = await _collect_variables(workspace_dir, project_id)

    # 模板替换
    resolved = {}
    for key, value_template in env_template.items():
        resolved_value = value_template
        for var_name, var_value in variables.items():
            placeholder = "{" + var_name + "}"
            if placeholder in resolved_value:
                resolved_value = resolved_value.replace(placeholder, var_value)
        resolved[key] = resolved_value

    # 安全: 过滤空值 (未解析的模板变量)
    resolved = {k: v for k, v in resolved.items() if v and "{" not in v}

    return resolved


async def _collect_variables(
    workspace_dir: Optional[str] = None,
    project_id: Optional[int] = None,
) -> Dict[str, str]:
    """从各配置源收集变量值

    优先级: WorkspaceDir (按 project/active) → 全局 settings → 空
    """
    variables: Dict[str, str] = {}

    try:
        from studio.backend.core.database import async_session_maker
        from studio.backend.models import WorkspaceDir, Project

        async with async_session_maker() as db:
            ws = None

            # 1) 尝试从 project 的 workspace_dir 获取
            if project_id:
                project = (await db.execute(
                    select(Project).where(Project.id == project_id).limit(1)
                )).scalar_one_or_none()
                if project and project.workspace_dir:
                    ws = (await db.execute(
                        select(WorkspaceDir)
                        .where(WorkspaceDir.path == project.workspace_dir)
                        .limit(1)
                    )).scalar_one_or_none()

            # 2) 尝试从指定 workspace_dir 获取
            if ws is None and workspace_dir:
                ws = (await db.execute(
                    select(WorkspaceDir)
                    .where(WorkspaceDir.path == workspace_dir)
                    .limit(1)
                )).scalar_one_or_none()

            # 3) 回退到活跃工作目录
            if ws is None:
                ws = (await db.execute(
                    select(WorkspaceDir)
                    .where(WorkspaceDir.is_active == True)
                    .limit(1)
                )).scalar_one_or_none()

            if ws:
                # 仅设置非空值，空值留给 settings fallback
                if ws.github_token:
                    variables["github_token"] = ws.github_token
                if ws.github_repo:
                    variables["github_repo"] = ws.github_repo
                if ws.gitlab_token:
                    variables["gitlab_token"] = ws.gitlab_token
                if ws.gitlab_repo:
                    variables["gitlab_repo"] = ws.gitlab_repo
                if ws.gitlab_url:
                    variables["gitlab_url"] = ws.gitlab_url
                if ws.path:
                    variables["workspace_path"] = ws.path

    except Exception as e:
        logger.warning(f"SecretResolver: DB 查询失败: {e}")

    # 全局 settings fallback
    from studio.backend.core.config import settings
    variables.setdefault("github_token", settings.github_token or "")
    variables.setdefault("github_repo", settings.github_repo or "")
    variables.setdefault("workspace_path", settings.workspace_path or "")

    return variables


def validate_secrets(
    env_template: Dict[str, str],
    resolved_env: Dict[str, str],
) -> Dict[str, Any]:
    """检查凭据解析是否完整

    Returns:
        {"complete": bool, "missing": [...], "resolved_keys": [...]}
    """
    missing = []
    resolved_keys = list(resolved_env.keys())

    for key, template in env_template.items():
        if key not in resolved_env or not resolved_env[key]:
            missing.append(key)

    return {
        "complete": len(missing) == 0,
        "missing": missing,
        "resolved_keys": resolved_keys,
    }
