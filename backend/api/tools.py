"""
设计院 (Studio) - 工具管理 API
数据驱动的 AI 工具配置 CRUD + 权限列表
"""
import logging
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.database import get_db, async_session_maker
from studio.backend.models import ToolDefinition
from studio.backend.services.tool_registry import load_tools_from_db as _refresh_tool_cache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/tools", tags=["Tools"])


# ==================== Schemas ====================

class ToolCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    display_name: str = Field(..., min_length=1, max_length=100)
    icon: str = Field("🔧", max_length=10)
    description: str = Field("", max_length=500)
    permission_key: str = Field(..., min_length=1, max_length=50)
    function_def: Dict[str, Any] = Field(default_factory=dict)
    executor_type: str = Field("builtin", pattern=r"^(builtin|command|http)$")
    executor_config: Dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0


class ToolUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    display_name: Optional[str] = Field(None, min_length=1, max_length=100)
    icon: Optional[str] = Field(None, max_length=10)
    description: Optional[str] = None
    permission_key: Optional[str] = Field(None, min_length=1, max_length=50)
    is_enabled: Optional[bool] = None
    function_def: Optional[Dict[str, Any]] = None
    executor_type: Optional[str] = Field(None, pattern=r"^(builtin|command|http)$")
    executor_config: Optional[Dict[str, Any]] = None
    sort_order: Optional[int] = None


class ToolResponse(BaseModel):
    id: int
    name: str
    display_name: str
    icon: str
    description: str
    permission_key: str
    is_builtin: bool
    is_enabled: bool
    function_def: dict
    executor_type: str
    executor_config: dict
    sort_order: int
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class PermissionInfo(BaseModel):
    """权限摘要 — 供 ProjectPermissions 面板使用"""
    key: str
    label: str
    icon: str
    tip: str
    is_meta: bool = False  # 元权限标记 (非实际工具权限)
    parent: Optional[str] = None  # 父权限 key (用于嵌套展示)


# ==================== Seed Data ====================

