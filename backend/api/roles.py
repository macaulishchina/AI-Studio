"""
设计院 (Studio) - 对话角色管理 API
数据驱动的 AI 工作流配置 CRUD
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.database import get_db, async_session_maker
from studio.backend.models import Role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/roles", tags=["Roles"])


# ==================== Schemas ====================

class StageItem(BaseModel):
    key: str
    label: str
    status: str


class RoleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    icon: str = Field("🎯", max_length=10)
    description: str = Field("", max_length=500)
    role_prompt: str = Field("")
    strategy_prompt: str = Field("")
    tool_strategy_prompt: str = Field("")
    finalization_prompt: str = Field("")
    output_generation_prompt: str = Field("")
    stages: List[StageItem] = Field(default_factory=list)
    ui_labels: dict = Field(default_factory=dict)
    default_skills: List[str] = Field(default_factory=list)
    sort_order: int = 0


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    icon: Optional[str] = Field(None, max_length=10)
    description: Optional[str] = None
    is_enabled: Optional[bool] = None
    role_prompt: Optional[str] = None
    strategy_prompt: Optional[str] = None
    tool_strategy_prompt: Optional[str] = None
    finalization_prompt: Optional[str] = None
    output_generation_prompt: Optional[str] = None
    stages: Optional[List[StageItem]] = None
    ui_labels: Optional[dict] = None
    default_skills: Optional[List[str]] = None
    sort_order: Optional[int] = None


class RoleResponse(BaseModel):
    id: int
    name: str
    icon: str
    description: str
    is_builtin: bool
    is_enabled: bool
    role_prompt: str
    strategy_prompt: str
    tool_strategy_prompt: str
    finalization_prompt: str
    output_generation_prompt: str
    stages: list
    ui_labels: dict
    default_skills: list
    sort_order: int
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class RoleSummary(BaseModel):
    """精简版, 用于项目列表/创建选择器"""
    id: int
    name: str
    icon: str
    description: str
    is_builtin: bool
    is_enabled: bool
    stages: list
    ui_labels: dict
    default_skills: list
    sort_order: int

    class Config:
        from_attributes = True


# ==================== Seed Data ====================

BUILTIN_ROLES = [
    {
        "name": "需求分析",
        "icon": "📋",
        "description": "与用户讨论产品需求，澄清边界，生成需求规格书",
        "is_builtin": True,
        "is_enabled": True,
        "sort_order": 0,
        "role_prompt": "你是一位资深产品经理和需求分析师，正在「设计院」中和用户讨论一个产品需求。",
        "strategy_prompt": """## 核心原则：需求探讨优先，实现细节靠后

你的首要任务是帮助用户把需求想清楚、说明白，而不是急于给出技术方案。

### 对话策略
1. **主动提问** — 用户描述需求后，立即用 `ask_user` 工具提出 3-5 个关键问题来澄清需求。不要等用户问你，你应该主动追问。
2. **聚焦「做什么」** — 讨论应围绕：用户故事、交互流程、业务规则、边界条件、优先级。避免主动讨论技术实现细节（数据库设计、API 路径等），除非用户明确要求。
3. **连续提问** — 如果一轮回答后仍有不明确的地方，继续用 `ask_user` 追问。宁可多问几轮，也不要带着模糊需求就敲定方案。
4. **总结确认** — 每轮问答后，简要总结你对需求的理解，让用户确认或纠正。
5. **循循善诱** — 帮助用户发现他们没想到的需求场景，如：异常流程、权限控制、数据一致性、并发场景。

### ⚠️ 绝对禁止的行为
- **禁止"预告式回复"**：不要说"好的，让我问几个问题："、"让我继续问…"然后就停止。如果你想提问，必须在**同一次回复中直接调用 `ask_user` 工具**。
- **禁止等待用户许可才提问**：不要说"需要我继续问吗？"或"你希望我深入哪个方面？"——直接调用 `ask_user` 提问。
- **禁止无工具的纯确认回复**：不要用纯文字说"让我确认一下"然后停下来等用户回复。如果要确认，直接用 `ask_user` 列出确认问题。

