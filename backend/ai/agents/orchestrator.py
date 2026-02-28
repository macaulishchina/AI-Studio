"""
Agent 编排器

AgentOrchestrator 管理 Agent 的选择和切换:
  - 根据项目 workflow stage 选择 Agent 策略
  - 支持 react / planning / orchestrated 三种模式
  - 默认回退到 ReActAgent
"""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator, Dict, Optional

from .base import AgentConfig, AgentEvent, AgentInput, BaseAgent
from .react import ReActAgent

logger = logging.getLogger(__name__)

# Agent 策略映射
AGENT_STRATEGIES = {
    "react": "ReActAgent",
    "planning": "PlanningAgent",
    "orchestrated": "OrchestratedAgent",
}


def create_agent(
    strategy: str = "react",
    config: Optional[AgentConfig] = None,
) -> BaseAgent:
    """
    根据策略创建 Agent 实例

    Args:
        strategy: "react" | "planning" | "orchestrated"
        config: Agent 配置

    Returns:
        BaseAgent 实例
    """
    cfg = config or AgentConfig()

    if strategy == "react":
        return ReActAgent(cfg)
    # 未来扩展:
    # elif strategy == "planning":
    #     return PlanningAgent(cfg)
    # elif strategy == "orchestrated":
    #     return OrchestratedAgent(cfg)
    else:
        logger.warning(f"未知 Agent 策略 '{strategy}', 回退到 ReActAgent")
        return ReActAgent(cfg)


async def run_agent(
    input: AgentInput,
    strategy: str = "react",
    config: Optional[AgentConfig] = None,
) -> AsyncGenerator[AgentEvent, None]:
    """
    便捷函数: 创建 Agent 并执行

    兼容 chat_stream() 的事件协议 — 可作为 drop-in 替换。
    """
    agent = create_agent(strategy, config)
    async for event in agent.run(input):
        yield event