BUILTIN_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "ask_user",
        "display_name": "主动提问",
        "icon": "❓",
        "description": "AI 遇到不明确需求时可主动向用户提问澄清",
        "permission_key": "ask_user",
        "is_builtin": True,
        "sort_order": 0,
        "executor_type": "builtin",
        "function_def": {
            "name": "ask_user",
            "description": (
                "向用户提出需要澄清的问题。当描述模糊、有多种理解方式、"
                "或缺少关键信息时，主动调用此工具提问。可以一次提出多个问题。\n\n"
                "## 使用规范\n"
                "- 每个问题通过 type 指定 'single'(单选) 或 'multi'(多选)\n"
                "- options 数组中的选项按推荐程度从高到低排列\n"
                "- 为最推荐的 1-2 个选项设置 recommended: true\n"
                "- 单选题最后一个选项通常是'其他（请说明）'之类的自定义选项\n"
                "- 用 context 字段简要说明为什么需要明确这个问题\n"
                "- 调用此工具后你必须停止，等待用户回答后再继续"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "description": "问题列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string", "description": "问题文本"},
                                "type": {"type": "string", "enum": ["single", "multi"], "description": "单选 single 或多选 multi"},
                                "options": {
                                    "type": "array",
                                    "description": "选项列表",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {"type": "string", "description": "选项文本"},
                                            "description": {"type": "string", "description": "选项补充说明"},
                                            "recommended": {"type": "boolean", "description": "是否推荐"},
                                        },
                                        "required": ["label"],
                                    },
                                },
                                "context": {"type": "string", "description": "为什么需要明确这个问题"},
                            },
                            "required": ["question"],
                        },
                    },
                },
                "required": ["questions"],
            },
        },
    },
    {
        "name": "read_file",
        "display_name": "读取文件",
        "icon": "📖",
        "description": "允许 AI 读取项目源代码文件内容",
        "permission_key": "read_source",
        "is_builtin": True,
        "sort_order": 1,
        "executor_type": "builtin",
        "function_def": {
            "name": "read_file",
            "description": (
                "读取项目中的文件内容。支持指定起始行号来精确读取感兴趣的片段，"
                "不必每次从头读取整个文件。推荐策略：先用 search_text 定位行号，"
                "再用 start_line 跳转到目标位置读取。单次最多返回 200 行。"
                "小文件（<200行）直接一次读完，不要拆分。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对于项目根目录的文件路径"},
                    "start_line": {"type": "integer", "description": "起始行号 (1-based)"},
                    "end_line": {"type": "integer", "description": "结束行号 (1-based, inclusive)"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "name": "search_text",
        "display_name": "搜索代码",
        "icon": "🔍",
        "description": "允许 AI 在项目中进行全文搜索",
        "permission_key": "search",
        "is_builtin": True,
        "sort_order": 2,
        "executor_type": "builtin",
        "function_def": {
            "name": "search_text",
            "description": (
                "在项目文件中搜索文本或正则表达式，返回匹配的文件路径、行号和上下文。"
                "这是最高效的代码定位工具——先搜索确定位置，再用 read_file 的 start_line 精确读取。"
                "务必指定 include_pattern 缩小搜索范围。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索的文本或正则表达式"},
                    "is_regex": {"type": "boolean", "description": "是否为正则表达式", "default": False},
                    "include_pattern": {"type": "string", "description": "文件名 glob 过滤，如 '*.py'"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "name": "list_directory",
        "display_name": "浏览目录",
        "icon": "🌳",
        "description": "允许 AI 列出目录下的文件和子目录",
        "permission_key": "tree",
        "is_builtin": True,
        "sort_order": 3,
        "executor_type": "builtin",
        "function_def": {
            "name": "list_directory",
            "description": (
                "列出目录下的文件和子目录。用于了解项目局部结构。"
                "建议先用 get_file_tree 获取整体概览，再用此工具查看特定目录的详细内容。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对于项目根目录的目录路径", "default": ""},
                },
                "required": [],
            },
        },
    },
    {
        "name": "get_file_tree",
        "display_name": "文件树",
        "icon": "🗂️",
        "description": "获取项目完整文件树结构概览",
        "permission_key": "tree",
        "is_builtin": True,
        "sort_order": 4,
        "executor_type": "builtin",
        "function_def": {
            "name": "get_file_tree",
            "description": (
                "获取项目完整文件树（带缩进的树状结构）。"
                "适合在对话开始时调用一次，快速了解项目整体结构。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "子目录路径", "default": ""},
                    "max_depth": {"type": "integer", "description": "目录树最大深度", "default": 3},
                },
                "required": [],
            },
        },
    },
    {
        "name": "run_command",
        "display_name": "执行命令",
        "icon": "🖥️",
        "description": "在项目工作目录中执行 shell 命令。默认仅允许只读命令 (git log, ls, grep 等)；开启「写入命令」权限后可执行修改命令，受命令授权规则约束",
        "permission_key": "execute_readonly_command",
        "is_builtin": True,
        "sort_order": 5,
        "executor_type": "builtin",
        "function_def": {
            "name": "run_command",
            "description": (
                "在项目工作目录中执行 shell 命令。支持常用的只读命令如 "
                "git (log, diff, show, status, blame), ls, cat, head, tail, find, "
                "grep, wc, diff, python3 -c 等。非只读命令需要额外授权。\n\n"
                "常用场景：\n"
                "- `git log --oneline -20` 查看近 20 条提交\n"
                "- `git diff origin/main...HEAD -- path/to/file` 查看单文件变更\n"
                "- `find . -name '*.py' -newer some_file` 查找新修改的文件\n"
                "- `python3 -c \"import json; ...\"` 执行简单脚本\n"
                "- `docker ps` 查看运行中的容器"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的 shell 命令 (单行)"},
                },
                "required": ["command"],
            },
        },
    },
]

