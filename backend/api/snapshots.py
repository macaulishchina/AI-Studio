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
from studio.backend.models import Snapshot, WorkspaceDir
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


class _GitProviderBody(BaseModel):
    provider: str = Field(..., description="github | gitlab")


class _GitLabTokenBody(BaseModel):
    token: str = Field(..., min_length=1)


class _GitLabRepoBody(BaseModel):
    repo: str = Field(..., min_length=1, description="namespace/project 格式")


class _GitLabUrlBody(BaseModel):
    url: str = Field(..., min_length=1, description="如 https://gitlab.com")


def _mask_token(t: str) -> str:
    if not t:
        return ""
    if len(t) > 16:
        return t[:8] + "•" * 12 + t[-4:]
    return "•" * len(t)


@system_router.post("/github-token")
async def set_github_token(body: _GHTokenBody, db: AsyncSession = Depends(get_db)):
    """设置 / 更新 GitHub Token（绑定到当前活跃工作目录）"""
    from studio.backend.core.config import settings as _s
    token = body.token.strip()
    row = (await db.execute(select(WorkspaceDir).where(WorkspaceDir.is_active == True).limit(1))).scalar_one_or_none()
    if row:
        row.github_token = token
        _s.github_token = token
        return {
            "ok": True,
            "masked_token": _mask_token(token),
            "scope": "workspace",
            "workspace_id": row.id,
            "workspace_path": row.path,
        }

    # 无活跃目录时回退到进程内全局设置（临时）
    _s.github_token = token
    return {"ok": True, "masked_token": _mask_token(_s.github_token), "scope": "runtime"}


@system_router.delete("/github-token")
async def clear_github_token(db: AsyncSession = Depends(get_db)):
    """清除 GitHub Token（当前活跃工作目录）"""
    from studio.backend.core.config import settings as _s
    row = (await db.execute(select(WorkspaceDir).where(WorkspaceDir.is_active == True).limit(1))).scalar_one_or_none()
    if row:
        row.github_token = ""
        _s.github_token = ""
        return {"ok": True, "scope": "workspace", "workspace_id": row.id, "workspace_path": row.path}
    _s.github_token = ""
    return {"ok": True, "scope": "runtime"}


@system_router.post("/github-repo")
async def set_github_repo(body: _GHRepoBody, db: AsyncSession = Depends(get_db)):
    """设置 GitHub 仓库（绑定到当前活跃工作目录）"""
    from studio.backend.core.config import settings as _s
    repo = body.repo.strip()
    row = (await db.execute(select(WorkspaceDir).where(WorkspaceDir.is_active == True).limit(1))).scalar_one_or_none()
    if row:
        row.github_repo = repo
        _s.github_repo = repo
        return {"ok": True, "repo": repo, "scope": "workspace", "workspace_id": row.id, "workspace_path": row.path}

    _s.github_repo = repo
    return {"ok": True, "repo": _s.github_repo, "scope": "runtime"}


@system_router.delete("/github-repo")
async def clear_github_repo(db: AsyncSession = Depends(get_db)):
    """清除 GitHub 仓库绑定（当前活跃工作目录）"""
    from studio.backend.core.config import settings as _s
    row = (await db.execute(select(WorkspaceDir).where(WorkspaceDir.is_active == True).limit(1))).scalar_one_or_none()
    if row:
        row.github_repo = ""
        _s.github_repo = ""
        return {"ok": True, "scope": "workspace", "workspace_id": row.id, "workspace_path": row.path}
    _s.github_repo = ""
    return {"ok": True, "scope": "runtime"}


@system_router.post("/git-provider")
async def set_git_provider(body: _GitProviderBody, db: AsyncSession = Depends(get_db)):
    """设置 Git 平台（github/gitlab），绑定当前活跃工作目录。"""
    from studio.backend.core.config import settings as _s
    provider = (body.provider or "").strip().lower()
    if provider not in {"github", "gitlab"}:
        raise HTTPException(status_code=400, detail="provider 仅支持 github 或 gitlab")

    row = (await db.execute(select(WorkspaceDir).where(WorkspaceDir.is_active == True).limit(1))).scalar_one_or_none()
    if row:
        row.git_provider = provider
        _s.git_provider = provider
        return {
            "ok": True,
            "provider": provider,
            "scope": "workspace",
            "workspace_id": row.id,
            "workspace_path": row.path,
        }

    _s.git_provider = provider
    return {"ok": True, "provider": provider, "scope": "runtime"}


