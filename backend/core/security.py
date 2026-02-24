"""
设计院 (Studio) - 认证安全模块

认证策略:
  1. 管理员账户: 用户名+密码登录, 由环境变量配置
  2. 主项目 session 复用: 检测 localStorage 中的主项目 JWT,
     通过主项目 API 验证后签发 Studio token
  3. DB 注册用户: 注册→审批→登录
  4. Studio 签发独立 JWT, 包含 source 和 user_info
"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

import httpx
from jose import jwt, JWTError
from fastapi import Request, HTTPException, status

from studio.backend.core.config import settings

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"


# ==================== 密码哈希 (轻量 PBKDF2, 无需额外依赖) ====================

def hash_password(password: str) -> str:
    """PBKDF2-SHA256 哈希密码"""
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}${h.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """验证密码是否匹配"""
    try:
        salt, stored_hash = hashed.split("$", 1)
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
        return secrets.compare_digest(h.hex(), stored_hash)
    except Exception:
        return False


# ==================== Token 操作 ====================

def create_studio_token(
    username: str,
    nickname: str = "",
    source: str = "admin",
    user_id: Optional[int] = None,
    role: Optional[str] = None,
    permissions: Optional[list] = None,
    db_user_id: Optional[int] = None,
) -> str:
    """
    创建 Studio JWT

    Args:
        username: 用户名 (admin / 主项目用户名 / 注册用户名)
        nickname: 显示昵称
        source: 'admin' | 'main_project' | 'db_user'
        user_id: 主项目用户ID (仅 source=main_project 时)
        role: 用户角色 (仅 source=db_user 时)
        permissions: 细分权限列表 (仅 source=db_user 时)
        db_user_id: DB 用户ID (仅 source=db_user 时)
    """
    expire = datetime.utcnow() + timedelta(days=settings.studio_token_expire_days)
    payload = {
        "sub": username,
        "nickname": nickname or username,
        "source": source,
        "exp": expire,
    }
    if user_id is not None:
        payload["main_user_id"] = user_id
    if db_user_id is not None:
        payload["db_user_id"] = db_user_id
    if role:
        payload["role"] = role
    if permissions:
        payload["permissions"] = permissions
    return jwt.encode(payload, settings.studio_secret_key, algorithm=ALGORITHM)


def decode_studio_token(token: str) -> Optional[dict]:
    """解码 Studio JWT, 返回 payload 或 None"""
    try:
        return jwt.decode(token, settings.studio_secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None


# ==================== 管理员登录 ====================

def verify_admin(username: str, password: str) -> bool:
    """验证管理员凭据"""
    return (
        username == settings.studio_admin_user
        and password == settings.studio_admin_pass
    )


# ==================== 主项目 Token 验证 ====================

async def verify_main_project_token(token: str) -> Optional[dict]:
    """
    通过主项目 API 验证 token, 返回用户信息
    当 main_api_url 未配置时直接返回 None

    Returns:
        {"id": 1, "username": "xxx", "nickname": "xxx"} 或 None
    """
    if not settings.main_api_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{settings.main_api_url}/api/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 200:
                data = response.json()
                return {
                    "id": data.get("id"),
                    "username": data.get("username", ""),
                    "nickname": data.get("nickname", data.get("username", "")),
                }
    except Exception as e:
        logger.warning(f"主项目 token 验证失败: {e}")
    return None


# ==================== 请求认证依赖 ====================

def get_studio_user(request: Request) -> dict:
    """
    FastAPI 依赖: 从请求中获取当前用户

    检查顺序:
      1. Authorization: Bearer <studio_token>
      2. 返回 401

    Returns:
        {"username": "...", "nickname": "...", "source": "admin"|"main_project"}
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = auth_header[7:]
    payload = decode_studio_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "username": payload.get("sub", "unknown"),
        "nickname": payload.get("nickname", payload.get("sub", "unknown")),
        "source": payload.get("source", "admin"),
        "main_user_id": payload.get("main_user_id"),
        "db_user_id": payload.get("db_user_id"),
        "role": payload.get("role", "admin" if payload.get("source") == "admin" else None),
        "permissions": payload.get("permissions", []),
    }


def get_optional_studio_user(request: Request) -> Optional[dict]:
    """
    FastAPI 依赖: 获取当前用户 (可选, 不强制)
    """
    try:
        return get_studio_user(request)
    except HTTPException:
        return None


def require_admin(user: dict) -> bool:
    """检查用户是否为管理员 (env admin 或 role=admin 的 DB 用户)"""
    if user.get("source") == "admin":
        return True
    if user.get("role") == "admin":
        return True
    return False


def get_admin_user(request: Request) -> dict:
    """FastAPI 依赖: 仅允许管理员访问"""
    user = get_studio_user(request)
    if not require_admin(user):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


# ==================== 权限定义 ====================

# 细分权限列表 (前端渲染 & 后端校验共用)
STUDIO_PERMISSIONS = [
    # 项目相关
    {"key": "project.create",   "label": "创建项目",   "group": "项目", "icon": "📁"},
    {"key": "project.edit",     "label": "编辑项目",   "group": "项目", "icon": "✏️"},
    {"key": "project.delete",   "label": "删除项目",   "group": "项目", "icon": "🗑️"},
    {"key": "project.archive",  "label": "归档项目",   "group": "项目", "icon": "📦"},
    # AI 对话
    {"key": "ai.chat",          "label": "AI 对话",    "group": "AI",   "icon": "💬"},
    {"key": "ai.finalize",      "label": "确定方案",   "group": "AI",   "icon": "📋"},
    # 实施 & 部署
    {"key": "impl.review",      "label": "审查代码",   "group": "实施", "icon": "🔍"},
    {"key": "impl.deploy",      "label": "部署上线",   "group": "实施", "icon": "🚀"},
    # 设置
    {"key": "settings.view",    "label": "查看设置",   "group": "设置", "icon": "⚙️"},
    {"key": "settings.edit",    "label": "修改设置",   "group": "设置", "icon": "🔧"},
    # 用户管理 (仅 admin)
    {"key": "users.manage",     "label": "用户管理",   "group": "管理", "icon": "👥"},
]

# 角色对应的预设权限
ROLE_DEFAULT_PERMISSIONS: dict[str, list[str]] = {
    "admin": [p["key"] for p in STUDIO_PERMISSIONS],  # 全部
    "developer": [
        "project.create", "project.edit", "project.archive",
        "ai.chat", "ai.finalize",
        "impl.review",
        "settings.view",
    ],
    "viewer": [
        "settings.view",
    ],
}
