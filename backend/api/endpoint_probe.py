"""
设计院 (Studio) - GitHub 外部接口健康检测

批量探测所有项目依赖的 GitHub / Copilot / Azure 外部 API 端点，
确保接口可用性和响应格式未发生破坏性变更。

接口分组:
  1. GitHub Core API  — 基本 GitHub REST API (需要 GITHUB_TOKEN)
  2. GitHub Models API — Azure 托管的推理模型列表 (需要 GITHUB_TOKEN)
  3. Copilot OAuth     — 设备流 / token 端点 (无需认证即可 HEAD)
  4. Copilot Internal  — /copilot_internal/* (需要 OAuth token)
  5. Copilot Chat API  — api.githubcopilot.com (需要 session token)
"""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

import httpx
from fastapi import APIRouter, Query

from studio.backend.core.config import settings
from studio.backend.services.copilot_auth import copilot_auth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/endpoint-probe", tags=["Endpoint Probe"])


# ==================== 端点定义 ====================

@dataclass
class EndpointDef:
    """外部端点定义"""
    id: str                     # 唯一 ID
    group: str                  # 分组
    name: str                   # 显示名
    method: str                 # HTTP 方法
    url: str                    # 完整 URL
    auth_type: str = "none"     # none / github_pat / copilot_oauth / copilot_session
    description: str = ""       # 说明 (项目中哪里用到)
    expect_status: int = 200    # 期望状态码
    expect_keys: List[str] = field(default_factory=list)  # 期望 JSON 包含的顶层 key
    body: Optional[Dict] = None  # POST body
    skip_if_no_auth: bool = True  # 缺少认证时跳过而非标记错误


# 所有项目依赖的外部端点
ENDPOINT_REGISTRY: List[EndpointDef] = [
    # ── GitHub Core API ──
    EndpointDef(
        id="github_user",
        group="GitHub Core API",
        name="GET /user",
        method="GET",
        url="https://api.github.com/user",
        auth_type="github_pat",
        description="获取当前用户信息 (github_service.py 所有操作的基础)",
        expect_keys=["login", "id"],
    ),
    EndpointDef(
        id="github_repo",
        group="GitHub Core API",
        name="GET /repos/{repo}",
        method="GET",
        url=f"https://api.github.com/repos/{settings.github_repo}",
        auth_type="github_pat",
        description="获取仓库信息 (github_service.py 的 _repo_url 基础)",
        expect_keys=["full_name", "default_branch"],
    ),
    EndpointDef(
        id="github_rate_limit",
        group="GitHub Core API",
        name="GET /rate_limit",
        method="GET",
        url="https://api.github.com/rate_limit",
        auth_type="github_pat",
        description="GitHub API 速率限制 (健康检查)",
        expect_keys=["rate", "resources"],
    ),

    # ── GitHub Models API ──
    EndpointDef(
        id="models_api_list",
        group="GitHub Models API",
        name="GET /models",
        method="GET",
        url=f"{settings.github_models_endpoint}/models",
        auth_type="github_pat",
        description="模型列表 (models_api._fetch_github_models 主源)",
        expect_keys=[],  # 可能是 list 或 dict
    ),

    # ── GitHub Docs (定价数据源) ──
    EndpointDef(
        id="github_docs_pricing",
        group="GitHub Core API",
        name="GET copilot-requests (docs)",
        method="GET",
        url="https://docs.github.com/en/copilot/concepts/billing/copilot-requests",
        auth_type="none",
        description="Copilot 模型定价文档 (models_api._scrape_github_pricing 数据源)",
        expect_status=200,
        skip_if_no_auth=False,
    ),

    # ── Copilot OAuth (可达性检测, 不发送实际 OAuth 请求) ──
    EndpointDef(
        id="copilot_device_code_reachable",
        group="Copilot OAuth",
        name="POST /login/device/code (空 body)",
        method="POST",
        url="https://github.com/login/device/code",
        auth_type="none",
        description="设备流端点可达性 (copilot_auth.start_device_flow)",
        expect_status=200,  # 空 body 会返回 422 但说明端点存在
        skip_if_no_auth=False,
    ),
    EndpointDef(
        id="copilot_oauth_token_reachable",
        group="Copilot OAuth",
        name="POST /login/oauth/access_token (空 body)",
        method="POST",
        url="https://github.com/login/oauth/access_token",
        auth_type="none",
        description="OAuth token 交换端点可达性 (copilot_auth.poll_for_token)",
        expect_status=200,  # 空 body 仍返回 200 但 error=
        skip_if_no_auth=False,
    ),

    # ── Copilot Internal API (需要 OAuth token) ──
    EndpointDef(
        id="copilot_internal_token",
        group="Copilot Internal API",
        name="GET /copilot_internal/v2/token",
        method="GET",
        url="https://api.github.com/copilot_internal/v2/token",
        auth_type="copilot_oauth",
        description="获取短期 session token (copilot_auth.ensure_session)",
        expect_keys=["token", "expires_at"],
    ),
    EndpointDef(
        id="copilot_internal_user",
        group="Copilot Internal API",
        name="GET /copilot_internal/user",
        method="GET",
        url="https://api.github.com/copilot_internal/user",
        auth_type="copilot_oauth",
        description="用户 Copilot 信息 + 配额快照 (copilot_auth_api.usage)",
        expect_keys=["login", "copilot_plan", "quota_snapshots"],
    ),

    # ── Copilot Chat API (需要 session token) ──
    EndpointDef(
        id="copilot_api_models",
        group="Copilot Chat API",
        name="GET /models",
        method="GET",
        url="https://api.githubcopilot.com/models",
        auth_type="copilot_session",
        description="Copilot 可用模型列表 (models_api._fetch_copilot_api_models)",
    ),
    EndpointDef(
        id="copilot_api_agents",
        group="Copilot Chat API",
        name="GET /agents",
        method="GET",
        url="https://api.githubcopilot.com/agents",
        auth_type="copilot_session",
        description="Copilot agents 列表",
        expect_keys=["agents"],
    ),
    EndpointDef(
        id="copilot_api_chat",
        group="Copilot Chat API",
        name="POST /chat/completions (mini)",
        method="POST",
        url="https://api.githubcopilot.com/chat/completions",
        auth_type="copilot_session",
        description="Chat 补全端点 (ai_service.chat_stream 核心调用)",
        body={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Say OK"}],
            "max_tokens": 5,
            "stream": False,
        },
        expect_keys=["choices", "usage"],
    ),

    # ── Copilot Individual API (从 token 响应中获取的端点) ──
    EndpointDef(
        id="copilot_individual_models",
        group="Copilot Individual API",
        name="GET /models (individual)",
        method="GET",
        url="https://api.individual.githubcopilot.com/models",
        auth_type="copilot_session",
        description="Individual API 模型列表 (token.endpoints.api)",
    ),
]


