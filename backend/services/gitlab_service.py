"""
设计院 (Studio) - GitLab API 服务
仅用于连接可用性检查与基础仓库信息。
"""
from typing import Optional, Dict, Any
from urllib.parse import quote

import httpx


async def check_connection(base_url: str, repo: str, token: str) -> Dict[str, Any]:
    """检查 GitLab 连接状态（仓库 + token）。"""
    if not base_url:
        return {"connected": False, "error": "缺少 GitLab 地址"}
    if not repo:
        return {"connected": False, "error": "缺少 GitLab 仓库（namespace/project）"}
    if not token:
        return {"connected": False, "error": "缺少 GitLab Token"}

    base = base_url.rstrip("/")
    project = quote(repo, safe="")
    url = f"{base}/api/v4/projects/{project}"
    headers = {"PRIVATE-TOKEN": token}

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return {
                "connected": True,
                "repo": data.get("path_with_namespace") or repo,
                "default_branch": data.get("default_branch") or "",
                "private": data.get("visibility") == "private",
                "host": base,
            }
    except Exception as e:
        return {"connected": False, "error": str(e), "host": base}
