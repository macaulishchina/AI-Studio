"""
设计院 (Studio) - 快照管理 API
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.database import get_db
from studio.backend.models import Snapshot
from studio.backend.services import snapshot_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/snapshots", tags=["Snapshots"])


# ==================== Schemas ====================

class SnapshotOut(BaseModel):
    id: int
    project_id: Optional[int]
    git_commit: str
    git_tag: str
    docker_image_tags: dict
    db_backup_path: str
    description: str
    is_healthy: bool
    created_at: datetime

    class Config:
        from_attributes = True


class SnapshotCreate(BaseModel):
    description: str = Field("手动快照", max_length=500)
    project_id: Optional[int] = None


class RollbackRequest(BaseModel):
    restore_db: bool = Field(False, description="是否同时恢复数据库")


# ==================== Routes ====================

@router.get("", response_model=List[SnapshotOut])
async def list_snapshots(
    project_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    """获取快照列表"""
    query = select(Snapshot).order_by(Snapshot.created_at.desc())
    if project_id is not None:
        query = query.where(Snapshot.project_id == project_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("", response_model=SnapshotOut)
async def create_snapshot_manual(
    data: SnapshotCreate,
    db: AsyncSession = Depends(get_db),
):
    """手动创建快照"""
    snapshot = await snapshot_service.create_snapshot(
        db, description=data.description, project_id=data.project_id
    )
    return snapshot


@router.get("/{snapshot_id}", response_model=SnapshotOut)
async def get_snapshot(snapshot_id: int, db: AsyncSession = Depends(get_db)):
    """获取快照详情"""
    result = await db.execute(select(Snapshot).where(Snapshot.id == snapshot_id))
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        raise HTTPException(status_code=404, detail="快照不存在")
    return snapshot


@router.post("/{snapshot_id}/rollback")
async def rollback(
    snapshot_id: int,
    data: RollbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """回滚到指定快照"""
    result = await snapshot_service.rollback_to_snapshot(
        db, snapshot_id, restore_db=data.restore_db
    )
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "回滚失败"))
    return result


# ==================== 系统状态 ====================

system_router = APIRouter(prefix="/studio-api/system", tags=["System"])


@system_router.get("/status")
async def system_status():
    """获取系统状态"""
    import asyncio

    async def run_cmd(cmd):
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip()

    # Docker 容器状态
    containers = await run_cmd(
        "docker ps --format '{{.Names}}|{{.Status}}|{{.Ports}}' 2>/dev/null"
    )

    # Git 状态
    from studio.backend.core.config import settings as _settings
    _ws = _settings.workspace_path
    git_branch = await run_cmd(f"git -C {_ws} branch --show-current 2>/dev/null")
    git_log = await run_cmd(f"git -C {_ws} log --oneline -5 2>/dev/null")

    # GitHub 连接 (仅在配置了 GitHub 时检查)
    github_status = {"connected": False, "error": "GitHub 未配置"}
    if _settings.github_repo and _settings.github_token:
        from studio.backend.services import github_service
        github_status = await github_service.check_connection()

    return {
        "containers": [
            dict(zip(["name", "status", "ports"], c.split("|")))
            for c in containers.split("\n") if c
        ],
        "git": {
            "branch": git_branch,
            "recent_commits": git_log.split("\n") if git_log else [],
        },
        "github": github_status,
    }