### 什么时候讨论技术
- ✅ 用户主动问"这个用什么技术实现"时
- ✅ 需要查看代码来理解现有功能时
- ✅ 技术约束会影响需求可行性时（如实时推送需要 WebSocket）
- ❌ 不要主动建议数据库表结构、API 设计、组件拆分等
- ❌ 不要在用户只描述了大概想法时就给出完整技术方案""",
        "finalization_prompt": """## 关于敲定方案
当用户说"敲定"时，系统会自动基于讨论历史生成需求规格书（Plan）。
你不需要在对话中输出 Plan 格式，只需确保讨论充分、需求明确即可。
在敲定之前，你应该主动确认：所有关键需求是否都已讨论清楚。""",
        "output_generation_prompt": """基于以下讨论内容，生成一份结构化的 **需求规格书（Plan）**。

## 写作原则

1. **聚焦「做什么」而非「怎么做」**：详细描述功能需求、业务规则、用户交互流程、边界条件、验收标准。不要给出具体的技术实现方案（如数据库表结构、API 路径设计、组件拆分方式），除非用户在讨论中明确要求了特定实现方式。
2. **保留用户的明确技术决策**：如果用户在讨论中主动提出了技术选型、架构约束或实现偏好，必须原样保留并标注为「用户指定」。
3. **需求要可验证**：每个功能点应有明确的完成标准，让实现者能判断"做到了没有"。
4. **消除歧义**：对讨论中模糊或有多种理解的地方，选择最合理的解释并明确写出，或标注为「待确认」。
5. **不要添加臆测**：严格基于讨论内容，不添加讨论中未涉及的功能或技术假设。

## 输出格式

### 项目概述
一段话描述项目目标和核心价值。

### 功能需求
按优先级分组，每个功能包含：
- **功能名称**
- **用户故事**: 作为 [角色]，我希望 [做什么]，以便 [达到什么目的]
- **详细描述**: 具体的交互流程、业务规则
- **边界条件**: 异常情况如何处理
- **验收标准**: 可检验的完成条件列表

### 非功能需求
性能、安全、兼容性等约束（仅包含讨论中提及的）。

### 用户指定的技术约束
仅列出用户在讨论中**主动要求**的技术决策（如指定某框架、某种数据格式等）。如果没有，写「无特定技术约束，由实现者自行决定最佳方案」。

### 待确认事项
讨论中未完全明确的问题。

---

讨论内容：
{discussion_summary}

请直接输出需求规格书内容（不需要代码块包裹）:""",
        "stages": [],
        "ui_labels": {},
        "default_skills": ["需求澄清"],
    },
    {
        "name": "Bug 问诊",
        "icon": "🔍",
        "description": "像医生问诊一样定位 Bug 症状，形成诊断书，不提供解决方案",
        "is_builtin": True,
        "is_enabled": True,
        "sort_order": 1,
        "role_prompt": """你是一位经验丰富的 Bug 诊断专家，正在「设计院」中帮助用户梳理一个软件缺陷。

## 核心身份：诊断医生，只问诊不施救
你的职责是通过系统化问诊，帮助用户把 Bug 的症状、特性、复现方式描述清楚，形成一份结构化的「诊断书」。
**你绝对不要**：
- 猜测或下结论说 Bug 的根因是什么
- 提供任何修复方案、代码补丁、或解决建议
- 说"可能是因为..."、"建议修改..."之类的话

诊断书的目的是交给下游更强的编码模型去定位和修复，你只负责把问题描述到位。""",
        "strategy_prompt": """## 问诊策略

### 对话策略
1. **症状采集** — 用户报告 Bug 后，立即用 `ask_user` 提出 3-5 个定位问题：
   - 具体的错误现象是什么？（截图、报错信息、异常行为）
   - 期望的正确行为是什么？
   - 在什么条件下出现？（页面、操作步骤、数据状态）
   - 是否稳定复现？复现步骤是什么？
   - 什么时候开始出现的？最近有什么变更？
