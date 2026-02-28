"""
上下文源 — 角色源

从 Role / Skill 注入 AI 人设、策略、提示词。
"""
from __future__ import annotations

import logging
from typing import Any, List

from ..builder import BaseContextSource, ContextSection

logger = logging.getLogger(__name__)

# 默认反伪造头
ANTI_FABRICATION_HEADER = (
    "⚠️ 你可以调用提供的工具(function calling)来执行命令、读取文件等操作。\n"
    "严禁在文本中编造或伪造命令执行结果，你必须通过 tool_call 调用工具来获取真实结果。\n"
    "如果你需要执行命令，请使用 run_command 工具。\n\n"
)

# 默认工具使用策略
DEFAULT_TOOL_STRATEGY = (
    "## 工具使用策略\n"
    "你可以使用以下工具来精准获取项目信息:\n"
    "1. **ask_user**: 需要澄清需求时向用户提问\n"
    "2. **get_file_tree**: 获取项目目录结构 (建议对话开始时调用一次)\n"
    "3. **search_text**: 搜索代码 (务必指定 include_pattern)\n"
    "4. **read_file**: 读取文件 (配合 search_text 的行号使用 start_line)\n"
    "5. **list_directory**: 查看目录详细内容\n"
    "6. **run_command**: 执行命令 (只读命令直接执行, 写命令需授权)\n\n"
    "⚠️ 调用工具后等待真实结果再继续，不要提前编造结果。\n"
)


class RoleContextSource(BaseContextSource):
    """角色/策略上下文源"""
    name = "role"
    priority = 10  # 最高优先级

    async def gather(self, budget_tokens: int, **kwargs) -> List[ContextSection]:
        role = kwargs.get("role")
        skills = kwargs.get("skills", [])
        tool_permissions = kwargs.get("tool_permissions")
        project_title = kwargs.get("project_title", "")
        project_description = kwargs.get("project_description", "")

        sections = []

        # 1. 反伪造头 (如果有命令执行工具)
        if tool_permissions and ("execute_readonly_command" in tool_permissions or "execute_command" in tool_permissions):
            sections.append(ContextSection(
                name="安全规则",
                content=ANTI_FABRICATION_HEADER,
                priority=0,
                trimmable=False,
            ))

        # 2. 角色人设
        role_prompt = ""
        if role:
            role_prompt = getattr(role, "system_prompt", "") or ""
        if not role_prompt:
            role_prompt = "你是一个专业的 AI 助手，帮助用户分析和解决问题。"
        sections.append(ContextSection(
            name="角色人设",
            content=role_prompt,
            priority=5,
            trimmable=False,
        ))

        # 3. 项目基本信息
        if project_title:
            project_info = f"## 当前项目\n- 名称: {project_title}"
            if project_description:
                project_info += f"\n- 描述: {project_description}"
            sections.append(ContextSection(
                name="项目信息",
                content=project_info,
                priority=15,
                trimmable=True,
            ))

        # 4. 工具策略
        tool_strategy = ""
        if role and hasattr(role, "tool_strategy_prompt") and role.tool_strategy_prompt:
            tool_strategy = role.tool_strategy_prompt
        else:
            tool_strategy = DEFAULT_TOOL_STRATEGY
        if tool_strategy:
            sections.append(ContextSection(
                name="工具策略",
                content=tool_strategy,
                priority=20,
                trimmable=True,
            ))

        # 5. 技能注入 (通过 SkillEngine 完整组装)
        if skills:
            from studio.backend.ai.skills.engine import SkillSpec, get_skill_engine
            engine = get_skill_engine()
            specs = []
            for skill in skills:
                try:
                    specs.append(SkillSpec.from_orm(skill))
                except Exception:
                    # fallback: 旧式纯 prompt 注入
                    name = getattr(skill, "name", "")
                    instruction = getattr(skill, "instruction_prompt", "") or ""
                    if instruction:
                        specs.append(SkillSpec(
                            id=getattr(skill, "id", 0),
                            name=name,
                            instruction_prompt=instruction,
                        ))
            if specs:
                skill_prompt = engine.compose(specs)
                if skill_prompt.system_block:
                    sections.append(ContextSection(
                        name="活跃技能",
                        content=skill_prompt.system_block,
                        priority=25,
                        trimmable=True,
                    ))

        return sections
