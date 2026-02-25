"""
设计院 (Studio) - 工作目录管理 API
支持添加、删除、切换工作目录
"""
import logging
import os
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.config import settings, PROJECT_ROOT
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
    git_provider: Optional[str] = Field(None, description="github | gitlab")
    github_token: Optional[str] = None
    github_repo: Optional[str] = None
    gitlab_url: Optional[str] = None
    gitlab_token: Optional[str] = None
    gitlab_repo: Optional[str] = None
    svn_repo_url: Optional[str] = None
    svn_username: Optional[str] = None
    svn_password: Optional[str] = None
    svn_trunk_path: Optional[str] = None


class WorkspaceDirOut(BaseModel):
    id: int
    path: str
    label: str
    is_active: bool
    exists: bool = True       # 目录是否实际存在
    vcs_type: str = "none"    # 版本控制类型
    git_provider: str = "github"
    github_token_configured: bool = False
    github_repo: Optional[str] = None
    gitlab_url: str = "https://gitlab.com"
    gitlab_token_configured: bool = False
    gitlab_repo: Optional[str] = None
    is_builtin: bool = False  # 来自环境变量 WORKSPACE_PATH 的内置目录
    svn_repo_configured: bool = False
    svn_username_configured: bool = False
    svn_username: Optional[str] = None
    svn_trunk_path: str = "trunk"
    svn_repo_url: Optional[str] = None
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
    d["git_provider"] = (getattr(ws, "git_provider", "github") or "github").lower()
    d["github_token_configured"] = bool(getattr(ws, "github_token", ""))
    d["github_repo"] = (getattr(ws, "github_repo", "") or None)
    d["gitlab_url"] = (getattr(ws, "gitlab_url", "") or "https://gitlab.com")
    d["gitlab_token_configured"] = bool(getattr(ws, "gitlab_token", ""))
    d["gitlab_repo"] = (getattr(ws, "gitlab_repo", "") or None)
    d["is_builtin"] = _is_env_builtin_path(ws.path)
    d["svn_repo_url"] = (getattr(ws, "svn_repo_url", "") or "").strip() or None
    d["svn_repo_configured"] = bool(getattr(ws, "svn_repo_url", ""))
    d["svn_username_configured"] = bool(getattr(ws, "svn_username", ""))
    d["svn_username"] = (getattr(ws, "svn_username", "") or "").strip() or None
    d["svn_trunk_path"] = (getattr(ws, "svn_trunk_path", "") or "trunk").strip() or "trunk"
    return d


def _get_env_workspace_path() -> str:
    """获取环境变量中的工作目录绝对路径（无配置则返回空字符串）。"""
    raw = (os.environ.get("WORKSPACE_PATH", "") or "").strip()
    if not raw:
        return ""

    p = Path(raw)
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    return os.path.normcase(os.path.normpath(str(p)))