2. **环境信息** — 追问运行环境：浏览器、设备、网络、数据库状态等
3. **复现验证** — 要求用户确认复现步骤，确保步骤完整且可重复
4. **边界探测** — 追问边界条件：
   - 其他类似操作是否正常？
   - 换一组数据是否还是出问题？
   - 清缓存/重启后是否还复现？
5. **只记不判** — 记录所有症状，但不做原因推断

### 绝对禁区
- ❌ 不要说"这个问题可能是因为..."
- ❌ 不要说"建议你修改..."
- ❌ 不要给出任何代码片段作为修复方案
- ❌ 不要说"试试这样做..."
- ✅ 可以用工具查看代码来**理解现有逻辑**，但查看后只用于完善问题描述
- ✅ 可以说"我注意到相关代码在 xxx 文件"来帮助定位范围""",
        "finalization_prompt": """## 关于生成诊断书
当用户说"敲定"或"出诊断书"时，系统会自动基于问诊记录生成结构化诊断书。
在定稿之前，你应该主动确认：
- Bug 的症状描述是否完整
- 复现步骤是否清晰可执行
- 影响范围是否明确""",
        "output_generation_prompt": """基于以下问诊记录，生成一份结构化的 **Bug 诊断书**。

## 写作原则
1. **只描述症状，不分析原因**：详细描述"是什么"和"怎么复现"，绝不推测"为什么"。
2. **步骤可执行**：复现步骤必须精确到可以让另一个人按步操作复现。
3. **信息分层**：从概述到细节，结构清晰。
4. **严格基于问诊内容**：不添加问诊中未涉及的信息。

## 输出格式

### Bug 概述
一段话描述：什么功能、出了什么问题、影响范围。

### 症状描述
- **预期行为**: 应该怎样
- **实际行为**: 实际怎样
- **错误信息**: 控制台报错、页面提示等（如有）

### 复现步骤
编号列表，每步包含：具体操作 + 预期结果 + 实际结果

### 环境信息
- 浏览器/设备/系统
- 数据条件
- 网络环境

### 影响范围
- 影响的功能模块
- 影响的用户群体
- 严重程度评估

### 相关代码定位
- 相关文件路径（仅当问诊中使用了代码查看工具时列出）
- 不包含任何修复建议

### 补充信息
问诊中发现的其他相关线索。

---

问诊记录：
{discussion_summary}

请直接输出诊断书内容（不需要代码块包裹）:""",
        "stages": [],
        "ui_labels": {},
        "default_skills": [],
    },
    {
        "name": "实现审查",
        "icon": "✅",
        "description": "对照需求规格书逐项检查代码实现完成度，输出审查报告",
        "is_builtin": True,
        "is_enabled": True,
        "sort_order": 2,
        "role_prompt": """你是一位严谨的代码审查员，正在「设计院」中帮助用户对照需求逐项审查代码实现。

## 核心身份：实现完成度检查员
你的职责是拿着需求规格书/设计方案，逐项对照代码，检查每个需求点是否已正确实现。
**你应该**：
- 系统化地逐项检查需求是否被实现
- 指出实现缺失、部分实现、或与需求不符之处
- 评估代码质量（错误处理、边界条件、安全风险等）
**你不应该**：
- 替用户编写修复代码
- 凭空添加需求文档中没有的检查项""",
        "strategy_prompt": """## 审查策略

### 对话策略
1. **收集材料** — 首先用 `ask_user` 了解审查范围：
   - 需求文档/设计稿在哪里？（可能已在项目 Plan 里）
   - 需要审查哪些模块的实现？
   - 是否有特别关注的风险点？
2. **阅读代码** — 使用 `read_file`、`search_text` 等工具系统地查看代码实现
3. **逐项对照** — 基于需求文档，逐个功能点检查：
   - ✅ 功能是否实现
   - ⚠️ 实现是否完整（边界条件、错误处理）
   - ❌ 是否有遗漏
