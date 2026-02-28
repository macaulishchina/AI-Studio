"""
设计院 (Studio) - MCP (Model Context Protocol) 框架

为 Studio 提供标准化的 MCP 服务接入能力:
  - MCPServerRegistry: MCP 服务注册与元数据管理
  - MCPClientManager: MCP 连接生命周期管理 (stdio/sse)
  - tool_adapter: MCP 工具定义 ↔ Studio 工具定义 适配
  - MCPExecutionAdapter: 统一执行入口 (含 fallback 到本地服务)
  - PermissionBridge: 复用 Project.tool_permissions 体系
  - SecretResolver: 按 workspace/project 解析凭据
  - AuditLogger: MCP 调用审计与限流

首个 MCP 服务: GitHub MCP Server (github/github-mcp-server)
运行方式: stdio 本地子进程

架构要点:
  1. MCP 层是"侧车"模式 — 不替换现有 ToolRegistry, 而是并行扩展
  2. 现有 github_service.py 保留为 fallback (MCP 不可用时兜底)
  3. 工具权限体系复用 Project.tool_permissions, 新增 mcp_* 权限键
  4. 所有 MCP 调用经过统一审计 (MCPAuditLog)
"""

from studio.backend.services.mcp.registry import MCPServerRegistry
from studio.backend.services.mcp.client_manager import MCPClientManager
from studio.backend.services.mcp.execution_adapter import MCPExecutionAdapter

__all__ = [
    "MCPServerRegistry",
    "MCPClientManager",
    "MCPExecutionAdapter",
]
