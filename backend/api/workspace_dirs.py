"""
设计院 (Studio) - 工作目录管理 API
支持添加、删除、切换工作目录
"""
import logging
import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.config import settings
from studio.backend.core.database import get_db
from studio.backend.core.security import get_optional_studio_user
from studio.backend.models import WorkspaceDir

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/workspace-dirs", tags=["WorkspaceDirs"])


# ── Schemas ──────────────────────────────────

class WorkspaceDirCreate(BaseModel):
    path: str = Field(..., min_length=1, max_length=500, description="工作目录绝对路径")
    label: str = Field("", max_length=100, description="可选标签")


class WorkspaceDirUpdate(BaseModel):
    label: Optional[str] = Field(None, max_length=100)


class WorkspaceDirOut(BaseModel):
    id: int
    path: str
    label: str
    is_active: bool
    exists: bool = True       # 目录是否实际存在
    vcs_type: str = "none"    # 版本控制类型
    github_token_configured: bool = False
    github_repo: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ActiveWorkspaceOut(BaseModel):
    """当前活跃工作目录"""
    path: str
    label: str = ""
    source: str = "env"  # "db" | "env" — 来源是数据库还是环境变量


# ── Helpers ──────────────────────────────────

def _enrich_dir(ws: WorkspaceDir) -> dict:
    """给工作目录记录附加运行时信息"""
    d = {c.name: getattr(ws, c.name) for c in ws.__table__.columns}
    d["exists"] = os.path.isdir(ws.path)
    # 快速检测 VCS
    vcs = "none"
    if d["exists"]:
        if os.path.isdir(os.path.join(ws.path, ".git")):
            vcs = "git"
        elif os.path.isdir(os.path.join(ws.path, ".svn")):
            vcs = "svn"
        else:
            # 向上查找 .svn (SVN 1.7+)
            cur = os.path.abspath(ws.path)
            for _ in range(10):
                parent = os.path.dirname(cur)
                if parent == cur:
                    break
                if os.path.isdir(os.path.join(parent, ".svn")):
                    vcs = "svn"
                    break
                cur = parent
    d["vcs_type"] = vcs
    d["github_token_configured"] = bool(getattr(ws, "github_token", ""))
    d["github_repo"] = (getattr(ws, "github_repo", "") or None)
    return d


async def get_active_workspace_path(db: AsyncSession) -> str:
    """
    获取当前活跃的工作目录路径。
    优先级: DB 中 is_active=True > 环境变量 WORKSPACE_PATH > 默认 /workspace
    """
    result = await db.execute(
        select(WorkspaceDir).where(WorkspaceDir.is_active == True).limit(1)
    )
    active = result.scalar_one_or_none()
    if active:
        return active.path
    return settings.workspace_path


# ── Routes ───────────────────────────────────

@router.get("", response_model=List[WorkspaceDirOut])
async def list_workspace_dirs(db: AsyncSession = Depends(get_db)):
    """列出所有已配置的工作目录"""
    result = await db.execute(
        select(WorkspaceDir).order_by(WorkspaceDir.created_at)
    )
    dirs = result.scalars().all()

    # 修正历史遗留的无意义标签
    for d in dirs:
        if d.label and d.label.startswith("默认 ("):
            d.label = os.path.basename(os.path.normpath(d.path)) or d.path
            await db.flush()

    # 如果数据库为空, 自动添加环境变量中配置的工作目录
    if not dirs and settings.workspace_path and settings.workspace_path != "/workspace":
        env_ws = WorkspaceDir(
            path=settings.workspace_path,
            label=os.path.basename(os.path.normpath(settings.workspace_path)) or settings.workspace_path,
            is_active=True,
            github_token=settings.github_token or "",
            github_repo=settings.github_repo or "",
        )
        db.add(env_ws)
        await db.flush()
        await db.refresh(env_ws)
        dirs = [env_ws]

    return [_enrich_dir(d) for d in dirs]


