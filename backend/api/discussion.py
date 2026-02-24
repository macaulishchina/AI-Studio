"""
设计院 (Studio) - AI 讨论 API
支持多轮对话、图片上传、流式输出、Tool Calling、Plan 生成

SSE 事件协议:
  {"type": "content",     "content": "..."}          - 文本内容增量
  {"type": "thinking",    "content": "..."}          - 推理思考过程
  {"type": "tool_call",   "tool_call": {...}}        - AI 请求调用工具
  {"type": "tool_result", "tool_call_id": "...", ...}- 工具执行结果
  {"type": "tool_error",  "tool_call_id": "...", ...}- 工具执行失败
  {"type": "usage",       "usage": {...}}            - token 使用统计
  {"type": "context",     "context": {...}}          - 上下文管理信息
  {"type": "done",        "message_id": N}           - 完成
  {"type": "error",       "error": "..."}            - 错误
"""
import json
import logging
import os
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.config import settings
from studio.backend.core.database import get_db
from studio.backend.core.model_capabilities import capability_cache
from studio.backend.core.project_types import get_role_for_status, get_ui_labels
from studio.backend.core.security import get_studio_user, get_optional_studio_user
from studio.backend.models import Project, Message, MessageRole, MessageType, ProjectStatus, Role
from studio.backend.services import ai_service, context_service
from studio.backend.services.ai_service import new_request_id
from studio.backend.services.context_manager import prepare_context, build_usage_summary, summarize_context_if_needed, _generate_summary
from studio.backend.services.tool_registry import get_tool_definitions, execute_tool, DEFAULT_PERMISSIONS
from studio.backend.core.token_utils import estimate_tokens, truncate_text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/projects", tags=["Discussion"])


async def _resolve_active_role(project: Project, db: AsyncSession):
    """
    根据 project_type + 当前 status 解析当前活跃的 Role ORM 对象.
    返回 (role_obj_or_None, ui_labels_dict)
    """
    type_key = getattr(project, 'project_type', None) or 'requirement'
    status = project.status.value if hasattr(project.status, 'value') else str(project.status)
    ui_labels = get_ui_labels(type_key)

    role_name = get_role_for_status(type_key, status)
    if not role_name:
        # 当前阶段没有绑定 role (如 draft, implementing, deploying)
        # 回退: 取讨论阶段的 role
        role_name = get_role_for_status(type_key, 'discussing')

    role_obj = None
    if role_name:
        result = await db.execute(
            select(Role).where(Role.name == role_name, Role.is_enabled.is_(True))
        )
        role_obj = result.scalar_one_or_none()

    return role_obj, ui_labels


async def _check_model_supports_tools(model: str) -> bool:
    """检查模型是否支持 tool calling (查询模型缓存)"""
    try:
        from studio.backend.api.models_api import _model_cache
        models = await _model_cache.get_models()
        for m in models:
            if m.id == model:
                return m.supports_tools
    except Exception:
        pass
    # 缓存不可用时保守地允许 (避免阻断正常流程)
    return True


# ==================== Schemas ====================

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
    parent_message_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class DiscussRequest(BaseModel):
    message: str = Field("", max_length=10000)
    sender_name: str = Field("user", max_length=100)
    # 图片通过单独的上传接口处理, 这里传已上传的 attachment 引用
    attachments: List[dict] = Field(default_factory=list)
    regenerate: bool = Field(False, description="重新生成: 不保存用户消息, 直接用历史生成 AI 回复")
    max_tool_rounds: int = Field(15, ge=0, le=100, description="工具调用轮次上限 (前端根据免费/付费模型配置)")


# ==================== AI 禁言控制 ====================

@router.post("/{project_id}/ai-mute")
async def toggle_ai_mute(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(get_studio_user),
):
    """切换 AI 禁言状态"""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    project.ai_muted = not project.ai_muted
    await db.commit()
    status = "muted" if project.ai_muted else "unmuted"
    logger.info(f"AI {'禁言' if project.ai_muted else '解除禁言'} by {user['nickname']}")
    return {"ai_muted": project.ai_muted, "status": status}


