"""
设计院 (Studio) - 工作区管理服务

为每个项目的不同阶段提供独立的 git 工作区：
- 讨论阶段：首次使用默认 /workspace，迭代时使用独立克隆
- 审查阶段：克隆实施分支到独立目录
- 隔离性：每个项目/阶段的工作区互不影响
"""
import asyncio
import logging
import os
import shutil
from typing import Dict, Optional, Tuple

from studio.backend.core.config import settings

logger = logging.getLogger(__name__)

# 工作区根目录
WORKSPACES_ROOT = os.path.join(settings.data_path, "workspaces")


def _ensure_workspaces_root():
    """确保工作区根目录存在"""
    os.makedirs(WORKSPACES_ROOT, exist_ok=True)


def get_review_workspace_path(project_id: int) -> str:
    """获取审查阶段的工作区路径"""
    return os.path.join(WORKSPACES_ROOT, f"project-{project_id}-review")


def get_iteration_workspace_path(project_id: int, iteration: int) -> str:
    """获取迭代讨论的工作区路径"""
    return os.path.join(WORKSPACES_ROOT, f"project-{project_id}-iter-{iteration}")


def get_effective_workspace(project) -> str:
    """
    获取项目当前有效的工作区路径。
    优先使用 project.workspace_dir，否则使用全局默认 /workspace
    """
    ws = getattr(project, 'workspace_dir', None)
    if ws and os.path.isdir(ws):
        return ws
    return settings.workspace_path


async def _run_git(cmd: list, cwd: str, timeout: int = 120) -> Tuple[int, str, str]:
    """执行 git 命令"""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "", "git 命令超时"


def _build_clone_url(repo: str, token: str) -> str:
    """
    构建 clone URL
    优先使用 GIT_CLONE_URL 环境变量 (支持任意 Git 仓库)
    否则回退到 GitHub repo 格式 (owner/repo)
    """
    # 优先使用通用 Git 克隆 URL
    if settings.git_clone_url:
        url = settings.git_clone_url
        # 如果是 HTTPS URL 且有 token, 注入认证信息
        if token and url.startswith("https://"):
            # https://example.com/repo.git → https://x-access-token:TOKEN@example.com/repo.git
            url = url.replace("https://", f"https://x-access-token:{token}@", 1)
        return url
    # 回退: 使用 GitHub repo 格式
    if not repo:
        raise ValueError("Git 仓库未配置。请设置 GIT_CLONE_URL 或 GITHUB_REPO 环境变量。")
    if token:
        return f"https://x-access-token:{token}@github.com/{repo}.git"
    return f"https://github.com/{repo}.git"


async def prepare_review_workspace(
    project_id: int,
    branch_name: str,
    pr_number: Optional[int] = None,
) -> Dict:
    """
    准备审查阶段的工作区

    1. 克隆/更新仓库到 /data/workspaces/project-{id}-review/
    2. 切换到实施分支
    3. 获取变更文件列表和 diff 统计

    Returns:
        {
            "workspace_dir": "/data/workspaces/project-5-review",
            "branch": "copilot/fix-xxx",
            "base_branch": "main",
            "diff_stat": "+100 -50, 8 files changed",
            "changed_files": ["file1.py", "file2.vue", ...],
            "success": True,
            "message": "工作区已就绪"
        }
    """
    _ensure_workspaces_root()
    ws_path = get_review_workspace_path(project_id)
    repo = settings.github_repo
    token = settings.github_token
    clone_url = _build_clone_url(repo, token)

    result = {
        "workspace_dir": ws_path,
        "branch": branch_name,
        "base_branch": "main",
        "diff_stat": "",
        "changed_files": [],
        "success": False,
        "message": "",
    }

    try:
        if os.path.isdir(os.path.join(ws_path, ".git")):
            # 已存在 → fetch + checkout
            logger.info(f"更新已有工作区: {ws_path}")
            rc, out, err = await _run_git(["git", "fetch", "--all", "--prune"], ws_path)
            if rc != 0:
                result["message"] = f"git fetch 失败: {err}"
                return result
            rc, out, err = await _run_git(["git", "checkout", branch_name], ws_path)
            if rc != 0:
                # 可能是远程分支，尝试 origin/
                rc, out, err = await _run_git(
                    ["git", "checkout", "-b", branch_name, f"origin/{branch_name}"],
                    ws_path,
                )
                if rc != 0:
                    result["message"] = f"git checkout 失败: {err}"
                    return result
            rc, out, err = await _run_git(["git", "pull", "--ff-only"], ws_path)
        else:
            # 全新克隆
            if os.path.exists(ws_path):
                shutil.rmtree(ws_path)
            logger.info(f"克隆仓库到: {ws_path}")
            rc, out, err = await _run_git(
                ["git", "clone", "--branch", branch_name, clone_url, ws_path],
                WORKSPACES_ROOT,
                timeout=300,
            )
            if rc != 0:
                result["message"] = f"git clone 失败: {err}"
                return result

        # 获取 diff 统计 (对比 main)
        rc, out, err = await _run_git(
            ["git", "diff", "--stat", "origin/main...HEAD"],
            ws_path,
        )
        if rc == 0 and out.strip():
            result["diff_stat"] = out.strip().split("\n")[-1].strip()  # 最后一行是总结

        # 获取变更文件列表
        rc, out, err = await _run_git(
            ["git", "diff", "--name-only", "origin/main...HEAD"],
            ws_path,
        )
        if rc == 0 and out.strip():
            result["changed_files"] = [f for f in out.strip().split("\n") if f]

        # 获取 base branch
        rc, out, err = await _run_git(
            ["git", "log", "--oneline", "-1", "origin/main"],
            ws_path,
        )
        if rc == 0:
            result["base_branch"] = "main"

        result["success"] = True
        result["message"] = f"审查工作区已就绪 ({len(result['changed_files'])} 个文件变更)"
        logger.info(f"审查工作区就绪: project={project_id}, branch={branch_name}, files={len(result['changed_files'])}")

    except Exception as e:
        logger.exception(f"准备审查工作区失败: {e}")
        result["message"] = f"准备工作区失败: {str(e)}"

    return result


