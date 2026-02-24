"""
设计院 (Studio) - 技能管理 API
Skill = 可复用的 AI 能力模块 (区别于 Role 的人设/策略)

主流定义: Skill 包含 instruction_prompt (核心指令) + output_format (输出格式)
         + examples (少样本) + constraints (约束) + recommended_tools

与 Role 的关系:
  Role → 定义 AI 是谁 (persona + strategy)
  Skill → 定义 AI 会什么 (capability)
  一个 Role 可挂载多个 Skill, Workflow stage 也可指定 Skills
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.database import get_db, async_session_maker
from studio.backend.models import Skill

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/skills", tags=["Skills"])


# ==================== Schemas ====================

class SkillCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    icon: str = Field("⚡", max_length=10)
    description: str = Field("", max_length=500)
    category: str = Field("general", max_length=50)
    instruction_prompt: str = Field("")
    output_format: str = Field("")
    examples: list = Field(default_factory=list)
    constraints: list = Field(default_factory=list)
    recommended_tools: list = Field(default_factory=list)
    tags: list = Field(default_factory=list)
    sort_order: int = 0


class SkillUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    icon: Optional[str] = Field(None, max_length=10)
    description: Optional[str] = None
    category: Optional[str] = None
    is_enabled: Optional[bool] = None
    instruction_prompt: Optional[str] = None
    output_format: Optional[str] = None
    examples: Optional[list] = None
    constraints: Optional[list] = None
    recommended_tools: Optional[list] = None
    tags: Optional[list] = None
    sort_order: Optional[int] = None


class SkillResponse(BaseModel):
    id: int
    name: str
    icon: str
    description: str
    category: str
    is_builtin: bool
    is_enabled: bool
    instruction_prompt: str
    output_format: str
    examples: list
    constraints: list
    recommended_tools: list
    tags: list
    sort_order: int
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


# ==================== Seed Data ====================

BUILTIN_SKILLS = [
    {
        "name": "需求澄清",
        "icon": "🔎",
        "description": "通过结构化提问澄清模糊需求，确保需求完整、可验证",
        "category": "analysis",
        "is_builtin": True,
        "is_enabled": True,
        "sort_order": 0,
        "instruction_prompt": """## 需求澄清技能

你正在帮助用户把一个模糊的想法变成清晰、可操作的需求。

### 方法论
1. **5W1H 框架** — 对每个需求追问:
   - Who: 谁是目标用户？谁会受影响？
   - What: 具体要做什么？边界在哪？
   - When: 什么时候触发？有时间约束吗？
   - Where: 在哪个页面/模块/环境？
   - Why: 为什么需要这个？解决什么痛点？
   - How: 用户如何操作？有多少步骤？

2. **用户故事格式** — 将需求转化为:
   「作为 [角色]，我希望 [做什么]，以便 [达到什么目的]」

3. **边界探测** — 主动追问:
   - 异常流程: 失败了怎么办？数据不完整呢？
   - 权限控制: 谁能操作？谁不能？
   - 性能约束: 数据量大时怎么办？并发呢？
   - 兼容性: 需要支持哪些端/浏览器？