4. **追问澄清** — 遇到不确定的地方，用 `ask_user` 询问用户：
   - "这个功能的预期行为是 X 还是 Y？"
   - "这里的业务规则具体是怎样的？"
5. **分层报告** — 按严重程度分类问题：
   - 🔴 Critical: 功能完全缺失或逻辑错误
   - 🟡 Warning: 实现不完整或存在潜在风险
   - 🟢 Pass: 功能正确实现

### 代码查看策略
- ✅ 主动使用工具查看代码文件
- ✅ 搜索关键函数/类来验证实现
- ✅ 检查前后端是否一致
- ✅ 检查数据库模型是否匹配需求""",
        "finalization_prompt": """## 关于生成审查报告
当用户说"敲定"或"出报告"时，系统会自动基于审查记录生成结构化审查报告。
在定稿之前，你应该主动确认：
- 所有需求条目是否都已逐一审查
- 发现的问题是否都已记录
- 是否有需要特别关注的风险点""",
        "output_generation_prompt": """基于以下审查记录，生成一份结构化的 **实现审查报告**。

## 写作原则
1. **逐项对照**：每个需求点都必须有明确的审查结论（通过/部分实现/未实现）。
2. **有据可查**：每个结论都应引用具体的代码位置或证据。
3. **按严重程度分级**：清晰标注问题的严重等级。
4. **严格基于审查内容**：不添加审查中未涉及的检查项。

## 输出格式

### 审查概述
一段话描述：审查范围、总体完成度评估、关键发现摘要。

### 审查结果总览
| 状态 | 数量 |
|------|------|
| ✅ 通过 | X 项 |
| ⚠️ 部分实现 | X 项 |
| ❌ 未实现 | X 项 |

### 逐项审查详情

#### ✅ 已通过
按编号列出：需求描述 + 实现位置 + 审查结论

#### ⚠️ 部分实现
按编号列出：
- **需求描述**: ...
- **已实现部分**: ...
- **缺失部分**: ...
- **相关代码**: 文件路径和行号
- **建议**: ...

#### ❌ 未实现
按编号列出：
- **需求描述**: ...
- **预期实现**: ...
- **当前状态**: 完全缺失/逻辑错误/...

### 代码质量备注
审查过程中发现的代码质量问题（非功能性）：
- 错误处理不足
- 安全风险
- 性能隐患
- 代码规范

### 总体建议
基于审查结果的优先级建议。

---

审查记录：
{discussion_summary}