async def prepare_iteration_workspace(
    project_id: int,
    iteration: int,
    branch_name: str,
) -> Dict:
    """
    准备迭代讨论的工作区 (基于当前实施分支)

    Returns:
        {
            "workspace_dir": "/data/workspaces/project-5-iter-1",
            "branch": "copilot/fix-xxx",
            "success": True,
            "message": "迭代工作区已就绪"
        }
    """
    _ensure_workspaces_root()
    ws_path = get_iteration_workspace_path(project_id, iteration)
    repo = settings.github_repo
    token = settings.github_token
    clone_url = _build_clone_url(repo, token)

    result = {
        "workspace_dir": ws_path,
        "branch": branch_name,
        "success": False,
        "message": "",
    }

    try:
        if os.path.exists(ws_path):
            shutil.rmtree(ws_path)

        logger.info(f"克隆迭代工作区: {ws_path}, branch={branch_name}")
        rc, out, err = await _run_git(
            ["git", "clone", "--branch", branch_name, clone_url, ws_path],
            WORKSPACES_ROOT,
            timeout=300,
        )
        if rc != 0:
            result["message"] = f"git clone 失败: {err}"
            return result

        result["success"] = True
        result["message"] = f"迭代工作区已就绪 (基于 {branch_name})"
        logger.info(f"迭代工作区就绪: project={project_id}, iter={iteration}")

    except Exception as e:
        logger.exception(f"准备迭代工作区失败: {e}")
        result["message"] = f"准备工作区失败: {str(e)}"

    return result


async def get_workspace_git_info(workspace_dir: str) -> Dict:
    """获取工作区的 git 信息"""
    info = {
        "branch": "",
        "commit": "",
        "commit_short": "",
        "commit_message": "",
    }
    if not workspace_dir or not os.path.isdir(os.path.join(workspace_dir, ".git")):
        return info

    rc, out, _ = await _run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"], workspace_dir)
    if rc == 0:
        info["branch"] = out.strip()

    rc, out, _ = await _run_git(["git", "rev-parse", "HEAD"], workspace_dir)
    if rc == 0:
        info["commit"] = out.strip()
        info["commit_short"] = out.strip()[:8]

    rc, out, _ = await _run_git(["git", "log", "--oneline", "-1", "--format=%s"], workspace_dir)
    if rc == 0:
        info["commit_message"] = out.strip()

    return info


def cleanup_project_workspaces(project_id: int):
    """清理项目相关的所有工作区"""
    _ensure_workspaces_root()
    prefix = f"project-{project_id}"
    for entry in os.listdir(WORKSPACES_ROOT):
        if entry.startswith(prefix):
            path = os.path.join(WORKSPACES_ROOT, entry)
            if os.path.isdir(path):
                try:
                    shutil.rmtree(path)
                    logger.info(f"清理工作区: {path}")
                except Exception as e:
                    logger.warning(f"清理工作区失败: {path}: {e}")
