"""
Skills — 技能执行引擎

将 Skill 从"纯 prompt 模板"升级为"可执行能力模块":
  - 完整注入: instruction + output_format + examples + constraints
  - 工具偏好: recommended_tools 自动优先排序
  - 输出验证: 可选的结构化输出校验
  - 行为组合: 多技能激活时的冲突检测 & 合并
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


@dataclass
class SkillSpec:
    """技能规格 (从 ORM Skill 模型转换而来)"""
    id: int
    name: str
    category: str = "general"
    icon: str = "⚡"
    description: str = ""
    instruction_prompt: str = ""
    output_format: str = ""
    examples: List[dict] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    recommended_tools: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    @classmethod
    def from_orm(cls, skill_obj) -> "SkillSpec":
        """从 ORM Skill 对象构建"""
        return cls(
            id=skill_obj.id,
            name=skill_obj.name,
            category=getattr(skill_obj, "category", "general"),
            icon=getattr(skill_obj, "icon", "⚡"),
            description=getattr(skill_obj, "description", ""),
            instruction_prompt=getattr(skill_obj, "instruction_prompt", ""),
            output_format=getattr(skill_obj, "output_format", ""),
            examples=getattr(skill_obj, "examples", None) or [],
            constraints=getattr(skill_obj, "constraints", None) or [],
            recommended_tools=getattr(skill_obj, "recommended_tools", None) or [],
            tags=getattr(skill_obj, "tags", None) or [],
        )


@dataclass
class SkillPrompt:
    """组装后的技能 prompt 块"""
    system_block: str       # 注入到 system message 的内容
    tool_hints: List[str]   # 推荐的工具名
    constraints: List[str]  # 约束列表


class SkillEngine:
    """
    技能执行引擎

    职责:
    1. 将 SkillSpec 组装为结构化 prompt
    2. 多技能组合 (合并 prompt / 检测冲突)
    3. 工具偏好排序
    4. 输出格式验证 (可选)
    """

    def compose(self, skills: Sequence[SkillSpec]) -> SkillPrompt:
        """
        组合多个技能为单一 SkillPrompt

        Args:
            skills: 激活的技能列表

        Returns:
            合并后的 SkillPrompt
        """
        if not skills:
            return SkillPrompt(system_block="", tool_hints=[], constraints=[])

        blocks: List[str] = []
        all_tools: List[str] = []
        all_constraints: List[str] = []

        for skill in skills:
            block = self._build_skill_block(skill)
            if block:
                blocks.append(block)
            all_tools.extend(skill.recommended_tools)
            all_constraints.extend(skill.constraints)

        # 去重工具
        seen_tools = set()
        unique_tools = []
        for t in all_tools:
            if t not in seen_tools:
                seen_tools.add(t)
                unique_tools.append(t)

        # 去重约束
        unique_constraints = list(dict.fromkeys(all_constraints))

        system_block = ""
        if blocks:
            system_block = "## 活跃技能\n\n" + "\n\n".join(blocks)
            if unique_constraints:
                system_block += "\n\n### 全局约束\n"
                for c in unique_constraints:
                    system_block += f"- {c}\n"

        return SkillPrompt(
            system_block=system_block,
            tool_hints=unique_tools,
            constraints=unique_constraints,
        )

    def _build_skill_block(self, skill: SkillSpec) -> str:
        """为单个技能构建 prompt 块"""
        parts = [f"### {skill.icon} 技能: {skill.name}"]

        if skill.description:
            parts.append(f"_{skill.description}_")

        # 核心指令
        if skill.instruction_prompt:
            parts.append(skill.instruction_prompt)

        # 输出格式
        if skill.output_format:
            parts.append(f"\n**输出格式:**\n```\n{skill.output_format}\n```")

        # Few-shot 示例
        if skill.examples:
            parts.append("\n**示例:**")
            for i, ex in enumerate(skill.examples[:3], 1):
                inp = ex.get("input", "")
                out = ex.get("output", "")
                if inp and out:
                    parts.append(f"\n示例 {i}:")
                    parts.append(f"输入: {inp}")
                    parts.append(f"输出: {out}")

        # 工具提示
        if skill.recommended_tools:
            tools_str = ", ".join(f"`{t}`" for t in skill.recommended_tools)
            parts.append(f"\n推荐工具: {tools_str}")

        # 技能级约束
        if skill.constraints:
            parts.append("\n约束:")
            for c in skill.constraints:
                parts.append(f"  - {c}")

        return "\n".join(parts)

    def prioritize_tools(
        self,
        available_tools: List[dict],
        skill_hints: List[str],
    ) -> List[dict]:
        """
        根据技能推荐重排工具列表

        推荐的工具排在前面, 其余保持原序。
        """
        if not skill_hints:
            return available_tools

        hint_set = set(skill_hints)
        prioritized = []
        rest = []

        for tool in available_tools:
            name = tool.get("function", {}).get("name", "") if "function" in tool else tool.get("name", "")
            if name in hint_set:
                prioritized.append(tool)
            else:
                rest.append(tool)

        return prioritized + rest

    def validate_output(
        self,
        output: str,
        skill: SkillSpec,
    ) -> Dict[str, Any]:
        """
        验证输出是否符合技能的 output_format

        返回:
            {"valid": bool, "issues": [...]}
        """
        if not skill.output_format:
            return {"valid": True, "issues": []}

        issues = []
        fmt = skill.output_format.strip()

        # 检查是否要求 JSON
        if "json" in fmt.lower() or fmt.startswith("{"):
            import json as json_mod
            try:
                json_mod.loads(output)
            except (json_mod.JSONDecodeError, ValueError):
                issues.append("输出不是有效的 JSON 格式")

        # 检查必需的 section headers
        headers = re.findall(r'^#{1,3}\s+(.+)$', fmt, re.MULTILINE)
        for header in headers:
            if header.lower() not in output.lower():
                issues.append(f"缺少章节: {header}")

        # 检查必需的字段
        fields = re.findall(r'\{(\w+)\}', fmt)
        for f in fields:
            # 占位符应该已被替换
            if f"{{{f}}}" in output:
                issues.append(f"占位符未填充: {{{f}}}")

        return {"valid": len(issues) == 0, "issues": issues}

    def detect_conflicts(self, skills: Sequence[SkillSpec]) -> List[str]:
        """
        检测技能之间的潜在冲突

        例如: 两个技能都指定了不同的输出格式
        """
        conflicts = []

        # 检查输出格式冲突
        formats = [(s.name, s.output_format) for s in skills if s.output_format]
        if len(formats) > 1:
            conflicts.append(
                f"多个技能指定了输出格式: {', '.join(s[0] for s in formats)}。"
                f"将优先使用第一个 ({formats[0][0]}) 的格式。"
            )

        # 检查工具冲突 (例如 read-only 技能 vs write 技能)
        categories = set(s.category for s in skills)
        if "review" in categories and "coding" in categories:
            conflicts.append(
                "同时激活了代码审查和编码技能, "
                "AI 可能在审查和修改之间角色混淆。"
            )

        return conflicts


# ── 全局单例 ──

_engine: Optional[SkillEngine] = None


def get_skill_engine() -> SkillEngine:
    global _engine
    if _engine is None:
        _engine = SkillEngine()
    return _engine