def _is_env_builtin_path(path: str) -> bool:
    """判断是否为来自 WORKSPACE_PATH 的内置目录。"""
    env_ws = _get_env_workspace_path()
    if not env_ws:
        return False
    try:
        cur = os.path.normcase(os.path.normpath(path))
    except Exception:
        return False
    return cur == env_ws


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
            git_provider=(settings.git_provider or "github"),
            github_token=settings.github_token or "",
            github_repo=settings.github_repo or "",
            gitlab_url=settings.gitlab_url or "https://gitlab.com",
            gitlab_token=settings.gitlab_token or "",
            gitlab_repo=settings.gitlab_repo or "",
            svn_repo_url=settings.svn_repo_url or "",
            svn_username=settings.svn_username or "",
            svn_password=settings.svn_password or "",
            svn_trunk_path=settings.svn_trunk_path or "trunk",
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
    settings.git_provider = (ws.git_provider or "github")
    settings.github_token = ws.github_token or ""
    settings.github_repo = ws.github_repo or ""
    settings.gitlab_url = ws.gitlab_url or "https://gitlab.com"
    settings.gitlab_token = ws.gitlab_token or ""
    settings.gitlab_repo = ws.gitlab_repo or ""
    settings.svn_repo_url = ws.svn_repo_url or ""
    settings.svn_username = ws.svn_username or ""
    settings.svn_password = ws.svn_password or ""
    settings.svn_trunk_path = ws.svn_trunk_path or "trunk"

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
    if data.git_provider is not None:
        provider = data.git_provider.strip().lower()
        if provider not in {"github", "gitlab"}:
            raise HTTPException(status_code=400, detail="git_provider 仅支持 github 或 gitlab")
        ws.git_provider = provider
    if data.github_token is not None:
        ws.github_token = data.github_token.strip()
    if data.github_repo is not None:
        ws.github_repo = data.github_repo.strip()
    if data.gitlab_url is not None:
        ws.gitlab_url = data.gitlab_url.strip().rstrip("/") or "https://gitlab.com"
    if data.gitlab_token is not None:
        ws.gitlab_token = data.gitlab_token.strip()
    if data.gitlab_repo is not None:
        ws.gitlab_repo = data.gitlab_repo.strip()
    if data.svn_repo_url is not None:
        ws.svn_repo_url = data.svn_repo_url.strip()
    if data.svn_username is not None:
        ws.svn_username = data.svn_username.strip()
    if data.svn_password is not None:
        ws.svn_password = data.svn_password.strip()
    if data.svn_trunk_path is not None:
        ws.svn_trunk_path = data.svn_trunk_path.strip() or "trunk"

    await db.flush()
    await db.refresh(ws)

    # 若更新的是当前活跃目录，同步到运行时
    if ws.is_active:
        settings.git_provider = ws.git_provider or "github"
        settings.github_token = ws.github_token or ""
        settings.github_repo = ws.github_repo or ""
        settings.gitlab_url = ws.gitlab_url or "https://gitlab.com"
        settings.gitlab_token = ws.gitlab_token or ""
        settings.gitlab_repo = ws.gitlab_repo or ""
        settings.svn_repo_url = ws.svn_repo_url or ""
        settings.svn_username = ws.svn_username or ""
        settings.svn_password = ws.svn_password or ""
        settings.svn_trunk_path = ws.svn_trunk_path or "trunk"

    return _enrich_dir(ws)