# 权限元数据 (工具权限 + 特殊控制权限)
PERMISSION_META: List[Dict[str, Any]] = [
    {"key": "ask_user",      "label": "主动提问", "icon": "❓", "tip": "AI 遇到不明确需求时可主动向用户提问澄清", "is_meta": False},
    {"key": "read_source",   "label": "读取源码", "icon": "📖", "tip": "允许 AI 读取项目源代码文件内容", "is_meta": False},
    {"key": "read_config",   "label": "读取配置", "icon": "📄", "tip": "允许 AI 读取 package.json、Dockerfile 等配置文件", "is_meta": False},
    {"key": "search",        "label": "搜索代码", "icon": "🔍", "tip": "允许 AI 在项目中进行全文搜索", "is_meta": False},
    {"key": "tree",          "label": "浏览目录", "icon": "🌳", "tip": "允许 AI 浏览项目的目录结构", "is_meta": False},
    {"key": "execute_readonly_command", "label": "执行命令", "icon": "🖥️", "tip": "允许 AI 在项目目录执行 shell 命令（默认仅限只读命令，如 git log、ls、grep 等）", "is_meta": False},
    {"key": "execute_command", "label": "写入命令", "icon": "⚠️", "tip": "解除只读限制，允许执行修改文件、安装依赖等写命令。受「设置 → 命令授权」规则约束，默认每次需用户审批", "is_meta": False, "parent": "execute_readonly_command"},
]


async def seed_tools():
    """初始化内置工具定义"""
    async with async_session_maker() as db:
        result = await db.execute(select(ToolDefinition).where(ToolDefinition.is_builtin.is_(True)))
        existing = {t.name: t for t in result.scalars().all()}

        for tool_data in BUILTIN_TOOLS:
            name = tool_data["name"]
            if name in existing:
                # 更新内置工具定义 (保留 is_enabled 等用户可编辑字段)
                tool = existing[name]
                tool.display_name = tool_data["display_name"]
                tool.icon = tool_data["icon"]
                tool.description = tool_data["description"]
                tool.permission_key = tool_data["permission_key"]
                tool.function_def = tool_data["function_def"]
                tool.executor_type = tool_data["executor_type"]
                tool.sort_order = tool_data["sort_order"]
                logger.info(f"🔄 更新内置工具: {name}")
            else:
                tool = ToolDefinition(
                    name=name,
                    display_name=tool_data["display_name"],
                    icon=tool_data["icon"],
                    description=tool_data["description"],
                    permission_key=tool_data["permission_key"],
                    is_builtin=True,
                    is_enabled=True,
                    function_def=tool_data["function_def"],
                    executor_type=tool_data.get("executor_type", "builtin"),
                    executor_config=tool_data.get("executor_config", {}),
                    sort_order=tool_data["sort_order"],
                )
                db.add(tool)
                logger.info(f"✨ 创建内置工具: {name}")

        await db.commit()
        logger.info("✅ 工具定义种子初始化完成")


# ==================== Helper ====================

def _tool_to_response(tool: ToolDefinition) -> ToolResponse:
    return ToolResponse(
        id=tool.id,
        name=tool.name,
        display_name=tool.display_name,
        icon=tool.icon or "🔧",
        description=tool.description or "",
        permission_key=tool.permission_key or "",
        is_builtin=tool.is_builtin or False,
        is_enabled=tool.is_enabled if tool.is_enabled is not None else True,
        function_def=tool.function_def or {},
        executor_type=tool.executor_type or "builtin",
        executor_config=tool.executor_config or {},
        sort_order=tool.sort_order or 0,
        created_at=tool.created_at.isoformat() + "Z" if tool.created_at else "",
        updated_at=tool.updated_at.isoformat() + "Z" if tool.updated_at else "",
    )


