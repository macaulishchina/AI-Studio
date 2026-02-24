"""
AI 任务 API — 前端订阅/查询/取消后台 AI 任务

核心端点:
  GET  /projects/{pid}/active-task          获取项目当前活跃任务
  GET  /studio-api/tasks/{tid}/stream       SSE 订阅任务事件流 (支持重连/多客户端)
  GET  /studio-api/tasks/{tid}/status       获取任务当前状态 (轻量轮询)
  POST /studio-api/tasks/{tid}/cancel       取消运行中的任务
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.database import get_db
from studio.backend.core.security import get_optional_studio_user
from studio.backend.models import AiTask
from studio.backend.services.task_runner import TaskManager, ProjectEventBus

logger = logging.getLogger(__name__)

# 两个 router: 一个挂 /projects 前缀, 一个挂 /tasks 前缀
project_router = APIRouter(prefix="/studio-api/projects", tags=["Tasks"])
task_router = APIRouter(prefix="/studio-api/tasks", tags=["Tasks"])


# ==================== Schemas ====================

class TaskStatusResponse(BaseModel):
    task_id: int
    project_id: int
    task_type: str
    status: str  # pending / running / completed / failed / cancelled
    model: str = ""
    has_content: bool = False
    has_error: bool = False
    error_message: str = ""
    result_message_id: Optional[int] = None
    created_at: Optional[datetime] = None


# ==================== 项目活跃任务 ====================

@project_router.get("/{project_id}/active-task")
async def get_active_task(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    获取项目当前活跃的 AI 任务.
    前端在 mount 时调用, 如果有 running 任务则自动订阅事件流.
    """
    # 优先查内存 (有实时事件)
    rt = TaskManager.get_project_active_task(project_id)
    if rt:
        return {
            "active": True,
            "task_id": rt.task_id,
            "status": rt.status,
            "has_content": bool(rt.content),
            "events_count": len(rt.events),
        }

    # 查 DB: 最近 running/pending 任务 (可能服务刚重启)
    result = await db.execute(
        select(AiTask)
        .where(AiTask.project_id == project_id, AiTask.status.in_(["pending", "running"]))
        .order_by(AiTask.created_at.desc())
        .limit(1)
    )
    task = result.scalar_one_or_none()
    if task:
        return {
            "active": True,
            "task_id": task.id,
            "status": task.status,
            "has_content": bool(task.output_content),
            "events_count": 0,
        }

    return {"active": False}


@project_router.get("/{project_id}/active-tasks")
async def get_active_tasks(project_id: int):
    """获取项目所有活跃 AI 任务 (多任务并发)"""
    tasks = TaskManager.get_project_active_tasks(project_id)
    return {
        "tasks": [
            {
                "task_id": rt.task_id,
                "status": rt.status,
                "model": rt.model,
                "sender_name": rt.sender_name,
                "has_content": bool(rt.content),
                "events_count": len(rt.events),
            }
            for rt in tasks
        ]
    }


@project_router.get("/{project_id}/events")
async def stream_project_events(
    project_id: int,
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """
    SSE 项目事件总线: 单连接接收项目内所有实时事件.

    事件类型:
    - task_started: 新 AI 任务开始 (model, sender_name)
    - content/thinking/tool_call/done/error 等: AI 任务事件 (带 task_id)
    - new_message: 新用户消息
    - message_deleted: 消息删除
    - heartbeat: 心跳保活
    """
    bus = ProjectEventBus.get_or_create(project_id)
    replay, queue = bus.subscribe()

    async def event_gen():
        try:
            # replay: 所有活跃任务的历史事件 (断线重连恢复)
            for i, event in enumerate(replay):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if (i + 1) % 5 == 0:
                    await asyncio.sleep(0)

            # 实时流新事件
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'heartbeat'}, ensure_ascii=False)}\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== SSE 事件流订阅 ====================