@router.post("/{dir_id}/validate")
async def validate_workspace_dir(
    dir_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_optional_studio_user),
):
    """验证工作目录配置。Git: 校验远程连接；SVN: 自动探测或使用覆盖参数校验。"""
    result = await db.execute(select(WorkspaceDir).where(WorkspaceDir.id == dir_id))
    ws = result.scalar_one_or_none()
    if not ws:
        raise HTTPException(status_code=404, detail="工作目录不存在")

    enriched = _enrich_dir(ws)
    vcs_type = enriched.get("vcs_type", "none")

    if vcs_type == "git":
        provider = (ws.git_provider or "github").lower()
        if provider == "gitlab":
            from studio.backend.services import gitlab_service
            status = await gitlab_service.check_connection(
                base_url=ws.gitlab_url or "https://gitlab.com",
                repo=ws.gitlab_repo or "",
                token=ws.gitlab_token or "",
            )
            return {"ok": bool(status.get("connected")), "vcs_type": "git", "provider": "gitlab", "status": status}

        from studio.backend.services import github_service
        status = await github_service.check_connection(repo=ws.github_repo or "", token=ws.github_token or "")
        return {"ok": bool(status.get("connected")), "vcs_type": "git", "provider": "github", "status": status}

    if vcs_type == "svn":
        target = (ws.svn_repo_url or "").strip() or ws.path
        cmd = ["svn", "info", target]
        if ws.svn_username:
            cmd += ["--username", ws.svn_username]
        if ws.svn_password:
            cmd += ["--password", ws.svn_password, "--non-interactive", "--trust-server-cert"]

        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=20)
            out = (proc.stdout or b"").decode("utf-8", errors="replace")
            err = (proc.stderr or b"").decode("utf-8", errors="replace")
            if proc.returncode != 0:
                hint = ""
                if not ws.svn_username and not ws.svn_password:
                    hint = "当前使用系统认证；若权限不足，可在此目录单独填写 SVN 用户名/密码。"
                return {
                    "ok": False,
                    "vcs_type": "svn",
                    "status": {
                        "connected": False,
                        "message": f"SVN 校验失败: {err[:300]}",
                        "target": target,
                        "auth_mode": "system" if not ws.svn_username else "override",
                        "repo_url": "",
                        "username": "",
                        "hint": hint,
                    }
                }

            detected_url = ""
            detected_user = ""
            last_changed_author = ""
            for line in out.splitlines():
                if line.startswith("URL:"):
                    detected_url = line.split(":", 1)[1].strip()
                elif line.startswith("Username:"):
                    detected_user = line.split(":", 1)[1].strip()
                elif line.startswith("Last Changed Author:"):
                    last_changed_author = line.split(":", 1)[1].strip()
            return {
                "ok": True,
                "vcs_type": "svn",
                "status": {
                    "connected": True,
                    "message": "SVN 配置可用",
                    "target": target,
                    "repo_url": detected_url or (ws.svn_repo_url or ""),
                    "username": detected_user or (ws.svn_username or ""),
                    "last_changed_author": last_changed_author,
                    "repo_source": "manual" if ws.svn_repo_url else "auto",
                    "auth_mode": "system" if not ws.svn_username else "override",
                }
            }
        except FileNotFoundError:
            return {"ok": False, "vcs_type": "svn", "status": {"connected": False, "message": "未安装 svn 客户端或未加入 PATH"}}
        except Exception as e:
            return {"ok": False, "vcs_type": "svn", "status": {"connected": False, "message": f"SVN 校验异常: {e}"}}

    return {"ok": False, "vcs_type": vcs_type, "status": {"connected": False, "message": "当前目录不是 Git/SVN 仓库"}}


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

    # 内置目录（来自 WORKSPACE_PATH）禁止删除
    if _is_env_builtin_path(ws.path):
        raise HTTPException(
            status_code=400,
            detail="该目录来自环境变量 WORKSPACE_PATH，属于内置目录，不能删除。",
        )

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
            settings.git_provider = (next_ws.git_provider or "github")
            settings.github_token = next_ws.github_token or ""
            settings.github_repo = next_ws.github_repo or ""
            settings.gitlab_url = next_ws.gitlab_url or "https://gitlab.com"
            settings.gitlab_token = next_ws.gitlab_token or ""
            settings.gitlab_repo = next_ws.gitlab_repo or ""
            settings.svn_repo_url = next_ws.svn_repo_url or ""
            settings.svn_username = next_ws.svn_username or ""
            settings.svn_password = next_ws.svn_password or ""
            settings.svn_trunk_path = next_ws.svn_trunk_path or "trunk"
            await db.flush()
        else:
            # 恢复到环境变量默认值
            settings.workspace_path = os.environ.get("WORKSPACE_PATH", "/workspace")
            settings.git_provider = os.environ.get("GIT_PROVIDER", "github")
            settings.github_token = os.environ.get("GITHUB_TOKEN", "")
            settings.github_repo = os.environ.get("GITHUB_REPO", "")
            settings.gitlab_url = os.environ.get("GITLAB_URL", "https://gitlab.com")
            settings.gitlab_token = os.environ.get("GITLAB_TOKEN", "")
            settings.gitlab_repo = os.environ.get("GITLAB_REPO", "")
            settings.svn_repo_url = os.environ.get("SVN_REPO_URL", "")
            settings.svn_username = os.environ.get("SVN_USERNAME", "")
            settings.svn_password = os.environ.get("SVN_PASSWORD", "")
            settings.svn_trunk_path = os.environ.get("SVN_TRUNK_PATH", "trunk")

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
