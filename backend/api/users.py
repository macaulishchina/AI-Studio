"""
设计院 (Studio) - 用户管理 API

功能:
  1. 注册 (无需登录, 创建 pending 用户)
  2. DB 用户登录
  3. 管理员: 审批/拒绝/禁用用户, 分配角色和权限
  4. 权限定义列表 (供前端渲染)
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.core.security import (
    hash_password,
    verify_password,
    create_studio_token,
    get_studio_user,
    get_admin_user,
    STUDIO_PERMISSIONS,
    ROLE_DEFAULT_PERMISSIONS,
)
from backend.models import StudioUser, UserStatus, UserRole

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/users", tags=["Users"])


# ==================== Schemas ====================

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=4, max_length=200)
    nickname: str = Field("", max_length=100)


class UserLoginRequest(BaseModel):
    username: str = Field(..., max_length=100)
    password: str = Field(..., max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    nickname: str
    source: str
    role: str
    permissions: list


class UserResponse(BaseModel):
    id: int
    username: str
    nickname: str
    role: str
    status: str
    permissions: list
    created_at: str
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    last_login_at: Optional[str] = None

    class Config:
        from_attributes = True


class ApproveRequest(BaseModel):
    """审批请求"""
    role: str = Field("viewer", description="授予角色: admin / developer / viewer")
    permissions: List[str] = Field(default_factory=list, description="细分权限列表")


class UpdateUserRequest(BaseModel):
    """更新用户"""
    nickname: Optional[str] = Field(None, max_length=100)
    role: Optional[str] = None
    permissions: Optional[List[str]] = None
    status: Optional[str] = None


class PermissionGroup(BaseModel):
    group: str
    items: list


# ==================== Helper ====================

def _fmt_dt(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() + "Z" if dt else None


def _user_response(u: StudioUser) -> UserResponse:
    return UserResponse(
        id=u.id,
        username=u.username,
        nickname=u.nickname or u.username,
        role=u.role.value if u.role else "viewer",
        status=u.status.value if u.status else "pending",
        permissions=u.permissions or [],
        created_at=_fmt_dt(u.created_at) or "",
        approved_by=u.approved_by,
        approved_at=_fmt_dt(u.approved_at),
        last_login_at=_fmt_dt(u.last_login_at),
    )


# ==================== 公开端点 ====================

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """注册新用户 (无需登录, 创建后状态为 pending, 需管理员审批)"""
    # 检查用户名是否已存在
    existing = await db.execute(
        select(StudioUser).where(StudioUser.username == data.username)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"用户名「{data.username}」已存在")

    user = StudioUser(
        username=data.username,
        password_hash=hash_password(data.password),
        nickname=data.nickname or data.username,
        role=UserRole.viewer,
        status=UserStatus.pending,
        permissions=[],
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    logger.info(f"📝 新用户注册: {data.username} (待审批)")
    return _user_response(user)


@router.post("/login", response_model=TokenResponse)
async def user_login(data: UserLoginRequest, db: AsyncSession = Depends(get_db)):
    """DB 注册用户登录"""
    result = await db.execute(
        select(StudioUser).where(StudioUser.username == data.username)
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if user.status == UserStatus.pending:
        raise HTTPException(status_code=403, detail="账户待审批，请联系管理员激活")
    if user.status == UserStatus.disabled:
        raise HTTPException(status_code=403, detail="账户已被禁用")

    # 更新最后登录时间
    user.last_login_at = datetime.utcnow()
    await db.flush()

    perms = user.permissions or []
    token = create_studio_token(
        username=user.username,
        nickname=user.nickname or user.username,
        source="db_user",
        db_user_id=user.id,
        role=user.role.value if user.role else "viewer",
        permissions=perms,
    )
    logger.info(f"✅ DB 用户登录: {user.username}")
    return TokenResponse(
        access_token=token,
        username=user.username,
        nickname=user.nickname or user.username,
        source="db_user",
        role=user.role.value if user.role else "viewer",
        permissions=perms,
    )


# ==================== 管理端点 (需管理员) ====================

@router.get("", response_model=List[UserResponse])
async def list_users(
    status_filter: Optional[str] = None,
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """列出所有注册用户"""
    query = select(StudioUser).order_by(StudioUser.created_at.desc())
    if status_filter:
        query = query.where(StudioUser.status == status_filter)
    result = await db.execute(query)
    return [_user_response(u) for u in result.scalars().all()]


@router.post("/{user_id}/approve", response_model=UserResponse)
async def approve_user(
    user_id: int,
    data: ApproveRequest,
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """审批用户 (激活 + 分配角色权限)"""
    result = await db.execute(select(StudioUser).where(StudioUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 设置角色
    try:
        user.role = UserRole(data.role)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效角色: {data.role}")

    # 设置权限: 如果未指定, 使用角色默认权限
    if data.permissions:
        user.permissions = data.permissions
    else:
        user.permissions = ROLE_DEFAULT_PERMISSIONS.get(data.role, [])

    user.status = UserStatus.active
    user.approved_by = admin.get("username", "admin")
    user.approved_at = datetime.utcnow()

    await db.flush()
    await db.refresh(user)
    logger.info(f"✅ 管理员 {admin['username']} 审批用户 {user.username} → {data.role}")
    return _user_response(user)


@router.post("/{user_id}/reject", response_model=UserResponse)
async def reject_user(
    user_id: int,
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """拒绝/禁用用户"""
    result = await db.execute(select(StudioUser).where(StudioUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    user.status = UserStatus.disabled
    await db.flush()
    await db.refresh(user)
    logger.info(f"🚫 管理员 {admin['username']} 禁用用户 {user.username}")
    return _user_response(user)


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    data: UpdateUserRequest,
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """更新用户信息/角色/权限"""
    result = await db.execute(select(StudioUser).where(StudioUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if data.nickname is not None:
        user.nickname = data.nickname
    if data.role is not None:
        try:
            user.role = UserRole(data.role)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效角色: {data.role}")
    if data.permissions is not None:
        user.permissions = data.permissions
    if data.status is not None:
        try:
            user.status = UserStatus(data.status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"无效状态: {data.status}")

    await db.flush()
    await db.refresh(user)
    logger.info(f"📝 管理员 {admin['username']} 更新用户 {user.username}")
    return _user_response(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """删除用户"""
    result = await db.execute(select(StudioUser).where(StudioUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    await db.delete(user)
    logger.info(f"🗑️ 管理员 {admin['username']} 删除用户 {user.username}")


@router.post("/{user_id}/reset-password", response_model=UserResponse)
async def reset_password(
    user_id: int,
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """管理员重置用户密码为默认 (studio123)"""
    result = await db.execute(select(StudioUser).where(StudioUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    user.password_hash = hash_password("studio123")
    await db.flush()
    logger.info(f"🔑 管理员 {admin['username']} 重置用户 {user.username} 的密码")
    return _user_response(user)


# ==================== 权限定义 ====================

@router.get("/permissions/definitions")
async def get_permission_definitions():
    """获取所有细分权限定义 (按分组返回, 供前端渲染)"""
    groups: dict[str, list] = {}
    for p in STUDIO_PERMISSIONS:
        g = p["group"]
        if g not in groups:
            groups[g] = []
        groups[g].append({"key": p["key"], "label": p["label"], "icon": p["icon"]})

    return {
        "groups": [{"group": g, "items": items} for g, items in groups.items()],
        "role_defaults": ROLE_DEFAULT_PERMISSIONS,
    }


@router.get("/pending-count")
async def pending_count(
    admin: dict = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """获取待审批用户数量"""
    from sqlalchemy import func
    result = await db.execute(
        select(func.count()).select_from(StudioUser).where(StudioUser.status == UserStatus.pending)
    )
    return {"count": result.scalar() or 0}
