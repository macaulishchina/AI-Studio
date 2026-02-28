"""
MCP Audit Logger — 审计与限流

职责:
  - 记录每次 MCP 工具调用 (调用方、参数、结果、耗时)
  - 按项目/用户/server 维度限流
  - 提供审计查询接口
"""
import logging
import time
from collections import defaultdict
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ==================== 内存限流器 ====================

class _RateLimiter:
    """简单的滑动窗口限流器 (内存级)"""

    def __init__(self):
        # key → [timestamp, timestamp, ...]
        self._windows: Dict[str, list] = defaultdict(list)

    def check_and_record(
        self,
        key: str,
        max_calls: int = 60,
        window_seconds: int = 60,
    ) -> bool:
        """检查是否超限, 未超限则记录

        Returns:
            True = 允许, False = 超限
        """
        now = time.time()
        window = self._windows[key]

        # 清理过期记录
        cutoff = now - window_seconds
        self._windows[key] = [t for t in window if t > cutoff]
        window = self._windows[key]

        if len(window) >= max_calls:
            return False

        window.append(now)
        return True

    def get_usage(self, key: str, window_seconds: int = 60) -> int:
        """获取当前窗口内的调用次数"""
        now = time.time()
        cutoff = now - window_seconds
        window = self._windows.get(key, [])
        return sum(1 for t in window if t > cutoff)


_rate_limiter = _RateLimiter()


# ==================== 审计日志 ====================

async def log_mcp_call(
    server_slug: str,
    tool_name: str,
    arguments: Dict[str, Any],
    result_text: str,
    duration_ms: int,
    success: bool,
    project_id: Optional[int] = None,
    error_message: str = "",
):
    """记录 MCP 工具调用到数据库"""
    try:
        from studio.backend.core.database import async_session_maker
        from studio.backend.models import MCPAuditLog

        async with async_session_maker() as db:
            log = MCPAuditLog(
                server_slug=server_slug,
                tool_name=tool_name,
                arguments=arguments,
                result_preview=result_text[:500] if result_text else "",
                duration_ms=duration_ms,
                success=success,
                project_id=project_id,
                error_message=error_message[:500] if error_message else "",
            )
            db.add(log)
            await db.commit()

    except Exception as e:
        # 审计失败不应阻塞主流程
        logger.warning(f"MCP 审计日志记录失败: {e}")


def check_rate_limit(
    server_slug: str,
    project_id: Optional[int] = None,
    max_calls_per_minute: int = 60,
) -> bool:
    """检查 MCP 调用频率是否超限

    Returns:
        True = 允许, False = 超限
    """
    # 按 server + project 双维度限流
    key = f"mcp:{server_slug}:{project_id or 'global'}"
    return _rate_limiter.check_and_record(
        key,
        max_calls=max_calls_per_minute,
        window_seconds=60,
    )


def get_rate_limit_status(
    server_slug: str,
    project_id: Optional[int] = None,
) -> Dict[str, Any]:
    """获取当前限流状态"""
    key = f"mcp:{server_slug}:{project_id or 'global'}"
    usage = _rate_limiter.get_usage(key, 60)
    return {
        "server": server_slug,
        "project_id": project_id,
        "calls_last_minute": usage,
    }
