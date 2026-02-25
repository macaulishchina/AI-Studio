"""
设计院 (Studio) - 工作流管理 API
工作流 = 功能模块的有序组装，定义项目的完整生命周期流水线。
包含: 功能模块 CRUD + 工作流 CRUD + 内置种子数据
"""
import logging
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.database import get_db, async_session_maker
from studio.backend.models import WorkflowModule, Workflow

logger = logging.getLogger(__name__)

module_router = APIRouter(prefix="/studio-api/workflow-modules", tags=["WorkflowModules"])
workflow_router = APIRouter(prefix="/studio-api/workflows", tags=["Workflows"])


# ==================== Schemas ====================

class ModuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    display_name: str = Field(..., min_length=1, max_length=100)
    icon: str = Field("📦", max_length=10)
    description: str = Field("", max_length=500)
    component_key: str = Field(..., min_length=1, max_length=100)
    default_config: Dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0


class ModuleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    display_name: Optional[str] = Field(None, min_length=1, max_length=100)
    icon: Optional[str] = Field(None, max_length=10)
    description: Optional[str] = None
    component_key: Optional[str] = Field(None, min_length=1, max_length=100)
    default_config: Optional[Dict[str, Any]] = None
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = None


class ModuleResponse(BaseModel):
    id: int
    name: str
    display_name: str
    icon: str
    description: str
    component_key: str
    default_config: dict
    is_builtin: bool
    is_enabled: bool
    sort_order: int
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class WorkflowModuleItem(BaseModel):
    """工作流中单个模块配置"""
    module_name: str
    tab_key: str
    tab_label: str
    stage_statuses: List[str] = Field(default_factory=list)
    role_name: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)


class StageItem(BaseModel):
    key: str
    label: str
    status: str
    role: Optional[str] = None


class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    display_name: str = Field(..., min_length=1, max_length=100)
    icon: str = Field("🔄", max_length=10)
    description: str = Field("", max_length=500)
    stages: List[StageItem] = Field(default_factory=list)
    modules: List[WorkflowModuleItem] = Field(default_factory=list)
    ui_labels: Dict[str, str] = Field(default_factory=dict)
    sort_order: int = 0


class WorkflowUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    display_name: Optional[str] = Field(None, min_length=1, max_length=100)
    icon: Optional[str] = Field(None, max_length=10)
    description: Optional[str] = None
    is_enabled: Optional[bool] = None
    stages: Optional[List[StageItem]] = None
    modules: Optional[List[WorkflowModuleItem]] = None
    ui_labels: Optional[Dict[str, str]] = None
    sort_order: Optional[int] = None


class WorkflowResponse(BaseModel):
    id: int
    name: str
    display_name: str
    icon: str
    description: str
    is_builtin: bool
    is_enabled: bool
    stages: list
    modules: list
    ui_labels: dict
    sort_order: int
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


# ==================== Seed Data: Builtin Modules ====================

BUILTIN_MODULES: List[Dict[str, Any]] = [
    {
        "name": "ai_chat",
        "display_name": "AI 对话",
        "icon": "💬",
        "description": "AI 驱动的对话面板，支持技能绑定、方案输出侧栏、讨论/审查两种模式",
        "component_key": "ChatPanel",
        "default_config": {"plan_panel": True},
        "sort_order": 1,
    },
    {
        "name": "implement",
        "display_name": "代码实施",
        "icon": "🔨",
        "description": "代码实施面板，管理工作区、分支、代码变更与 AI 自动编码",
        "component_key": "ImplementPanel",
        "default_config": {},
        "sort_order": 2,
    },
    {
        "name": "deploy",
        "display_name": "部署发布",
        "icon": "🚀",
        "description": "部署面板，支持预览、合并部署、回滚和健康检查",
        "component_key": "DeployPanel",
        "default_config": {},
        "sort_order": 3,
    },
    {
        "name": "snapshot",
        "display_name": "快照管理",
        "icon": "📸",
        "description": "代码快照管理，支持备份和恢复",
        "component_key": "SnapshotPanel",
        "default_config": {},
        "sort_order": 4,
    },
]