# ==================== Routes ====================

@router.get("", response_model=List[ToolResponse])
async def list_tools(
    enabled_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """列出所有工具定义"""
    query = select(ToolDefinition).order_by(ToolDefinition.sort_order, ToolDefinition.id)
    if enabled_only:
        query = query.where(ToolDefinition.is_enabled.is_(True))
    result = await db.execute(query)
    tools = result.scalars().all()
    return [_tool_to_response(t) for t in tools]


@router.get("/permissions", response_model=List[PermissionInfo])
async def list_permissions(db: AsyncSession = Depends(get_db)):
    """获取所有权限定义（动态合并 DB 工具权限 + 元权限），供前端权限面板使用

    动态流程:
    1. 从 DB 加载所有已启用工具, 按 permission_key 去重, 生成工具权限列表
    2. 收集已禁用工具的 permission_key (防止 step 3 把它们加回来)
    3. 追加 PERMISSION_META 中的元权限 (is_meta=True), 跳过已被禁用工具覆盖的非元权限
    """
    # 1) 从 DB 获取已启用工具的权限
    result = await db.execute(
        select(ToolDefinition)
        .where(ToolDefinition.is_enabled.is_(True))
        .order_by(ToolDefinition.sort_order, ToolDefinition.id)
    )
    tools = result.scalars().all()

    # 2) 收集已禁用工具的 permission_key
    disabled_result = await db.execute(
        select(ToolDefinition.permission_key)
        .where(ToolDefinition.is_enabled.is_(False))
    )
    disabled_perm_keys: set[str] = {
        row[0] for row in disabled_result.fetchall() if row[0]
    }

    # 构建 PERMISSION_META 查找表 (用于获取 parent 等额外字段)
    meta_lookup = {pm["key"]: pm for pm in PERMISSION_META}

    seen_keys: set[str] = set()
    perms: list[PermissionInfo] = []

    for t in tools:
        pk = t.permission_key
        if pk and pk not in seen_keys:
            seen_keys.add(pk)
            # 从 PERMISSION_META 获取补充信息 (label/tip 可能更精确)
            meta = meta_lookup.get(pk, {})
            perms.append(PermissionInfo(
                key=pk,
                label=meta.get("label") or t.display_name or t.name,
                icon=meta.get("icon") or t.icon or "🔧",
                tip=meta.get("tip") or t.description or "",
                is_meta=meta.get("is_meta", False),
                parent=meta.get("parent"),
            ))

    # 3) 追加 PERMISSION_META 中未被 DB 工具覆盖的权限
    #    - 元权限 (is_meta=True) 始终添加 (如 execute_command, auto_approve_commands)
    #    - 非元权限: 仅当该 key 没有对应的已禁用工具时才添加 (遗留兼容)
    for pm in PERMISSION_META:
        if pm["key"] not in seen_keys:
            # 跳过被明确禁用的工具权限 (仅跳过非元权限)
            if not pm.get("is_meta", False) and pm["key"] in disabled_perm_keys:
                continue
            # 非元权限的 parent 被禁用时也跳过 (如 execute_command 的 parent execute_readonly_command 被禁用)
            parent_key = pm.get("parent")
            if parent_key and parent_key in disabled_perm_keys and parent_key not in seen_keys:
                continue
            seen_keys.add(pm["key"])
            perms.append(PermissionInfo(**{k: v for k, v in pm.items() if k in PermissionInfo.model_fields}))

    # 4) 排序: 确保子权限紧跟在父权限后面
    #    先保持原有顺序, 再把有 parent 的项移到父项之后
    ordered: list[PermissionInfo] = []
    deferred: list[PermissionInfo] = []  # 有 parent 的项
    for p in perms:
        if p.parent:
            deferred.append(p)
        else:
            ordered.append(p)
    # 将子权限插到父权限后面
    for child in deferred:
        parent_idx = next((i for i, p in enumerate(ordered) if p.key == child.parent), None)
        if parent_idx is not None:
            ordered.insert(parent_idx + 1, child)
        else:
            ordered.append(child)

    return ordered


@router.get("/{tool_id}", response_model=ToolResponse)
async def get_tool(tool_id: int, db: AsyncSession = Depends(get_db)):
    """获取工具详情"""
    result = await db.execute(select(ToolDefinition).where(ToolDefinition.id == tool_id))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=404, detail="工具不存在")
    return _tool_to_response(tool)


