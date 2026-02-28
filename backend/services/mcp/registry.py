"""
MCP Server Registry — 服务注册与元数据管理

管理所有已配置的 MCP Server:
  - 从 DB (MCPServer 表) 加载配置
  - 内存缓存 + 按需刷新
  - 提供按 slug/name 查找能力
  - 维护每个 server 的工具列表 (通过 tools/list 协议获取)
"""
import logging
from typing import Dict, List, Optional

from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConfig:
    """MCP Server 配置 (内存表示)"""
    id: int
    slug: str                       # 唯一标识, 如 "github"
    name: str                       # 显示名, 如 "GitHub MCP Server"
    description: str = ""
    transport: str = "stdio"        # "stdio" | "sse" | "streamable_http"
    command: str = ""               # stdio 模式的启动命令
    args: List[str] = field(default_factory=list)   # 命令参数
    env: Dict[str, str] = field(default_factory=dict)  # 环境变量模板
    url: str = ""                   # sse/http 模式的 URL
    enabled: bool = True
    # 权限映射: MCP tool name → Studio permission_key
    # 未映射的工具默认使用 "mcp_{slug}_{tool_name}" 权限键
    permission_map: Dict[str, str] = field(default_factory=dict)
    # 该 server 上发现的工具列表 (运行时填充)
    discovered_tools: List[Dict] = field(default_factory=list)


class MCPServerRegistry:
    """MCP Server 注册表 — 单例管理所有 MCP Server 配置"""

    _instance: Optional["MCPServerRegistry"] = None

    def __init__(self):
        self._servers: Dict[str, MCPServerConfig] = {}
        self._loaded = False

    @classmethod
    def get_instance(cls) -> "MCPServerRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def servers(self) -> Dict[str, MCPServerConfig]:
        return self._servers

    async def load_from_db(self):
        """从 DB 加载所有 MCP Server 配置到内存"""
        try:
            from studio.backend.core.database import async_session_maker
            from studio.backend.models import MCPServer
            from sqlalchemy import select

            async with async_session_maker() as db:
                result = await db.execute(
                    select(MCPServer).order_by(MCPServer.sort_order, MCPServer.id)
                )
                rows = result.scalars().all()

            self._servers.clear()
            for row in rows:
                config = MCPServerConfig(
                    id=row.id,
                    slug=row.slug,
                    name=row.name,
                    description=row.description or "",
                    transport=row.transport or "stdio",
                    command=row.command or "",
                    args=row.args or [],
                    env=row.env_template or {},
                    url=row.url or "",
                    enabled=row.enabled,
                    permission_map=row.permission_map or {},
                )
                self._servers[row.slug] = config

            self._loaded = True
            logger.info(f"✅ MCP Registry: 加载了 {len(self._servers)} 个服务")

        except Exception as e:
            logger.warning(f"⚠️ MCP Registry: 加载失败 (首次启动可忽略): {e}")
            self._loaded = True  # 标记已尝试加载, 避免重复失败

    def get_server(self, slug: str) -> Optional[MCPServerConfig]:
        """按 slug 获取 server 配置"""
        return self._servers.get(slug)

    def get_enabled_servers(self) -> List[MCPServerConfig]:
        """获取所有已启用的 server"""
        return [s for s in self._servers.values() if s.enabled]

    def register_server(self, config: MCPServerConfig):
        """手动注册 server (测试/临时用)"""
        self._servers[config.slug] = config
        logger.info(f"MCP Registry: 注册 {config.slug} ({config.name})")

    def unregister_server(self, slug: str):
        """移除 server"""
        if slug in self._servers:
            del self._servers[slug]
            logger.info(f"MCP Registry: 移除 {slug}")

    def update_discovered_tools(self, slug: str, tools: List[Dict]):
        """更新 server 发现的工具列表"""
        server = self._servers.get(slug)
        if server:
            server.discovered_tools = tools
            logger.info(f"MCP Registry: {slug} 发现 {len(tools)} 个工具")

    async def refresh(self):
        """强制刷新 (DB → 内存)"""
        await self.load_from_db()

    def get_all_mcp_tools(self) -> List[Dict]:
        """获取所有已启用 server 的工具列表 (合并)"""
        all_tools = []
        for server in self.get_enabled_servers():
            for tool in server.discovered_tools:
                all_tools.append({
                    **tool,
                    "_mcp_server": server.slug,
                })
        return all_tools