@router.get("/{project_id}/ai-mute")
async def get_ai_mute_status(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取 AI 禁言状态"""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"ai_muted": project.ai_muted}


@router.get("/{project_id}/streaming-status")
async def get_streaming_status(project_id: int):
    """获取 AI 是否正在生成 (兼容旧前端 + 新 task 系统)"""
    from studio.backend.services.task_runner import TaskManager
    rt = TaskManager.get_project_active_task(project_id)
    return {"streaming": rt is not None and rt.status == "running"}


# ==================== 图片上传 ====================

@router.post("/{project_id}/upload-image")
async def upload_image(
    project_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    上传讨论用的图片
    返回图片 URL 和 base64, 供后续讨论消息引用
    """
    # 验证项目存在
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="项目不存在")

    # 验证文件类型
    allowed_types = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"不支持的图片格式: {file.content_type}")

    # 限制大小 10MB
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片不能超过 10MB")

    # 保存文件
    ext = file.filename.split(".")[-1] if "." in file.filename else "png"
    filename = f"{uuid.uuid4().hex}.{ext}"
    upload_dir = os.path.join(settings.uploads_path, str(project_id))
    os.makedirs(upload_dir, exist_ok=True)
    filepath = os.path.join(upload_dir, filename)

    with open(filepath, "wb") as f:
        f.write(content)

    # 生成 base64 (用于 AI 调用)
    base64_data = ai_service.encode_image_to_base64(content)
    mime_type = file.content_type

    return {
        "filename": filename,
        "url": f"/studio-uploads/{project_id}/{filename}",
        "base64": base64_data,
        "mime_type": mime_type,
        "size": len(content),
    }


# ==================== 讨论对话 ====================

@router.post("/{project_id}/discuss")
async def discuss(
    project_id: int,
    data: DiscussRequest,
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_studio_user),
):
    """
    发送讨论消息，返回 AI 后台任务 ID.
    前端通过 GET /studio-api/tasks/{task_id}/stream 订阅 SSE 事件流.

    返回:
      {"task_id": N}           — AI 任务已启动
      {"status": "queued", "active_task_id": N} — 已有任务在运行, 消息已保存
      {"status": "muted"}      — AI 已禁言, 消息已保存
    """
    from studio.backend.services.task_runner import TaskManager, ProjectEventBus

    # 获取项目
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 更新项目状态
    if project.status == ProjectStatus.draft:
        project.status = ProjectStatus.discussing

    # 确定发送者名称 (优先使用认证用户)
    sender = data.sender_name
    if user:
        sender = user.get("nickname", user.get("username", sender))

    # 保存用户消息 (regenerate 模式跳过)
    user_message_id = None
    if not data.regenerate:
        user_msg = Message(
            project_id=project_id,
            role=MessageRole.user,
            sender_name=sender,
            content=data.message,
            message_type=MessageType.image if data.attachments else MessageType.chat,
            attachments=data.attachments,
        )
        db.add(user_msg)

    # ⚠️ 提前 commit: 释放 SQLite 写锁
    await db.commit()

    # 获取用户消息 ID 并广播到项目事件总线
    if not data.regenerate and user_msg:
        user_message_id = user_msg.id
        bus = ProjectEventBus.get_or_create(project_id)
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

    # 检查 AI 禁言模式 → 仅保存消息, 不触发 AI
    if project.ai_muted and not data.regenerate:
        return {"status": "muted", "message": "AI 已禁言, 消息已保存", "user_message_id": user_message_id}

    model = project.discussion_model or "gpt-4o"

    # 启动后台 AI 任务
    task_id = await TaskManager.start_discussion_task(
        project_id=project_id,
        model=model,
        sender_name=sender,
        message=data.message,
        attachments=data.attachments,
        max_tool_rounds=data.max_tool_rounds,
        regenerate=data.regenerate,
    )

    return {"task_id": task_id, "user_message_id": user_message_id}


# ==================== 消息历史 ====================