@task_router.get("/{task_id}/stream")
async def stream_task_events(
    task_id: int,
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """
    SSE 订阅: 获取任务的实时事件流.

    - 如果任务仍在运行: 返回历史事件 (replay) + 后续新事件
    - 如果任务已完成且仍在缓存中: 返回所有缓存事件
    - 如果任务不在缓存中: 从 DB 重建最终状态事件
    """
    # 1. 查内存中的任务
    rt = TaskManager.get_running_task(task_id)
    if rt:
        replay, queue = rt.subscribe()

        async def event_gen():
            try:
                # 先发送所有历史事件 (每几个事件穿插 asyncio.sleep(0)
                # 让 ASGI 服务器有机会刷新缓冲区, 使前端能增量渲染)
                for i, event in enumerate(replay):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if (i + 1) % 5 == 0:
                        await asyncio.sleep(0)

                # 如果任务已完成就不需要等新事件了
                if rt.status in ("completed", "failed", "cancelled"):
                    return

                # 实时流新事件
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=25)
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                        etype = event.get("type", "")
                        if etype in ("done", "cancelled"):
                            break
                        if etype == "error" and not rt.status == "running":
                            break
                    except asyncio.TimeoutError:
                        # 心跳保持连接
                        yield f"data: {json.dumps({'type': 'heartbeat'}, ensure_ascii=False)}\n\n"
            finally:
                rt.unsubscribe(queue)

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # 2. 不在内存中 → 查 DB
    async with async_session_maker() as db:
        result = await db.execute(select(AiTask).where(AiTask.id == task_id))
        task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 从 DB 状态重建事件
    async def replay_from_db():
        if task.output_content:
            yield f"data: {json.dumps({'type': 'content', 'content': task.output_content}, ensure_ascii=False)}\n\n"
        if task.thinking_content:
            yield f"data: {json.dumps({'type': 'thinking', 'content': task.thinking_content}, ensure_ascii=False)}\n\n"
        if task.token_usage:
            yield f"data: {json.dumps({'type': 'usage', 'usage': task.token_usage}, ensure_ascii=False)}\n\n"
        if task.status == "failed" and task.error_message:
            yield f"data: {json.dumps({'type': 'error', 'error': task.error_message}, ensure_ascii=False)}\n\n"
        elif task.status == "cancelled":
            yield f"data: {json.dumps({'type': 'cancelled'}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'message_id': task.result_message_id or -1}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        replay_from_db(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== 任务状态查询 ====================

@task_router.get("/{task_id}/status", response_model=TaskStatusResponse)
async def get_task_status(
    task_id: int,
    db: AsyncSession = Depends(get_db),
):
    """轻量级任务状态查询 (用于快速轮询)"""
    # 优先查内存
    rt = TaskManager.get_running_task(task_id)
    if rt:
        return TaskStatusResponse(
            task_id=rt.task_id,
            project_id=rt.project_id,
            task_type="discuss",
            status=rt.status,
            has_content=bool(rt.content),
            has_error=bool(rt.error),
            error_message=rt.error[:200] if rt.error else "",
            result_message_id=rt.result_message_id,
        )

    # 查 DB
    result = await db.execute(select(AiTask).where(AiTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return TaskStatusResponse(
        task_id=task.id,
        project_id=task.project_id,
        task_type=task.task_type,
        status=task.status,
        model=task.model or "",
        has_content=bool(task.output_content),
        has_error=bool(task.error_message),
        error_message=(task.error_message or "")[:200],
        result_message_id=task.result_message_id,
        created_at=task.created_at,
    )


# ==================== 取消任务 ====================

@task_router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: int,
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """取消正在运行的任务"""
    await TaskManager.cancel_task(task_id)
    return {"ok": True, "task_id": task_id}


# ==================== 命令审批 ====================

class CommandApprovalRequest(BaseModel):
    approved: bool
    scope: str = "once"  # once | session | project | permanent
    all_commands: bool = False  # 是否授权所有命令 (创建 * 通配符规则)

@task_router.post("/{task_id}/approve-command")
async def approve_command(
    task_id: int,
    body: CommandApprovalRequest,
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """
    用户批准/拒绝 AI 请求执行的写命令.

    scope 授权范围:
    - once:      仅本次命令
    - session:   本次 AI 回复内的同类命令自动批准
    - project:   本项目内所有写命令自动批准 (写入项目配置)
    - permanent: 创建全局永久授权规则 (所有项目的同类命令自动批准)
    """
    rt = TaskManager.get_running_task(task_id)
    if not rt:
        raise HTTPException(status_code=404, detail="任务不存在或已结束")

    if rt._command_approval_event is None:
        raise HTTPException(status_code=409, detail="当前没有待审批的命令")

    rt.resolve_command_approval(body.approved, body.scope, body.all_commands)
    return {"ok": True, "task_id": task_id, "approved": body.approved, "scope": body.scope}


# 需要在 tasks.py 模块中也能用 async_session_maker
from studio.backend.core.database import async_session_maker
