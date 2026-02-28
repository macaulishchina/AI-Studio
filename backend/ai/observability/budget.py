"""
Observability — Token 预算管理器

控制每次对话/任务的 token 消耗上限:
  - 会话级预算 (单次对话)
  - 项目级预算 (累计)
  - 全局预算 (月度限额)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class BudgetLimit:
    """预算限制"""
    max_tokens: int = 0  # 0 = 无限制
    max_cost_cents: float = 0  # 0 = 无限制
    period_seconds: int = 0  # 0 = 永久, 否则滚动窗口


@dataclass
class BudgetUsage:
    """预算使用情况"""
    tokens_used: int = 0
    cost_cents: float = 0.0
    requests: int = 0
    window_start: float = field(default_factory=time.time)


class BudgetManager:
    """
    Token 预算管理器

    支持三个层级的预算控制:
    1. 会话级 (session): 单次对话的上限
    2. 项目级 (project): 项目累计上限
    3. 全局级 (global): 系统总预算
    """

    def __init__(self):
        # 默认限额
        self._limits: Dict[str, BudgetLimit] = {
            "session": BudgetLimit(max_tokens=200_000),
            "global": BudgetLimit(max_tokens=0, max_cost_cents=0),  # 默认不限
        }
        self._usage: Dict[str, BudgetUsage] = {}

    def set_limit(self, scope: str, limit: BudgetLimit):
        """设置预算限制"""
        self._limits[scope] = limit

    def get_limit(self, scope: str) -> Optional[BudgetLimit]:
        """获取预算限制"""
        return self._limits.get(scope)

    def record_usage(
        self,
        tokens: int,
        cost_cents: float = 0.0,
        session_id: str = "",
        project_id: str = "",
    ):
        """记录 token 使用"""
        now = time.time()

        # 全局
        self._record_scope("global", tokens, cost_cents, now)

        # 项目级
        if project_id:
            self._record_scope(f"project:{project_id}", tokens, cost_cents, now)

        # 会话级
        if session_id:
            self._record_scope(f"session:{session_id}", tokens, cost_cents, now)

    def check_budget(
        self,
        session_id: str = "",
        project_id: str = "",
    ) -> Dict[str, any]:
        """
        检查预算是否超限

        Returns:
            {"allowed": bool, "warnings": [...], "details": {...}}
        """
        warnings = []
        details = {}

        # 检查全局
        g_ok, g_detail = self._check_scope("global")
        details["global"] = g_detail
        if not g_ok:
            return {"allowed": False, "warnings": ["全局预算已耗尽"], "details": details}
        if g_detail.get("usage_pct", 0) > 80:
            warnings.append(f"全局预算已使用 {g_detail['usage_pct']:.0f}%")

        # 检查项目
        if project_id:
            p_ok, p_detail = self._check_scope(f"project:{project_id}")
            details["project"] = p_detail
            if not p_ok:
                return {"allowed": False, "warnings": [f"项目 {project_id} 预算已耗尽"], "details": details}

        # 检查会话
        if session_id:
            s_ok, s_detail = self._check_scope(f"session:{session_id}")
            details["session"] = s_detail
            if not s_ok:
                return {"allowed": False, "warnings": ["单次会话 token 上限已达到"], "details": details}

        return {"allowed": True, "warnings": warnings, "details": details}

    def get_usage_summary(self, project_id: Optional[str] = None) -> dict:
        """获取使用量汇总"""
        result = {}

        # 全局
        g_usage = self._usage.get("global", BudgetUsage())
        g_limit = self._limits.get("global", BudgetLimit())
        result["global"] = {
            "tokens_used": g_usage.tokens_used,
            "cost_cents": round(g_usage.cost_cents, 2),
            "requests": g_usage.requests,
            "limit_tokens": g_limit.max_tokens,
            "limit_cost_cents": g_limit.max_cost_cents,
        }

        # 项目
        if project_id:
            key = f"project:{project_id}"
            p_usage = self._usage.get(key, BudgetUsage())
            p_limit = self._limits.get(key, BudgetLimit())
            result["project"] = {
                "tokens_used": p_usage.tokens_used,
                "cost_cents": round(p_usage.cost_cents, 2),
                "requests": p_usage.requests,
                "limit_tokens": p_limit.max_tokens,
                "limit_cost_cents": p_limit.max_cost_cents,
            }

        return result

    def reset_session(self, session_id: str):
        """重置会话预算"""
        key = f"session:{session_id}"
        self._usage.pop(key, None)

    # ── 内部方法 ──

    def _record_scope(self, scope: str, tokens: int, cost: float, now: float):
        if scope not in self._usage:
            self._usage[scope] = BudgetUsage(window_start=now)

        usage = self._usage[scope]
        limit = self._limits.get(scope)

        # 检查滚动窗口
        if limit and limit.period_seconds > 0:
            if now - usage.window_start > limit.period_seconds:
                # 窗口过期, 重置
                self._usage[scope] = BudgetUsage(window_start=now)
                usage = self._usage[scope]

        usage.tokens_used += tokens
        usage.cost_cents += cost
        usage.requests += 1

    def _check_scope(self, scope: str) -> tuple[bool, dict]:
        limit = self._limits.get(scope)
        usage = self._usage.get(scope, BudgetUsage())

        detail = {
            "tokens_used": usage.tokens_used,
            "cost_cents": round(usage.cost_cents, 2),
            "requests": usage.requests,
        }

        if not limit or (limit.max_tokens == 0 and limit.max_cost_cents == 0):
            detail["usage_pct"] = 0
            return True, detail

        # Token 检查
        if limit.max_tokens > 0:
            pct = usage.tokens_used / limit.max_tokens * 100
            detail["usage_pct"] = round(pct, 1)
            detail["limit_tokens"] = limit.max_tokens
            if usage.tokens_used >= limit.max_tokens:
                return False, detail

        # 成本检查
        if limit.max_cost_cents > 0:
            cost_pct = usage.cost_cents / limit.max_cost_cents * 100
            detail["cost_pct"] = round(cost_pct, 1)
            detail["limit_cost_cents"] = limit.max_cost_cents
            if usage.cost_cents >= limit.max_cost_cents:
                return False, detail

        return True, detail


# ── 全局单例 ──

_budget_mgr: Optional[BudgetManager] = None


def get_budget_manager() -> BudgetManager:
    global _budget_mgr
    if _budget_mgr is None:
        _budget_mgr = BudgetManager()
    return _budget_mgr
