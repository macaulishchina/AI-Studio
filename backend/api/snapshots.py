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


# ── GitHub Token / Repo 运行时管理 ─────────────

class _GHTokenBody(BaseModel):
    token: str = Field(..., min_length=1)

class _GHRepoBody(BaseModel):
    repo: str = Field(..., min_length=1, description="owner/repo 格式")


def _mask_token(t: str) -> str:
    if not t:
        return ""
    if len(t) > 16:
        return t[:8] + "•" * 12 + t[-4:]
    return "•" * len(t)


@system_router.post("/github-token")
async def set_github_token(body: _GHTokenBody):
    """设置 / 更新 GitHub Token（运行时生效，重启后需 .env 持久化）"""
    from studio.backend.core.config import settings as _s
    _s.github_token = body.token.strip()
    return {"ok": True, "masked_token": _mask_token(_s.github_token)}


@system_router.delete("/github-token")
async def clear_github_token():
    """清除 GitHub Token（运行时）"""
    from studio.backend.core.config import settings as _s
    _s.github_token = ""
    return {"ok": True}


@system_router.post("/github-repo")
async def set_github_repo(body: _GHRepoBody):
    """设置 GitHub 仓库（运行时生效）"""
    from studio.backend.core.config import settings as _s
    _s.github_repo = body.repo.strip()
    return {"ok": True, "repo": _s.github_repo}


@system_router.delete("/github-repo")
async def clear_github_repo():
    """清除 GitHub 仓库绑定（运行时）"""
    from studio.backend.core.config import settings as _s
    _s.github_repo = ""
    return {"ok": True}


@system_router.get("/status")
async def system_status():
    """获取系统状态（含 VCS 自动检测 + 后向兼容 git 字段）"""
    import asyncio
    import subprocess as _sp
    from functools import partial as _partial

    def _decode(raw: bytes) -> str:
        if not raw:
            return ""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
        import locale
        try:
            return raw.decode(locale.getpreferredencoding(False))
        except (UnicodeDecodeError, LookupError):
            pass
        return raw.decode("utf-8", errors="replace")

    def _run_cmd_sync(cmd):
        try:
            r = _sp.run(cmd, capture_output=True, timeout=15, shell=True)
            return _decode(r.stdout).strip()
        except Exception:
            return ""

    async def run_cmd(cmd):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _partial(_run_cmd_sync, cmd))

    # Docker 容器状态
    try:
        containers = await run_cmd(
            "docker ps --format \"{{.Names}}|{{.Status}}|{{.Ports}}\""
        )
    except Exception:
        containers = ""

    # VCS 状态（自动检测 git/svn）
    from studio.backend.core.config import settings as _settings
    from studio.backend.services.workspace_service import get_workspace_vcs_info, _get_git_recent_commits, _get_svn_recent_commits, detect_vcs_type
    _ws = _settings.workspace_path
    vcs_info = {"type": "none", "branch": "", "commit": "", "commit_short": "", "commit_message": ""}
    _vt = "none"
    _recent = []
    try:
        _vt = detect_vcs_type(_ws)
        vcs_info = await get_workspace_vcs_info(_ws)
    except Exception as _e:
        logger.warning(f"VCS 检测失败: {_e}")
    try:
        if _vt == "git":
            _recent = await _get_git_recent_commits(_ws, 5)
        elif _vt == "svn":
            _recent = await _get_svn_recent_commits(_ws, 5)
    except Exception:
        pass

    # GitHub 连接 (区分: 无token / 有token无repo / 全配置)
    github_status: dict = {"connected": False}
    _has_token = bool(_settings.github_token)
    _has_repo = bool(_settings.github_repo)
    if not _has_token:
        github_status["error"] = "GitHub Token 未配置，请在下方设置或 .env 中配置 GITHUB_TOKEN"
    elif not _has_repo:
        github_status["error"] = "GitHub Token 已配置，但未绑定仓库。请在 .env 中配置 GITHUB_REPO=owner/repo"
        github_status["token_set"] = True
    else:
        from studio.backend.services import github_service
        github_status = await github_service.check_connection()

    # Token 脱敏信息
    _masked_token = _mask_token(_settings.github_token) if _has_token else ""

    return {
        "containers": [
            dict(zip(["name", "status", "ports"], c.split("|")))
            for c in containers.split("\n") if c
        ],
        # 后向兼容：保留 git 字段
        "git": {
            "branch": vcs_info.get("branch", ""),
            "recent_commits": [
                f"{c.get('hash', '')[:7]} {c.get('message', '')}"
                for c in _recent
            ],
        },
        # 新增完整 VCS 字段
        "vcs": vcs_info,
        "github": {**github_status, "masked_token": _masked_token, "repo_configured": _has_repo, "repo": _settings.github_repo or None},
    }


@system_router.get("/workspace-overview")
async def workspace_overview(force_refresh: bool = False):
    """
    获取工作区概览：VCS 信息 / 语言统计 / 关键文件 / 贡献者 / 近期提交。
    首次请求较慢（需扫描文件系统），之后 60 秒缓存。
    """
    from studio.backend.services.workspace_service import get_workspace_overview
    return await get_workspace_overview(force_refresh=force_refresh)
