"""
Dogi - 独立对话 API (Conversations)

不绑定项目的自由对话, 支持:
- 对话 CRUD (创建/列表/查看/更新/删除)
- 消息发送 (SSE 流式输出)
- 消息历史
- 上下文管理 (总结/清空)
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.config import settings
from studio.backend.core.database import get_db
from studio.backend.core.security import get_studio_user, get_optional_studio_user
from studio.backend.models import Conversation, Message, MessageRole, MessageType, Role, AiTask

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/conversations", tags=["Conversations"])


# ==================== Schemas ====================

class ConversationCreate(BaseModel):
    title: str = Field("新对话", max_length=200)
    model: str = Field("gpt-4o", max_length=100)
    role_id: Optional[int] = None
    tool_permissions: Optional[List[str]] = None


class ConversationUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    model: Optional[str] = Field(None, max_length=100)
    role_id: Optional[int] = None
    tool_permissions: Optional[List[str]] = None
    is_pinned: Optional[bool] = None
    is_archived: Optional[bool] = None


class ConversationOut(BaseModel):
    id: int
    title: str
    model: str
    tool_permissions: list = []
    role_id: Optional[int] = None
    is_pinned: bool = False
    is_archived: bool = False
    created_by: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MessageOut(BaseModel):
    id: int
    role: MessageRole
    sender_name: str
    content: str
    message_type: MessageType
    attachments: list = []
    model_used: Optional[str] = None
    token_usage: Optional[dict] = None
    thinking_content: Optional[str] = None
    tool_calls: Optional[list] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ConversationDiscussRequest(BaseModel):
    message: str = Field("", max_length=10000)
    sender_name: str = Field("user", max_length=100)
    attachments: List[dict] = Field(default_factory=list)
    regenerate: bool = Field(False)
    max_tool_rounds: int = Field(15, ge=0, le=100)


# ==================== CRUD ====================

@router.get("", response_model=List[ConversationOut])
async def list_conversations(
    archived: bool = False,
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """列出对话 (默认不含已归档)"""
    q = select(Conversation).where(Conversation.is_archived == archived)
    q = q.order_by(desc(Conversation.is_pinned), desc(Conversation.updated_at))
    result = await db.execute(q)
    return result.scalars().all()


@router.post("", response_model=ConversationOut)
async def create_conversation(
    data: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """创建新对话"""
    conv = Conversation(
        title=data.title,
        model=data.model,
        role_id=data.role_id,
        tool_permissions=data.tool_permissions or [
            "ask_user", "read_source", "read_config", "search", "tree", "execute_readonly_command"
        ],
        created_by=user.get("nickname", "user") if user else "user",
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv


@router.get("/{conv_id}", response_model=ConversationOut)
async def get_conversation(
    conv_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取对话详情"""
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conv


@router.patch("/{conv_id}", response_model=ConversationOut)
async def update_conversation(
    conv_id: int,
    data: ConversationUpdate,
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """更新对话设置"""
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")

    if data.title is not None:
        conv.title = data.title
    if data.model is not None:
        conv.model = data.model
    if data.role_id is not None:
        conv.role_id = data.role_id
    if data.tool_permissions is not None:
        conv.tool_permissions = data.tool_permissions
    if data.is_pinned is not None:
        conv.is_pinned = data.is_pinned
    if data.is_archived is not None:
        conv.is_archived = data.is_archived

    await db.commit()
    await db.refresh(conv)
    return conv


@router.delete("/{conv_id}")
async def delete_conversation(
    conv_id: int,
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """删除对话及其所有消息"""
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")
    await db.delete(conv)
    await db.commit()
    return {"status": "deleted"}


# ==================== Messages ====================

@router.get("/{conv_id}/messages", response_model=List[MessageOut])
async def get_messages(
    conv_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取对话消息历史"""
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id)
        .order_by(Message.created_at)
    )
    return result.scalars().all()


@router.delete("/{conv_id}/messages/{message_id}")
async def delete_message(
    conv_id: int,
    message_id: int,
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """删除单条消息"""
    result = await db.execute(
        select(Message).where(Message.id == message_id, Message.conversation_id == conv_id)
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="消息不存在")
    await db.delete(msg)
    await db.commit()
    return {"status": "deleted"}


# ==================== Discuss ====================

@router.post("/{conv_id}/discuss")
async def discuss(
    conv_id: int,
    data: ConversationDiscussRequest,
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """
    发送消息到对话, 启动 AI 后台任务.
    返回 {"task_id": N} — 前端通过 GET /studio-api/tasks/{task_id}/stream 订阅 SSE.
    """
    from studio.backend.services.task_runner import TaskManager, ProjectEventBus

    # 获取对话
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")

    # 确定发送者
    sender = data.sender_name
    if user:
        sender = user.get("nickname", user.get("username", sender))

    # 保存用户消息 (regenerate 模式跳过)
    user_message_id = None
    if not data.regenerate:
        user_msg = Message(
            conversation_id=conv_id,
            role=MessageRole.user,
            sender_name=sender,
            content=data.message,
            message_type=MessageType.image if data.attachments else MessageType.chat,
            attachments=data.attachments,
        )
        db.add(user_msg)

    # 更新对话时间 + 自动标题
    conv.updated_at = datetime.utcnow()
    if conv.title == "新对话" and data.message:
        conv.title = data.message[:50]

    await db.commit()

    # 广播用户消息
    if not data.regenerate and user_msg:
        user_message_id = user_msg.id
        # 使用负数 conv_id 作为 event bus key 避免与 project_id 冲突
        bus = ProjectEventBus.get_or_create(-conv_id)
        bus.publish({
            "type": "new_message",
            "message": {
                "id": user_msg.id,
                "role": "user",
                "sender_name": sender,
                "content": data.message,
                "attachments": data.attachments or [],
                "created_at": user_msg.created_at.isoformat() if user_msg.created_at else None,
            },
        })

    model = conv.model or "gpt-4o"

    # 启动后台 AI 任务
    task_id = await TaskManager.start_conversation_task(
        conversation_id=conv_id,
        model=model,
        sender_name=sender,
        message=data.message,
        attachments=data.attachments,
        max_tool_rounds=data.max_tool_rounds,
        regenerate=data.regenerate,
    )

    return {"task_id": task_id, "user_message_id": user_message_id}


# ==================== Context Management ====================

@router.delete("/{conv_id}/clear-context")
async def clear_context(
    conv_id: int,
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """清空对话上下文 (删除所有消息)"""
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")

    await db.execute(
        Message.__table__.delete().where(Message.conversation_id == conv_id)
    )
    conv.memory_summary = None
    await db.commit()
    return {"status": "cleared"}


@router.post("/{conv_id}/summarize-context")
async def summarize_context(
    conv_id: int,
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """手动触发上下文总结"""
    from studio.backend.services.context_manager import _generate_summary

    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="对话不存在")

    # 获取所有消息
    msg_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id)
        .order_by(Message.created_at)
    )
    messages = msg_result.scalars().all()

    if not messages:
        return {"status": "no_messages"}

    # 生成摘要
    full_text = "\n".join(
        f"{m.role.value}: {m.content[:500]}" for m in messages
    )
    summary = await _generate_summary(full_text, conv.model or "gpt-4o")
    conv.memory_summary = summary
    await db.commit()

    return {"status": "summarized", "summary_length": len(summary)}
