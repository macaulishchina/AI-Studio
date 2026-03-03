"""
Memory API — 记忆管理端点 (v2)

提供:
- 记忆查询/搜索/创建/更新/删除
- 用户画像 + 统计
- 合并 + 清空
- 记忆配置 GET/PUT
"""
import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.core.security import get_optional_studio_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/memory", tags=["Memory"])


# ==================== Schemas ====================

class MemoryItemOut(BaseModel):
    id: str
    content: str
    memory_type: str
    project_id: Optional[str] = None
    user_id: Optional[str] = None
    conversation_id: Optional[int] = None
    importance: float = 0.5
    tags: list = []
    source: str = ""
    access_count: int = 0
    created_at: float = 0
    updated_at: float = 0

    class Config:
        from_attributes = True


class MemoryUpdateRequest(BaseModel):
    content: Optional[str] = None
    importance: Optional[float] = Field(None, ge=0, le=1)


class MemoryCreateRequest(BaseModel):
    content: str = Field(..., max_length=2000)
    memory_type: str = Field("fact", pattern="^(fact|decision|preference|episode|profile)$")
    importance: float = Field(0.5, ge=0, le=1)
    tags: List[str] = []


def _resolve_user_id(user: Optional[dict]) -> str:
    """从 JWT 用户信息解析 user_id (统一用 username)"""
    if user:
        return user.get("username", user.get("nickname", "user"))
    return "user"


def _item_to_dict(item) -> dict:
    return {
        "id": item.id,
        "content": item.content,
        "memory_type": item.memory_type,
        "project_id": item.project_id,
        "user_id": item.user_id,
        "conversation_id": item.conversation_id,
        "importance": item.importance,
        "tags": item.tags or [],
        "source": item.source or "",
        "access_count": item.access_count or 0,
        "created_at": item.created_at or 0,
        "updated_at": item.updated_at or 0,
    }


# ==================== CRUD Endpoints ====================

@router.get("", response_model=List[MemoryItemOut])
async def list_memories(
    user_id: Optional[str] = None,
    conversation_id: Optional[int] = None,
    memory_type: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """查询记忆列表"""
    from backend.ai.memory.store import get_memory_store
    store = get_memory_store()

    uid = user_id or _resolve_user_id(user)

    if conversation_id:
        items = await store.list_by_conversation(conversation_id, limit=limit)
    elif query:
        items = await store.search(
            query=query, user_id=uid,
            memory_type=memory_type, top_k=limit,
        )
    else:
        items = await store.list_recent(
            user_id=uid, memory_type=memory_type, limit=limit,
        )

    return [_item_to_dict(it) for it in items]


@router.get("/profile")
async def get_user_profile(
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """获取当前用户画像"""
    from backend.ai.memory.user_memory import get_memory_service
    svc = get_memory_service()
    uid = _resolve_user_id(user)
    profile = await svc.get_profile(uid)
    stats = await svc.get_stats(uid)
    return {"profile": profile, "stats": stats}


@router.post("")
async def create_memory(
    data: MemoryCreateRequest,
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """手动添加记忆"""
    from backend.ai.memory.store import get_memory_store
    store = get_memory_store()
    uid = _resolve_user_id(user)

    mid = await store.add(
        content=data.content,
        memory_type=data.memory_type,
        user_id=uid,
        importance=data.importance,
        tags=data.tags,
        source="manual",
    )
    return {"id": mid, "status": "created"}


@router.patch("/{memory_id}")
async def update_memory(
    memory_id: str,
    data: MemoryUpdateRequest,
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """更新记忆内容/重要性"""
    from backend.ai.memory.store import get_memory_store
    store = get_memory_store()
    existing = await store.get(memory_id)
    if not existing:
        raise HTTPException(status_code=404, detail="记忆不存在")

    if data.content is not None:
        await store.update_content(memory_id, data.content)
    if data.importance is not None:
        await store.update_importance(memory_id, data.importance)

    return {"status": "updated"}


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """删除单条记忆"""
    from backend.ai.memory.store import get_memory_store
    store = get_memory_store()
    ok = await store.remove(memory_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return {"status": "deleted"}


@router.post("/consolidate")
async def consolidate_memories(
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """手动触发记忆合并"""
    from backend.ai.memory.user_memory import get_memory_service
    svc = get_memory_service()
    uid = _resolve_user_id(user)
    removed = await svc.consolidate(uid)
    stats = await svc.get_stats(uid)
    return {"removed": removed, "stats": stats}


@router.get("/stats")
async def memory_stats(
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """记忆统计"""
    from backend.ai.memory.user_memory import get_memory_service
    svc = get_memory_service()
    uid = _resolve_user_id(user)
    return await svc.get_stats(uid)


@router.post("/clear")
async def clear_memories(
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """清空当前用户所有记忆"""
    from backend.ai.memory.user_memory import get_memory_service
    svc = get_memory_service()
    uid = _resolve_user_id(user)
    removed = await svc.clear(uid)
    return {"removed": removed, "status": "cleared"}


# ==================== Config Endpoints ====================

@router.get("/config")
async def get_memory_config_api(
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """获取记忆系统配置"""
    from backend.services.config_service import get_memory_config
    return await get_memory_config()


@router.put("/config")
async def update_memory_config_api(
    data: dict,
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """更新记忆系统配置"""
    from backend.services.config_service import set_memory_config
    return await set_memory_config(data)
