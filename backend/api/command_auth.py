"""
设计院 (Studio) — 命令授权管理 API

功能:
  1. 命令授权规则 CRUD (全局/项目级, 支持前缀/精确/包含/正则匹配)
  2. 项目级自动批准状态查询与撤销
  3. 命令执行审计日志查询
  4. 命令安全设置 (伪造检测开关等)
"""
import json
import logging
import os
import re
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, delete, desc
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.config import settings as studio_settings
from studio.backend.core.database import get_db
from studio.backend.core.security import get_studio_user  # noqa: keep for potential future use
from studio.backend.models import CommandAuthRule, CommandAuditLog, Project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/studio-api/command-auth", tags=["CommandAuth"])


# ==================== 设置文件 ====================

_SETTINGS_FILE = os.path.join(studio_settings.data_path, "command_auth_settings.json")
_settings_cache: Optional[dict] = None  # 内存缓存, 避免频繁磁盘读取


def _load_settings() -> dict:
    """加载命令授权设置"""
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache
    try:
        if os.path.exists(_SETTINGS_FILE):
            with open(_SETTINGS_FILE, "r") as f:
                _settings_cache = json.load(f)
                return _settings_cache
    except Exception:
        pass
    _settings_cache = {"fabrication_detection": False}
    return _settings_cache