@router.post("", response_model=WorkspaceDirOut, status_code=status.HTTP_201_CREATED)
async def add_workspace_dir(
    data: WorkspaceDirCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_optional_studio_user),
):
    """添加新的工作目录"""
    # 规范化路径
    norm_path = os.path.normpath(data.path)

    # 检查路径是否存在
    if not os.path.isdir(norm_path):
        raise HTTPException(
            status_code=400,
            detail=f"目录不存在: {norm_path}",
        )

    # 检查重复
    existing = await db.execute(
        select(WorkspaceDir).where(WorkspaceDir.path == norm_path)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"工作目录已存在: {norm_path}")

    # 如果是第一个, 自动设为活跃
    count_result = await db.execute(select(WorkspaceDir))
    is_first = len(count_result.scalars().all()) == 0

    ws = WorkspaceDir(
        path=norm_path,
        label=data.label or os.path.basename(norm_path),
        is_active=is_first,
    )
    db.add(ws)
    await db.flush()
    await db.refresh(ws)

    logger.info(f"✅ 添加工作目录: {norm_path} (label={ws.label}, active={ws.is_active})")
    return _enrich_dir(ws)


@router.post("/{dir_id}/activate", response_model=WorkspaceDirOut)
async def activate_workspace_dir(
    dir_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_optional_studio_user),
):
    """切换活跃工作目录"""
    result = await db.execute(select(WorkspaceDir).where(WorkspaceDir.id == dir_id))
    ws = result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="工作目录不存在")

    # 取消所有其他活跃状态
    await db.execute(
        update(WorkspaceDir).values(is_active=False)
    )
    # 设置当前为活跃
    ws.is_active = True
    await db.flush()
    await db.refresh(ws)

    # 同步更新运行时 settings (当前进程即时生效)
    settings.workspace_path = ws.path
    settings.github_token = ws.github_token or ""
    settings.github_repo = ws.github_repo or ""

    # 清除工作区概览缓存 (切换后需要重新扫描)
    from studio.backend.services.workspace_service import clear_overview_cache
    clear_overview_cache()

    logger.info(f"🔄 切换活跃工作目录: {ws.path}")
    return _enrich_dir(ws)


@router.patch("/{dir_id}", response_model=WorkspaceDirOut)
async def update_workspace_dir(
    dir_id: int,
    data: WorkspaceDirUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_optional_studio_user),
):
    """更新工作目录标签"""
    result = await db.execute(select(WorkspaceDir).where(WorkspaceDir.id == dir_id))
    ws = result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="工作目录不存在")

    if data.label is not None:
        ws.label = data.label

    await db.flush()
    await db.refresh(ws)
    return _enrich_dir(ws)


@router.delete("/{dir_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace_dir(
    dir_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_optional_studio_user),
):
    """删除工作目录 (不删除实际文件, 仅从配置移除)"""
    result = await db.execute(select(WorkspaceDir).where(WorkspaceDir.id == dir_id))
    ws = result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="工作目录不存在")

    was_active = ws.is_active
    await db.delete(ws)
    await db.flush()

    # 如果删除的是活跃目录, 自动激活第一个
    if was_active:
        remaining = await db.execute(
            select(WorkspaceDir).order_by(WorkspaceDir.created_at).limit(1)
        )
        next_ws = remaining.scalar_one_or_none()
        if next_ws:
            next_ws.is_active = True
            settings.workspace_path = next_ws.path
            settings.github_token = next_ws.github_token or ""
            settings.github_repo = next_ws.github_repo or ""
            await db.flush()
        else:
            # 恢复到环境变量默认值
            settings.workspace_path = os.environ.get("WORKSPACE_PATH", "/workspace")
            settings.github_token = os.environ.get("GITHUB_TOKEN", "")
            settings.github_repo = os.environ.get("GITHUB_REPO", "")

    logger.info(f"🗑️ 删除工作目录: {ws.path}")


@router.get("/active", response_model=ActiveWorkspaceOut)
async def get_active_workspace(db: AsyncSession = Depends(get_db)):
    """获取当前活跃工作目录"""
    result = await db.execute(
        select(WorkspaceDir).where(WorkspaceDir.is_active == True).limit(1)
    )
    active = result.scalar_one_or_none()
    if active:
        return ActiveWorkspaceOut(path=active.path, label=active.label, source="db")
    return ActiveWorkspaceOut(path=settings.workspace_path, label="默认", source="env")