4. **优先级排序** — 使用 MoSCoW 方法:
   - Must have (必须有)
   - Should have (应该有)
   - Could have (可以有)
   - Won't have (本次不做)""",
        "output_format": """### 需求清单

| # | 用户故事 | 优先级 | 验收标准 | 备注 |
|---|---------|--------|---------|------|
| 1 | 作为...我希望...以便... | Must | ✅ 条件1 ✅ 条件2 | |

### 待确认事项
- [ ] 问题1
- [ ] 问题2""",
        "examples": [],
        "constraints": [
            "不要替用户做技术决策",
            "每个需求必须有明确的验收标准",
            "优先使用 ask_user 工具提问，不要猜测",
        ],
        "recommended_tools": ["ask_user"],
        "tags": ["需求", "分析", "提问"],
    },
    {
        "name": "API 设计",
        "icon": "🔌",
        "description": "设计 RESTful API 端点、请求/响应结构和错误处理",
        "category": "coding",
        "is_builtin": True,
        "is_enabled": True,
        "sort_order": 1,
        "instruction_prompt": """## API 设计技能

你正在帮助设计清晰、一致的 RESTful API。

### 设计原则
1. **资源导向** — URL 表示资源而非操作: `/users/{id}` 而非 `/getUser`
2. **HTTP 语义** — 正确使用 GET/POST/PUT/PATCH/DELETE
3. **一致的命名** — snake_case 字段名，复数资源名
4. **分页与过滤** — 列表接口支持 `page`, `per_page`, `sort`, 过滤参数
5. **错误格式** — 统一 `{"detail": "message", "code": "ERROR_CODE"}`
6. **版本策略** — 是否需要 `/v1/` 前缀

### 输出要求
- 每个端点包含: 方法、路径、描述、请求体、响应体、状态码
- 字段类型明确 (string, integer, boolean, array, object)
- 必填/可选字段标注
- 包含认证要求说明""",
        "output_format": """### API 端点设计

#### `POST /api/resource`
- **描述**: 创建资源
- **认证**: Bearer Token
- **请求体**:
```json
{
  "name": "string (必填)",
  "description": "string (选填)"
}
```
- **响应** (201):
```json
{
  "id": 1,
  "name": "...",
  "created_at": "ISO8601"
}
```
- **错误**: 400 验证失败, 401 未认证, 409 重复""",
        "examples": [],
        "constraints": [
            "遵循 RESTful 最佳实践",
            "字段命名使用 snake_case",
            "所有示例使用 JSON 格式",
        ],
        "recommended_tools": ["read_file", "search_text"],
        "tags": ["API", "REST", "设计", "后端"],
    },
    {
        "name": "代码审查",
        "icon": "🔍",
        "description": "审查代码质量、安全性、性能和可维护性",
        "category": "review",
        "is_builtin": True,
        "is_enabled": True,
        "sort_order": 2,
        "instruction_prompt": """## 代码审查技能

你正在对代码变更进行专业审查。

### 审查维度
1. **正确性** — 逻辑是否正确？是否处理了边界情况？
2. **安全性** — SQL 注入、XSS、敏感数据暴露、权限检查？
3. **性能** — N+1 查询、不必要的循环、内存泄漏？
4. **可读性** — 命名清晰？注释充分？函数长度合理？
5. **可维护性** — DRY 原则？职责单一？耦合度？
6. **测试覆盖** — 关键路径是否有测试？

### 审查方法
- 先查看文件结构，理解改动范围
- 逐文件审查，标注行号和严重级别
- 区分: 🔴 必须修复 / 🟡 建议改进 / 🟢 建议(可选)
- 对每个问题给出具体的改进建议""",
        "output_format": """### 审查结论: ✅ 通过 / ⚠️ 有条件通过 / ❌ 需修改

### 问题列表

| # | 严重级别 | 文件:行号 | 问题描述 | 建议 |
|---|---------|----------|---------|------|
| 1 | 🔴 必须修复 | `file.py:42` | 未检查空值 | 添加 null check |
| 2 | 🟡 建议 | `api.py:15` | 命名不清晰 | 改为 xxx |

### 改进建议
- 整体建议1
- 整体建议2""",
        "examples": [],
        "constraints": [
            "审查前必须先用工具读取相关代码",
            "每个问题必须标注具体文件和行号",
            "区分严重级别，避免所有问题一视同仁",
            "给出具体可操作的修改建议",
        ],
        "recommended_tools": ["read_file", "search_text", "get_file_tree"],
        "tags": ["代码", "审查", "质量"],
    },
    {
        "name": "测试用例设计",
        "icon": "🧪",
        "description": "根据需求设计全面的测试用例，覆盖正常流程和边界条件",
        "category": "testing",
        "is_builtin": True,
        "is_enabled": True,
        "sort_order": 3,
        "instruction_prompt": """## 测试用例设计技能

你正在根据需求或代码设计测试用例。

### 方法论
1. **等价类划分** — 将输入分为有效/无效等价类
2. **边界值分析** — 测试边界条件 (min, max, min-1, max+1)
3. **状态转换** — 测试状态机的各种路径
4. **错误猜测** — 基于经验预测容易出错的场景
5. **组合测试** — 多个参数组合（使用 pairwise 方法减少组合数）

### 用例结构
- 每个用例有: ID、标题、前置条件、步骤、预期结果、优先级
- 覆盖: 正常流程 → 异常流程 → 边界条件 → 性能/并发
- 按功能模块分组""",
        "output_format": """### 测试用例

#### 模块: [功能名称]

| ID | 用例标题 | 优先级 | 前置条件 | 步骤 | 预期结果 |
|----|---------|--------|---------|------|---------|
| TC-001 | 正常创建 | P0 | 已登录 | 1. 填写表单 2. 点击提交 | 创建成功，返回详情 |
| TC-002 | 必填项为空 | P0 | 已登录 | 1. 不填名称 2. 点击提交 | 提示"请输入名称" |

### 覆盖统计
- 正常流程: X 个
- 异常流程: X 个
- 边界条件: X 个""",
        "examples": [],
        "constraints": [
            "P0 用例覆盖核心功能的正常和异常流程",
            "边界值必须包含 null/空字符串/超长/特殊字符",
            "并发场景至少设计一个用例",
        ],
        "recommended_tools": ["read_file", "search_text"],
        "tags": ["测试", "QA", "质量"],
    },
    {
        "name": "技术方案评估",
        "icon": "⚖️",
        "description": "对比多种技术方案的优劣，给出选型建议",
        "category": "analysis",
        "is_builtin": True,
        "is_enabled": True,
        "sort_order": 4,
        "instruction_prompt": """## 技术方案评估技能

你正在帮助用户评估和对比多种技术方案。

### 评估框架
1. **可行性** — 技术上能实现吗？团队有能力吗？
2. **成本** — 开发时间、维护成本、学习曲线
3. **性能** — 能满足需求吗？扩展性如何？
4. **生态** — 社区活跃度、文档质量、第三方集成
5. **风险** — 技术风险、锁定风险、安全风险
6. **未来** — 技术趋势、升级路径、向后兼容

### 评估方法
- 列出所有候选方案
- 定义评估维度和权重
- 逐维度打分 (1-5)
- 计算加权总分
- 给出推荐和理由""",
        "output_format": """### 方案对比

| 维度 | 权重 | 方案A | 方案B | 方案C |
|------|------|-------|-------|-------|
| 开发成本 | 30% | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| 性能 | 25% | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| 可维护性 | 20% | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| 生态 | 15% | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ |
| 风险 | 10% | ⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ |
| **加权总分** | | **X.X** | **X.X** | **X.X** |

### 推荐: 方案A
理由: ...""",
        "examples": [],
        "constraints": [
            "至少对比 2 个方案",
            "评估维度要有明确权重",
            "最终推荐要有充分理由",
            "列出每个方案的主要风险",
        ],
        "recommended_tools": ["read_file", "search_text"],
        "tags": ["技术", "评估", "选型"],
    },
    {
        "name": "文档撰写",
        "icon": "📝",
        "description": "生成结构化的技术文档、用户指南或设计文档",
        "category": "writing",
        "is_builtin": True,
        "is_enabled": True,
        "sort_order": 5,
        "instruction_prompt": """## 文档撰写技能

你正在帮助生成清晰、结构化的文档。

### 写作原则
1. **读者导向** — 根据目标读者调整技术深度和用语
2. **结构清晰** — 从概述到细节，逐层展开
3. **示例丰富** — 每个概念配合代码示例或使用场景
4. **可检索** — 良好的标题层级、关键词标注
5. **保持更新** — 标注文档版本和最后更新时间

### 文档类型
- **技术设计文档**: 架构、数据流、接口定义
- **API 文档**: 端点、参数、响应、错误码
- **用户指南**: 功能介绍、操作步骤、FAQ
- **变更日志**: 版本号、变更内容、影响范围""",
        "output_format": "",
        "examples": [],
        "constraints": [
            "标题层级不超过 4 级",
            "代码示例必须可运行",
            "关键术语首次出现时给出解释",
        ],
        "recommended_tools": ["read_file", "search_text", "get_file_tree"],
        "tags": ["文档", "写作", "技术"],
    },
]