# ==================== Seed Data: Builtin Workflows ====================

BUILTIN_WORKFLOWS: List[Dict[str, Any]] = [
    {
        "name": "requirement",
        "display_name": "需求迭代",
        "icon": "📋",
        "description": "产品需求分析、设计、实施、审查、部署的完整流程",
        "stages": [
            {"key": "draft", "label": "草稿", "status": "draft"},
            {"key": "discussing", "label": "讨论", "status": "discussing", "role": "需求分析"},
            {"key": "planned", "label": "定稿", "status": "planned"},
            {"key": "implementing", "label": "实施", "status": "implementing"},
            {"key": "reviewing", "label": "审查", "status": "reviewing", "role": "实现审查"},
            {"key": "deploying", "label": "部署", "status": "deploying"},
            {"key": "deployed", "label": "完成", "status": "deployed"},
        ],
        "modules": [
            {
                "module_name": "ai_chat",
                "tab_key": "discuss",
                "tab_label": "💬 讨论 & 设计",
                "stage_statuses": ["draft", "discussing", "planned"],
                "role_name": "需求分析",
                "config": {
                    "mode": "discuss",
                    "plan_panel": True,
                    "plan_output_noun": "需求规格书",
                    "plan_tab_label": "📋 设计稿",
                    "finalize_action": "敲定方案",
                },
            },
            {
                "module_name": "implement",
                "tab_key": "implement",
                "tab_label": "🔨 实施",
                "stage_statuses": ["implementing"],
                "config": {},
            },
            {
                "module_name": "ai_chat",
                "tab_key": "review",
                "tab_label": "💬 审查",
                "stage_statuses": ["reviewing"],
                "role_name": "实现审查",
                "config": {
                    "mode": "review",
                    "plan_panel": True,
                    "plan_output_noun": "审查报告",
                    "plan_tab_label": "📋 审查报告",
                    "finalize_action": "生成报告",
                },
            },
            {
                "module_name": "deploy",
                "tab_key": "deploy",
                "tab_label": "🚀 部署",
                "stage_statuses": ["deploying", "deployed"],
                "config": {},
            },
            {
                "module_name": "snapshot",
                "tab_key": "snapshots",
                "tab_label": "📸 快照",
                "stage_statuses": [],
                "config": {"always_visible": True},
            },
        ],
        "ui_labels": {
            "project_noun": "需求",
            "create_title": "📋 新建需求",
            "create_placeholder": "简明描述需求目标",
            "description_placeholder": "详细描述需求背景和期望效果...",
            "output_noun": "需求规格书",
            "output_tab_label": "📋 设计稿",
            "finalize_action": "敲定方案",
            "discuss_tab_label": "💬 讨论 & 设计",
            "review_output_noun": "审查报告",
            "review_tab_label": "📋 审查报告",
            "review_finalize_action": "生成报告",
            "review_discuss_tab_label": "💬 审查",
        },
        "sort_order": 1,
    },
    {
        "name": "bug",
        "display_name": "缺陷修复",
        "icon": "🔍",
        "description": "Bug 问诊、修复、验证、部署的完整流程",
        "stages": [
            {"key": "draft", "label": "报告", "status": "draft"},
            {"key": "discussing", "label": "问诊", "status": "discussing", "role": "Bug 问诊"},
            {"key": "planned", "label": "诊断书", "status": "planned"},
            {"key": "implementing", "label": "修复", "status": "implementing"},
            {"key": "reviewing", "label": "验证", "status": "reviewing", "role": "实现审查"},
            {"key": "deploying", "label": "部署", "status": "deploying"},
            {"key": "deployed", "label": "关闭", "status": "deployed"},
        ],
        "modules": [
            {
                "module_name": "ai_chat",
                "tab_key": "discuss",
                "tab_label": "💬 问诊",
                "stage_statuses": ["draft", "discussing", "planned"],
                "role_name": "Bug 问诊",
                "config": {
                    "mode": "discuss",
                    "plan_panel": True,
                    "plan_output_noun": "诊断书",
                    "plan_tab_label": "📋 诊断书",
                    "finalize_action": "生成诊断书",
                },
            },
            {
                "module_name": "implement",
                "tab_key": "implement",
                "tab_label": "🔨 修复",
                "stage_statuses": ["implementing"],
                "config": {},
            },
            {
                "module_name": "ai_chat",
                "tab_key": "review",
                "tab_label": "💬 验证",
                "stage_statuses": ["reviewing"],
                "role_name": "实现审查",
                "config": {
                    "mode": "review",
                    "plan_panel": True,
                    "plan_output_noun": "审查报告",
                    "plan_tab_label": "📋 审查报告",
                    "finalize_action": "生成报告",
                },
            },
            {
                "module_name": "deploy",
                "tab_key": "deploy",
                "tab_label": "🚀 部署",
                "stage_statuses": ["deploying", "deployed"],
                "config": {},
            },
            {
                "module_name": "snapshot",
                "tab_key": "snapshots",
                "tab_label": "📸 快照",
                "stage_statuses": [],
                "config": {"always_visible": True},
            },
        ],
        "ui_labels": {
            "project_noun": "缺陷",
            "create_title": "🐛 新建缺陷",
            "create_placeholder": "简明描述 Bug 现象",
            "description_placeholder": "描述 Bug 的具体表现、出现场景...",
            "output_noun": "诊断书",
            "output_tab_label": "📋 诊断书",
            "finalize_action": "生成诊断书",
            "discuss_tab_label": "💬 问诊",
            "review_output_noun": "审查报告",
            "review_tab_label": "📋 审查报告",
            "review_finalize_action": "生成报告",
            "review_discuss_tab_label": "💬 验证",
        },
        "sort_order": 2,
    },
]


