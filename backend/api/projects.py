"""
设计院 (Studio) - 项目管理 API
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, func, distinct
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from studio.backend.core.database import get_db
from studio.backend.core.security import get_optional_studio_user
from studio.backend.core.project_types import (
    get_project_type, get_all_project_types, get_stages, get_ui_labels,
    get_modules, DEFAULT_PROJECT_TYPE,
)
from studio.backend.models import Project, ProjectStatus, Message, Role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/projects", tags=["Projects"])


# ==================== Helpers ====================

class ProjectTypeInfo(BaseModel):
    """项目类型信息 (内嵌到项目响应)"""
    key: str
    name: str
    icon: str
    stages: list
    ui_labels: dict
    modules: list = []


def _build_project_type_info(project) -> Optional[ProjectTypeInfo]:
    """从项目的 project_type 构建类型信息"""
    type_key = getattr(project, 'project_type', None) or DEFAULT_PROJECT_TYPE
    pt = get_project_type(type_key)
    if not pt:
        return None
    return ProjectTypeInfo(
        key=type_key,
        name=pt["name"],
        icon=pt["icon"],
        stages=pt.get("stages", []),
        ui_labels=pt.get("ui_labels", {}),
        modules=pt.get("modules", []),
    )


# ==================== Schemas ====================

class ProjectCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=5000)
    discussion_model: str = Field("gpt-4o")
    project_type: str = Field(DEFAULT_PROJECT_TYPE)


class ProjectUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    status: Optional[ProjectStatus] = None
    plan_content: Optional[str] = None
    review_content: Optional[str] = None
    discussion_model: Optional[str] = None
    implementation_model: Optional[str] = None
    tool_permissions: Optional[List[str]] = None
    is_archived: Optional[bool] = None


class ProjectSummary(BaseModel):
    id: int
    title: str
    description: str
    status: ProjectStatus
    project_type: Optional[str] = None
    github_issue_number: Optional[int] = None
    github_pr_number: Optional[int] = None
    branch_name: Optional[str] = None
    discussion_model: str
    implementation_model: str
    tool_permissions: Optional[List[str]] = None
    is_archived: bool = False
    archived_at: Optional[datetime] = None
    created_by: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    participants: List[str] = Field(default_factory=list, description="参与讨论的用户列表")
    # 项目类型信息
    type_info: Optional[ProjectTypeInfo] = None
    # 角色关联
    role_id: Optional[int] = None
    role: Optional[dict] = None

    class Config:
        from_attributes = True


class ProjectDetail(ProjectSummary):
    plan_content: str
    plan_version: int
    review_content: str = ""
    review_version: int = 0
    preview_port: Optional[int] = None
    workspace_dir: Optional[str] = None
    iteration_count: int = 0


# ==================== Routes ====================

@router.post("", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
async def create_project(data: ProjectCreate, db: AsyncSession = Depends(get_db), user: dict = Depends(get_optional_studio_user)):
    """创建需求项目"""
    # 验证 project_type
    pt = get_project_type(data.project_type)
    if not pt:
        raise HTTPException(status_code=400, detail=f"未知的项目类型: {data.project_type}")

    project = Project(
        title=data.title,
        description=data.description,
        discussion_model=data.discussion_model,
        project_type=data.project_type,
        status=ProjectStatus.draft,
        created_by=user.get("username", "admin") if user else "admin",
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)

    type_info = _build_project_type_info(project)

    return ProjectDetail(
        **{c.name: getattr(project, c.name) for c in project.__table__.columns},
        message_count=0,
        participants=[],
        type_info=type_info,
        role=None,
    )


@router.get("", response_model=List[ProjectSummary])
async def list_projects(
    status_filter: Optional[ProjectStatus] = None,
    include_archived: bool = False,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """项目列表"""
    query = select(Project)
    if not include_archived:
        query = query.where(Project.is_archived.is_(False))
    if status_filter:
        query = query.where(Project.status == status_filter)
    query = query.order_by(Project.updated_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    projects = result.scalars().all()

    # 批量获取所有项目的消息数量和参与者
    project_ids = [p.id for p in projects]
    if project_ids:
        # 消息数量
        msg_counts_result = await db.execute(
            select(Message.project_id, func.count(Message.id))
            .where(Message.project_id.in_(project_ids))
            .group_by(Message.project_id)
        )
        msg_counts = dict(msg_counts_result.all())

        # 参与者: 仅人类用户消息 (role=user), 排除系统名称
        participants_result = await db.execute(
            select(Message.project_id, Message.sender_name)
            .where(
                Message.project_id.in_(project_ids),
                Message.role == "user",
                Message.sender_name != "",
                Message.sender_name.isnot(None),
            )
            .distinct()
        )
        participants_map: dict[int, list[str]] = {}
        for pid, name in participants_result.all():
            participants_map.setdefault(pid, []).append(name)
    else:
        msg_counts = {}
        participants_map = {}

    summaries = []
    for p in projects:
        type_info = _build_project_type_info(p)
        summaries.append(ProjectSummary(
            **{c.name: getattr(p, c.name) for c in p.__table__.columns},
            message_count=msg_counts.get(p.id, 0),
            participants=participants_map.get(p.id, []),
            type_info=type_info,
            role=None,
        ))
    return summaries


@router.get("/{project_id}", response_model=ProjectDetail)
async def get_project(project_id: int, db: AsyncSession = Depends(get_db)):
    """获取项目详情"""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    msg_count_result = await db.execute(
        select(func.count(Message.id)).where(Message.project_id == project.id)
    )
    msg_count = msg_count_result.scalar() or 0

    # 参与者
    participants_result = await db.execute(
        select(distinct(Message.sender_name))
        .where(
            Message.project_id == project.id,
            Message.role == "user",
            Message.sender_name != "",
            Message.sender_name.isnot(None),
        )
    )
    participants = [r[0] for r in participants_result.all()]

    type_info = _build_project_type_info(project)

    return ProjectDetail(
        **{c.name: getattr(project, c.name) for c in project.__table__.columns},
        message_count=msg_count,
        participants=participants,
        type_info=type_info,
        role=None,
    )


@router.patch("/{project_id}", response_model=ProjectDetail)
async def update_project(
    project_id: int,
    data: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新项目"""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    update_data = data.model_dump(exclude_unset=True)

    # 状态跳转验证: 不允许跳过阶段
    if "status" in update_data:
        from studio.backend.core.project_types import validate_stage_transition
        type_key = getattr(project, 'project_type', None) or 'requirement'
        current_status = project.status.value if hasattr(project.status, 'value') else str(project.status)
        new_status = update_data["status"]
        if hasattr(new_status, 'value'):
            new_status = new_status.value
        ok, err_msg = validate_stage_transition(type_key, current_status, str(new_status))
        if not ok:
            raise HTTPException(status_code=400, detail=err_msg)

    # 如果更新 plan_content, 自增版本号
    if "plan_content" in update_data and update_data["plan_content"] != project.plan_content:
        project.plan_version += 1

    # 如果更新 review_content, 自增版本号
    if "review_content" in update_data and update_data["review_content"] != project.review_content:
        project.review_version += 1

    # 归档时间戳维护
    if "is_archived" in update_data:
        archive_value = bool(update_data["is_archived"])
        if archive_value and not project.is_archived:
            project.archived_at = datetime.utcnow()
        elif not archive_value and project.is_archived:
            project.archived_at = None

    for key, value in update_data.items():
        setattr(project, key, value)

    project.updated_at = datetime.utcnow()
    await db.flush()
    await db.refresh(project)

    msg_count_result = await db.execute(
        select(func.count(Message.id)).where(Message.project_id == project.id)
    )
    msg_count = msg_count_result.scalar() or 0

    # 参与者
    participants_result = await db.execute(
        select(distinct(Message.sender_name))
        .where(
            Message.project_id == project.id,
            Message.role == "user",
            Message.sender_name != "",
            Message.sender_name.isnot(None),
        )
    )
    participants = [r[0] for r in participants_result.all()]

    type_info = _build_project_type_info(project)

    return ProjectDetail(
        **{c.name: getattr(project, c.name) for c in project.__table__.columns},
        message_count=msg_count,
        participants=participants,
        type_info=type_info,
        role=None,
    )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: int, db: AsyncSession = Depends(get_db)):
    """删除项目"""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    await db.delete(project)


# ==================== 项目类型 API ====================

@router.get("/types/list")
async def list_project_types():
    """获取所有项目类型配置"""
    return get_all_project_types()