@system_router.post("/gitlab-token")
async def set_gitlab_token(body: _GitLabTokenBody, db: AsyncSession = Depends(get_db)):
    """设置 / 更新 GitLab Token（绑定到当前活跃工作目录）"""
    from studio.backend.core.config import settings as _s
    token = body.token.strip()
    row = (await db.execute(select(WorkspaceDir).where(WorkspaceDir.is_active == True).limit(1))).scalar_one_or_none()
    if row:
        row.gitlab_token = token
        _s.gitlab_token = token
        return {
            "ok": True,
            "masked_token": _mask_token(token),
            "scope": "workspace",
            "workspace_id": row.id,
            "workspace_path": row.path,
        }
    _s.gitlab_token = token
    return {"ok": True, "masked_token": _mask_token(_s.gitlab_token), "scope": "runtime"}


@system_router.delete("/gitlab-token")
async def clear_gitlab_token(db: AsyncSession = Depends(get_db)):
    from studio.backend.core.config import settings as _s
    row = (await db.execute(select(WorkspaceDir).where(WorkspaceDir.is_active == True).limit(1))).scalar_one_or_none()
    if row:
        row.gitlab_token = ""
        _s.gitlab_token = ""
        return {"ok": True, "scope": "workspace", "workspace_id": row.id, "workspace_path": row.path}
    _s.gitlab_token = ""
    return {"ok": True, "scope": "runtime"}


@system_router.post("/gitlab-repo")
async def set_gitlab_repo(body: _GitLabRepoBody, db: AsyncSession = Depends(get_db)):
    """设置 GitLab 仓库（namespace/project，绑定当前活跃工作目录）"""
    from studio.backend.core.config import settings as _s
    repo = body.repo.strip()
    row = (await db.execute(select(WorkspaceDir).where(WorkspaceDir.is_active == True).limit(1))).scalar_one_or_none()
    if row:
        row.gitlab_repo = repo
        _s.gitlab_repo = repo
        return {"ok": True, "repo": repo, "scope": "workspace", "workspace_id": row.id, "workspace_path": row.path}

    _s.gitlab_repo = repo
    return {"ok": True, "repo": _s.gitlab_repo, "scope": "runtime"}


@system_router.delete("/gitlab-repo")
async def clear_gitlab_repo(db: AsyncSession = Depends(get_db)):
    from studio.backend.core.config import settings as _s
    row = (await db.execute(select(WorkspaceDir).where(WorkspaceDir.is_active == True).limit(1))).scalar_one_or_none()
    if row:
        row.gitlab_repo = ""
        _s.gitlab_repo = ""
        return {"ok": True, "scope": "workspace", "workspace_id": row.id, "workspace_path": row.path}
    _s.gitlab_repo = ""
    return {"ok": True, "scope": "runtime"}


@system_router.post("/gitlab-url")
async def set_gitlab_url(body: _GitLabUrlBody, db: AsyncSession = Depends(get_db)):
    """设置 GitLab 实例地址（默认 https://gitlab.com）"""
    from studio.backend.core.config import settings as _s
    url = body.url.strip().rstrip("/")
    row = (await db.execute(select(WorkspaceDir).where(WorkspaceDir.is_active == True).limit(1))).scalar_one_or_none()
    if row:
        row.gitlab_url = url
        _s.gitlab_url = url
        return {"ok": True, "url": url, "scope": "workspace", "workspace_id": row.id, "workspace_path": row.path}

    _s.gitlab_url = url
    return {"ok": True, "url": _s.gitlab_url, "scope": "runtime"}


@system_router.delete("/gitlab-url")
async def clear_gitlab_url(db: AsyncSession = Depends(get_db)):
    from studio.backend.core.config import settings as _s
    row = (await db.execute(select(WorkspaceDir).where(WorkspaceDir.is_active == True).limit(1))).scalar_one_or_none()
    if row:
        row.gitlab_url = "https://gitlab.com"
        _s.gitlab_url = "https://gitlab.com"
        return {"ok": True, "scope": "workspace", "workspace_id": row.id, "workspace_path": row.path}
    _s.gitlab_url = "https://gitlab.com"
    return {"ok": True, "scope": "runtime"}