# 技能分类定义
SKILL_CATEGORIES = {
    "general": {"name": "通用", "icon": "⚡"},
    "analysis": {"name": "分析", "icon": "🔎"},
    "coding": {"name": "编码", "icon": "💻"},
    "writing": {"name": "写作", "icon": "📝"},
    "review": {"name": "审查", "icon": "🔍"},
    "testing": {"name": "测试", "icon": "🧪"},
}


# ==================== Helper ====================

def _skill_to_response(skill: Skill) -> dict:
    return {
        "id": skill.id,
        "name": skill.name,
        "icon": skill.icon,
        "description": skill.description,
        "category": skill.category or "general",
        "is_builtin": skill.is_builtin,
        "is_enabled": skill.is_enabled,
        "instruction_prompt": skill.instruction_prompt or "",
        "output_format": skill.output_format or "",
        "examples": skill.examples or [],
        "constraints": skill.constraints or [],
        "recommended_tools": skill.recommended_tools or [],
        "tags": skill.tags or [],
        "sort_order": skill.sort_order or 0,
        "created_at": skill.created_at.isoformat() if skill.created_at else "",
        "updated_at": skill.updated_at.isoformat() if skill.updated_at else "",
    }


# ==================== Endpoints ====================

@router.get("", response_model=List[SkillResponse])
async def list_skills(
    enabled_only: bool = False,
    category: str = None,
    db: AsyncSession = Depends(get_db),
):
    """列出所有技能"""
    q = select(Skill).order_by(Skill.sort_order, Skill.id)
    if enabled_only:
        q = q.where(Skill.is_enabled.is_(True))
    if category:
        q = q.where(Skill.category == category)
    result = await db.execute(q)
    skills = result.scalars().all()
    return [_skill_to_response(s) for s in skills]


