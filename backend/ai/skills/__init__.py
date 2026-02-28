"""
Skills 子系统

模块:
  - engine: 技能执行引擎 (组合/优先级/验证)
  - catalog: 技能目录管理 (内置种子/加载/查询)
"""
from studio.backend.ai.skills.engine import (
    SkillSpec, SkillPrompt, SkillEngine, get_skill_engine,
)
from studio.backend.ai.skills.catalog import (
    BUILTIN_SKILLS, load_skills_for_role, list_available_skills,
)

__all__ = [
    "SkillSpec", "SkillPrompt", "SkillEngine", "get_skill_engine",
    "BUILTIN_SKILLS", "load_skills_for_role", "list_available_skills",
]
