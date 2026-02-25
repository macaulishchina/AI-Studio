"""
设计院 (Studio) - GitHub API 服务
封装所有 GitHub API 操作: Issue, PR, Branch, Merge
"""
import logging
from typing import Optional, Dict, Any, List

import httpx

from studio.backend.core.config import settings

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def _headers(token: Optional[str] = None) -> Dict[str, str]:
    auth_token = (token if token is not None else settings.github_token) or ""
    return {
        "Authorization": f"Bearer {auth_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo_url(path: str = "", repo: Optional[str] = None) -> str:
    target_repo = (repo if repo is not None else settings.github_repo) or ""
    return f"{GITHUB_API}/repos/{target_repo}{path}"


async def create_issue(
    title: str,
    body: str,
    labels: Optional[List[str]] = None,
    assignees: Optional[List[str]] = None,
    agent_assignment: Optional[Dict[str, Any]] = None,
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """创建 GitHub Issue (可附带 Copilot agent_assignment)"""
    payload: Dict[str, Any] = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels
    if assignees:
        payload["assignees"] = assignees
    if agent_assignment:
        payload["agent_assignment"] = agent_assignment

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(_repo_url("/issues", repo=repo), headers=_headers(token), json=payload)
        resp.raise_for_status()
        return resp.json()


async def assign_issue_to_copilot(
    issue_number: int,
    agent_assignment: Optional[Dict[str, Any]] = None,
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """将已有 Issue 分配给 Copilot Coding Agent"""
    payload: Dict[str, Any] = {
        "assignees": ["copilot-swe-agent[bot]"],
    }
    if agent_assignment:
        payload["agent_assignment"] = agent_assignment

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _repo_url(f"/issues/{issue_number}/assignees", repo=repo),
            headers=_headers(token),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def get_issue(issue_number: int, repo: Optional[str] = None, token: Optional[str] = None) -> Dict[str, Any]:
    """获取 Issue 详情"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(_repo_url(f"/issues/{issue_number}", repo=repo), headers=_headers(token))
        resp.raise_for_status()
        return resp.json()


async def update_issue(issue_number: int, repo: Optional[str] = None, token: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    """更新 Issue"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(
            _repo_url(f"/issues/{issue_number}", repo=repo), headers=_headers(token), json=kwargs
        )
        resp.raise_for_status()
        return resp.json()


async def list_pulls(state: str = "open", head: Optional[str] = None, repo: Optional[str] = None, token: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出 PR"""
    params: Dict[str, str] = {"state": state, "per_page": "30"}
    target_repo = (repo if repo is not None else settings.github_repo) or ""
    if head:
        # head 格式: owner:branch
        params["head"] = f"{target_repo.split('/')[0]}:{head}"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(_repo_url("/pulls", repo=target_repo), headers=_headers(token), params=params)
        resp.raise_for_status()
        return resp.json()


async def get_pull(pr_number: int, repo: Optional[str] = None, token: Optional[str] = None) -> Dict[str, Any]:
    """获取 PR 详情"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(_repo_url(f"/pulls/{pr_number}", repo=repo), headers=_headers(token))
        resp.raise_for_status()
        return resp.json()


async def get_pull_diff(pr_number: int, repo: Optional[str] = None, token: Optional[str] = None) -> str:
    """获取 PR diff"""
    headers = {**_headers(token), "Accept": "application/vnd.github.v3.diff"}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(_repo_url(f"/pulls/{pr_number}", repo=repo), headers=headers)
        resp.raise_for_status()
        return resp.text


async def get_pull_files(pr_number: int, repo: Optional[str] = None, token: Optional[str] = None) -> List[Dict[str, Any]]:
    """获取 PR 变更的文件列表"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(_repo_url(f"/pulls/{pr_number}/files", repo=repo), headers=_headers(token))
        resp.raise_for_status()
        return resp.json()


async def merge_pull(
    pr_number: int,
    merge_method: str = "squash",
    commit_message: Optional[str] = None,
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """合并 PR"""
    payload: Dict[str, Any] = {"merge_method": merge_method}
    if commit_message:
        payload["commit_message"] = commit_message

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            _repo_url(f"/pulls/{pr_number}/merge", repo=repo), headers=_headers(token), json=payload
        )
        resp.raise_for_status()
        return resp.json()


async def create_branch(branch_name: str, from_ref: str = "master", repo: Optional[str] = None, token: Optional[str] = None) -> Dict[str, Any]:
    """创建分支"""
    # 先获取 from_ref 的 SHA
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            _repo_url(f"/git/ref/heads/{from_ref}", repo=repo), headers=_headers(token)
        )
        resp.raise_for_status()
        sha = resp.json()["object"]["sha"]

        # 创建新分支
        resp = await client.post(
            _repo_url("/git/refs", repo=repo),
            headers=_headers(token),
            json={"ref": f"refs/heads/{branch_name}", "sha": sha},
        )
        resp.raise_for_status()
        return resp.json()


async def delete_branch(branch_name: str, repo: Optional[str] = None, token: Optional[str] = None) -> bool:
    """删除分支"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(
            _repo_url(f"/git/refs/heads/{branch_name}", repo=repo), headers=_headers(token)
        )
        return resp.status_code == 204


async def get_repo_info(repo: Optional[str] = None, token: Optional[str] = None) -> Dict[str, Any]:
    """获取仓库信息"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(_repo_url(repo=repo), headers=_headers(token))
        resp.raise_for_status()
        return resp.json()


async def check_connection(repo: Optional[str] = None, token: Optional[str] = None) -> Dict[str, Any]:
    """检查 GitHub 连接状态"""
    try:
        repo_info = await get_repo_info(repo=repo, token=token)
        return {
            "connected": True,
            "repo": repo_info.get("full_name"),
            "default_branch": repo_info.get("default_branch"),
            "private": repo_info.get("private"),
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}


# ==================== GitHub Actions Workflow ====================

async def list_workflow_runs(
    branch: Optional[str] = None,
    event: Optional[str] = None,
    status: Optional[str] = None,
    per_page: int = 10,
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """列出 GitHub Actions workflow runs"""
    params: Dict[str, str] = {"per_page": str(per_page)}
    if branch:
        params["branch"] = branch
    if event:
        params["event"] = event
    if status:
        params["status"] = status

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            _repo_url("/actions/runs", repo=repo), headers=_headers(token), params=params
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("workflow_runs", [])


async def get_workflow_run(run_id: int, repo: Optional[str] = None, token: Optional[str] = None) -> Dict[str, Any]:
    """获取单个 workflow run 详情"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            _repo_url(f"/actions/runs/{run_id}", repo=repo), headers=_headers(token)
        )
        resp.raise_for_status()
        return resp.json()


async def get_copilot_workflow_status(branch: str, repo: Optional[str] = None, token: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    获取指定分支上 Copilot coding agent 的 workflow 状态。
    返回最新的 workflow run 信息, 或 None (未找到)。
    """
    try:
        runs = await list_workflow_runs(branch=branch, per_page=5, repo=repo, token=token)
        # 找 Copilot coding agent 的 run
        for run in runs:
            name = (run.get("name") or "").lower()
            if "copilot" in name:
                return {
                    "run_id": run["id"],
                    "name": run.get("name"),
                    "status": run.get("status"),         # queued, in_progress, completed
                    "conclusion": run.get("conclusion"),  # success, failure, cancelled, ...
                    "html_url": run.get("html_url"),
                    "created_at": run.get("created_at"),
                    "updated_at": run.get("updated_at"),
                }
        # 没找到, 尝试所有 run
        if runs:
            run = runs[0]
            return {
                "run_id": run["id"],
                "name": run.get("name"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "html_url": run.get("html_url"),
                "created_at": run.get("created_at"),
                "updated_at": run.get("updated_at"),
            }
    except Exception as e:
        logger.warning(f"获取 Copilot workflow 状态失败: {e}")
    return None
