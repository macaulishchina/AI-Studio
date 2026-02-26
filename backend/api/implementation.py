"""
设计院 (Studio) - 代码实施 API
创建 GitHub Issue → 分配 Copilot Coding Agent → 监控 PR

实施流程 (两步法, 对齐官方文档):
  Step 1: 创建 GitHub Issue (不含 assignee)
  Step 2: POST /issues/{n}/assignees 分配 copilot-swe-agent[bot] + agent_assignment
  → Copilot 自动创建 copilot/* 分支和 Draft PR

参考:
  https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/create-a-pr
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
import httpx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.config import settings
from studio.backend.core.database import get_db
from studio.backend.models import Project, ProjectStatus, WorkspaceDir
from studio.backend.services import github_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/projects", tags=["Implementation"])


async def _resolve_project_github_config(db: AsyncSession, project: Project) -> tuple[str, str]:
    """按项目工作目录解析 GitHub 配置（repo, token）。"""
    ws = None
    if project.workspace_dir:
        ws = (await db.execute(
            select(WorkspaceDir).where(WorkspaceDir.path == project.workspace_dir).limit(1)
        )).scalar_one_or_none()
    if ws is None:
        ws = (await db.execute(
            select(WorkspaceDir).where(WorkspaceDir.is_active == True).limit(1)
        )).scalar_one_or_none()

    if ws is not None:
        return (ws.github_repo or "").strip(), (ws.github_token or "").strip()
    return (settings.github_repo or "").strip(), (settings.github_token or "").strip()


def _require_github(repo: str, token: str):
    """GitHub 集成前置检查 — 未配置时返回 501"""
    if not repo or not token:
        raise HTTPException(
            status_code=501,
            detail="当前工作目录未配置 GitHub（Token + owner/repo）。请在系统设置中为该工作目录配置后重试。",
        )


class ImplementRequest(BaseModel):
    """发起实施请求"""
    custom_instructions: str = ""
    base_branch: str = "main"
    model: str = ""  # AI 模型选择 (空=Auto)


class ImplementationStatus(BaseModel):
    """实施状态"""
    project_id: int
    status: str  # not_started, task_created, agent_working, agent_done, pr_created, pr_merged
    github_issue_number: Optional[int] = None
    github_pr_number: Optional[int] = None
    branch_name: Optional[str] = None
    pr_title: Optional[str] = None
    pr_url: Optional[str] = None
    pr_state: Optional[str] = None
    pr_files_changed: int = 0
    # Workflow 信息
    workflow_status: Optional[str] = None     # queued, in_progress, completed
    workflow_conclusion: Optional[str] = None  # success, failure, cancelled
    workflow_url: Optional[str] = None
    workflow_name: Optional[str] = None
    # 会话追踪
    session_url: str = "https://github.com/copilot/agents"
    issue_url: Optional[str] = None
    copilot_assigned: bool = False


# ==================== 预检 ====================


@router.get("/{project_id}/preflight")
async def preflight_check(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    发起实施前的预检:
    1. 验证 GitHub Token 权限
    2. 检查 Copilot Coding Agent 是否可用
    3. 获取仓库默认分支等信息
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    repo, token = await _resolve_project_github_config(db, project)
    _require_github(repo, token)

    # 并行检查权限和 Copilot 可用性
    perm_result, copilot_result = await asyncio.gather(
        github_service.check_token_permissions(repo=repo, token=token),
        github_service.check_copilot_available(repo=repo, token=token),
    )

    # 构建检查项列表
    checks = []

    checks.append({
        "name": "仓库访问",
        "passed": perm_result["repo_access"],
        "detail": f"仓库: {repo}" if perm_result["repo_access"] else "无法访问仓库",
    })
    checks.append({
        "name": "Issues 写入权限",
        "passed": perm_result["issues_write"],
        "detail": "已授权" if perm_result["issues_write"] else "Token 缺少 Issues 写入权限",
    })
    checks.append({
        "name": "Actions 读取权限",
        "passed": perm_result["actions_read"],
        "detail": "已授权" if perm_result["actions_read"] else "Token 缺少 Actions 读取权限 (影响状态监控)",
    })
    checks.append({
        "name": "Issues 已启用",
        "passed": perm_result.get("has_issues", False),
        "detail": "仓库已启用 Issues" if perm_result.get("has_issues") else "仓库未启用 Issues",
    })
    checks.append({
        "name": "Copilot Coding Agent",
        "passed": copilot_result["available"],
        "detail": copilot_result["message"],
    })

    all_passed = all(c["passed"] for c in checks)

    return {
        "ready": all_passed,
        "checks": checks,
        "default_branch": perm_result.get("default_branch", "main"),
        "repo": repo,
        "errors": perm_result.get("errors", []),
    }


# ==================== 发起实施 ====================


@router.post("/{project_id}/implement")
async def start_implementation(
    project_id: int,
    data: ImplementRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    发起代码实施 (两步法, 对齐官方 REST API 文档):

    Step 1: 创建 GitHub Issue (不含 assignee)
    Step 2: POST /issues/{n}/assignees 分配 copilot-swe-agent[bot]
            附带完整的 agent_assignment (target_repo, base_branch, custom_instructions, custom_agent, model)
    → Copilot 自动创建 copilot/* 分支和 Draft PR
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    repo, token = await _resolve_project_github_config(db, project)
    _require_github(repo, token)

    if not project.plan_content:
        raise HTTPException(status_code=400, detail="请先敲定设计方案 (plan)")

    # ── 构建 Issue body ──
    issue_body = f"""## 设计院需求 #{project.id}: {project.title}