# ==================== 探测执行 ====================

async def _probe_one(ep: EndpointDef, timeout: int = 15) -> Dict[str, Any]:
    """探测单个端点, 返回结果字典"""
    result: Dict[str, Any] = {
        "id": ep.id,
        "group": ep.group,
        "name": ep.name,
        "method": ep.method,
        "url": ep.url,
        "description": ep.description,
        "auth_type": ep.auth_type,
        "status": "pending",
        "http_status": 0,
        "latency_ms": 0,
        "message": "",
        "response_keys": [],
        "warnings": [],
    }

    # 构建请求头
    headers: Dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": "Studio/1.0",
    }

    # 根据 auth_type 添加认证
    if ep.auth_type == "github_pat":
        if not settings.github_token:
            result["status"] = "skipped"
            result["message"] = "未配置 GITHUB_TOKEN"
            return result
        headers["Authorization"] = f"Bearer {settings.github_token}"

    elif ep.auth_type == "copilot_oauth":
        if not copilot_auth.is_authenticated:
            if ep.skip_if_no_auth:
                result["status"] = "skipped"
                result["message"] = "Copilot 未授权"
                return result
        headers["Authorization"] = f"token {copilot_auth.oauth_token}"
        headers["editor-version"] = "vscode/1.96.0"
        headers["editor-plugin-version"] = "copilot-chat/0.24.0"

    elif ep.auth_type == "copilot_session":
        if not copilot_auth.is_authenticated:
            if ep.skip_if_no_auth:
                result["status"] = "skipped"
                result["message"] = "Copilot 未授权"
                return result
        try:
            session_token = await copilot_auth.ensure_session()
            headers["Authorization"] = f"Bearer {session_token}"
            headers["editor-version"] = "vscode/1.96.0"
            headers["editor-plugin-version"] = "copilot-chat/0.24.0"
            headers["copilot-integration-id"] = "vscode-chat"
            headers["openai-intent"] = "conversation-panel"
            headers["x-request-id"] = str(uuid.uuid4())
            headers["vscode-sessionid"] = str(uuid.uuid4())
            headers["vscode-machineid"] = "probe-machine"
        except Exception as e:
            result["status"] = "error"
            result["message"] = f"获取 session token 失败: {e}"
            return result

    # 执行请求
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if ep.method == "GET":
                resp = await client.get(ep.url, headers=headers)
            elif ep.method == "POST":
                headers["Content-Type"] = "application/json"
                resp = await client.post(ep.url, headers=headers, json=ep.body or {})
            else:
                resp = await client.request(ep.method, ep.url, headers=headers)

        latency = (time.monotonic() - t0) * 1000
        result["http_status"] = resp.status_code
        result["latency_ms"] = round(latency, 1)

        # 状态码校验 (允许一定灵活性)
        status_ok = resp.status_code == ep.expect_status
        # 对于无 body 的 POST 可达性测试，只要不是 5xx/DNS 错就算成功
        if ep.auth_type == "none" and ep.method == "POST":
            status_ok = resp.status_code < 500

        if not status_ok:
            result["status"] = "error"
            result["message"] = f"期望 HTTP {ep.expect_status}, 实际 {resp.status_code}"
            # 尝试获取错误详情
            try:
                err_body = resp.json()
                result["message"] += f" | {err_body.get('message', resp.text[:200])}"
            except Exception:
                result["message"] += f" | {resp.text[:200]}"
            return result

        # JSON 结构校验
        if ep.expect_keys:
            try:
                body_json = resp.json()
                if isinstance(body_json, dict):
                    result["response_keys"] = list(body_json.keys())[:20]
                    missing = [k for k in ep.expect_keys if k not in body_json]
                    if missing:
                        result["status"] = "warning"
                        result["warnings"].append(f"缺少字段: {missing}")
                        result["message"] = f"响应缺少预期字段: {missing}"
                    else:
                        result["status"] = "ok"
                        result["message"] = "响应结构正常"
                elif isinstance(body_json, list):
                    result["response_keys"] = [f"list[{len(body_json)}]"]
                    result["status"] = "ok"
                    result["message"] = f"返回 {len(body_json)} 项"
                else:
                    result["status"] = "warning"
                    result["warnings"].append("响应格式非预期 (非 dict/list)")
                    result["message"] = "响应格式异常"
            except Exception:
                result["status"] = "warning"
                result["warnings"].append("响应不是有效 JSON")
                result["message"] = "响应解析失败"
        else:
            # 无 expect_keys 时只验证状态码
            result["status"] = "ok"
            result["message"] = f"HTTP {resp.status_code}"
            # 尝试提取返回体结构信息
            try:
                body_json = resp.json()
                if isinstance(body_json, dict):
                    result["response_keys"] = list(body_json.keys())[:20]
                elif isinstance(body_json, list):
                    result["response_keys"] = [f"list[{len(body_json)}]"]
            except Exception:
                pass

        # 延迟警告
        if latency > 5000:
            result["warnings"].append(f"响应较慢: {latency:.0f}ms")

    except httpx.TimeoutException:
        result["status"] = "error"
        result["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
        result["message"] = f"请求超时 ({timeout}s)"
    except httpx.ConnectError as e:
        result["status"] = "error"
        result["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
        result["message"] = f"连接失败: {e}"
    except Exception as e:
        result["status"] = "error"
        result["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
        result["message"] = f"探测异常: {type(e).__name__}: {e}"

    return result


# ==================== API 端点 ====================

@router.get("/endpoints")
async def list_endpoints():
    """列出所有受监控的外部端点 (不执行探测)"""
    return [
        {
            "id": ep.id,
            "group": ep.group,
            "name": ep.name,
            "method": ep.method,
            "url": ep.url,
            "auth_type": ep.auth_type,
            "description": ep.description,
        }
        for ep in ENDPOINT_REGISTRY
    ]


@router.post("/test-all")
async def test_all_endpoints(timeout: int = Query(15, ge=5, le=60)):
    """
    批量探测所有端点

    并发执行所有探测，返回每个端点的状态。
    """
    t0 = time.monotonic()
    tasks = [_probe_one(ep, timeout=timeout) for ep in ENDPOINT_REGISTRY]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 处理异常
    final_results = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            final_results.append({
                "id": ENDPOINT_REGISTRY[i].id,
                "group": ENDPOINT_REGISTRY[i].group,
                "name": ENDPOINT_REGISTRY[i].name,
                "status": "error",
                "message": f"探测异常: {r}",
            })
        else:
            final_results.append(r)

    total_ms = round((time.monotonic() - t0) * 1000, 1)

    # 统计
    ok_count = sum(1 for r in final_results if r.get("status") == "ok")
    warn_count = sum(1 for r in final_results if r.get("status") == "warning")
    err_count = sum(1 for r in final_results if r.get("status") == "error")
    skip_count = sum(1 for r in final_results if r.get("status") == "skipped")

    return {
        "total": len(final_results),
        "ok": ok_count,
        "warning": warn_count,
        "error": err_count,
        "skipped": skip_count,
        "total_ms": total_ms,
        "results": final_results,
    }


@router.post("/test-one/{endpoint_id}")
async def test_one_endpoint(
    endpoint_id: str,
    timeout: int = Query(15, ge=5, le=60),
):
    """探测单个端点"""
    ep = next((e for e in ENDPOINT_REGISTRY if e.id == endpoint_id), None)
    if not ep:
        return {"status": "error", "message": f"未知端点 ID: {endpoint_id}"}
    return await _probe_one(ep, timeout=timeout)
