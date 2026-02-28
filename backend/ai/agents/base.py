"""
Agent 基础框架

BaseAgent ABC 定义了 Agent 的生命周期:
  plan → act → reflect → finalize

AgentEvent 兼容现有 SSE 协议 + 新增 plan/reflection 事件。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Callable, Awaitable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ==================== Agent Event ====================

class AgentEventType(str, Enum):
    """Agent 事件类型 — 与 SSE 协议对齐"""
    CONTENT = "content"
    THINKING = "thinking"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TOOL_ERROR = "tool_error"
    USAGE = "usage"
    TRUNCATED = "truncated"
    ASK_USER_PENDING = "ask_user_pending"
    ERROR = "error"
    # 新增 Agent 事件
    PLAN_UPDATE = "plan_update"
    REFLECTION = "reflection"
    AGENT_SWITCH = "agent_switch"


@dataclass
class AgentEvent:
    """Agent 执行过程中产出的事件"""
    type: AgentEventType
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为 SSE 协议兼容的 dict"""
        result = {"type": self.type.value}
        result.update(self.data)
        return result


# ==================== Agent I/O ====================

@dataclass
class AgentInput:
    """Agent 运行输入"""
    messages: List[Dict[str, Any]]
    system_prompt: str = ""
    model: str = "gpt-4o"
    temperature: float = 0.7
    max_tokens: int = 8192
    tools: Optional[List[Dict[str, Any]]] = None
    tool_executor: Optional[Callable[[str, Dict[str, Any]], Awaitable[str]]] = None
    max_tool_rounds: int = 15
    request_id: str = ""
    # 扩展
    enable_reflection: bool = False
    reflection_interval: int = 5  # 每 N 轮 tool call 后反思
    enable_planning: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentConfig:
    """Agent 配置"""
    model: str = "gpt-4o"
    temperature: float = 0.7
    max_tokens: int = 8192
    max_tool_rounds: int = 15
    enable_reflection: bool = False
    reflection_interval: int = 5
    enable_planning: bool = False
    parallel_tools: bool = False


@dataclass
class PlanStep:
    """计划中的一个步骤"""
    step_id: int
    description: str
    status: str = "pending"  # pending / in_progress / completed / skipped
    result: Optional[str] = None


@dataclass
class AgentPlan:
    """Agent 执行计划"""
    goal: str
    steps: List[PlanStep] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "steps": [
                {"step_id": s.step_id, "description": s.description,
                 "status": s.status, "result": s.result}
                for s in self.steps
            ],
        }


@dataclass
class ReflectionResult:
    """Agent 反思结果"""
    summary: str
    action: str = "continue"  # continue / adjust / abort
    adjustments: Optional[str] = None


# ==================== BaseAgent ====================

class BaseAgent:
    """
    Agent 基类 — 定义执行生命周期

    子类需实现:
      - act(): 执行单轮 LLM + tool 调用
    可选覆盖:
      - plan(): 任务规划
      - reflect(): 自我反思
      - finalize(): 结束汇总
    """

    def __init__(self, config: Optional[AgentConfig] = None):
        self.config = config or AgentConfig()

    async def run(self, input: AgentInput) -> AsyncGenerator[AgentEvent, None]:
        """
        Agent 主执行循环 — Template Method

        yield AgentEvent 事件流，与现有 SSE 协议兼容。
        """
        # 1. Plan (可选)
        plan = None
        if input.enable_planning:
            plan = await self.plan(input)
            if plan:
                yield AgentEvent(
                    type=AgentEventType.PLAN_UPDATE,
                    data={"plan": plan.to_dict()},
                )

        # 2. Act loop
        async for event in self.act(input, plan):
            yield event

    async def plan(self, input: AgentInput) -> Optional[AgentPlan]:
        """任务规划 (可选覆盖)"""
        return None

    async def act(
        self, input: AgentInput, plan: Optional[AgentPlan] = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """执行主逻辑 — 子类必须实现"""
        raise NotImplementedError
        yield  # make it a generator

    async def reflect(
        self, input: AgentInput, rounds_completed: int, context: Dict[str, Any],
    ) -> Optional[ReflectionResult]:
        """自我反思 (可选覆盖)"""
        return None

    async def finalize(
        self, input: AgentInput, result: Dict[str, Any],
    ) -> None:
        """结束处理 (可选覆盖)"""
        pass