### 需求描述
{project.description}

### 实施计划
{project.plan_content}

---
> 🤖 此 Issue 由设计院自动创建
> 📋 项目 ID: {project.id}
"""
    if data.custom_instructions:
        issue_body += f"\n### 附加指令\n{data.custom_instructions}\n"

    try:
        # ── Step 1: 创建 Issue (不含 assignee) ──
        logger.info(f"[实施] 项目 {project_id}: 创建 Issue...")
        issue = await github_service.create_issue(
            title=f"[设计院] {project.title}",
            body=issue_body,
            labels=["studio"],
            repo=repo,
            token=token,
        )
        issue_number = issue["number"]
        logger.info(f"[实施] 项目 {project_id}: Issue #{issue_number} 已创建")

        # ── Step 2: 分配 Copilot Coding Agent ──
        logger.info(f"[实施] 项目 {project_id}: 分配 copilot-swe-agent[bot] 到 Issue #{issue_number}...")
        try:
            assign_result = await github_service.assign_copilot_to_issue(
                issue_number=issue_number,
                target_repo=repo,
                base_branch=data.base_branch,
                custom_instructions=data.custom_instructions,
                model=data.model,
                repo=repo,
                token=token,
            )

            # 验证分配是否成功
            assignees = assign_result.get("assignees", [])
            copilot_ok = any(
                "copilot-swe-agent" in a.get("login", "") for a in assignees
            )
        except httpx.HTTPStatusError as assign_err:
            status_code = assign_err.response.status_code if assign_err.response else 500
            raw = (assign_err.response.text or "")[:500] if assign_err.response else str(assign_err)
            logger.error(f"[实施] 分配 Agent 失败 (HTTP {status_code}): {raw}")

            # Issue 已创建但分配失败 — 保存 Issue 信息并返回详细错误
            project.github_issue_number = issue_number
            project.updated_at = datetime.utcnow()

            detail = (
                f"Issue #{issue_number} 已创建, 但分配 Copilot Agent 失败 (HTTP {status_code})。\n"
            )
            if status_code == 403:
                detail += (
                    "可能的原因:\n"
                    "• Token 缺少 Issues 或 Pull Requests 的 Read & Write 权限\n"
                    "• 仓库 Ruleset 阻止了 Bot 的分配\n"
                    "• Copilot Coding Agent 未在此仓库启用\n"
                )
            elif status_code == 422:
                detail += (
                    "可能的原因:\n"
                    "• copilot-swe-agent[bot] 不可用 (Copilot 未启用或订阅不支持)\n"
                    "• agent_assignment 参数格式错误\n"
                )
            detail += f"\n你可以手动在 GitHub 上将 Issue #{issue_number} 分配给 Copilot。\n"
            detail += f"GitHub 返回: {raw}"
            raise HTTPException(status_code=502, detail=detail)

        project.github_issue_number = issue_number
        project.status = ProjectStatus.implementing
        project.updated_at = datetime.utcnow()

        response = {
            "success": True,
            "issue_number": issue_number,
            "issue_url": issue["html_url"],
            "copilot_assigned": copilot_ok,
            "session_url": "https://github.com/copilot/agents",
            "message": "任务已创建并分配给 Copilot Coding Agent" if copilot_ok
                       else f"Issue #{issue_number} 已创建, 但 Copilot 可能未成功分配。请在 GitHub 上检查 Issue 的 assignees。",
        }
        if not copilot_ok:
            response["warning"] = (
                "copilot-swe-agent[bot] 未在 assignees 列表中。"
                "可能的原因: Copilot 不可用或权限不足。"
                f"请访问 {issue['html_url']} 手动检查。"
            )
        return response

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code if e.response is not None else 500
        raw_text = (e.response.text or "")[:300] if e.response is not None else str(e)
        gh_message = ""
        if e.response is not None:
            try:
                gh_message = (e.response.json() or {}).get("message", "")
            except Exception:
                gh_message = ""

        if status_code == 403:
            detail = (
                "GitHub 权限不足（403）。请检查：\n"
                "1) Token 是否对仓库有写入权限\n"
                "2) Fine-grained PAT 需要: metadata(R), actions(RW), contents(RW), issues(RW), pull_requests(RW)\n"
                "3) 仓库是否开启了 Issues\n"
                "4) 仓库 Ruleset 是否允许 Bot 操作\n"
                f"目标仓库: {repo}\n"
                f"GitHub 返回: {gh_message or raw_text}"
            )
            raise HTTPException(status_code=403, detail=detail)

        if status_code == 404:
            detail = (
                "GitHub 资源不存在（404）。请检查 owner/repo 配置是否正确，"
                f"并确认 Token 有访问该仓库的权限。\n"
                f"目标仓库: {repo}\n"
                f"GitHub 返回: {gh_message or raw_text}"
            )
            raise HTTPException(status_code=404, detail=detail)

        raise HTTPException(
            status_code=502,
            detail=f"GitHub API 调用失败 (HTTP {status_code}): {gh_message or raw_text}",
        )
    except Exception as e:
        logger.exception("创建 GitHub Issue 失败")
        raise HTTPException(status_code=500, detail=f"GitHub API 错误: {str(e)}")


# ==================== 会话监控 ====================


@router.get("/{project_id}/session")
async def get_copilot_session(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    获取 Copilot Coding Agent 会话信息。
    包含:
    - 会话追踪 URL (GitHub Agents 页面)
    - Issue/PR 状态
    - Copilot 是否成功分配
    - 分支信息
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    repo, token = await _resolve_project_github_config(db, project)
    _require_github(repo, token)

    if not project.github_issue_number:
        return {
            "has_session": False,
            "message": "尚未发起实施",
            "session_url": "https://github.com/copilot/agents",
        }

    session_info = await github_service.get_issue_copilot_session_info(
        issue_number=project.github_issue_number,
        repo=repo,
        token=token,
    )

    # 同步 PR 信息到项目
    if session_info.get("pr_number") and not project.github_pr_number:
        project.github_pr_number = session_info["pr_number"]
        project.branch_name = session_info.get("branch")
        project.updated_at = datetime.utcnow()

    return {
        "has_session": True,
        **session_info,
    }


@router.get("/{project_id}/implementation", response_model=ImplementationStatus)
async def get_implementation_status(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """查询实施进度 (轮询 GitHub Actions workflow + PR 状态)"""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    repo, token = await _resolve_project_github_config(db, project)
    _require_github(repo, token)

    status_info = ImplementationStatus(
        project_id=project_id,
        status="not_started",
        github_issue_number=project.github_issue_number,
        github_pr_number=project.github_pr_number,
        branch_name=project.branch_name,
        session_url="https://github.com/copilot/agents",
        issue_url=f"https://github.com/{repo}/issues/{project.github_issue_number}" if project.github_issue_number else None,
    )

    if not project.github_issue_number:
        return status_info

    status_info.status = "task_created"

    try:
        # ---- Step 0: 检查 Copilot 是否被成功分配 ----
        try:
            issue_data = await github_service.get_issue(
                project.github_issue_number, repo=repo, token=token
            )
            assignees = issue_data.get("assignees", [])
            status_info.copilot_assigned = any(
                "copilot-swe-agent" in a.get("login", "") for a in assignees
            )
        except Exception:
            pass

        # ---- Step 1: 查找关联 PR (如果还没有记录) ----
        if not project.github_pr_number:
            pulls = await github_service.list_pulls(state="all", repo=repo, token=token)
            for pr in pulls:
                branch = pr.get("head", {}).get("ref", "")
                body = pr.get("body", "") or ""
                title = pr.get("title", "") or ""
                is_copilot_branch = branch.startswith("copilot/")
                refs_issue = (
                    f"#{project.github_issue_number}" in body
                    or f"#{project.github_issue_number}" in title
                    or project.title in title
                )
                if is_copilot_branch and refs_issue:
                    project.github_pr_number = pr["number"]
                    project.branch_name = branch
                    project.updated_at = datetime.utcnow()
                    # copilot/* 分支存在即证明 Agent 曾经工作
                    status_info.copilot_assigned = True
                    break

        # copilot/* 分支名本身就是 Agent 曾工作的证据
        if project.branch_name and project.branch_name.startswith("copilot/"):
            status_info.copilot_assigned = True

        # ---- Step 2: 检查 workflow 状态 (核心监控) ----
        branch = project.branch_name
        if branch:
            wf = await github_service.get_copilot_workflow_status(branch, repo=repo, token=token)
            if wf:
                status_info.workflow_status = wf.get("status")
                status_info.workflow_conclusion = wf.get("conclusion")
                status_info.workflow_url = wf.get("html_url")
                status_info.workflow_name = wf.get("name")

                wf_status = wf.get("status", "")
                wf_conclusion = wf.get("conclusion", "")

                if wf_status == "completed":
                    # Workflow 结束: 根据结论判断
                    if wf_conclusion == "success":
                        status_info.status = "agent_done"
                    else:
                        # failure/cancelled 等也视为 done (可查看结果)
                        status_info.status = "agent_done"
                elif wf_status in ("queued", "in_progress"):
                    status_info.status = "agent_working"
                else:
                    status_info.status = "agent_working"
        elif project.github_pr_number:
            # 有 PR 但还没 branch 记录
            pass
        else:
            # 还没找到 PR, agent 可能还在初始化
            status_info.status = "agent_working"

        # ---- Step 3: 补充 PR 信息 ----
        if project.github_pr_number:
            pr = await github_service.get_pull(project.github_pr_number, repo=repo, token=token)
            status_info.github_pr_number = pr["number"]
            status_info.pr_title = pr.get("title")
            status_info.pr_url = pr.get("html_url")
            status_info.pr_state = pr.get("state")
            status_info.pr_files_changed = pr.get("changed_files", 0)
            status_info.branch_name = pr.get("head", {}).get("ref")

            if pr.get("merged"):
                status_info.status = "pr_merged"
            elif status_info.status == "agent_done":
                status_info.status = "agent_done"
            elif pr.get("state") == "open" and status_info.status not in ("agent_working",):
                status_info.status = "pr_created"

        # ---- Step 4: 当 agent 完成时, 自动推进到 reviewing 并触发自动审查 ----
        if status_info.status == "agent_done" and project.status == ProjectStatus.implementing:
            project.status = ProjectStatus.reviewing
            project.updated_at = datetime.utcnow()
            logger.info(f"项目 {project_id} Copilot Agent 完成, 自动进入审查阶段")
            # 异步触发自动审查 (不阻塞状态查询)
            asyncio.create_task(_trigger_auto_review(
                project_id,
                project.branch_name,
                project.github_pr_number,
            ))

    except Exception as e:
        logger.warning(f"查询 GitHub 状态失败: {e}")

    return status_info


@router.get("/{project_id}/pr-diff")
async def get_pr_diff(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取 PR 的 diff 内容"""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project or not project.github_pr_number:
        raise HTTPException(status_code=404, detail="未找到 PR")

    repo, token = await _resolve_project_github_config(db, project)
    _require_github(repo, token)

    try:
        diff = await github_service.get_pull_diff(project.github_pr_number, repo=repo, token=token)
        files = await github_service.get_pull_files(project.github_pr_number, repo=repo, token=token)
        return {
            "diff": diff,
            "files": [
                {
                    "filename": f["filename"],
                    "status": f["status"],
                    "additions": f["additions"],
                    "deletions": f["deletions"],
                    "patch": f.get("patch", ""),
                }
                for f in files
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取 Diff 失败: {str(e)}")


@router.post("/{project_id}/pr/approve")
async def approve_and_merge_pr(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Review 通过并合并 PR"""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project or not project.github_pr_number:
        raise HTTPException(status_code=404, detail="未找到 PR")

    repo, token = await _resolve_project_github_config(db, project)
    _require_github(repo, token)

    try:
        merge_result = await github_service.merge_pull(
            project.github_pr_number,
            merge_method="squash",
            commit_message=f"[设计院] {project.title} (#{project.github_issue_number})",
            repo=repo,
            token=token,
        )
        project.status = ProjectStatus.deploying
        project.updated_at = datetime.utcnow()

        return {
            "success": True,
            "merged": merge_result.get("merged", False),
            "message": merge_result.get("message", ""),
            "sha": merge_result.get("sha", ""),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"合并 PR 失败: {str(e)}")


# ==================== 审查准备 ====================

class PrepareReviewResponse(BaseModel):
    success: bool
    workspace_dir: str = ""
    branch: str = ""
    base_branch: str = "main"
    diff_stat: str = ""
    changed_files: list = []
    message: str = ""


@router.post("/{project_id}/prepare-review", response_model=PrepareReviewResponse)
async def prepare_review(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    准备审查环境:
    1. 克隆/更新仓库到独立目录
    2. 切换到实施分支
    3. 获取 diff 统计和变更文件列表
    4. 更新项目的工作区路径
    """
    from studio.backend.services import workspace_service

    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    repo, token = await _resolve_project_github_config(db, project)
    _require_github(repo, token)

    branch = project.branch_name
    if not branch:
        raise HTTPException(status_code=400, detail="项目没有关联的实施分支")

    try:
        ws_result = await workspace_service.prepare_review_workspace(
            project_id=project_id,
            branch_name=branch,
            pr_number=project.github_pr_number,
        )

        if ws_result["success"]:
            project.workspace_dir = ws_result["workspace_dir"]
            project.updated_at = datetime.utcnow()

        return PrepareReviewResponse(**ws_result)

    except Exception as e:
        logger.exception("准备审查工作区失败")
        raise HTTPException(status_code=500, detail=f"准备审查环境失败: {str(e)}")


@router.get("/{project_id}/workspace-info")
async def get_workspace_info(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取项目当前工作区的 git 信息"""
    from studio.backend.services import workspace_service

    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    ws = workspace_service.get_effective_workspace(project)
    git_info = await workspace_service.get_workspace_git_info(ws)

    return {
        "workspace_dir": ws,
        "is_custom": ws != settings.workspace_path,
        **git_info,
    }


# ==================== 迭代管理 ====================

@router.post("/{project_id}/start-iteration")
async def start_iteration(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    开始新一轮迭代:
    1. 基于当前实施分支创建新的讨论工作区
    2. 重置状态为 discussing
    3. 递增 iteration_count
    """
    from studio.backend.services import workspace_service

    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    repo, token = await _resolve_project_github_config(db, project)
    _require_github(repo, token)

    if project.status != ProjectStatus.reviewing:
        raise HTTPException(status_code=400, detail="只有在审查阶段才能发起迭代")

    branch = project.branch_name
    if not branch:
        raise HTTPException(status_code=400, detail="没有关联的实施分支，无法创建迭代工作区")

    iteration = (getattr(project, 'iteration_count', None) or 0) + 1

    try:
        ws_result = await workspace_service.prepare_iteration_workspace(
            project_id=project_id,
            iteration=iteration,
            branch_name=branch,
        )

        if not ws_result["success"]:
            raise HTTPException(status_code=500, detail=ws_result["message"])

        project.workspace_dir = ws_result["workspace_dir"]
        project.iteration_count = iteration
        project.status = ProjectStatus.discussing
        project.updated_at = datetime.utcnow()

        return {
            "success": True,
            "iteration": iteration,
            "workspace_dir": ws_result["workspace_dir"],
            "branch": branch,
            "message": f"第 {iteration} 轮迭代已开始 (基于 {branch})",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("开始迭代失败")
        raise HTTPException(status_code=500, detail=f"创建迭代工作区失败: {str(e)}")


# ==================== 自动审查触发 ====================

async def _trigger_auto_review(project_id: int, branch: str, pr_number: int = None):
    """
    后台任务: 准备审查工作区 → 启动自动审查 AI 任务.
    由 get_implementation_status 在检测到 agent_done 时异步触发.
    """
    try:
        from studio.backend.services import workspace_service
        from studio.backend.core.database import async_session_maker

        # 1. 准备审查工作区 (克隆/更新仓库, 切换到实施分支)
        ws_result = await workspace_service.prepare_review_workspace(
            project_id=project_id,
            branch_name=branch,
            pr_number=pr_number,
        )

        if ws_result.get("success"):
            # 保存工作区路径到项目
            async with async_session_maker() as db:
                from sqlalchemy import select as sa_select
                result = await db.execute(sa_select(Project).where(Project.id == project_id))
                project = result.scalar_one_or_none()
                if project:
                    project.workspace_dir = ws_result["workspace_dir"]
                    await db.commit()

            # 2. 启动自动审查任务
            from studio.backend.services.task_runner import TaskManager
            task_id = await TaskManager.start_auto_review_task(project_id)
            logger.info(f"项目 {project_id} 自动审查已启动 (task_id={task_id})")
        else:
            logger.warning(f"项目 {project_id} 准备审查工作区失败: {ws_result.get('message', '未知错误')}")

    except Exception as e:
        logger.warning(f"项目 {project_id} 自动审查触发失败: {e}")
