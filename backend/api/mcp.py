"""
设计院 (Studio) - MCP 服务管理 API
MCP Server 的注册、配置、状态管理、工具发现、审计日志
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db, async_session_maker
from backend.models import MCPServer, MCPAuditLog
from backend.services.mcp.registry import MCPServerRegistry
from backend.services.mcp.client_manager import MCPClientManager
from backend.services.mcp.permission_bridge import get_available_mcp_permission_keys
from backend.services.mcp.secret_resolver import validate_secrets, resolve_env_for_server

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/mcp", tags=["MCP"])


# ==================== Schemas ====================

class MCPServerCreate(BaseModel):
    slug: str = Field(..., min_length=1, max_length=50, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field("", max_length=500)
    icon: str = Field("🔌", max_length=10)
    transport: str = Field("stdio", pattern=r"^(stdio|sse|streamable_http)$")
    command: str = Field("", max_length=500)
    args: List[str] = Field(default_factory=list)
    env_template: Dict[str, str] = Field(default_factory=dict)
    url: str = Field("", max_length=500)
    permission_map: Dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    sort_order: int = 0


class MCPServerUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    icon: Optional[str] = Field(None, max_length=10)
    transport: Optional[str] = Field(None, pattern=r"^(stdio|sse|streamable_http)$")
    command: Optional[str] = Field(None, max_length=500)
    args: Optional[List[str]] = None
    env_template: Optional[Dict[str, str]] = None
    url: Optional[str] = Field(None, max_length=500)
    permission_map: Optional[Dict[str, str]] = None
    enabled: Optional[bool] = None
    sort_order: Optional[int] = None


class MCPServerResponse(BaseModel):
    id: int
    slug: str
    name: str
    description: str
    icon: str
    transport: str
    command: str
    args: List[str]
    env_template: Dict[str, str]
    url: str
    permission_map: Dict[str, str]
    enabled: bool
    is_builtin: bool
    sort_order: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # 运行时信息
    connected: bool = False
    tools_count: int = 0

    class Config:
        from_attributes = True


# ==================== CRUD ====================

@router.get("/servers", response_model=List[MCPServerResponse])
async def list_servers(db: AsyncSession = Depends(get_db)):
    """列出所有 MCP Server 配置"""
    result = await db.execute(
        select(MCPServer).order_by(MCPServer.sort_order, MCPServer.id)
    )
    servers = result.scalars().all()

    # 附加运行时状态
    client_manager = MCPClientManager.get_instance()
    status_map = client_manager.get_status()
    registry = MCPServerRegistry.get_instance()

    response = []
    for s in servers:
        runtime = status_map.get(s.slug, {})
        server_config = registry.get_server(s.slug)
        tools_count = len(server_config.discovered_tools) if server_config else 0

        resp = MCPServerResponse(
            id=s.id,
            slug=s.slug,
            name=s.name,
            description=s.description or "",
            icon=s.icon or "🔌",
            transport=s.transport or "stdio",
            command=s.command or "",
            args=s.args or [],
            env_template=s.env_template or {},
            url=s.url or "",
            permission_map=s.permission_map or {},
            enabled=s.enabled,
            is_builtin=s.is_builtin,
            sort_order=s.sort_order or 0,
            created_at=s.created_at,
            updated_at=s.updated_at,
            connected=runtime.get("connected", False),
            tools_count=tools_count,
        )
        response.append(resp)

    return response


@router.post("/servers", response_model=MCPServerResponse)
async def create_server(
    data: MCPServerCreate,
    db: AsyncSession = Depends(get_db),
):
    """创建 MCP Server 配置"""
    # 检查 slug 唯一
    existing = (await db.execute(
        select(MCPServer).where(MCPServer.slug == data.slug).limit(1)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail=f"slug '{data.slug}' 已存在")

    server = MCPServer(
        slug=data.slug,
        name=data.name,
        description=data.description,
        icon=data.icon,
        transport=data.transport,
        command=data.command,
        args=data.args,
        env_template=data.env_template,
        url=data.url,
        permission_map=data.permission_map,
        enabled=data.enabled,
        sort_order=data.sort_order,
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)

    # 刷新内存注册表
    await MCPServerRegistry.get_instance().refresh()

    return MCPServerResponse(
        id=server.id,
        slug=server.slug,
        name=server.name,
        description=server.description or "",
        icon=server.icon or "🔌",
        transport=server.transport or "stdio",
        command=server.command or "",
        args=server.args or [],
        env_template=server.env_template or {},
        url=server.url or "",
        permission_map=server.permission_map or {},
        enabled=server.enabled,
        is_builtin=server.is_builtin,
        sort_order=server.sort_order or 0,
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


@router.patch("/servers/{slug}")
async def update_server(
    slug: str,
    data: MCPServerUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新 MCP Server 配置"""
    server = (await db.execute(
        select(MCPServer).where(MCPServer.slug == slug).limit(1)
    )).scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail=f"MCP Server '{slug}' 不存在")

    update_fields = data.model_dump(exclude_unset=True)
    for key, value in update_fields.items():
        setattr(server, key, value)

    await db.commit()

    # 刷新内存注册表
    await MCPServerRegistry.get_instance().refresh()

    # 如果配置变更, 断开旧连接 (下次调用时重连)
    await MCPClientManager.get_instance().disconnect(slug)

    return {"ok": True, "slug": slug}