# ==================== Seed Functions ====================

async def seed_workflow_modules():
    """种子数据: 内置功能模块 (仅插入不存在的)"""
    async with async_session_maker() as db:
        for data in BUILTIN_MODULES:
            existing = await db.execute(
                select(WorkflowModule).where(WorkflowModule.name == data["name"])
            )
            if existing.scalar_one_or_none():
                continue
            mod = WorkflowModule(is_builtin=True, is_enabled=True, **data)
            db.add(mod)
            logger.info(f"✅ 种子功能模块: {data['name']}")
        await db.commit()


async def seed_workflows():
    """种子数据: 内置工作流 (仅插入不存在的)"""
    async with async_session_maker() as db:
        for data in BUILTIN_WORKFLOWS:
            existing = await db.execute(
                select(Workflow).where(Workflow.name == data["name"])
            )
            if existing.scalar_one_or_none():
                continue
            wf = Workflow(is_builtin=True, is_enabled=True, **data)
            db.add(wf)
            logger.info(f"✅ 种子工作流: {data['name']}")
        await db.commit()


# ==================== 内存缓存 (供 project_types.py 使用) ====================

_workflow_cache: Dict[str, Dict[str, Any]] = {}


async def load_workflows_to_cache():
    """从 DB 加载所有已启用的工作流到内存缓存, 并解析 module_name → component_key"""
    global _workflow_cache
    try:
        async with async_session_maker() as db:
            # 先加载所有模块, 用于 component_key 解析
            mod_result = await db.execute(select(WorkflowModule))
            all_modules = {m.name: m for m in mod_result.scalars().all()}

            result = await db.execute(
                select(Workflow).where(Workflow.is_enabled.is_(True))
            )
            workflows = result.scalars().all()
            new_cache: Dict[str, Dict[str, Any]] = {}
            for wf in workflows:
                # 为每个 module entry 注入 component_key
                modules_resolved = []
                for mod_entry in (wf.modules or []):
                    entry = dict(mod_entry)
                    mod_def = all_modules.get(entry.get("module_name"))
                    if mod_def:
                        entry["component_key"] = mod_def.component_key
                    modules_resolved.append(entry)

                new_cache[wf.name] = {
                    "id": wf.id,
                    "name": wf.display_name,
                    "icon": wf.icon,
                    "description": wf.description,
                    "stages": wf.stages or [],
                    "modules": modules_resolved,
                    "ui_labels": wf.ui_labels or {},
                    "is_builtin": wf.is_builtin,
                }
            _workflow_cache = new_cache
            logger.info(f"✅ 工作流缓存已加载: {list(new_cache.keys())}")
    except Exception as e:
        logger.warning(f"⚠️ 加载工作流缓存失败 (将使用 hardcoded fallback): {e}")


