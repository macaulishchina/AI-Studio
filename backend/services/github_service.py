"""
设计院 (Studio) - GitHub API 服务
封装所有 GitHub API 操作: Issue, PR, Branch, Merge
以及 Copilot Coding Agent 触发 & 会话监控

参考文档:
  - https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/create-a-pr
  - https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/create-custom-agents
  - https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/changing-the-ai-model
"""
import logging
from typing import Optional, Dict, Any, List

import httpx

from studio.backend.core.config import settings

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"

# GraphQL 需要的特性标记, 用于 Copilot 编码代理的 Issue 分配
COPILOT_GRAPHQL_FEATURES = "issues_copilot_assignment_api_support,coding_agent_model_selection"


def _headers(token: Optional[str] = None) -> Dict[str, str]:
    auth_token = (token if token is not None else settings.github_token) or ""
    return {
        "Authorization": f"Bearer {auth_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _graphql_headers(token: Optional[str] = None) -> Dict[str, str]:
    """GraphQL 请求头, 包含 Copilot 特性标记"""
    h = _headers(token)
    h["GraphQL-Features"] = COPILOT_GRAPHQL_FEATURES
    return h


def _repo_url(path: str = "", repo: Optional[str] = None) -> str:
    target_repo = (repo if repo is not None else settings.github_repo) or ""
    return f"{GITHUB_API}/repos/{target_repo}{path}"


# ==================== Copilot Agent 可用性检查 ====================


async def check_copilot_available(
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    通过 GraphQL suggestedActors 查询检查仓库是否启用 Copilot Coding Agent。
    返回 {"available": bool, "bot_id": str|None, "message": str}
    """
    target_repo = (repo if repo is not None else settings.github_repo) or ""
    parts = target_repo.split("/")
    if len(parts) != 2:
        return {"available": False, "bot_id": None, "message": f"仓库格式错误: {target_repo}"}

    owner, name = parts
    query = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) {
        suggestedActors(capabilities: [CAN_BE_ASSIGNED], first: 100) {
          nodes {
            login
            __typename
            ... on Bot { id }
            ... on User { id }
          }
        }
      }
    }
    """
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                GITHUB_GRAPHQL,
                headers=_graphql_headers(token),
                json={"query": query, "variables": {"owner": owner, "name": name}},
            )
            resp.raise_for_status()
            data = resp.json()

            if "errors" in data:
                errors = data["errors"]
                msg = errors[0].get("message", str(errors)) if errors else "未知 GraphQL 错误"
                return {"available": False, "bot_id": None, "message": f"GraphQL 错误: {msg}"}

            nodes = (
                data.get("data", {})
                .get("repository", {})
                .get("suggestedActors", {})
                .get("nodes", [])
            )

            for node in nodes:
                if node.get("login") == "copilot-swe-agent":
                    return {
                        "available": True,
                        "bot_id": node.get("id"),
                        "message": "Copilot Coding Agent 已启用",
                    }

            return {
                "available": False,
                "bot_id": None,
                "message": "此仓库未启用 Copilot Coding Agent。请检查: "
                           "1) 你的 Copilot 订阅是否包含 Coding Agent; "
                           "2) 仓库设置中是否启用了 Copilot coding agent。",
            }

    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response else 0
        if status == 401:
            return {"available": False, "bot_id": None, "message": "Token 无效或过期"}
        if status == 403:
            return {"available": False, "bot_id": None, "message": "Token 权限不足, 无法查询仓库信息"}
        return {"available": False, "bot_id": None, "message": f"GitHub API 错误 (HTTP {status})"}
    except Exception as e:
        return {"available": False, "bot_id": None, "message": f"检查失败: {str(e)}"}


async def check_token_permissions(
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    检查 Token 对仓库的权限, 返回详细的权限诊断信息。
    """
    result: Dict[str, Any] = {
        "repo_access": False,
        "issues_write": False,
        "pr_write": False,
        "actions_read": False,
        "default_branch": None,
        "has_issues": False,
        "errors": [],
    }

    try:
        repo_info = await get_repo_info(repo=repo, token=token)
        result["repo_access"] = True
        result["default_branch"] = repo_info.get("default_branch")
        result["has_issues"] = repo_info.get("has_issues", False)
        perms = repo_info.get("permissions", {})
        if perms:
            result["issues_write"] = perms.get("push", False) or perms.get("admin", False)
            result["pr_write"] = perms.get("push", False) or perms.get("admin", False)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response else 0
        if status == 404:
            result["errors"].append(f"仓库不存在或无访问权限")
        elif status == 401:
            result["errors"].append("Token 无效或已过期")
        elif status == 403:
            result["errors"].append("Token 权限不足")
        else:
            result["errors"].append(f"API 错误 (HTTP {status})")
        return result
    except Exception as e:
        result["errors"].append(f"连接失败: {str(e)}")
        return result

    try:
        await list_workflow_runs(per_page=1, repo=repo, token=token)
        result["actions_read"] = True
    except Exception:
        result["errors"].append("无法读取 Actions (可能未授予 Actions 权限)")

    return result


# ==================== Issue 操作 ====================


async def create_issue(
    title: str,
    body: str,
    labels: Optional[List[str]] = None,
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    创建 GitHub Issue（不含 assignee）。
    触发 Copilot Agent 使用单独的 assign_copilot_to_issue() 以提高可靠性。
    """
    payload: Dict[str, Any] = {"title": title, "body": body}
    if labels:
        payload["labels"] = labels

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _repo_url("/issues", repo=repo), headers=_headers(token), json=payload
        )
        resp.raise_for_status()
        return resp.json()


async def assign_copilot_to_issue(
    issue_number: int,
    target_repo: str,
    base_branch: str = "main",
    custom_instructions: str = "",
    custom_agent: str = "",
    model: str = "",
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    将 Issue 分配给 Copilot Coding Agent。

    使用 REST POST /repos/{owner}/{repo}/issues/{issue_number}/assignees 端点,
    附带完整的 agent_assignment 参数。这是官方推荐的触发方式。

    参考: https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/create-a-pr
    """
    payload: Dict[str, Any] = {
        "assignees": ["copilot-swe-agent[bot]"],
        "agent_assignment": {
            "target_repo": target_repo,
            "base_branch": base_branch,
            "custom_instructions": custom_instructions,
            "custom_agent": custom_agent,
            "model": model,
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _repo_url(f"/issues/{issue_number}/assignees", repo=repo),
            headers=_headers(token),
            json=payload,
        )
        resp.raise_for_status()
        result = resp.json()

        # 验证 copilot-swe-agent[bot] 是否成功被添加为 assignee
        assignees = result.get("assignees", [])
        copilot_assigned = any(
            a.get("login", "").startswith("copilot-swe-agent") for a in assignees
        )
        if not copilot_assigned:
            logger.warning(
                f"Issue #{issue_number} assignees 中未找到 copilot-swe-agent: "
                f"{[a.get('login') for a in assignees]}"
            )

        return result


async def get_issue(
    issue_number: int,
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """获取 Issue 详情"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            _repo_url(f"/issues/{issue_number}", repo=repo), headers=_headers(token)
        )
        resp.raise_for_status()
        return resp.json()


async def update_issue(
    issue_number: int,
    repo: Optional[str] = None,
    token: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """更新 Issue"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(
            _repo_url(f"/issues/{issue_number}", repo=repo),
            headers=_headers(token),
            json=kwargs,
        )
        resp.raise_for_status()
        return resp.json()


async def get_issue_events(
    issue_number: int,
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """获取 Issue 时间线事件 (用于追踪 Agent 分配状态)"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            _repo_url(f"/issues/{issue_number}/timeline", repo=repo),
            headers={**_headers(token), "Accept": "application/vnd.github.mockingbird-preview+json"},
        )
        resp.raise_for_status()
        return resp.json()


# ==================== PR 操作 ====================


async def list_pulls(
    state: str = "open",
    head: Optional[str] = None,
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """列出 PR"""
    params: Dict[str, str] = {"state": state, "per_page": "30"}
    target_repo = (repo if repo is not None else settings.github_repo) or ""
    if head:
        params["head"] = f"{target_repo.split('/')[0]}:{head}"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            _repo_url("/pulls", repo=target_repo), headers=_headers(token), params=params
        )
        resp.raise_for_status()
        return resp.json()


async def get_pull(
    pr_number: int,
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """获取 PR 详情"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            _repo_url(f"/pulls/{pr_number}", repo=repo), headers=_headers(token)
        )
        resp.raise_for_status()
        return resp.json()


async def get_pull_diff(
    pr_number: int,
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> str:
    """获取 PR diff"""
    headers = {**_headers(token), "Accept": "application/vnd.github.v3.diff"}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            _repo_url(f"/pulls/{pr_number}", repo=repo), headers=headers
        )
        resp.raise_for_status()
        return resp.text


async def get_pull_files(
    pr_number: int,
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """获取 PR 变更的文件列表"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            _repo_url(f"/pulls/{pr_number}/files", repo=repo), headers=_headers(token)
        )
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
            _repo_url(f"/pulls/{pr_number}/merge", repo=repo),
            headers=_headers(token),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


# ==================== Branch 操作 ====================


async def create_branch(
    branch_name: str,
    from_ref: str = "master",
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """创建分支"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            _repo_url(f"/git/ref/heads/{from_ref}", repo=repo), headers=_headers(token)
        )
        resp.raise_for_status()
        sha = resp.json()["object"]["sha"]

        resp = await client.post(
            _repo_url("/git/refs", repo=repo),
            headers=_headers(token),
            json={"ref": f"refs/heads/{branch_name}", "sha": sha},
        )
        resp.raise_for_status()
        return resp.json()


async def delete_branch(
    branch_name: str,
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> bool:
    """删除分支"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(
            _repo_url(f"/git/refs/heads/{branch_name}", repo=repo), headers=_headers(token)
        )
        return resp.status_code == 204


# ==================== 仓库信息 ====================


async def get_repo_info(
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """获取仓库信息"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(_repo_url(repo=repo), headers=_headers(token))
        resp.raise_for_status()
        return resp.json()


async def check_connection(
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
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


async def list_branches(
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """列出仓库分支"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            _repo_url("/branches", repo=repo),
            headers=_headers(token),
            params={"per_page": "100"},
        )
        resp.raise_for_status()
        return resp.json()


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


async def get_workflow_run(
    run_id: int,
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """获取单个 workflow run 详情"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            _repo_url(f"/actions/runs/{run_id}", repo=repo), headers=_headers(token)
        )
        resp.raise_for_status()
        return resp.json()


async def get_copilot_workflow_status(
    branch: str,
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    获取指定分支上 Copilot coding agent 的 workflow 状态。
    返回最新的 workflow run 信息, 或 None (未找到)。
    """
    try:
        runs = await list_workflow_runs(branch=branch, per_page=5, repo=repo, token=token)
        # 优先找 Copilot coding agent 的 run
        for run in runs:
            name = (run.get("name") or "").lower()
            if "copilot" in name:
                return {
                    "run_id": run["id"],
                    "name": run.get("name"),
                    "status": run.get("status"),
                    "conclusion": run.get("conclusion"),
                    "html_url": run.get("html_url"),
                    "created_at": run.get("created_at"),
                    "updated_at": run.get("updated_at"),
                }
        # 没有 copilot 相关的, 返回最近的 run
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


# ==================== Copilot Session 监控 ====================


async def get_issue_copilot_session_info(
    issue_number: int,
    repo: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    获取 Issue 关联的 Copilot 会话信息。
    通过检查 Issue 的 assignees 和关联 PR 来推断会话状态。
    """
    target_repo = (repo if repo is not None else settings.github_repo) or ""
    info: Dict[str, Any] = {
        "issue_number": issue_number,
        "copilot_assigned": False,
        "session_url": "https://github.com/copilot/agents",
        "issue_url": f"https://github.com/{target_repo}/issues/{issue_number}",
        "pr_number": None,
        "pr_url": None,
        "pr_state": None,
        "branch": None,
        "copilot_status": "unknown",
    }

    try:
        # 检查 Issue assignees
        issue = await get_issue(issue_number, repo=repo, token=token)
        assignees = issue.get("assignees", [])
        for a in assignees:
            if "copilot-swe-agent" in a.get("login", ""):
                info["copilot_assigned"] = True
                info["copilot_status"] = "assigned"
                break

        if issue.get("state") == "closed":
            info["copilot_status"] = "completed"

        # 查找关联 PR
        pulls = await list_pulls(state="all", repo=repo, token=token)
        for pr in pulls:
            branch = pr.get("head", {}).get("ref", "")
            body = pr.get("body", "") or ""
            title = pr.get("title", "") or ""
            is_copilot_branch = branch.startswith("copilot/")
            refs_issue = f"#{issue_number}" in body or f"#{issue_number}" in title
            if is_copilot_branch and refs_issue:
                info["pr_number"] = pr["number"]
                info["pr_url"] = pr.get("html_url")
                info["pr_state"] = pr.get("state")
                info["branch"] = branch
                if pr.get("merged"):
                    info["copilot_status"] = "merged"
                elif pr.get("draft"):
                    info["copilot_status"] = "working"
                elif pr.get("state") == "open":
                    info["copilot_status"] = "completed"
                break

    except Exception as e:
        logger.warning(f"获取 Copilot 会话信息失败: {e}")

    return info