@router.delete("/servers/{slug}")
async def delete_server(
    slug: str,
    db: AsyncSession = Depends(get_db),
):
    """删除 MCP Server 配置"""
    server = (await db.execute(
        select(MCPServer).where(MCPServer.slug == slug).limit(1)
    )).scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail=f"MCP Server '{slug}' 不存在")
    if server.is_builtin:
        raise HTTPException(status_code=403, detail="内置 MCP Server 不可删除")

    # 断开连接
    await MCPClientManager.get_instance().disconnect(slug)

    await db.delete(server)
    await db.commit()

    # 刷新注册表
    await MCPServerRegistry.get_instance().refresh()

    return {"ok": True}


# ==================== 连接管理 ====================

@router.post("/servers/{slug}/connect")
async def connect_server(slug: str):
    """手动连接 MCP Server"""
    registry = MCPServerRegistry.get_instance()
    config = registry.get_server(slug)
    if not config:
        raise HTTPException(status_code=404, detail=f"MCP Server '{slug}' 未注册")
    if not config.enabled:
        raise HTTPException(status_code=400, detail=f"MCP Server '{slug}' 已禁用")

    # 解析凭据
    env_override = await resolve_env_for_server(slug, config.env)
    secret_check = validate_secrets(config.env, env_override)
    if not secret_check.get("complete", False):
        missing = secret_check.get("missing", [])
        raise HTTPException(
            status_code=400,
            detail=f"缺少必要凭证: {', '.join(missing) if missing else '未知'}"
        )

    # 连接
    client_manager = MCPClientManager.get_instance()
    conn = await client_manager.get_or_connect(config, env_override)
    if not conn:
        err = client_manager.get_last_error(slug)
        detail = f"无法连接到 MCP Server '{slug}'"
        if err:
            detail = f"{detail}: {err}"
        raise HTTPException(status_code=502, detail=detail)

    return {
        "ok": True,
        "slug": slug,
        "server_info": conn.server_info,
        "tools_count": len(config.discovered_tools),
    }


@router.post("/servers/{slug}/disconnect")
async def disconnect_server(slug: str):
    """手动断开 MCP Server"""
    await MCPClientManager.get_instance().disconnect(slug)
    return {"ok": True, "slug": slug}


@router.get("/servers/{slug}/tools")
async def list_server_tools(slug: str):
    """获取指定 MCP Server 的工具列表"""
    registry = MCPServerRegistry.get_instance()
    config = registry.get_server(slug)
    if not config:
        raise HTTPException(status_code=404, detail=f"MCP Server '{slug}' 未注册")

    return {
        "slug": slug,
        "tools": config.discovered_tools,
        "tools_count": len(config.discovered_tools),
    }


@router.get("/servers/{slug}/permissions")
async def list_server_permissions(slug: str):
    """获取指定 MCP Server 的权限键定义 (供前端权限面板使用)"""
    registry = MCPServerRegistry.get_instance()
    config = registry.get_server(slug)
    if not config:
        raise HTTPException(status_code=404, detail=f"MCP Server '{slug}' 未注册")

    permissions = get_available_mcp_permission_keys(slug, config.discovered_tools)
    return {"slug": slug, "permissions": permissions}


@router.post("/servers/{slug}/validate-secrets")
async def validate_server_secrets(slug: str):
    """检查 MCP Server 凭据是否完整"""
    registry = MCPServerRegistry.get_instance()
    config = registry.get_server(slug)
    if not config:
        raise HTTPException(status_code=404, detail=f"MCP Server '{slug}' 未注册")

    resolved = await resolve_env_for_server(slug, config.env)
    result = validate_secrets(config.env, resolved)
    return {"slug": slug, **result}