def get_workflow_cache() -> Dict[str, Dict[str, Any]]:
    """获取工作流内存缓存 (供 project_types.py 调用)"""
    return _workflow_cache


# ==================== Module CRUD Routes ====================

@module_router.get("", response_model=List[ModuleResponse])
async def list_modules(db: AsyncSession = Depends(get_db)):
    """获取所有功能模块"""
    result = await db.execute(
        select(WorkflowModule).order_by(WorkflowModule.sort_order, WorkflowModule.id)
    )
    modules = result.scalars().all()
    return [_module_to_response(m) for m in modules]


@module_router.post("", response_model=ModuleResponse, status_code=status.HTTP_201_CREATED)
async def create_module(data: ModuleCreate, db: AsyncSession = Depends(get_db)):
    """创建功能模块"""
    existing = await db.execute(
        select(WorkflowModule).where(WorkflowModule.name == data.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"模块 '{data.name}' 已存在")

    mod = WorkflowModule(
        **data.model_dump(),
        is_builtin=False,
        is_enabled=True,
    )
    db.add(mod)
    await db.flush()
    await db.refresh(mod)
    return _module_to_response(mod)


@module_router.put("/{module_id}", response_model=ModuleResponse)
async def update_module(module_id: int, data: ModuleUpdate, db: AsyncSession = Depends(get_db)):
    """更新功能模块"""
    result = await db.execute(select(WorkflowModule).where(WorkflowModule.id == module_id))
    mod = result.scalar_one_or_none()
    if not mod:
        raise HTTPException(status_code=404, detail="模块不存在")
    if mod.is_builtin:
        raise HTTPException(status_code=403, detail="内置模块不可编辑")

    update_data = data.model_dump(exclude_unset=True)
    for k, v in update_data.items():
        setattr(mod, k, v)
    await db.flush()
    await db.refresh(mod)
    return _module_to_response(mod)


@module_router.delete("/{module_id}")
async def delete_module(module_id: int, db: AsyncSession = Depends(get_db)):
    """删除功能模块 (内置模块不可删除)"""
    result = await db.execute(select(WorkflowModule).where(WorkflowModule.id == module_id))
    mod = result.scalar_one_or_none()
    if not mod:
        raise HTTPException(status_code=404, detail="模块不存在")
    if mod.is_builtin:
        raise HTTPException(status_code=400, detail="内置模块不可删除")
    await db.delete(mod)
    return {"detail": "已删除"}


# ==================== Workflow CRUD Routes ====================

@workflow_router.get("", response_model=List[WorkflowResponse])
async def list_workflows(db: AsyncSession = Depends(get_db)):
    """获取所有工作流"""
    result = await db.execute(
        select(Workflow).order_by(Workflow.sort_order, Workflow.id)
    )
    workflows = result.scalars().all()
    return [_workflow_to_response(wf) for wf in workflows]


@workflow_router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: int, db: AsyncSession = Depends(get_db)):
    """获取单个工作流"""
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    wf = result.scalar_one_or_none()
    if not wf:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return _workflow_to_response(wf)