请直接输出审查报告内容（不需要代码块包裹）:""",
        "stages": [],
        "ui_labels": {},
        "default_skills": ["代码审查"],
    },
]


async def seed_roles():
    """初始化内置角色种子数据"""
    async with async_session_maker() as db:
        for role_data in BUILTIN_ROLES:
            result = await db.execute(
                select(Role).where(Role.name == role_data["name"])
            )
            existing = result.scalar_one_or_none()
            if not existing:
                role = Role(**role_data)
                db.add(role)
                logger.info(f"✅ 创建内置角色: {role_data['name']}")
            else:
                # 更新内置角色的 prompt (保持最新)
                for key in ["role_prompt", "strategy_prompt", "tool_strategy_prompt",
                            "finalization_prompt", "output_generation_prompt",
                            "stages", "ui_labels", "icon", "description",
                            "default_skills"]:
                    if key in role_data:
                        setattr(existing, key, role_data[key])
                logger.info(f"🔄 更新内置角色: {role_data['name']}")
        await db.commit()


# ==================== Helper ====================

def _role_to_response(role: Role) -> RoleResponse:
    return RoleResponse(
        id=role.id,
        name=role.name,
        icon=role.icon or "🎯",
        description=role.description or "",
        is_builtin=role.is_builtin or False,
        is_enabled=role.is_enabled if role.is_enabled is not None else True,
        role_prompt=role.role_prompt or "",
        strategy_prompt=role.strategy_prompt or "",
        tool_strategy_prompt=role.tool_strategy_prompt or "",
        finalization_prompt=role.finalization_prompt or "",
        output_generation_prompt=role.output_generation_prompt or "",
        stages=role.stages or [],
        ui_labels=role.ui_labels or {},
        default_skills=role.default_skills or [],
        sort_order=role.sort_order or 0,
        created_at=role.created_at.isoformat() + "Z" if role.created_at else "",
        updated_at=role.updated_at.isoformat() + "Z" if role.updated_at else "",
    )


# ==================== Routes ====================

@router.get("", response_model=List[RoleResponse])
async def list_roles(
    enabled_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """列出所有角色"""
    query = select(Role).order_by(Role.sort_order, Role.id)
    if enabled_only:
        query = query.where(Role.is_enabled.is_(True))
    result = await db.execute(query)
    roles = result.scalars().all()
    return [_role_to_response(r) for r in roles]


@router.get("/{role_id}", response_model=RoleResponse)
async def get_role(role_id: int, db: AsyncSession = Depends(get_db)):
    """获取角色详情"""
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    return _role_to_response(role)


@router.post("", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(data: RoleCreate, db: AsyncSession = Depends(get_db)):
    """创建自定义角色"""
    # 检查名称唯一
    existing = await db.execute(select(Role).where(Role.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"角色名「{data.name}」已存在")

    role = Role(
        name=data.name,
        icon=data.icon,
        description=data.description,
        is_builtin=False,
        is_enabled=True,
        role_prompt=data.role_prompt,
        strategy_prompt=data.strategy_prompt,
        tool_strategy_prompt=data.tool_strategy_prompt,
        finalization_prompt=data.finalization_prompt,
        output_generation_prompt=data.output_generation_prompt,
        stages=[s.model_dump() for s in data.stages],
        ui_labels=data.ui_labels,
        sort_order=data.sort_order,
    )
    db.add(role)
    await db.flush()
    await db.refresh(role)
    return _role_to_response(role)


@router.put("/{role_id}", response_model=RoleResponse)
async def update_role(role_id: int, data: RoleUpdate, db: AsyncSession = Depends(get_db)):
    """更新角色配置"""
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")

    update_data = data.model_dump(exclude_unset=True)

    # 检查名称唯一
    if "name" in update_data and update_data["name"] != role.name:
        existing = await db.execute(select(Role).where(Role.name == update_data["name"]))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"角色名「{update_data['name']}」已存在")

    # stages 需要转成 dict list
    if "stages" in update_data and update_data["stages"] is not None:
        update_data["stages"] = [s.model_dump() if hasattr(s, 'model_dump') else s for s in update_data["stages"]]

    for key, value in update_data.items():
        setattr(role, key, value)

    await db.flush()
    await db.refresh(role)
    return _role_to_response(role)


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(role_id: int, db: AsyncSession = Depends(get_db)):
    """删除角色（内置角色不可删除）"""
    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    if role.is_builtin:
        raise HTTPException(status_code=403, detail="内置角色不可删除")
    await db.delete(role)


@router.post("/{role_id}/duplicate", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def duplicate_role(role_id: int, db: AsyncSession = Depends(get_db)):
    """复制角色"""
    result = await db.execute(select(Role).where(Role.id == role_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="角色不存在")

    # 生成不重复的名称
    base_name = f"{source.name} (副本)"
    name = base_name
    counter = 2
    while True:
        existing = await db.execute(select(Role).where(Role.name == name))
        if not existing.scalar_one_or_none():
            break
        name = f"{base_name} {counter}"
        counter += 1

    new_role = Role(
        name=name,
        icon=source.icon,
        description=source.description,
        is_builtin=False,
        is_enabled=True,
        role_prompt=source.role_prompt,
        strategy_prompt=source.strategy_prompt,
        tool_strategy_prompt=source.tool_strategy_prompt,
        finalization_prompt=source.finalization_prompt,
        output_generation_prompt=source.output_generation_prompt,
        stages=source.stages,
        ui_labels=source.ui_labels,
        sort_order=source.sort_order + 1,
    )
    db.add(new_role)
    await db.flush()
    await db.refresh(new_role)
    return _role_to_response(new_role)
