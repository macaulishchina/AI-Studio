"""
上下文构建器

ContextBuilder 组装 AI 系统提示符:
  - 可插拔 ContextSource 管道
  - 竞争式预算分配
  - 支持 section 详情返回 (前端 inspector)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from studio.backend.core.token_utils import estimate_tokens

logger = logging.getLogger(__name__)


@dataclass
class ContextSection:
    """上下文中的一个命名片段"""
    name: str
    content: str
    tokens: int = 0
    priority: int = 50  # 0=最高优先级, 100=最低
    trimmable: bool = True  # 预算不足时是否可裁剪
    children: List["ContextSection"] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "name": self.name,
            "tokens": self.tokens,
            "content": self.content[:5000] if self.content else "",
        }
        if self.children:
            result["children"] = [c.to_dict() for c in self.children]
        return result


class BaseContextSource:
    """
    上下文源接口

    子类实现 gather() 返回一组 ContextSection。
    ContextBuilder 按优先级依次调用各 source。
    """
    name: str = "base"
    priority: int = 50  # 默认优先级

    async def gather(
        self,
        budget_tokens: int,
        **kwargs,
    ) -> List[ContextSection]:
        """
        收集上下文片段

        Args:
            budget_tokens: 可用 token 预算
            **kwargs: 项目/角色/技能等上下文参数

        Returns:
            ContextSection 列表
        """
        return []


class ContextBuilder:
    """
    上下文管道构建器

    使用方式:
        builder = ContextBuilder()
        builder.add_source(RoleContextSource())
        builder.add_source(WorkspaceContextSource())
        builder.add_source(RAGContextSource())
        prompt = await builder.build(budget_tokens=4000, project=project)
    """

    def __init__(self):
        self._sources: List[BaseContextSource] = []

    def add_source(self, source: BaseContextSource) -> "ContextBuilder":
        """添加上下文源 (按 priority 自动排序)"""
        self._sources.append(source)
        self._sources.sort(key=lambda s: s.priority)
        return self

    async def build(
        self,
        budget_tokens: int = 4000,
        return_sections: bool = False,
        **kwargs,
    ) -> Union[str, Tuple[str, List[Dict[str, Any]]]]:
        """
        构建系统提示符

        Args:
            budget_tokens: 总 token 预算
            return_sections: 是否返回 section 详情
            **kwargs: 传递给各 ContextSource 的参数

        Returns:
            str (系统提示符) 或 (str, sections_info) tuple
        """
        all_sections: List[ContextSection] = []
        remaining_budget = budget_tokens

        for source in self._sources:
            if remaining_budget <= 0:
                break
            try:
                sections = await source.gather(remaining_budget, **kwargs)
                for section in sections:
                    section.tokens = estimate_tokens(section.content)
                    if section.tokens <= remaining_budget:
                        all_sections.append(section)
                        remaining_budget -= section.tokens
                    elif section.trimmable:
                        # 裁剪到适合预算
                        ratio = remaining_budget / max(section.tokens, 1)
                        trimmed_len = int(len(section.content) * ratio * 0.9)
                        section.content = section.content[:trimmed_len] + "\n... (上下文已截断)"
                        section.tokens = estimate_tokens(section.content)
                        all_sections.append(section)
                        remaining_budget -= section.tokens
            except Exception as e:
                logger.warning(f"ContextSource '{source.name}' 执行失败: {e}")

        # 组装
        prompt_parts = [s.content for s in all_sections if s.content]
        prompt = "\n\n".join(prompt_parts)

        if return_sections:
            sections_info = [s.to_dict() for s in all_sections]
            return prompt, sections_info
        return prompt