@workflow_router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(data: WorkflowCreate, db: AsyncSession = Depends(get_db)):
    """创建工作流"""
    existing = await db.execute(select(Workflow).where(Workflow.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"工作流 '{data.name}' 已存在")

    wf = Workflow(
        name=data.name,
        display_name=data.display_name,
        icon=data.icon,
        description=data.description,
        stages=[s.model_dump() for s in data.stages],
        modules=[m.model_dump() for m in data.modules],
        ui_labels=data.ui_labels,
        sort_order=data.sort_order,
        is_builtin=False,
        is_enabled=True,
    )
    db.add(wf)
    await db.flush()
    await db.refresh(wf)
    await load_workflows_to_cache()
    return _workflow_to_response(wf)


@workflow_router.put("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(workflow_id: int, data: WorkflowUpdate, db: AsyncSession = Depends(get_db)):
    """更新工作流"""
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    wf = result.scalar_one_or_none()
    if not wf:
        raise HTTPException(status_code=404, detail="工作流不存在")
    if wf.is_builtin:
        raise HTTPException(status_code=403, detail="内置工作流不可编辑")

    update_data = data.model_dump(exclude_unset=True)
    if "stages" in update_data and update_data["stages"] is not None:
        update_data["stages"] = [s.model_dump() if hasattr(s, 'model_dump') else s for s in update_data["stages"]]
    if "modules" in update_data and update_data["modules"] is not None:
        update_data["modules"] = [m.model_dump() if hasattr(m, 'model_dump') else m for m in update_data["modules"]]
    for k, v in update_data.items():
        setattr(wf, k, v)
    await db.flush()
    await db.refresh(wf)
    await load_workflows_to_cache()
    return _workflow_to_response(wf)


@workflow_router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: int, db: AsyncSession = Depends(get_db)):
    """删除工作流 (内置工作流不可删除)"""
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    wf = result.scalar_one_or_none()
    if not wf:
        raise HTTPException(status_code=404, detail="工作流不存在")
    if wf.is_builtin:
        raise HTTPException(status_code=400, detail="内置工作流不可删除")
    await db.delete(wf)
    await load_workflows_to_cache()
    return {"detail": "已删除"}


@workflow_router.post("/{workflow_id}/duplicate", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def duplicate_workflow(workflow_id: int, db: AsyncSession = Depends(get_db)):
    """复制工作流"""
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    src = result.scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail="工作流不存在")

    # 生成不冲突的名称
    base_name = src.name + "_copy"
    suffix = 1
    while True:
        candidate = f"{base_name}_{suffix}" if suffix > 1 else base_name
        existing = await db.execute(select(Workflow).where(Workflow.name == candidate))
        if not existing.scalar_one_or_none():
            break
        suffix += 1

    new_wf = Workflow(
        name=candidate,
        display_name=f"{src.display_name} (副本)",
        icon=src.icon,
        description=src.description,
        stages=src.stages,
        modules=src.modules,
        ui_labels=src.ui_labels,
        sort_order=src.sort_order + 1,
        is_builtin=False,
        is_enabled=True,
    )
    db.add(new_wf)
    await db.flush()
    await db.refresh(new_wf)
    await load_workflows_to_cache()
    return _workflow_to_response(new_wf)


# ==================== Helpers ====================

def _module_to_response(m: WorkflowModule) -> ModuleResponse:
    from datetime import datetime as dt
    return ModuleResponse(
        **{c.name: (str(getattr(m, c.name)) if isinstance(getattr(m, c.name), dt) else getattr(m, c.name))
           for c in m.__table__.columns}
    )


def _workflow_to_response(wf: Workflow) -> WorkflowResponse:
    from datetime import datetime as dt
    return WorkflowResponse(
        **{c.name: (str(getattr(wf, c.name)) if isinstance(getattr(wf, c.name), dt) else getattr(wf, c.name))
           for c in wf.__table__.columns}
    )