def _save_settings(data: dict):
    """保存命令授权设置"""
    global _settings_cache
    _settings_cache = data
    try:
        with open(_SETTINGS_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存命令授权设置失败: {e}")


def is_fabrication_detection_enabled() -> bool:
    """供 ai_service 调用 — 检查伪造检测是否启用"""
    return _load_settings().get("fabrication_detection", False)


# ==================== Schemas ====================

class RuleCreate(BaseModel):
    pattern: str = Field(..., min_length=1, max_length=500)
    pattern_type: str = Field("prefix", pattern=r"^(prefix|exact|contains|regex)$")
    scope: str = Field("global", pattern=r"^(global|project)$")
    project_id: Optional[int] = None
    action: str = Field("allow", pattern=r"^(allow|deny)$")
    note: str = ""

class RuleUpdate(BaseModel):
    pattern: Optional[str] = None
    pattern_type: Optional[str] = None
    action: Optional[str] = None
    note: Optional[str] = None
    is_enabled: Optional[bool] = None

class RuleResponse(BaseModel):
    id: int
    pattern: str
    pattern_type: str
    scope: str
    project_id: Optional[int]
    project_title: Optional[str] = None
    action: str
    created_by: str
    note: str
    is_enabled: bool
    created_at: str

class AuditLogResponse(BaseModel):
    id: int
    project_id: Optional[int]
    project_title: str
    command: str
    action: str
    method: str
    scope: str
    operator: str
    created_at: str

class ProjectOverride(BaseModel):
    project_id: int
    project_title: str
    auto_approve: bool


# ==================== 规则 CRUD ====================

@router.get("/rules", response_model=List[RuleResponse])
async def list_rules(
    scope: Optional[str] = Query(None, pattern=r"^(global|project)$"),
    project_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    """列出命令授权规则"""
    q = select(CommandAuthRule).order_by(desc(CommandAuthRule.created_at))
    if scope:
        q = q.where(CommandAuthRule.scope == scope)
    if project_id is not None:
        q = q.where(CommandAuthRule.project_id == project_id)
    result = await db.execute(q)
    rules = result.scalars().all()

    # 批量获取项目标题
    project_ids = {r.project_id for r in rules if r.project_id}
    title_map = {}
    if project_ids:
        pr = await db.execute(select(Project.id, Project.title).where(Project.id.in_(project_ids)))
        title_map = {row[0]: row[1] for row in pr.fetchall()}

    return [
        RuleResponse(
            id=r.id,
            pattern=r.pattern,
            pattern_type=r.pattern_type,
            scope=r.scope,
            project_id=r.project_id,
            project_title=title_map.get(r.project_id, "") if r.project_id else None,
            action=r.action,
            created_by=r.created_by or "",
            note=r.note or "",
            is_enabled=r.is_enabled if r.is_enabled is not None else True,
            created_at=r.created_at.isoformat() + "Z" if r.created_at else "",
        )
        for r in rules
    ]


@router.post("/rules", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    body: RuleCreate,
    db: AsyncSession = Depends(get_db),
):
    """创建命令授权规则"""
    # 验证 regex 有效性
    if body.pattern_type == "regex":
        try:
            re.compile(body.pattern)
        except re.error as e:
            raise HTTPException(400, detail=f"无效的正则表达式: {e}")

    # scope=project 时必须有 project_id
    if body.scope == "project" and not body.project_id:
        raise HTTPException(400, detail="项目级规则必须指定 project_id")

    rule = CommandAuthRule(
        pattern=body.pattern,
        pattern_type=body.pattern_type,
        scope=body.scope,
        project_id=body.project_id if body.scope == "project" else None,
        action=body.action,
        created_by=body.note[:20] if body.note else "",
        note=body.note,
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)

    # 获取项目标题
    project_title = None
    if rule.project_id:
        pr = await db.execute(select(Project.title).where(Project.id == rule.project_id))
        row = pr.scalar_one_or_none()
        project_title = row if row else ""

    return RuleResponse(
        id=rule.id,
        pattern=rule.pattern,
        pattern_type=rule.pattern_type,
        scope=rule.scope,
        project_id=rule.project_id,
        project_title=project_title,
        action=rule.action,
        created_by=rule.created_by or "",
        note=rule.note or "",
        is_enabled=True,
        created_at=rule.created_at.isoformat() + "Z" if rule.created_at else "",
    )


@router.put("/rules/{rule_id}", response_model=RuleResponse)
async def update_rule(
    rule_id: int,
    body: RuleUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新命令授权规则"""
    result = await db.execute(select(CommandAuthRule).where(CommandAuthRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, detail="规则不存在")

    if body.pattern is not None:
        if body.pattern_type == "regex" or (body.pattern_type is None and rule.pattern_type == "regex"):
            try:
                re.compile(body.pattern)
            except re.error as e:
                raise HTTPException(400, detail=f"无效的正则表达式: {e}")
        rule.pattern = body.pattern
    if body.pattern_type is not None:
        rule.pattern_type = body.pattern_type
    if body.action is not None:
        rule.action = body.action
    if body.note is not None:
        rule.note = body.note
    if body.is_enabled is not None:
        rule.is_enabled = body.is_enabled

    await db.flush()
    await db.refresh(rule)

    project_title = None
    if rule.project_id:
        pr = await db.execute(select(Project.title).where(Project.id == rule.project_id))
        project_title = pr.scalar_one_or_none() or ""

    return RuleResponse(
        id=rule.id,
        pattern=rule.pattern,
        pattern_type=rule.pattern_type,
        scope=rule.scope,
        project_id=rule.project_id,
        project_title=project_title,
        action=rule.action,
        created_by=rule.created_by or "",
        note=rule.note or "",
        is_enabled=rule.is_enabled if rule.is_enabled is not None else True,
        created_at=rule.created_at.isoformat() + "Z" if rule.created_at else "",
    )


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
):
    """删除命令授权规则"""
    result = await db.execute(select(CommandAuthRule).where(CommandAuthRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, detail="规则不存在")
    await db.delete(rule)


# ==================== 审计日志 ====================

@router.get("/audit-log", response_model=List[AuditLogResponse])
async def list_audit_log(
    project_id: Optional[int] = None,
    action: Optional[str] = Query(None, pattern=r"^(approved|rejected|timeout)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """查询命令执行审计日志"""
    q = select(CommandAuditLog).order_by(desc(CommandAuditLog.created_at))
    if project_id is not None:
        q = q.where(CommandAuditLog.project_id == project_id)
    if action:
        q = q.where(CommandAuditLog.action == action)
    q = q.offset(offset).limit(limit)

    result = await db.execute(q)
    logs = result.scalars().all()
    return [
        AuditLogResponse(
            id=log.id,
            project_id=log.project_id,
            project_title=log.project_title or "",
            command=log.command or "",
            action=log.action or "",
            method=log.method or "",
            scope=log.scope or "",
            operator=log.operator or "",
            created_at=log.created_at.isoformat() + "Z" if log.created_at else "",
        )
        for log in logs
    ]


@router.get("/audit-log/stats")
async def audit_log_stats(
    db: AsyncSession = Depends(get_db),
):
    """审计日志统计 (用于 dashboard)"""
    total = await db.execute(select(func.count()).select_from(CommandAuditLog))
    approved = await db.execute(
        select(func.count()).select_from(CommandAuditLog).where(CommandAuditLog.action == "approved")
    )
    rejected = await db.execute(
        select(func.count()).select_from(CommandAuditLog).where(CommandAuditLog.action == "rejected")
    )
    rule_count = await db.execute(
        select(func.count()).select_from(CommandAuthRule).where(CommandAuthRule.is_enabled == True)
    )
    return {
        "total_commands": total.scalar() or 0,
        "approved_count": approved.scalar() or 0,
        "rejected_count": rejected.scalar() or 0,
        "active_rules": rule_count.scalar() or 0,
    }


# ==================== 规则匹配引擎 (供 task_runner 调用) ====================

async def match_command_rule(command: str, project_id: Optional[int] = None) -> Optional[dict]:
    """
    检查命令是否匹配任何授权规则。

    匹配优先级:
    1. 项目级 deny 规则
    2. 全局 deny 规则
    3. 项目级 allow 规则
    4. 全局 allow 规则

    返回匹配的规则信息 {"rule_id": int, "action": "allow"|"deny"} 或 None
    """
    from studio.backend.core.database import async_session_maker

    async with async_session_maker() as db:
        q = select(CommandAuthRule).where(CommandAuthRule.is_enabled == True)
        if project_id:
            q = q.where(
                (CommandAuthRule.scope == "global") |
                ((CommandAuthRule.scope == "project") & (CommandAuthRule.project_id == project_id))
            )
        else:
            q = q.where(CommandAuthRule.scope == "global")
        result = await db.execute(q)
        rules = result.scalars().all()

    if not rules:
        return None

    # 排序: project deny > global deny > project allow > global allow
    def sort_key(r):
        scope_order = 0 if r.scope == "project" else 1
        action_order = 0 if r.action == "deny" else 1
        return (action_order, scope_order)

    rules_sorted = sorted(rules, key=sort_key)

    for rule in rules_sorted:
        if _command_matches_pattern(command, rule.pattern, rule.pattern_type):
            return {"rule_id": rule.id, "action": rule.action}

    return None


def _command_matches_pattern(command: str, pattern: str, pattern_type: str) -> bool:
    """检查命令是否匹配给定模式"""
    cmd = command.strip()
    pat = pattern.strip()

    # 通配符 * 匹配所有命令
    if pat == "*":
        return True

    if pattern_type == "exact":
        return cmd == pat
    elif pattern_type == "prefix":
        return cmd.startswith(pat) or cmd.split()[0] == pat if cmd else False
    elif pattern_type == "contains":
        return pat in cmd
    elif pattern_type == "regex":
        try:
            return bool(re.search(pat, cmd))
        except re.error:
            return False
    return False


async def log_command_audit(
    project_id: Optional[int],
    project_title: str,
    command: str,
    action: str,
    method: str,
    scope: str,
    operator: str = "",
):
    """记录命令审计日志"""
    from studio.backend.core.database import async_session_maker

    try:
        async with async_session_maker() as db:
            log = CommandAuditLog(
                project_id=project_id,
                project_title=project_title,
                command=command[:2000],  # 截断过长命令
                action=action,
                method=method,
                scope=scope,
                operator=operator,
            )
            db.add(log)
            await db.commit()
    except Exception as e:
        logger.warning(f"记录命令审计日志失败: {e}")


# ==================== 命令安全设置 ====================

@router.get("/settings")
async def get_command_auth_settings():
    """获取命令安全设置"""
    return _load_settings()


@router.put("/settings")
async def update_command_auth_settings(body: dict):
    """更新命令安全设置"""
    current = _load_settings()
    # 只允许已知的设置项
    if "fabrication_detection" in body:
        current["fabrication_detection"] = bool(body["fabrication_detection"])
    _save_settings(current)
    return current