@router.post("", response_model=ToolResponse, status_code=status.HTTP_201_CREATED)
async def create_tool(data: ToolCreate, db: AsyncSession = Depends(get_db)):
    """创建自定义工具"""
    existing = await db.execute(select(ToolDefinition).where(ToolDefinition.name == data.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"工具名「{data.name}」已存在")

    tool = ToolDefinition(
        name=data.name,
        display_name=data.display_name,
        icon=data.icon,
        description=data.description,
        permission_key=data.permission_key,
        is_builtin=False,
        is_enabled=True,
        function_def=data.function_def,
        executor_type=data.executor_type,
        executor_config=data.executor_config,
        sort_order=data.sort_order,
    )
    db.add(tool)
    await db.flush()
    await db.refresh(tool)
    await _refresh_tool_cache()  # 刷新运行时缓存
    return _tool_to_response(tool)


@router.put("/{tool_id}", response_model=ToolResponse)
async def update_tool(tool_id: int, data: ToolUpdate, db: AsyncSession = Depends(get_db)):
    """更新工具配置"""
    result = await db.execute(select(ToolDefinition).where(ToolDefinition.id == tool_id))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=404, detail="工具不存在")

    update_data = data.model_dump(exclude_unset=True)

    # 检查名称唯一
    if "name" in update_data and update_data["name"] != tool.name:
        existing = await db.execute(select(ToolDefinition).where(ToolDefinition.name == update_data["name"]))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"工具名「{update_data['name']}」已存在")

    for key, value in update_data.items():
        setattr(tool, key, value)

    await db.flush()
    await db.refresh(tool)
    await _refresh_tool_cache()  # 刷新运行时缓存
    return _tool_to_response(tool)


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(tool_id: int, db: AsyncSession = Depends(get_db)):
    """删除工具（内置工具不可删除）"""
    result = await db.execute(select(ToolDefinition).where(ToolDefinition.id == tool_id))
    tool = result.scalar_one_or_none()
    if not tool:
        raise HTTPException(status_code=404, detail="工具不存在")
    if tool.is_builtin:
        raise HTTPException(status_code=403, detail="内置工具不可删除")
    await db.delete(tool)
    await _refresh_tool_cache()  # 刷新运行时缓存


@router.post("/{tool_id}/duplicate", response_model=ToolResponse, status_code=status.HTTP_201_CREATED)
async def duplicate_tool(tool_id: int, db: AsyncSession = Depends(get_db)):
    """复制工具"""
    result = await db.execute(select(ToolDefinition).where(ToolDefinition.id == tool_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="工具不存在")

    base_name = f"{source.name}_copy"
    name = base_name
    counter = 2
    while True:
        existing = await db.execute(select(ToolDefinition).where(ToolDefinition.name == name))
        if not existing.scalar_one_or_none():
            break
        name = f"{base_name}_{counter}"
        counter += 1

    new_tool = ToolDefinition(
        name=name,
        display_name=f"{source.display_name} (副本)",
        icon=source.icon,
        description=source.description,
        permission_key=source.permission_key,
        is_builtin=False,
        is_enabled=True,
        function_def=source.function_def,
        executor_type=source.executor_type,
        executor_config=source.executor_config,
        sort_order=source.sort_order + 1,
    )
    db.add(new_tool)
    await db.flush()
    await db.refresh(new_tool)
    return _tool_to_response(new_tool)
