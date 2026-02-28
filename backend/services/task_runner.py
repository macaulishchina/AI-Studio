"""
AI 后台任务运行器 — 核心服务

设计思想:
  AI 的发言（包括工具调用循环）在后台异步执行，不依赖任何前端 HTTP 连接。
  前端通过订阅 SSE 事件流获取实时进度。断开后可重连，获取所有历史事件 + 后续新事件。

多人协作:
  ProjectEventBus 实现项目级事件广播, 所有连接的客户端通过一条 SSE 获取:
  - 其他用户的新消息 (new_message)
  - 所有 AI 任务的流式事件 (content/tool_call/done 等, 带 task_id 标记)
  支持同一项目内多个 AI 任务并发执行.

核心类:
  ProjectEventBus — 项目级事件总线 (多人实时同步)
  RunningTask    — 一次 AI 执行的运行时状态 (内存中的事件缓冲 + 订阅者队列)
  TaskManager    — 单例管理器: 创建/查找/取消任务
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.database import async_session_maker
from studio.backend.models import (
    AiTask, Message, MessageRole, MessageType, Project, ProjectStatus, Role,
)

logger = logging.getLogger(__name__)


# ======================== ProjectEventBus ========================

class ProjectEventBus:
    """项目级事件总线 — 多人实时同步

    每个项目一个 bus, 所有客户端通过订阅 bus 获取:
    - 新消息事件 (new_message / message_deleted)
    - AI 任务事件 (task_started / content / tool_call / done 等, 带 task_id)
    支持断线重连: subscribe() 返回所有活跃任务的 replay 事件.
    """

    _buses: Dict[int, "ProjectEventBus"] = {}

    def __init__(self, project_id: int):
        self.project_id = project_id
        self._subscribers: List[asyncio.Queue] = []

    @classmethod
    def get_or_create(cls, project_id: int) -> "ProjectEventBus":
        if project_id not in cls._buses:
            cls._buses[project_id] = cls(project_id)
        return cls._buses[project_id]

    @classmethod
    def get(cls, project_id: int) -> Optional["ProjectEventBus"]:
        return cls._buses.get(project_id)

    def publish(self, event: dict):
        """广播事件到所有订阅者"""
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.remove(q)

    def subscribe(self) -> tuple:
        """订阅: 返回 (replay_events, queue)

        replay = 所有当前活跃任务的缓冲事件 (带 task_id 标记),
        新客户端可用此数据恢复正在进行的流式输出.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=5000)
        replay = []
        active_tasks = TaskManager.get_project_active_tasks(self.project_id)
        for rt in active_tasks:
            task_meta = {
                "type": "task_started",
                "task_id": rt.task_id,
                "model": rt.model,
                "sender_name": rt.sender_name,
            }
            replay.append(task_meta)
            for ev in rt.events:
                replay.append({**ev, "task_id": rt.task_id})
        self._subscribers.append(q)
        return replay, q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._subscribers:
            self._subscribers.remove(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @classmethod
    def cleanup_empty(cls):
        """清理无订阅者的空 bus"""
        to_remove = [
            pid for pid, bus in cls._buses.items()
            if not bus._subscribers
        ]
        for pid in to_remove:
            cls._buses.pop(pid, None)


# ======================== 命令签名 ========================

def _command_signature(command: str) -> str:
    """提取命令签名用于会话级去重 (取第一段命令词)"""
    parts = command.strip().split()
    # 取前两个 token 作为签名 (如 "npm install", "pip install")
    return " ".join(parts[:2]).lower() if len(parts) >= 2 else command.strip().lower()


# 需要特殊处理的重定向/管道操作符
_REDIRECT_OPS = {">", ">>", "|", "&&", "||"}

# 常见的命令名 → 用第一个 token 作为规则
# 两段命令 (如 pip install, npm install) → 用前两个 token
_TWO_PART_COMMANDS = {"pip", "npm", "apt", "apt-get", "brew", "docker", "git", "systemctl", "cargo", "yarn", "pnpm", "conda"}


def _extract_command_pattern(command: str) -> str:
    """从完整命令中智能提取规则模式。

    规则:
    - 管道/重定向前的部分为主命令
    - 对于 pip install / npm install 等两段命令, 取前两个 token
    - 其他命令只取第一个 token (如 rm, touch, mkdir)

    Examples:
        "rm /tmp/1.txt"           → "rm"
        "touch /tmp/foo.txt"      → "touch"
        "pip install requests"    → "pip install"
        "echo hello > /tmp/a"     → "echo"
        "cat foo | grep bar"      → "cat"
        "docker compose up -d"    → "docker compose"
    """
    cmd = command.strip()
    if not cmd:
        return cmd

    # 取管道/重定向/链接之前的主命令部分
    for op in [" | ", " > ", " >> ", " && ", " || "]:
        idx = cmd.find(op)
        if idx > 0:
            cmd = cmd[:idx].strip()
            break

    parts = cmd.split()
    if not parts:
        return command.strip()

    first = parts[0].lower()

    # sudo → 跳过, 取后面的命令
    if first == "sudo" and len(parts) > 1:
        parts = parts[1:]
        first = parts[0].lower()

    # 两段命令
    if first in _TWO_PART_COMMANDS and len(parts) >= 2:
        return f"{parts[0]} {parts[1]}"

    return parts[0]


# ======================== RunningTask ========================

class RunningTask:
    """一次 AI 后台执行的运行时状态"""

    def __init__(self, task_id: int, project_id: int, sender_name: str = "", model: str = ""):
        self.task_id = task_id
        self.project_id = project_id
        self.sender_name = sender_name
        self.model = model
        self.events: List[dict] = []           # 有序事件缓冲
        self._subscribers: List[asyncio.Queue] = []
        self.content = ""
        self.thinking = ""
        self.tool_calls: List[dict] = []
        self.token_usage: Optional[dict] = None
        self.status = "running"
        self.error = ""
        self.result_message_id: Optional[int] = None
        self.completed_at: Optional[datetime] = None
        self._asyncio_task: Optional[asyncio.Task] = None
        self._cancel_requested = False

        # ---- 命令审批机制 ----
        self._command_approval_event: Optional[asyncio.Event] = None
        self._command_approval_result: Optional[dict] = None  # {"approved": bool, "scope": "once"|"session"|"project"|"permanent"}
        self._session_approved_commands: Set[str] = set()  # 本次回答已授权的命令签名

    async def request_command_approval(self, command: str, tool_call_id: str = "") -> dict:
        """请求用户批准一条写命令。阻塞直到收到回复或超时。

        检查顺序:
        1. 命令授权规则 (deny 优先, 含 * 通配符)
        2. 会话级缓存 (同类命令已授权)
        3. 请求用户实时审批

        返回 {"approved": bool, "scope": "once"|"session"|"project"|"permanent"}
        """
        from studio.backend.api.command_auth import match_command_rule, log_command_audit

        # 获取项目标题用于审计日志
        project_title = ""
        try:
            from studio.backend.core.database import async_session_maker
            from studio.backend.models import Project
            async with async_session_maker() as _db:
                _r = await _db.execute(select(Project.title).where(Project.id == self.project_id))
                project_title = _r.scalar_one_or_none() or ""
        except Exception:
            pass

        # 1. 检查命令授权规则 (全局 + 项目)
        rule_match = await match_command_rule(command, self.project_id)
        if rule_match:
            rule_action = rule_match["action"]
            rule_id = rule_match["rule_id"]
            if rule_action == "deny":
                await log_command_audit(
                    self.project_id, project_title, command,
                    action="rejected", method=f"rule:{rule_id}", scope="rule", operator="auto",
                )
                return {"approved": False, "scope": "rule", "reason": f"被规则 #{rule_id} 拒绝"}
            elif rule_action == "allow":
                await log_command_audit(
                    self.project_id, project_title, command,
                    action="approved", method=f"rule:{rule_id}", scope="rule", operator="auto",
                )
                return {"approved": True, "scope": "rule"}

        # 2. 检查是否本会话已授权
        cmd_sig = _command_signature(command)
        if cmd_sig in self._session_approved_commands:
            await log_command_audit(
                self.project_id, project_title, command,
                action="approved", method="session_cache", scope="session", operator="auto",
            )
            return {"approved": True, "scope": "session"}

        # 3. 发送审批请求事件, 等待用户决定
        self._command_approval_event = asyncio.Event()
        self._command_approval_result = None
        self.emit({
            "type": "command_approval_request",
            "command": command,
            "tool_call_id": tool_call_id,
        })

        # 等待用户回复 (5 分钟超时)
        try:
            await asyncio.wait_for(self._command_approval_event.wait(), timeout=300)
        except asyncio.TimeoutError:
            await log_command_audit(
                self.project_id, project_title, command,
                action="timeout", method="manual", scope="once", operator="timeout",
            )
            return {"approved": False, "scope": "once", "reason": "timeout"}
        finally:
            self._command_approval_event = None

        result = self._command_approval_result or {"approved": False, "scope": "once"}

        # 记录审计日志
        await log_command_audit(
            self.project_id, project_title, command,
            action="approved" if result.get("approved") else "rejected",
            method="manual",
            scope=result.get("scope", "once"),
            operator=self.sender_name or "user",
        )

        # 如果 scope=session, 记住此命令签名
        if result.get("approved") and result.get("scope") == "session":
            self._session_approved_commands.add(cmd_sig)

        all_commands = result.get("all_commands", False)

        # 如果 scope=project, 创建项目级授权规则
        if result.get("approved") and result.get("scope") == "project":
            await self._create_auth_rule(command, rule_scope="project", project_id=self.project_id, all_commands=all_commands)

        # 如果 scope=permanent, 创建全局授权规则
        if result.get("approved") and result.get("scope") == "permanent":
            await self._create_auth_rule(command, rule_scope="global", project_id=None, all_commands=all_commands)

        return result

    async def _create_auth_rule(self, command: str, rule_scope: str = "global", project_id: Optional[int] = None, all_commands: bool = False):
        """根据命令自动创建授权规则 (提取命令名作为前缀模式)

        Args:
            command: 原始命令
            rule_scope: "global" 或 "project"
            project_id: 项目ID (scope=project 时必填)
            all_commands: 如果为 True, 使用 * 通配符匹配所有命令
        """
        try:
            from studio.backend.core.database import async_session_maker
            from studio.backend.models import CommandAuthRule

            # 如果 all_commands=True, 使用 * 通配符; 否则智能提取命令名
            pattern = "*" if all_commands else _extract_command_pattern(command)
            pattern_type = "exact" if all_commands else "prefix"
            scope_label = "全局" if rule_scope == "global" else f"项目#{project_id}"

            # 检查是否已存在相同模式的规则 (避免重复)
            async with async_session_maker() as db:
                existing_q = select(CommandAuthRule).where(
                    CommandAuthRule.pattern == pattern,
                    CommandAuthRule.pattern_type == pattern_type,
                    CommandAuthRule.action == "allow",
                    CommandAuthRule.is_enabled == True,
                )
                if rule_scope == "project" and project_id:
                    existing_q = existing_q.where(
                        CommandAuthRule.scope == "project",
                        CommandAuthRule.project_id == project_id,
                    )
                else:
                    existing_q = existing_q.where(CommandAuthRule.scope == "global")
                existing = await db.execute(existing_q)
                if existing.scalar_one_or_none():
                    logger.info(f"规则已存在, 跳过创建: {scope_label} {pattern_type}='{pattern}'")
                    return

                rule = CommandAuthRule(
                    pattern=pattern,
                    pattern_type=pattern_type,
                    scope=rule_scope,
                    project_id=project_id if rule_scope == "project" else None,
                    action="allow",
                    created_by=self.sender_name or "user",
                    note=f"从命令审批中自动创建 ({'所有命令' if all_commands else '原始命令: ' + command[:100]})",
                )
                db.add(rule)
                await db.commit()
                logger.info(f"创建{scope_label}授权规则: {pattern_type}='{pattern}'")
        except Exception as e:
            logger.warning(f"创建授权规则失败: {e}")

    def resolve_command_approval(self, approved: bool, scope: str = "once", all_commands: bool = False):
        """用户回复审批结果（由 REST endpoint 调用）"""
        self._command_approval_result = {"approved": approved, "scope": scope, "all_commands": all_commands}
        if self._command_approval_event:
            self._command_approval_event.set()

    def emit(self, event: dict):
        """发送事件: 写入缓冲 + 推送到所有订阅者"""
        self.events.append(event)
        etype = event.get("type")
        if etype == "content":
            self.content += event.get("content", "")
        elif etype == "thinking":
            self.thinking += event.get("content", "")
        elif etype == "tool_result":
            self.tool_calls.append({
                "id": event.get("tool_call_id", ""),
                "name": event.get("name", ""),
                "arguments": event.get("arguments", {}),
                "result": event.get("result", ""),
                "duration_ms": event.get("duration_ms", 0),
            })
        elif etype == "tool_error":
            self.tool_calls.append({
                "id": event.get("tool_call_id", ""),
                "name": event.get("name", ""),
                "arguments": {},
                "result": f"ERROR: {event.get('error', '')}",
                "duration_ms": 0,
            })
        elif etype == "usage":
            self.token_usage = event.get("usage")
        # 推送到活跃订阅者 (per-task)
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.remove(q)
        # 广播到项目事件总线 (附加 task_id, 供多人实时同步)
        bus = ProjectEventBus.get(self.project_id)
        if bus:
            bus.publish({**event, "task_id": self.task_id})

    def subscribe(self) -> tuple:
        """订阅: 返回 (历史事件列表, 实时队列)"""
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        replay = list(self.events)
        self._subscribers.append(q)
        return replay, q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._subscribers:
            self._subscribers.remove(q)

    def request_cancel(self):
        self._cancel_requested = True
        if self._asyncio_task and not self._asyncio_task.done():
            self._asyncio_task.cancel()


# ======================== TaskManager ========================

class TaskManager:
    """AI 任务管理器 (单例)"""

    _tasks: Dict[int, RunningTask] = {}         # task_id -> RunningTask
    _project_tasks: Dict[int, Set[int]] = {}    # project_id -> active task_ids (支持多任务并发)
    _cleanup_started = False

    @classmethod
    def get_running_task(cls, task_id: int) -> Optional[RunningTask]:
        return cls._tasks.get(task_id)

    @classmethod
    def get_project_active_task(cls, project_id: int) -> Optional[RunningTask]:
        """返回项目第一个活跃任务 (向后兼容)"""
        tids = cls._project_tasks.get(project_id, set())
        for tid in list(tids):
            rt = cls._tasks.get(tid)
            if rt and rt.status == "running":
                return rt
            tids.discard(tid)
        return None

    @classmethod
    def get_project_active_tasks(cls, project_id: int) -> List[RunningTask]:
        """返回项目所有活跃任务"""
        tids = cls._project_tasks.get(project_id, set())
        result = []
        for tid in list(tids):
            rt = cls._tasks.get(tid)
            if rt and rt.status == "running":
                result.append(rt)
            else:
                tids.discard(tid)
        return result

    @classmethod
    def get_project_task_ids(cls, project_id: int) -> Set[int]:
        """返回项目活跃任务 ID 集合"""
        return cls._project_tasks.get(project_id, set()).copy()

    @classmethod
    async def start_discussion_task(
        cls,
        project_id: int,
        model: str,
        sender_name: str = "user",
        message: str = "",
        attachments: list = None,
        max_tool_rounds: int = 15,
        regenerate: bool = False,
    ) -> int:
        """创建 AiTask 并启动后台执行, 返回 task_id"""
        async with async_session_maker() as db:
            task = AiTask(
                project_id=project_id,
                task_type="discuss",
                status="pending",
                model=model,
                sender_name=sender_name,
                input_message=message,
                input_attachments=attachments or [],
                max_tool_rounds=max_tool_rounds,
                regenerate=regenerate,
            )
            db.add(task)
            await db.commit()
            task_id = task.id

        rt = RunningTask(task_id, project_id, sender_name=sender_name, model=model)
        cls._tasks[task_id] = rt
        cls._project_tasks.setdefault(project_id, set()).add(task_id)

        # 通知项目事件总线: 新任务开始
        bus = ProjectEventBus.get_or_create(project_id)
        bus.publish({"type": "task_started", "task_id": task_id, "model": model, "sender_name": sender_name})

        rt._asyncio_task = asyncio.create_task(
            _execute_discussion(rt, project_id, model, max_tool_rounds)
        )
        # 错误保护: 任务结束后清理
        rt._asyncio_task.add_done_callback(lambda _: cls._on_task_done(task_id, project_id))

        if not cls._cleanup_started:
            cls._cleanup_started = True
            asyncio.create_task(cls._cleanup_loop())

        return task_id

    @classmethod
    async def start_auto_review_task(cls, project_id: int) -> int:
        """创建自动审查任务"""
        async with async_session_maker() as db:
            result = await db.execute(select(Project).where(Project.id == project_id))
            project = result.scalar_one_or_none()
            if not project:
                raise ValueError(f"项目 {project_id} 不存在")
            model = project.discussion_model or "gpt-4o"

            task = AiTask(
                project_id=project_id,
                task_type="auto_review",
                status="pending",
                model=model,
                sender_name="AutoReview",
                input_message="",
                max_tool_rounds=30,  # 审查可能需要更多工具轮次
                regenerate=True,     # 无用户消息, 直接 AI 发言
            )
            db.add(task)
            await db.commit()
            task_id = task.id

        rt = RunningTask(task_id, project_id, sender_name="AutoReview", model=model)
        cls._tasks[task_id] = rt
        cls._project_tasks.setdefault(project_id, set()).add(task_id)

        # 通知项目事件总线
        bus = ProjectEventBus.get_or_create(project_id)
        bus.publish({"type": "task_started", "task_id": task_id, "model": model, "sender_name": "AutoReview"})

        rt._asyncio_task = asyncio.create_task(
            _execute_discussion(rt, project_id, model, 30)
        )
        rt._asyncio_task.add_done_callback(lambda _: cls._on_task_done(task_id, project_id))

        return task_id

    @classmethod
    async def cancel_task(cls, task_id: int):
        rt = cls._tasks.get(task_id)
        if rt and rt.status == "running":
            rt.request_cancel()
            rt.status = "cancelled"
            rt.completed_at = datetime.utcnow()
            rt.emit({"type": "cancelled"})
            # 持久化
            await _persist_task_final(rt, "cancelled")

    @classmethod
    def _on_task_done(cls, task_id: int, project_id: int):
        """asyncio.Task 完成回调"""
        rt = cls._tasks.get(task_id)
        if rt and rt.status == "running":
            # 异常退出, 标记为 failed
            rt.status = "failed"
            rt.completed_at = datetime.utcnow()
        # 清理 project 活跃任务引用 (从 Set 中移除)
        tids = cls._project_tasks.get(project_id)
        if tids:
            tids.discard(task_id)
            if not tids:
                cls._project_tasks.pop(project_id, None)

    @classmethod
    async def _cleanup_loop(cls):
        """定期清理已完成 >5 分钟的任务缓存"""
        while True:
            await asyncio.sleep(60)
            now = datetime.utcnow()
            to_remove = []
            for task_id, rt in cls._tasks.items():
                if rt.status in ("completed", "failed", "cancelled"):
                    if rt.completed_at and (now - rt.completed_at).total_seconds() > 300:
                        to_remove.append(task_id)
            for tid in to_remove:
                cls._tasks.pop(tid, None)
            # 清理无订阅者的空事件总线
            ProjectEventBus.cleanup_empty()

    @classmethod
    async def recover_stale_tasks(cls):
        """服务启动时: 将残留的 running 任务标记为 failed"""
        try:
            async with async_session_maker() as db:
                result = await db.execute(
                    select(AiTask).where(AiTask.status.in_(["pending", "running"]))
                )
                stale = result.scalars().all()
                for t in stale:
                    t.status = "failed"
                    t.error_message = "服务重启, 任务中断"
                    t.completed_at = datetime.utcnow()
                if stale:
                    await db.commit()
                    logger.info(f"✅ 恢复 {len(stale)} 个残留 AI 任务 → failed")
        except Exception as e:
            logger.warning(f"恢复残留任务失败: {e}")


# ======================== 讨论执行逻辑 ========================

async def _execute_discussion(
    rt: RunningTask,
    project_id: int,
    model: str,
    max_tool_rounds: int,
):
    """
    后台执行讨论 AI — 从 discussion.py event_stream() 提取的核心逻辑.
    在 asyncio.Task 中运行, 不依赖 HTTP 连接.
    """
    from studio.backend.services import ai_service, context_service
    from studio.backend.services.ai_service import new_request_id
    from studio.backend.services.context_manager import (
        prepare_context, build_usage_summary,
        summarize_context_if_needed, _generate_summary,
    )
    from studio.backend.services.tool_registry import (
        get_tool_definitions, execute_tool,
    )
    from studio.backend.core.model_capabilities import capability_cache
    from studio.backend.core.token_utils import estimate_tokens, truncate_text
    from studio.backend.core.config import settings
    from studio.backend.core.project_types import get_role_for_status, get_ui_labels

    # 更新 DB 状态 → running
    await _update_task_status(rt.task_id, "running")
    rt.status = "running"

    full_response: List[str] = []
    thinking_parts: List[str] = []
    collected_tool_calls: List[dict] = []
    collected_errors: List[str] = []
    token_usage = None

    try:
        async with async_session_maker() as db:
            # 加载项目
            result = await db.execute(select(Project).where(Project.id == project_id))
            project = result.scalar_one_or_none()
            if not project:
                rt.emit({"type": "error", "error": "项目不存在"})
                rt.status = "failed"
                rt.error = "项目不存在"
                await _persist_task_final(rt, "failed")
                return

            # ---- 构建消息历史 ----
            msg_result = await db.execute(
                select(Message)
                .where(Message.project_id == project_id)
                .order_by(Message.created_at)
            )
            history_msgs = msg_result.scalars().all()

            ai_messages = []
            for msg in history_msgs:
                if msg.message_type == MessageType.plan_final:
                    continue
                entry = {"role": msg.role.value, "content": msg.content}
                if msg.attachments:
                    images = []
                    for att in msg.attachments:
                        if att.get("type") == "image" and att.get("base64"):
                            images.append({
                                "base64": att["base64"],
                                "mime_type": att.get("mime_type", "image/png"),
                            })
                    if images:
                        entry["images"] = images
                ai_messages.append(entry)

            # ---- 构建系统 prompt ----
            max_input = capability_cache.get_max_input(model)
            system_budget = max(int(max_input * 0.15), 2000)

            # 解析活跃 role
            type_key = getattr(project, 'project_type', None) or 'requirement'
            status_val = project.status.value if hasattr(project.status, 'value') else str(project.status)
            ui_labels = get_ui_labels(type_key)
            role_name = get_role_for_status(type_key, status_val) or get_role_for_status(type_key, 'discussing')
            role_obj = None
            if role_name:
                sk_result = await db.execute(
                    select(Role).where(Role.name == role_name, Role.is_enabled.is_(True))
                )
                role_obj = sk_result.scalar_one_or_none()

            # ---- 加载活跃技能 ----
            active_skills = []
            if role_obj and role_obj.default_skills:
                from studio.backend.models import Skill
                skill_names = role_obj.default_skills or []
                if skill_names:
                    sk_q = await db.execute(
                        select(Skill).where(
                            Skill.name.in_(skill_names),
                            Skill.is_enabled.is_(True),
                        )
                    )
                    active_skills = list(sk_q.scalars().all())

            _pn = ui_labels.get("project_noun", "需求")
            _on = ui_labels.get("output_noun", "设计稿")
            extra_parts = [f"\n## 当前{_pn}\n标题: {project.title}\n描述: {project.description}"]

            if project.plan_content and project.plan_content.strip():
                extra_parts.append(
                    f"\n## 当前{_on} (v{project.plan_version})\n"
                    f"以下是当前最新的{_on}，用户可能正在同步编辑它。"
                    f"请结合{_on}内容和讨论进行回复。\n\n{project.plan_content.strip()}"
                )

            # 审查阶段额外上下文
            if status_val == "reviewing":
                from studio.backend.services.workspace_service import get_effective_workspace
                review_ws = get_effective_workspace(project)
                extra_parts.append(
                    f"\n## 审查环境\n"
                    f"- 工作目录: `{review_ws}`\n"
                    f"- 所有工具 (read_file, search_text, run_command 等) 均在此目录下执行\n"
                    f"- 你可以使用 `run_command` 工具执行 `git diff origin/main...HEAD` 查看完整变更\n"
                    f"- 也可以 `git diff origin/main...HEAD -- path/file` 查看单文件变更\n"
                    f"- PR 编号: #{project.github_pr_number or 'N/A'}\n"
                    f"- 分支: {project.branch_name or 'N/A'}\n"
                    f"- 迭代次数: {getattr(project, 'iteration_count', 0) or 0}"
                )

            raw_perms = getattr(project, 'tool_permissions', None)
            # 默认全开 (除 execute_command)，与 tool_registry.DEFAULT_PERMISSIONS 一致
            from studio.backend.services.tool_registry import DEFAULT_PERMISSIONS
            tool_permissions = set(raw_perms) if raw_perms else set(DEFAULT_PERMISSIONS)

            system_prompt, system_sections = context_service.build_project_context(
                role=role_obj,
                extra_context="\n".join(extra_parts),
                budget_tokens=system_budget,
                return_sections=True,
                tool_permissions=tool_permissions,
                ui_labels_override=ui_labels,
                skills=active_skills,
            )

        # ---- 上下文自动总结 ----
        ai_messages, summary_text = await summarize_context_if_needed(
            messages=ai_messages,
            system_prompt=system_prompt,
            model=model,
        )
        if summary_text:
            try:
                async with async_session_maker() as save_db:
                    summary_msg = Message(
                        project_id=project_id,
                        role=MessageRole.system,
                        sender_name="Context Summary",
                        content=f"[对话历史总结]\n\n{summary_text}",
                        message_type=MessageType.chat,
                        model_used=model,
                    )
                    save_db.add(summary_msg)
                    await save_db.commit()
            except Exception as se:
                logger.warning(f"保存上下文总结失败: {se}")

        # ---- 工具定义 ----
        from studio.backend.api.discussion import _check_model_supports_tools
        model_supports_tools = await _check_model_supports_tools(model)
        if not model_supports_tools:
            tool_permissions = set()
            tool_defs = []
        else:
            tool_defs = get_tool_definitions(tool_permissions)

        managed_messages, usage_info = prepare_context(
            messages=ai_messages,
            system_prompt=system_prompt,
            model=model,
            tool_definitions=tool_defs,
        )

        # 工具执行器
        from studio.backend.services.workspace_service import get_effective_workspace
        async with async_session_maker() as db2:
            result2 = await db2.execute(select(Project).where(Project.id == project_id))
            project2 = result2.scalar_one_or_none()
        workspace_path = get_effective_workspace(project2) if project2 else settings.workspace_path

        # 命令审批回调: 当 AI 尝试执行非只读命令时触发
        # 内部先检查 auto_approve / session 缓存, 否则弹窗请求用户审批
        async def _command_approval_fn(command: str, tool_call_id: str) -> dict:
            return await rt.request_command_approval(command, tool_call_id)

        # 获取项目的工作目录路径 (MCP 凭据解析用)
        _project_workspace_dir = project2.workspace_dir if project2 else None

        async def _tool_executor(name: str, arguments: dict) -> str:
            return await execute_tool(name, arguments, workspace_path, tool_permissions,
                                      command_approval_fn=_command_approval_fn,
                                      project_id=project_id,
                                      workspace_dir=_project_workspace_dir)

        # ---- 发送上下文信息 ----
        context_summary = build_usage_summary(
            usage_info, system_sections=system_sections,
            history_messages=managed_messages,
        )
        rt.emit({"type": "context", "context": context_summary})

        if summary_text:
            rt.emit({"type": "summary", "summary": summary_text})

        # ---- AI 流式调用 (含重试) ----
        current_managed_messages = managed_messages
        current_usage_info = usage_info

        for _attempt in range(2):
            if rt._cancel_requested:
                break
            overflow_retry = False

            async for event in ai_service.chat_stream(
                messages=current_managed_messages,
                model=model,
                system_prompt=system_prompt,
                tools=tool_defs if tool_defs else None,
                tool_executor=_tool_executor,
                request_id=new_request_id(),
                max_tool_rounds=max_tool_rounds,
            ):
                if rt._cancel_requested:
                    break
                if not isinstance(event, dict):
                    continue
                event_type = event.get("type", "")

                if event_type == "content":
                    full_response.append(event["content"])
                    rt.emit({"type": "content", "content": event["content"]})

                elif event_type == "thinking":
                    thinking_parts.append(event["content"])
                    rt.emit({"type": "thinking", "content": event["content"]})

                elif event_type == "tool_call_start":
                    rt.emit({"type": "tool_call_start", "tool_call": event["tool_call"]})

                elif event_type == "tool_call":
                    rt.emit({"type": "tool_call", "tool_call": event["tool_call"]})

                elif event_type == "tool_result":
                    collected_tool_calls.append({
                        "id": event["tool_call_id"],
                        "name": event["name"],
                        "arguments": event.get("arguments", {}),
                        "result": event["result"],
                        "duration_ms": event.get("duration_ms", 0),
                    })
                    rt.emit({
                        "type": "tool_result",
                        "tool_call_id": event["tool_call_id"],
                        "name": event["name"],
                        "result": event["result"],
                        "arguments": event.get("arguments", {}),
                        "duration_ms": event.get("duration_ms", 0),
                    })

                elif event_type == "tool_error":
                    collected_tool_calls.append({
                        "id": event["tool_call_id"],
                        "name": event["name"],
                        "arguments": {},
                        "result": f"ERROR: {event['error']}",
                        "duration_ms": 0,
                    })
                    rt.emit({
                        "type": "tool_error",
                        "tool_call_id": event["tool_call_id"],
                        "name": event["name"],
                        "error": event["error"],
                    })

                elif event_type == "usage":
                    token_usage = event["usage"]
                    rt.emit({"type": "usage", "usage": token_usage})

                elif event_type == "truncated":
                    rt.emit({"type": "truncated"})

                elif event_type == "ask_user_pending":
                    rt.emit({"type": "ask_user_pending"})

                elif event_type == "error":
                    error_meta = event.get('error_meta', {})
                    if (error_meta.get('error_type') == 'context_overflow'
                            and not full_response and _attempt == 0):
                        logger.info("上下文超限，自动压缩后重试...")
                        rt.emit({"type": "content", "content": "\n\n⏳ 上下文超限，正在自动压缩后重试...\n\n"})
                        try:
                            min_keep = min(len(ai_messages), 4)
                            to_summarize_part = ai_messages[:-min_keep] if min_keep < len(ai_messages) else []
                            recent_part = ai_messages[-min_keep:]
                            if to_summarize_part:
                                compress_summary = await _generate_summary(to_summarize_part, model)
                                if compress_summary:
                                    compressed = [{"role": "system", "content": f"[对话历史总结]\n\n{compress_summary}"}] + recent_part
                                    current_managed_messages, current_usage_info = prepare_context(
                                        messages=compressed, system_prompt=system_prompt,
                                        model=model, tool_definitions=tool_defs,
                                    )
                                    ctx = build_usage_summary(current_usage_info, system_sections=system_sections, history_messages=current_managed_messages)
                                    rt.emit({"type": "context", "context": ctx})
                                    try:
                                        async with async_session_maker() as save_db:
                                            s_msg = Message(
                                                project_id=project_id,
                                                role=MessageRole.system,
                                                sender_name="Context Summary",
                                                content=f"[对话历史总结]\n\n{compress_summary}",
                                                message_type=MessageType.chat,
                                                model_used=model,
                                            )
                                            save_db.add(s_msg)
                                            await save_db.commit()
                                    except Exception:
                                        pass
                                    overflow_retry = True
                                    full_response.clear()
                                    break
                        except Exception as ce:
                            logger.warning(f"自动压缩失败: {ce}")
                        if not overflow_retry:
                            collected_errors.append(event['error'])
                            err_data: dict = {'type': 'error', 'error': event['error']}
                            if event.get('error_meta'):
                                err_data['error_meta'] = event['error_meta']
                            rt.emit(err_data)
                    else:
                        collected_errors.append(event['error'])
                        err_data = {'type': 'error', 'error': event['error']}
                        if event.get('error_meta'):
                            err_data['error_meta'] = event['error_meta']
                        rt.emit(err_data)

            if not overflow_retry:
                break

        # ---- 保存 AI 回复 ----
        ai_content = "".join(full_response)
        if not ai_content and collected_errors:
            error_text = collected_errors[-1]
            brief = error_text[:300] + '...' if len(error_text) > 300 else error_text
            ai_content = f"**⚠️ AI 服务错误**\n\n❌ {brief}"
        thinking_content = "".join(thinking_parts) if thinking_parts else None

        ai_msg_id = None
        for attempt in range(3):
            try:
                async with async_session_maker() as save_db:
                    ai_msg = Message(
                        project_id=project_id,
                        role=MessageRole.assistant,
                        sender_name=model,
                        content=ai_content,
                        message_type=MessageType.chat,
                        model_used=model,
                        token_usage=token_usage,
                        thinking_content=thinking_content,
                        tool_calls=collected_tool_calls if collected_tool_calls else None,
                    )
                    save_db.add(ai_msg)
                    await save_db.commit()
                    ai_msg_id = ai_msg.id
                break
            except Exception as save_err:
                logger.warning(f"保存 AI 回复失败 (尝试 {attempt+1}/3): {save_err}")
                if attempt < 2:
                    await asyncio.sleep(0.5)

        rt.result_message_id = ai_msg_id
        rt.emit({"type": "done", "message_id": ai_msg_id or -1})
        rt.status = "completed"
        rt.completed_at = datetime.utcnow()
        await _persist_task_final(rt, "completed", ai_content, thinking_content, collected_tool_calls, token_usage, ai_msg_id)

    except asyncio.CancelledError:
        logger.info(f"任务 {rt.task_id} 被取消")
        rt.status = "cancelled"
        rt.completed_at = datetime.utcnow()
        rt.emit({"type": "cancelled"})
        await _persist_task_final(rt, "cancelled")

    except Exception as e:
        logger.exception(f"AI 任务 {rt.task_id} 异常")
        error_str = str(e)
        rt.error = error_str
        rt.emit({"type": "error", "error": error_str})

        # 保存错误消息
        if not full_response:
            brief = error_str[:300] + '...' if len(error_str) > 300 else error_str
            error_content = f"**⚠️ AI 服务错误**\n\n❌ {brief}"
            try:
                async with async_session_maker() as save_db:
                    err_msg = Message(
                        project_id=project_id,
                        role=MessageRole.assistant,
                        sender_name=model,
                        content=error_content,
                        message_type=MessageType.chat,
                        model_used=model,
                    )
                    save_db.add(err_msg)
                    await save_db.commit()
                    rt.result_message_id = err_msg.id
                    rt.emit({"type": "done", "message_id": err_msg.id})
            except Exception:
                rt.emit({"type": "done", "message_id": -1})

        rt.status = "failed"
        rt.completed_at = datetime.utcnow()
        await _persist_task_final(rt, "failed", error_message=error_str)


# ======================== 持久化辅助 ========================

async def _update_task_status(task_id: int, status: str):
    try:
        async with async_session_maker() as db:
            result = await db.execute(select(AiTask).where(AiTask.id == task_id))
            task = result.scalar_one_or_none()
            if task:
                task.status = status
                task.updated_at = datetime.utcnow()
                await db.commit()
    except Exception as e:
        logger.warning(f"更新任务状态失败: {e}")


async def _persist_task_final(
    rt: RunningTask,
    status: str,
    output_content: str = "",
    thinking_content: str = "",
    tool_calls: list = None,
    token_usage: dict = None,
    result_message_id: int = None,
    error_message: str = "",
):
    try:
        async with async_session_maker() as db:
            result = await db.execute(select(AiTask).where(AiTask.id == rt.task_id))
            task = result.scalar_one_or_none()
            if task:
                task.status = status
                task.output_content = output_content or rt.content
                task.thinking_content = thinking_content or rt.thinking
                task.tool_calls_data = tool_calls or rt.tool_calls
                task.token_usage = token_usage or rt.token_usage
                task.error_message = error_message or rt.error
                task.result_message_id = result_message_id or rt.result_message_id
                task.completed_at = rt.completed_at or datetime.utcnow()
                task.updated_at = datetime.utcnow()
                await db.commit()
    except Exception as e:
        logger.warning(f"持久化任务最终状态失败: {e}")