@router.get("/status")
async def get_mcp_status():
    """获取 MCP 系统整体状态"""
    registry = MCPServerRegistry.get_instance()
    client_manager = MCPClientManager.get_instance()

    servers = []
    for config in registry.get_enabled_servers():
        conn = client_manager.get_connection(config.slug)
        servers.append({
            "slug": config.slug,
            "name": config.name,
            "transport": config.transport,
            "enabled": config.enabled,
            "connected": conn.is_connected if conn else False,
            "tools_count": len(config.discovered_tools),
        })

    return {
        "servers": servers,
        "total_servers": len(registry.servers),
        "enabled_servers": len(registry.get_enabled_servers()),
        "connected_servers": sum(1 for s in servers if s["connected"]),
    }


@router.get("/health")
async def health_check():
    """MCP 健康检查"""
    client_manager = MCPClientManager.get_instance()
    health = await client_manager.health_check()
    return health


# ==================== 审计日志 ====================

@router.get("/audit-log")
async def list_audit_log(
    server_slug: Optional[str] = None,
    project_id: Optional[int] = None,
    success: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """查询 MCP 审计日志"""
    query = select(MCPAuditLog).order_by(desc(MCPAuditLog.created_at))

    if server_slug:
        query = query.where(MCPAuditLog.server_slug == server_slug)
    if project_id is not None:
        query = query.where(MCPAuditLog.project_id == project_id)
    if success is not None:
        query = query.where(MCPAuditLog.success == success)

    # 总数
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # 分页
    result = await db.execute(query.limit(limit).offset(offset))
    logs = result.scalars().all()

    return {
        "total": total,
        "items": [
            {
                "id": log.id,
                "server_slug": log.server_slug,
                "tool_name": log.tool_name,
                "arguments": log.arguments,
                "result_preview": log.result_preview,
                "duration_ms": log.duration_ms,
                "success": log.success,
                "error_message": log.error_message,
                "project_id": log.project_id,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
            for log in logs
        ],
    }


@router.get("/audit-log/stats")
async def audit_log_stats(db: AsyncSession = Depends(get_db)):
    """MCP 审计日志统计"""
    # 总调用
    total = (await db.execute(
        select(func.count()).select_from(MCPAuditLog)
    )).scalar() or 0

    # 成功/失败
    success_count = (await db.execute(
        select(func.count()).select_from(MCPAuditLog).where(MCPAuditLog.success == True)
    )).scalar() or 0

    # 按 server 分组
    by_server = (await db.execute(
        select(MCPAuditLog.server_slug, func.count())
        .group_by(MCPAuditLog.server_slug)
    )).all()

    return {
        "total_calls": total,
        "success_count": success_count,
        "failure_count": total - success_count,
        "by_server": {slug: count for slug, count in by_server},
    }


# ==================== 种子数据 ====================

async def seed_mcp_servers():
    """种子数据: 内置 GitHub MCP Server 配置"""
    try:
        async with async_session_maker() as db:
            existing = (await db.execute(
                select(MCPServer).where(MCPServer.slug == "github").limit(1)
            )).scalar_one_or_none()

            desired_description = "GitHub MCP 服务（npm 包）— 提供 GitHub 相关 MCP 工具。"
            desired_command = "npx"
            desired_args = ["-y", "@modelcontextprotocol/server-github"]
            desired_env_template = {
                "GITHUB_PERSONAL_ACCESS_TOKEN": "{github_token}",
            }

            if existing:
                changed = False
                if existing.command != desired_command:
                    existing.command = desired_command
                    changed = True
                if (existing.args or []) != desired_args:
                    existing.args = desired_args
                    changed = True
                if (existing.env_template or {}) != desired_env_template:
                    existing.env_template = desired_env_template
                    changed = True
                if existing.description != desired_description:
                    existing.description = desired_description
                    changed = True

                if changed:
                    await db.commit()
                    logger.info("✅ MCP Seed: 已更新 GitHub MCP Server 内置配置")
                else:
                    logger.info("MCP Seed: GitHub MCP Server 已存在, 跳过")
                return

            github_server = MCPServer(
                slug="github",
                name="GitHub MCP Server",
                description=desired_description,
                icon="🐙",
                transport="stdio",
                command=desired_command,
                args=desired_args,
                env_template=desired_env_template,
                url="",
                permission_map={},
                enabled=False,  # 默认禁用, 需手动启用
                is_builtin=True,
                sort_order=0,
            )
            db.add(github_server)
            await db.commit()
            logger.info("✅ MCP Seed: 创建 GitHub MCP Server 配置 (默认禁用)")

    except Exception as e:
        logger.warning(f"⚠️ MCP Seed 失败: {e}")
