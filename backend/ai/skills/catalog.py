"""
Skills — 技能目录管理

管理内置技能种子数据 + 技能查询。
将 api/skills.py 中的 seed 逻辑抽象到此处。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from studio.backend.ai.skills.engine import SkillSpec

logger = logging.getLogger(__name__)

# ── 内置技能种子 ──

BUILTIN_SKILLS: List[dict] = [
    {
        "name": "需求澄清",
        "icon": "🔍",
        "category": "analysis",
        "description": "通过结构化追问帮助用户明确和细化需求",
        "instruction_prompt": (
            "你正在执行需求澄清技能。请遵循以下方法论:\n"
            "1. 仔细阅读用户的需求描述\n"
            "2. 识别模糊、矛盾或缺失的点\n"
            "3. 按优先级提出澄清问题 (最多 5 个)\n"
            "4. 每个问题应包含: 问题本身 + 为什么需要澄清 + 可能的选项\n"
            "5. 根据用户回答更新需求理解"
        ),
        "output_format": (
            "## 需求理解\n{requirement_summary}\n\n"
            "## 待澄清问题\n1. {question}\n   - 原因: {reason}\n   - 选项: {options}\n\n"
            "## 假设 (需确认)\n- {assumption}"
        ),
        "constraints": ["不要自行假设关键业务决策", "保持问题简洁明了"],
        "recommended_tools": ["read_file", "search_text"],
        "tags": ["需求", "分析", "沟通"],
    },
    {
        "name": "API 设计",
        "icon": "🔌",
        "category": "coding",
        "description": "设计 RESTful API 端点和数据模型",
        "instruction_prompt": (
            "你正在执行 API 设计技能。请遵循以下流程:\n"
            "1. 分析需求，识别需要的资源 (Resource)\n"
            "2. 为每个资源设计 CRUD + 自定义端点\n"
            "3. 定义请求/响应 Schema\n"
            "4. 考虑分页、过滤、错误处理\n"
            "5. 评审一致性和 RESTful 风格"
        ),
        "output_format": (
            "## API 端点设计\n\n"
            "### {resource}\n"
            "| Method | Path | Description | Request | Response |\n"
            "|--------|------|-------------|---------|----------|\n"
            "| {method} | {path} | {desc} | {req} | {res} |"
        ),
        "constraints": ["遵循 RESTful 命名规范", "错误响应使用统一格式", "包含版本前缀"],
        "recommended_tools": ["read_file", "search_text", "list_directory"],
        "tags": ["API", "设计", "REST"],
    },
    {
        "name": "代码审查",
        "icon": "👁️",
        "category": "review",
        "description": "审查代码质量、安全性和最佳实践",
        "instruction_prompt": (
            "你正在执行代码审查技能。请按以下维度审查:\n"
            "1. **正确性**: 逻辑是否正确，边界条件是否处理\n"
            "2. **安全性**: SQL 注入、XSS、路径遍历等风险\n"
            "3. **性能**: 是否有 N+1、内存泄漏、不必要的计算\n"
            "4. **可读性**: 命名、注释、代码组织\n"
            "5. **架构**: 是否符合项目规范，是否有过度设计\n\n"
            "使用工具读取相关代码文件后再审查。"
        ),
        "output_format": (
            "## 代码审查报告\n\n"
            "### 总评\n{overview}\n\n"
            "### 问题列表\n"
            "| # | 严重度 | 文件:行 | 问题 | 建议 |\n"
            "|---|--------|---------|------|------|\n"
            "| {n} | {severity} | {location} | {issue} | {suggestion} |"
        ),
        "examples": [
            {
                "input": "审查 backend/api/projects.py",
                "output": "## 代码审查报告\n\n### 总评\n整体质量良好...\n\n### 问题列表\n| # | 严重度 | 文件:行 | 问题 | 建议 |\n|---|--------|---------|------|------|\n| 1 | 中 | projects.py:45 | SQL 未参数化 | 使用 SQLAlchemy ORM |",
            }
        ],
        "constraints": ["必须先读取代码再审查", "按严重度排序问题", "提供具体的修复建议"],
        "recommended_tools": ["read_file", "search_text", "list_directory", "get_file_tree"],
        "tags": ["审查", "质量", "安全"],
    },
    {
        "name": "测试用例设计",
        "icon": "🧪",
        "category": "testing",
        "description": "设计全面的测试用例覆盖方案",
        "instruction_prompt": (
            "你正在执行测试用例设计技能:\n"
            "1. 分析被测功能的所有分支和边界条件\n"
            "2. 采用等价类 + 边界值分析方法\n"
            "3. 包含正向、反向、异常测试\n"
            "4. 为关键路径设计端到端场景\n"
            "5. 估算优先级和测试时间"
        ),
        "output_format": (
            "## 测试用例\n\n"
            "| ID | 类型 | 描述 | 输入 | 预期结果 | 优先级 |\n"
            "|----|----- |------|------|----------|--------|\n"
            "| TC-{n} | {type} | {desc} | {input} | {expected} | {priority} |"
        ),
        "constraints": ["覆盖所有主要分支", "包含至少一个性能测试场景"],
        "recommended_tools": ["read_file", "search_text"],
        "tags": ["测试", "质量"],
    },
    {
        "name": "技术方案评估",
        "icon": "⚖️",
        "category": "analysis",
        "description": "多维度评估技术方案的可行性和风险",
        "instruction_prompt": (
            "你正在执行技术方案评估技能:\n"
            "1. 理解方案目标和约束条件\n"
            "2. 从以下维度评估:\n"
            "   - 技术可行性 (现有技术栈兼容性)\n"
            "   - 开发成本 (人力 × 时间)\n"
            "   - 性能影响 (延迟、吞吐、资源)\n"
            "   - 维护成本 (复杂度、依赖)\n"
            "   - 风险 (技术风险、业务风险)\n"
            "3. 如有替代方案，进行对比\n"
            "4. 给出明确建议"
        ),
        "output_format": (
            "## 方案评估\n\n"
            "### 评估维度\n"
            "| 维度 | 评分(1-5) | 说明 |\n"
            "|------|-----------|------|\n"
            "| {dimension} | {score} | {explanation} |\n\n"
            "### 风险项\n- {risk}\n\n"
            "### 建议\n{recommendation}"
        ),
        "constraints": ["必须量化评估", "风险项需标注概率和影响"],
        "recommended_tools": ["read_file", "search_text", "get_file_tree"],
        "tags": ["评估", "架构", "决策"],
    },
    {
        "name": "文档撰写",
        "icon": "📝",
        "category": "writing",
        "description": "撰写清晰、结构化的技术文档",
        "instruction_prompt": (
            "你正在执行文档撰写技能:\n"
            "1. 确定文档类型 (API 文档/设计文档/用户指南/README)\n"
            "2. 使用适当的 Markdown 格式\n"
            "3. 包含: 概述、快速开始、详细说明、FAQ\n"
            "4. 代码示例必须可运行\n"
            "5. 适当使用表格、流程图"
        ),
        "constraints": ["代码示例必须完整可运行", "使用中文撰写", "段落不超过 5 行"],
        "recommended_tools": ["read_file", "search_text", "get_file_tree"],
        "tags": ["文档", "写作"],
    },
]


async def load_skills_for_role(
    role_id: Optional[int] = None,
    skill_ids: Optional[List[int]] = None,
) -> List[SkillSpec]:
    """
    加载角色绑定的技能列表

    Args:
        role_id: 角色 ID (从 role.default_skills 获取 skill IDs)
        skill_ids: 直接指定的技能 ID 列表

    Returns:
        SkillSpec 列表
    """
    from studio.backend.core.database import async_session_maker
    from studio.backend.models import Skill, Role
    from sqlalchemy import select

    ids_to_load = list(skill_ids or [])

    # 从角色获取技能 ID
    if role_id and not ids_to_load:
        async with async_session_maker() as session:
            role = await session.get(Role, role_id)
            if role and role.default_skills:
                ids_to_load = role.default_skills

    if not ids_to_load:
        return []

    # 加载技能
    async with async_session_maker() as session:
        result = await session.execute(
            select(Skill).where(Skill.id.in_(ids_to_load), Skill.is_enabled == True)
        )
        skills = result.scalars().all()
        return [SkillSpec.from_orm(s) for s in skills]


async def list_available_skills(
    category: Optional[str] = None,
    enabled_only: bool = True,
) -> List[SkillSpec]:
    """获取所有可用技能"""
    from studio.backend.core.database import async_session_maker
    from studio.backend.models import Skill
    from sqlalchemy import select

    async with async_session_maker() as session:
        stmt = select(Skill)
        if enabled_only:
            stmt = stmt.where(Skill.is_enabled == True)
        if category:
            stmt = stmt.where(Skill.category == category)
        stmt = stmt.order_by(Skill.sort_order)
        result = await session.execute(stmt)
        skills = result.scalars().all()
        return [SkillSpec.from_orm(s) for s in skills]
