"""
Observability API — AI 可观测性接口

提供 Trace、指标、预算查询端点。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from typing import Optional

from studio.backend.core.security import get_studio_user

router = APIRouter(prefix="/studio-api/observability", tags=["observability"])


@router.get("/traces")
async def get_traces(
    project_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    _user=Depends(get_studio_user),
):
    """获取最近的 AI 调用 trace"""
    from studio.backend.ai.observability.tracer import get_tracer
    tracer = get_tracer()
    return {"traces": tracer.get_recent(limit, project_id)}


@router.get("/traces/stats")
async def get_trace_stats(
    project_id: Optional[str] = Query(None),
    _user=Depends(get_studio_user),
):
    """获取 trace 汇总统计"""
    from studio.backend.ai.observability.tracer import get_tracer
    tracer = get_tracer()
    return tracer.get_stats(project_id)


@router.get("/metrics")
async def get_metrics_dashboard(
    project_id: Optional[str] = Query(None),
    _user=Depends(get_studio_user),
):
    """获取指标仪表盘数据"""
    from studio.backend.ai.observability.metrics import get_metrics
    metrics = get_metrics()
    return metrics.get_dashboard_data(project_id)


@router.get("/budget")
async def get_budget_status(
    project_id: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    _user=Depends(get_studio_user),
):
    """获取预算使用情况"""
    from studio.backend.ai.observability.budget import get_budget_manager
    mgr = get_budget_manager()

    check = mgr.check_budget(
        session_id=session_id or "",
        project_id=project_id or "",
    )
    usage = mgr.get_usage_summary(project_id)

    return {
        "allowed": check["allowed"],
        "warnings": check["warnings"],
        "usage": usage,
    }


@router.get("/rag/status")
async def get_rag_status(_user=Depends(get_studio_user)):
    """获取 RAG 索引状态"""
    from studio.backend.ai.rag.index import get_vector_index
    from studio.backend.ai.rag.indexer import get_indexer

    index = get_vector_index()
    indexer = get_indexer()

    return {
        "index_size": index.size,
        "indexer_running": indexer.is_running,
        "indexed_files": indexer.indexed_count,
    }


@router.post("/rag/reindex")
async def trigger_reindex(_user=Depends(get_studio_user)):
    """手动触发重新索引"""
    from studio.backend.ai.rag.indexer import get_indexer
    indexer = get_indexer()
    stats = await indexer.index_once()
    return {"status": "ok", "stats": stats}


@router.get("/memory")
async def get_memory_items(
    project_id: Optional[str] = Query(None),
    memory_type: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    _user=Depends(get_studio_user),
):
    """获取记忆项列表"""
    from studio.backend.ai.memory.store import get_memory_store, MemoryType

    store = get_memory_store()
    mtype = MemoryType(memory_type) if memory_type else None
    items = await store.list_recent(
        project_id=project_id,
        memory_type=mtype,
        limit=limit,
    )
    return {
        "items": [
            {
                "id": item.id,
                "content": item.content,
                "type": item.memory_type.value,
                "project_id": item.project_id,
                "importance": item.importance,
                "tags": item.tags,
                "source": item.source,
                "created_at": item.created_at,
            }
            for item in items
        ]
    }


@router.delete("/memory/{memory_id}")
async def delete_memory_item(
    memory_id: str,
    _user=Depends(get_studio_user),
):
    """删除记忆项"""
    from studio.backend.ai.memory.store import get_memory_store
    store = get_memory_store()
    removed = await store.remove(memory_id)
    return {"removed": removed}