@router.get("/{project_id}/messages", response_model=List[MessageOut])
async def get_messages(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取项目讨论历史"""
    result = await db.execute(
        select(Message)
        .where(Message.project_id == project_id)
        .order_by(Message.created_at)
    )
    return result.scalars().all()


# ==================== 消息操作 ====================

@router.delete("/{project_id}/messages/{message_id}")
async def delete_message(
    project_id: int,
    message_id: int,
    db: AsyncSession = Depends(get_db),
):
    """删除单条消息"""
    result = await db.execute(
        select(Message).where(
            Message.id == message_id,
            Message.project_id == project_id,
        )
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="消息不存在")
    await db.delete(msg)
    await db.commit()

    # 广播消息删除事件到项目总线
    from studio.backend.services.task_runner import ProjectEventBus
    bus = ProjectEventBus.get(project_id)
    if bus:
        bus.publish({"type": "message_deleted", "message_id": message_id})

    return {"ok": True}


@router.post("/{project_id}/summarize-context")
async def manual_summarize_context(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_optional_studio_user),
):
    """
    手动触发上下文总结: 将旧消息压缩为一条总结，保留最近消息
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    model = project.discussion_model or "gpt-4o"

    msg_result = await db.execute(
        select(Message).where(Message.project_id == project_id).order_by(Message.created_at)
    )
    history_msgs = list(msg_result.scalars().all())
    if len(history_msgs) < 4:
        raise HTTPException(status_code=400, detail="消息太少，无需总结")

    # 保留最近 4 条，其余进行总结
    min_keep = min(len(history_msgs), 4)
    to_summarize_msgs = [{"role": msg.role.value, "content": msg.content} for msg in history_msgs[:-min_keep]]
    keep_msgs = history_msgs[-min_keep:]

    if not to_summarize_msgs:
        raise HTTPException(status_code=400, detail="消息太少，无需总结")

    summary_text = await _generate_summary(to_summarize_msgs, model)
    if not summary_text:
        raise HTTPException(status_code=500, detail="总结生成失败，请重试")

    # 删除被总结的旧消息
    keep_ids = {m.id for m in keep_msgs}
    for msg in history_msgs:
        if msg.id not in keep_ids:
            await db.delete(msg)

    # 插入总结消息
    summary_msg = Message(
        project_id=project_id,
        role=MessageRole.system,
        sender_name="Context Summary",
        content=f"[对话历史总结]\n\n{summary_text}",
        message_type=MessageType.chat,
        model_used=model,
    )
    db.add(summary_msg)
    await db.commit()

    # 返回最新消息列表
    msg_result = await db.execute(
        select(Message).where(Message.project_id == project_id).order_by(Message.created_at)
    )
    new_msgs = msg_result.scalars().all()

    return {
        "ok": True,
        "summarized_count": len(to_summarize_msgs),
        "remaining_count": len(list(new_msgs)),
        "summary": summary_text,
    }


@router.delete("/{project_id}/clear-context")
async def clear_context(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_optional_studio_user),
):
    """
    清空项目所有讨论消息
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    from sqlalchemy import delete as sql_delete
    del_result = await db.execute(
        sql_delete(Message).where(Message.project_id == project_id)
    )
    await db.commit()

    return {"ok": True, "deleted_count": del_result.rowcount}


@router.delete("/{project_id}/messages/{message_id}/and-after")
async def delete_message_and_after(
    project_id: int,
    message_id: int,
    db: AsyncSession = Depends(get_db),
):
    """删除指定消息及其之后的所有消息 (用于重试)"""
    result = await db.execute(
        select(Message).where(
            Message.id == message_id,
            Message.project_id == project_id,
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="消息不存在")

    from sqlalchemy import delete as sql_delete
    await db.execute(
        sql_delete(Message).where(
            Message.project_id == project_id,
            Message.created_at >= target.created_at,
        )
    )
    await db.commit()
    return {"ok": True}


# ==================== 敲定 Plan ====================

@router.post("/{project_id}/finalize-plan")
async def finalize_plan(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    基于讨论历史生成并敲定 plan.md
    流式返回 plan 内容
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 获取讨论历史
    msg_result = await db.execute(
        select(Message)
        .where(Message.project_id == project_id)
        .order_by(Message.created_at)
    )
    messages = msg_result.scalars().all()
    if not messages:
        raise HTTPException(status_code=400, detail="还没有讨论内容")

    model = project.discussion_model or "gpt-4o"

    # 构建讨论摘要 (自适应截断，防止超出模型上下文)
    discussion_text = "\n".join(
        f"[{msg.sender_name}]: {msg.content}" for msg in messages
    )
    max_input = capability_cache.get_max_input(model)
    # 给 system prompt + plan prompt + 输出预留空间
    discussion_budget = max(max_input - 12000, max_input // 2)
    if estimate_tokens(discussion_text) > discussion_budget:
        logger.info(f"敲定 plan: 讨论内容 ({estimate_tokens(discussion_text)} tokens) 超出预算 ({discussion_budget}), 截断")
        discussion_text = truncate_text(discussion_text, discussion_budget)

    # 构建生成 plan 的 prompt — 自适应压缩
    max_input = capability_cache.get_max_input(model)
    system_budget = max(int(max_input * 0.15), 2000)
    # 解析当前活跃的 role
    active_role, plan_labels = await _resolve_active_role(project, db)
    system_prompt = context_service.build_project_context(role=active_role, budget_tokens=system_budget, tool_permissions=set(), ui_labels_override=plan_labels)
    plan_prompt = context_service.build_plan_generation_prompt(discussion_text, role=active_role)

    async def event_stream():
        full_plan = []
        try:
            async for event in ai_service.chat_stream(
                messages=[{"role": "user", "content": plan_prompt}],
                model=model,
                system_prompt=system_prompt,
                max_tokens=8192,
                request_id=new_request_id(),
            ):
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "content":
                    full_plan.append(event["content"])
                    yield f"data: {json.dumps({'type': 'content', 'content': event['content']}, ensure_ascii=False)}\n\n"
                elif event.get("type") == "thinking":
                    yield f"data: {json.dumps({'type': 'thinking', 'content': event['content']}, ensure_ascii=False)}\n\n"
                elif event.get("type") == "error":
                    yield f"data: {json.dumps({'type': 'error', 'error': event['error']}, ensure_ascii=False)}\n\n"

            plan_content = "".join(full_plan)

            # 保存 plan
            from studio.backend.core.database import async_session_maker
            async with async_session_maker() as save_db:
                res = await save_db.execute(select(Project).where(Project.id == project_id))
                proj = res.scalar_one()
                proj.plan_content = plan_content
                proj.plan_version += 1
                proj.status = ProjectStatus.planned
                proj.updated_at = datetime.utcnow()

                # 同时保存 plan.md 文件
                plan_path = os.path.join(settings.plans_path, f"project-{project_id}-v{proj.plan_version}.md")
                with open(plan_path, "w", encoding="utf-8") as f:
                    f.write(plan_content)

                # 保存 plan 生成消息
                plan_msg = Message(
                    project_id=project_id,
                    role=MessageRole.assistant,
                    sender_name=f"Plan Generator ({model})",
                    content=plan_content,
                    message_type=MessageType.plan_final,
                    model_used=model,
                )
                save_db.add(plan_msg)
                await save_db.commit()

            yield f"data: {json.dumps({'type': 'done', 'plan_version': proj.plan_version}, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.exception("Plan 生成异常")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ==================== Plan 版本管理 ====================

@router.get("/{project_id}/plan-versions")
async def get_plan_versions(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取项目的 Plan 版本列表"""
    import glob

    # 验证项目存在
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    pattern = os.path.join(settings.plans_path, f"project-{project_id}-v*.md")
    files = glob.glob(pattern)
    versions = []
    for f in sorted(files):
        name = os.path.basename(f)
        v_str = name.replace(f"project-{project_id}-v", "").replace(".md", "")
        try:
            v_num = int(v_str)
            stat = os.stat(f)
            versions.append({
                "version": v_num,
                "filename": name,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "is_current": v_num == project.plan_version,
            })
        except (ValueError, OSError):
            pass

    versions.sort(key=lambda x: x["version"], reverse=True)
    return versions