@router.get("/categories")
async def list_categories():
    """获取技能分类定义"""
    return SKILL_CATEGORIES


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(skill_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="技能不存在")
    return _skill_to_response(skill)


@router.post("", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
async def create_skill(data: SkillCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Skill).where(Skill.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"技能名称 '{data.name}' 已存在")
    skill = Skill(**data.model_dump())
    db.add(skill)
    await db.flush()
    await db.refresh(skill)
    return _skill_to_response(skill)


@router.put("/{skill_id}", response_model=SkillResponse)
async def update_skill(skill_id: int, data: SkillUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="技能不存在")
    update_dict = data.model_dump(exclude_unset=True)
    # 名称唯一性检查
    if "name" in update_dict and update_dict["name"] != skill.name:
        dup = await db.execute(select(Skill).where(Skill.name == update_dict["name"]))
        if dup.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"技能名称 '{update_dict['name']}' 已存在")
    for k, v in update_dict.items():
        setattr(skill, k, v)
    await db.flush()
    await db.refresh(skill)
    return _skill_to_response(skill)


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(skill_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="技能不存在")
    if skill.is_builtin:
        raise HTTPException(status_code=403, detail="内置技能不可删除")
    await db.delete(skill)


@router.post("/{skill_id}/duplicate", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
async def duplicate_skill(skill_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="技能不存在")
    # 生成不重复名称
    base_name = f"{source.name} (副本)"
    suffix = 1
    while True:
        name = base_name if suffix == 1 else f"{base_name} {suffix}"
        dup = await db.execute(select(Skill).where(Skill.name == name))
        if not dup.scalar_one_or_none():
            break
        suffix += 1
    new_skill = Skill(
        name=name,
        icon=source.icon,
        description=source.description,
        category=source.category,
        is_builtin=False,
        is_enabled=source.is_enabled,
        instruction_prompt=source.instruction_prompt,
        output_format=source.output_format,
        examples=source.examples or [],
        constraints=source.constraints or [],
        recommended_tools=source.recommended_tools or [],
        tags=source.tags or [],
        sort_order=source.sort_order + 1,
    )
    db.add(new_skill)
    await db.flush()
    await db.refresh(new_skill)
    return _skill_to_response(new_skill)


# ==================== Seed ====================

async def seed_skills():
    """初始化内置技能 (幂等)"""
    async with async_session_maker() as db:
        for skill_data in BUILTIN_SKILLS:
            result = await db.execute(
                select(Skill).where(Skill.name == skill_data["name"])
            )
            existing = result.scalar_one_or_none()
            if not existing:
                skill = Skill(**skill_data)
                db.add(skill)
                logger.info(f"✅ 创建内置技能: {skill_data['name']}")
            else:
                # 更新内置技能的部分字段 (保留用户修改的 is_enabled, description)
                for key in ("instruction_prompt", "output_format", "examples",
                            "constraints", "recommended_tools", "tags",
                            "icon", "category", "sort_order"):
                    if key in skill_data:
                        setattr(existing, key, skill_data[key])
        await db.commit()