@system_router.post("/svn-validate")
async def validate_svn_config():
    """验证 SVN 配置可用性（基于当前环境变量）。"""
    import subprocess
    from studio.backend.core.config import settings as _s

    repo_url = (_s.svn_repo_url or "").strip()
    if not repo_url:
        return {"ok": False, "message": "未配置 SVN_REPO_URL"}

    cmd = ["svn", "info", repo_url]
    if _s.svn_username:
        cmd += ["--username", _s.svn_username]
    if _s.svn_password:
        cmd += ["--password", _s.svn_password, "--non-interactive", "--trust-server-cert"]

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=20)
        if proc.returncode == 0:
            return {"ok": True, "message": "SVN 配置可用"}

        err = (proc.stderr or b"").decode("utf-8", errors="replace")[:300]
        return {"ok": False, "message": f"SVN 校验失败: {err}"}
    except FileNotFoundError:
        return {"ok": False, "message": "未安装 svn 客户端或未加入 PATH"}
    except Exception as e:
        return {"ok": False, "message": f"SVN 校验异常: {e}"}


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
    from studio.backend.core.database import async_session_maker
    from studio.backend.services.workspace_service import get_workspace_vcs_info, _get_git_recent_commits, _get_svn_recent_commits, detect_vcs_type
    _active_ws = None
    try:
        async with async_session_maker() as db:
            _active_ws = (await db.execute(
                select(WorkspaceDir).where(WorkspaceDir.is_active == True).limit(1)
            )).scalar_one_or_none()
    except Exception:
        _active_ws = None

    _ws = (_active_ws.path if _active_ws else _settings.workspace_path)
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

    # Git 平台连接状态（按活跃工作目录配置）
    github_status: dict = {"connected": False}
    gitlab_status: dict = {"connected": False}

    _provider = ((_active_ws.git_provider if _active_ws else _settings.git_provider) or "github").lower()
    if _provider not in {"github", "gitlab"}:
        _provider = "github"

    _gh_token = (_active_ws.github_token if _active_ws else _settings.github_token) or ""
    _gh_repo = (_active_ws.github_repo if _active_ws else _settings.github_repo) or ""
    _gl_url = (_active_ws.gitlab_url if _active_ws else _settings.gitlab_url) or "https://gitlab.com"
    _gl_token = (_active_ws.gitlab_token if _active_ws else _settings.gitlab_token) or ""
    _gl_repo = (_active_ws.gitlab_repo if _active_ws else _settings.gitlab_repo) or ""

    _has_token = bool(_gh_token)
    _has_repo = bool(_gh_repo)
    _gl_has_token = bool(_gl_token)
    _gl_has_repo = bool(_gl_repo)

    if _vt != "git":
        github_status["optional"] = True
        github_status["hint"] = "当前工作目录不是 Git 仓库，Git 配置可选。"
        gitlab_status["optional"] = True
        gitlab_status["hint"] = "当前工作目录不是 Git 仓库，Git 配置可选。"
    else:
        # GitHub
        if not _has_token and not _has_repo:
            github_status["optional"] = True
            github_status["hint"] = "未配置 GitHub Token/仓库。"
        elif not _has_token:
            github_status["hint"] = "已配置仓库，但缺少 Token。"
        elif not _has_repo:
            github_status["hint"] = "已配置 Token，但缺少 owner/repo 仓库绑定。"
            github_status["token_set"] = True
        else:
            from studio.backend.services import github_service
            github_status = await github_service.check_connection(repo=_gh_repo, token=_gh_token)

        # GitLab
        if not _gl_has_token and not _gl_has_repo:
            gitlab_status["optional"] = True
            gitlab_status["hint"] = "未配置 GitLab Token/仓库。"
        elif not _gl_has_token:
            gitlab_status["hint"] = "已配置仓库，但缺少 Token。"
        elif not _gl_has_repo:
            gitlab_status["hint"] = "已配置 Token，但缺少 namespace/project 仓库绑定。"
            gitlab_status["token_set"] = True
        else:
            from studio.backend.services import gitlab_service
            gitlab_status = await gitlab_service.check_connection(base_url=_gl_url, repo=_gl_repo, token=_gl_token)

    # Token 脱敏信息
    _masked_token = _mask_token(_gh_token) if _has_token else ""
    _gl_masked_token = _mask_token(_gl_token) if _gl_has_token else ""

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
        "github": {
            **github_status,
            "masked_token": _masked_token,
            "repo_configured": _has_repo,
            "repo": _gh_repo or None,
            "scope": {
                "source": "workspace" if _active_ws else "runtime",
                "workspace_id": getattr(_active_ws, "id", None),
                "workspace_label": getattr(_active_ws, "label", "") if _active_ws else "",
                "workspace_path": _ws,
                "vcs_type": _vt,
            },
        },
        "gitlab": {
            **gitlab_status,
            "url": _gl_url,
            "masked_token": _gl_masked_token,
            "repo_configured": _gl_has_repo,
            "repo": _gl_repo or None,
        },
        "git_platform": {
            "provider": _provider,
            "supported": ["github", "gitlab"],
        },
    }


@system_router.get("/workspace-overview")
async def workspace_overview(force_refresh: bool = False):
    """
    获取工作区概览：VCS 信息 / 语言统计 / 关键文件 / 贡献者 / 近期提交。
    首次请求较慢（需扫描文件系统），之后 60 秒缓存。
    """
    from studio.backend.services.workspace_service import get_workspace_overview
    return await get_workspace_overview(force_refresh=force_refresh)