@router.get("/{project_id}/plan-versions/{version}")
async def get_plan_version(
    project_id: int,
    version: int,
):
    """获取指定版本的 Plan 内容"""
    path = os.path.join(settings.plans_path, f"project-{project_id}-v{version}.md")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="版本不存在")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"version": version, "content": content}


# ==================== 切换模型时上下文检查 ====================

class ContextCheckRequest(BaseModel):
    model: str = Field(..., description="目标模型 ID")

@router.post("/{project_id}/context-check")
async def check_context_on_model_switch(
    project_id: int,
    data: ContextCheckRequest,
    user=Depends(get_optional_studio_user),
    db: AsyncSession = Depends(get_db),
):
    """
    切换模型时检查上下文使用情况，必要时触发自动总结压缩。
    返回新模型下的上下文使用率信息。
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    model = data.model

    # 获取历史消息
    msg_result = await db.execute(
        select(Message)
        .where(Message.project_id == project_id)
        .order_by(Message.created_at)
    )
    history_msgs = msg_result.scalars().all()

    if not history_msgs:
        # 无消息，直接返回空使用率
        max_input = capability_cache.get_max_input(model)
        return {
            "context": build_usage_summary({"max_input": max_input, "total_tokens": 0,
                                             "system_tokens": 0, "tools_tokens": 0,
                                             "history_tokens": 0, "messages_kept": 0,
                                             "messages_dropped": 0, "messages_total": 0}),
            "summarized": False,
        }

    # 构建 AI 消息列表
    ai_messages = []
    for msg in history_msgs:
        # 跳过 plan_final 消息 — 已通过系统提示词注入
        if msg.message_type == MessageType.plan_final:
            continue
        entry = {"role": msg.role.value, "content": msg.content}
        if msg.attachments:
            images = []
            for att in msg.attachments:
                if att.get("type") == "image" and att.get("base64"):
                    images.append({"base64": att["base64"], "mime_type": att.get("mime_type", "image/png")})
            if images:
                entry["images"] = images
        ai_messages.append(entry)

    # 构建 system prompt
    max_input = capability_cache.get_max_input(model)
    system_budget = max(int(max_input * 0.15), 2000)
    chk_role, chk_labels = await _resolve_active_role(project, db)
    _chk_pn = chk_labels.get("project_noun", "需求")
    _chk_on = chk_labels.get("output_noun", "设计稿")
    extra_parts = [f"\n## 当前{_chk_pn}\n标题: {project.title}\n描述: {project.description}"]
    if project.plan_content and project.plan_content.strip():
        extra_parts.append(f"\n## 当前{_chk_on} (v{project.plan_version})\n{project.plan_content.strip()}")
    chk_raw_perms = getattr(project, 'tool_permissions', None)
    chk_tool_permissions = set(chk_raw_perms) if chk_raw_perms else set()
    system_prompt, check_system_sections = context_service.build_project_context(
        role=chk_role,
        extra_context="\n".join(extra_parts),
        budget_tokens=system_budget,
        return_sections=True,
        tool_permissions=chk_tool_permissions,
        ui_labels_override=chk_labels,
    )

    # 检查是否需要总结 — 超限时自动总结并保存到 DB
    compressed_messages, summary_text = await summarize_context_if_needed(
        messages=ai_messages,
        system_prompt=system_prompt,
        model=model,
    )
    # 如果触发了总结, 保存到 DB (用户切到小窗口模型时自动压缩)
    if summary_text:
        try:
            summary_db_msg = Message(
                project_id=project_id,
                role=MessageRole.system,
                sender_name="Context Summary",
                content=f"[对话历史总结]\n\n{summary_text}",
                message_type=MessageType.chat,
                model_used=model,
            )
            db.add(summary_db_msg)
            await db.commit()
        except Exception as se:
            logger.warning(f"保存模型切换总结失败: {se}")

    # 计算新模型下的上下文使用率
    tool_defs = []
    model_supports_tools = await _check_model_supports_tools(model)
    if model_supports_tools:
        raw_perms = getattr(project, 'tool_permissions', None)
        tool_permissions = set(raw_perms) if raw_perms else set()
        tool_defs = get_tool_definitions(tool_permissions)

    _, usage_info = prepare_context(
        messages=compressed_messages,
        system_prompt=system_prompt,
        model=model,
        tool_definitions=tool_defs,
    )

    context_summary = build_usage_summary(usage_info, system_sections=check_system_sections, history_messages=compressed_messages)

    return {
        "context": context_summary,
        "summarized": summary_text is not None,
        "summary_text": summary_text,
    }
