"""
设计院 (Studio) - 数据模型
独立的 ORM 模型，与主项目完全隔离
"""
import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, Float, Enum, ForeignKey, JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from studio.backend.core.database import Base


# ======================== Enums ========================

class ProjectStatus(str, enum.Enum):
    draft = "draft"
    discussing = "discussing"
    planned = "planned"
    implementing = "implementing"
    reviewing = "reviewing"
    deploying = "deploying"
    deployed = "deployed"
    rolled_back = "rolled_back"
    closed = "closed"


class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"
    system = "system"


class MessageType(str, enum.Enum):
    chat = "chat"
    plan_draft = "plan_draft"
    plan_final = "plan_final"
    code_review = "code_review"
    image = "image"


class DeployType(str, enum.Enum):
    preview = "preview"
    merge_deploy = "merge_deploy"
    direct_deploy = "direct_deploy"
    rollback = "rollback"


class DeployStatus(str, enum.Enum):
    pending = "pending"
    building = "building"
    deploying = "deploying"
    healthy = "healthy"
    failed = "failed"
    rolled_back = "rolled_back"


class AiTaskStatus(str, enum.Enum):
    """AI 后台任务状态"""
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class AiTaskType(str, enum.Enum):
    """AI 任务类型"""
    discuss = "discuss"
    finalize_plan = "finalize_plan"
    auto_review = "auto_review"


class UserStatus(str, enum.Enum):
    """Studio 用户状态"""
    pending = "pending"        # 待审批
    active = "active"          # 已激活
    disabled = "disabled"      # 已禁用


class UserRole(str, enum.Enum):
    """Studio 用户角色"""
    admin = "admin"            # 管理员 (全部权限)
    developer = "developer"    # 开发者 (项目相关权限)
    viewer = "viewer"          # 观察者 (只读)


# ======================== Models ========================

class WorkspaceDir(Base):
    """工作目录配置 (支持多目录切换)"""
    __tablename__ = "workspace_dirs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    path = Column(String(500), nullable=False, unique=True)   # 绝对路径
    label = Column(String(100), default="")                   # 可选标签
    is_active = Column(Boolean, default=False, nullable=False) # 是否为当前活跃工作区
    # Git 平台配置（默认 GitHub）
    git_provider = Column(String(20), default="github")
    # 可选 GitHub 绑定 (按工作目录隔离)
    github_token = Column(String(500), default="")
    github_repo = Column(String(255), default="")
    # 可选 GitLab 绑定 (按工作目录隔离)
    gitlab_url = Column(String(255), default="https://gitlab.com")
    gitlab_token = Column(String(500), default="")
    gitlab_repo = Column(String(255), default="")  # namespace/project
    # 可选 SVN 覆盖配置（为空则使用系统环境/当前工作副本）
    svn_repo_url = Column(String(500), default="")
    svn_username = Column(String(255), default="")
    svn_password = Column(String(500), default="")
    svn_trunk_path = Column(String(255), default="trunk")
    created_at = Column(DateTime, default=datetime.utcnow)


class StudioUser(Base):
    """设计院用户 (DB 注册用户)"""
    __tablename__ = "studio_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)
    nickname = Column(String(100), nullable=False, default="")
    role = Column(Enum(UserRole), nullable=False, default=UserRole.viewer)
    status = Column(Enum(UserStatus), nullable=False, default=UserStatus.pending)
    # 细分权限 JSON, 如 ["project.create", "project.edit", "ai.chat", "settings.view"]
    permissions = Column(JSON, default=list)
    # 元数据
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    approved_by = Column(String(100), nullable=True)       # 审批人用户名
    approved_at = Column(DateTime, nullable=True)
    last_login_at = Column(DateTime, nullable=True)


class Role(Base):
    """AI 对话角色定义 — 数据驱动的工作流配置"""
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    icon = Column(String(10), default="🎯")
    description = Column(Text, default="")
    is_builtin = Column(Boolean, default=False)
    is_enabled = Column(Boolean, default=True)

    # AI 对话配置
    role_prompt = Column(Text, nullable=False, default="")
    strategy_prompt = Column(Text, nullable=False, default="")
    tool_strategy_prompt = Column(Text, default="")
    finalization_prompt = Column(Text, default="")
    output_generation_prompt = Column(Text, default="")

    # 阶段流程配置 [{"key": "draft", "label": "草稿", "status": "draft"}, ...]
    stages = Column(JSON, nullable=False, default=list)

    # UI 文案配置 {"project_noun": "需求", "create_title": "...", ...}
    ui_labels = Column(JSON, default=lambda: {})

    # 默认技能列表 — 该角色激活时自动注入的技能名称
    # ["需求澄清", "API 设计"] — 使用名称而非 ID, 便于 seed/迁移
    default_skills = Column(JSON, default=list)

    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Skill(Base):
    """
    AI 技能定义 — 可复用的能力模块

    Skill 定义 AI 的具体能力 (WHAT it can do):
    - instruction_prompt: 核心指令, 告诉 AI 如何执行该技能
    - output_format: 期望的输出格式模板
    - examples: 少样本示例 (few-shot)
    - constraints: 约束条件列表

    与 Role 的关系:
    - Role 定义 AI 是谁 (persona), Skill 定义 AI 会什么 (capability)
    - 一个 Role 可挂载多个 Skills (通过 Role.default_skills)
    - Workflow stage 也可独立指定 Skills
    """
    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    icon = Column(String(10), default="⚡")
    description = Column(Text, default="")

    # 分类: general, analysis, coding, writing, review, testing
    category = Column(String(50), default="general")

    is_builtin = Column(Boolean, default=False)
    is_enabled = Column(Boolean, default=True)

    # ---- 技能核心定义 ----
    # 核心指令 — 告诉 AI 执行该技能时应遵循的详细步骤和方法论
    instruction_prompt = Column(Text, nullable=False, default="")
    # 输出格式模板 — 描述该技能的标准化输出结构 (Markdown/JSON 模板)
    output_format = Column(Text, default="")
    # 少样本示例 [{"input": "...", "output": "..."}, ...]
    examples = Column(JSON, default=list)
    # 约束条件 ["不要推测原因", "必须包含测试用例", ...]
    constraints = Column(JSON, default=list)

    # ---- 工具与标签 ----
    # 推荐工具列表 — 执行该技能时推荐使用的工具名
    # ["read_file", "search_text"]
    recommended_tools = Column(JSON, default=list)
    # 标签 (便于搜索和分组)
    tags = Column(JSON, default=list)

    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ToolDefinition(Base):
    """AI 工具定义 — 数据驱动的工具配置"""
    __tablename__ = "tool_definitions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)        # 工具函数名 (如 read_file)
    display_name = Column(String(100), nullable=False)             # 显示名称 (如 "读取文件")
    icon = Column(String(10), default="🔧")
    description = Column(Text, default="")                         # 管理员可见的描述
    permission_key = Column(String(50), nullable=False)            # 权限标识 (如 read_source)
    is_builtin = Column(Boolean, default=False)
    is_enabled = Column(Boolean, default=True)

    # OpenAI Function Calling 定义 (JSON)
    function_def = Column(JSON, nullable=False, default=dict)      # {"name":"...", "description":"...", "parameters":{...}}

    # 执行器类型: builtin (内部executor), command (shell), http (webhook)
    executor_type = Column(String(20), default="builtin")
    executor_config = Column(JSON, default=dict)                   # 执行器参数 (对 builtin 工具为空)

    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Project(Base):
    """需求项目"""
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    description = Column(Text, default="")
    status = Column(Enum(ProjectStatus), default=ProjectStatus.draft, nullable=False)

    # 项目类型 (定义生命周期)
    project_type = Column(String(50), default="requirement")  # requirement, bug, ...

    # 设计稿
    plan_content = Column(Text, default="")
    plan_version = Column(Integer, default=0)

    # 审查报告 (审查阶段产出)
    review_content = Column(Text, default="")
    review_version = Column(Integer, default=0)

    # GitHub 集成
    github_issue_number = Column(Integer, nullable=True)
    github_pr_number = Column(Integer, nullable=True)
    branch_name = Column(String(200), nullable=True)

    # 工作区管理
    workspace_dir = Column(String(500), nullable=True)  # 项目独立工作区路径 (审查/迭代)
    iteration_count = Column(Integer, default=0)  # 迭代次数

    # 预览
    preview_port = Column(Integer, nullable=True)

    # AI 模型配置
    discussion_model = Column(String(100), default="gpt-4o")
    implementation_model = Column(String(100), default="claude-sonnet-4-20250514")  # DEPRECATED: 不再使用

    # AI 禁言 (群聊模式: 禁言时 AI 不自动回复)
    ai_muted = Column(Boolean, default=False)

    # 角色关联
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=True)

    # 归档
    is_archived = Column(Boolean, default=False)
    archived_at = Column(DateTime, nullable=True)

    # 工具权限 (讨论阶段 AI 可用的代码查看工具)
    # 默认全开 (除 execute_command 需显式授权)
    tool_permissions = Column(JSON, default=lambda: [
        "ask_user", "read_source", "read_config", "search", "tree", "execute_readonly_command"
    ])

    # 元信息
    created_by = Column(String(100), default="admin")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    role = relationship("Role", lazy="joined")
    messages = relationship("Message", back_populates="project", cascade="all, delete-orphan",
                            order_by="Message.created_at")
    deployments = relationship("Deployment", back_populates="project", cascade="all, delete-orphan",
                               order_by="Deployment.started_at.desc()")


class Message(Base):
    """讨论消息"""
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    role = Column(Enum(MessageRole), nullable=False)
    sender_name = Column(String(100), default="")
    content = Column(Text, nullable=False)
    message_type = Column(Enum(MessageType), default=MessageType.chat)

    # 附件 (图片等)
    attachments = Column(JSON, default=list)  # [{"type":"image","url":"...","name":"..."}]

    # AI 元数据
    model_used = Column(String(100), nullable=True)
    token_usage = Column(JSON, nullable=True)  # {"prompt_tokens":x, "completion_tokens":y, "total_tokens":z}

    # 思考过程 (reasoning models)
    thinking_content = Column(Text, nullable=True)

    # 工具调用记录
    tool_calls = Column(JSON, nullable=True)  # [{"id":"...", "name":"...", "arguments":{...}, "result":"..."}]

    # 消息关系 (重试/编辑 → 指向原消息)
    parent_message_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="messages")


class Snapshot(Base):
    """代码快照"""
    __tablename__ = "snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    git_commit = Column(String(40), nullable=False)
    git_tag = Column(String(100), nullable=False)
    docker_image_tags = Column(JSON, default=dict)  # {"frontend":"tag","backend":"tag"}
    db_backup_path = Column(String(500), default="")
    description = Column(String(500), default="")
    is_healthy = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Deployment(Base):
    """部署记录"""
    __tablename__ = "deployments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    snapshot_before_id = Column(Integer, ForeignKey("snapshots.id"), nullable=True)
    snapshot_after_id = Column(Integer, ForeignKey("snapshots.id"), nullable=True)
    deploy_type = Column(Enum(DeployType), nullable=False)
    status = Column(Enum(DeployStatus), default=DeployStatus.pending)
    logs = Column(Text, default="")
    error_message = Column(Text, default="")
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="deployments")
    snapshot_before = relationship("Snapshot", foreign_keys=[snapshot_before_id])
    snapshot_after = relationship("Snapshot", foreign_keys=[snapshot_after_id])


class CustomModel(Base):
    """
    自定义/补充模型配置

    替代硬编码的 _COPILOT_PRO_EXTRA_MODELS 和 _COPILOT_EXCLUSIVE_MODELS，
    用户可通过设置页面增删改，系统首次启动时从内置种子数据初始化。
    """
    __tablename__ = "custom_models"
    __table_args__ = (
        UniqueConstraint("name", "api_backend", name="uq_custom_model_name_backend"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)              # 模型名 (API 调用用, 如 o1, claude-opus-4-20250514)
    friendly_name = Column(String(200), default="")         # 显示名
    model_family = Column(String(100), default="")          # openai, anthropic, google, ...
    task = Column(String(100), default="chat-completion")   # 任务类型
    tags = Column(JSON, default=list)                       # ["reasoning", "agents", "multimodal"]
    summary = Column(String(500), default="")               # 简介
    api_backend = Column(String(50), default="models")      # "models" = GitHub Models API, "copilot" = Copilot API
    enabled = Column(Boolean, default=True)
    is_seed = Column(Boolean, default=True)                 # True = 内置种子数据, False = 用户自建
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ModelCapabilityOverride(Base):
    """
    模型能力手动覆盖 (持久化到数据库)

    覆盖优先级最高: DB override > runtime learned > 硬编码静态 > 默认值
    model_name 已归一化 (小写、去掉 copilot: 前缀)
    """
    __tablename__ = "model_capability_overrides"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_name = Column(String(200), nullable=False, unique=True)  # 归一化名 (小写, 无 copilot: 前缀)
    max_input_tokens = Column(Integer, nullable=True)
    max_output_tokens = Column(Integer, nullable=True)
    supports_vision = Column(Boolean, nullable=True)        # null = 自动检测, true/false = 手动覆盖
    supports_tools = Column(Boolean, nullable=True)
    is_reasoning = Column(Boolean, nullable=True)
    premium_paid = Column(Float, nullable=True)              # Copilot 付费用户定价倍率 (null = 用硬编码)
    premium_free = Column(Float, nullable=True)              # Copilot 免费用户定价倍率 (null = 用硬编码, -1 = 需订阅)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AIProvider(Base):
    """
    AI 服务提供商配置

    支持三种类型:
    - github_models: GitHub Models API (内置, 用 GITHUB_TOKEN)
    - copilot: GitHub Copilot API (内置, 用 OAuth Device Flow)
    - openai_compatible: 第三方 OpenAI 兼容 API (用用户提供的 API Key)

    内置提供商 (is_builtin=True) 不可删除、不可改 base_url。
    预设提供商 (is_preset=True) 默认禁用, 用户填入 API Key 后启用。
    """
    __tablename__ = "ai_providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(50), nullable=False, unique=True)       # 唯一标识 (如 "deepseek", "qwen")
    name = Column(String(100), nullable=False)                   # 显示名 (如 "DeepSeek")
    provider_type = Column(String(50), nullable=False)           # github_models / copilot / openai_compatible
    base_url = Column(String(500), default="")                   # API base URL
    api_key = Column(String(500), default="")                    # API Key (明文存储, GET 时脱敏)
    enabled = Column(Boolean, default=False)                     # 是否启用
    is_builtin = Column(Boolean, default=False)                  # 内置 (不可删除)
    is_preset = Column(Boolean, default=False)                   # 预设第三方 (不可删 base_url)
    icon = Column(String(20), default="🔌")                     # Emoji 图标
    description = Column(String(500), default="")                # 说明
    default_models = Column(JSON, default=list)                  # 预设模型列表 [{name, friendly_name, ...}]
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class WorkflowModule(Base):
    """
    工作流功能模块 — 可复用的流水线构建块

    每个模块对应一种功能面板 (如 AI 对话、代码实施、部署等)，
    通过 component_key 映射到前端 Vue 组件。
    工作流通过引用模块进行组装。
    """
    __tablename__ = "workflow_modules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)       # 唯一标识 (如 "ai_chat")
    display_name = Column(String(100), nullable=False)            # 显示名 (如 "AI 对话")
    icon = Column(String(10), default="📦")
    description = Column(Text, default="")
    component_key = Column(String(100), nullable=False)           # Vue 组件 key: "ChatPanel", "ImplementPanel" 等
    default_config = Column(JSON, default=dict)                   # 默认配置 (可被 workflow 覆盖)
    is_builtin = Column(Boolean, default=False)
    is_enabled = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Workflow(Base):
    """
    工作流定义 — 由功能模块组装而成的流水线

    每个工作流定义了:
    - stages: 状态步骤条 (项目生命周期)
    - modules: 有序的功能模块列表 (每个引用 WorkflowModule, 含 tab 配置)
    - ui_labels: 界面文案
    Project.project_type → Workflow.name
    """
    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)       # 唯一标识 (如 "requirement")
    display_name = Column(String(100), nullable=False)            # 显示名 (如 "需求迭代")
    icon = Column(String(10), default="🔄")
    description = Column(Text, default="")
    is_builtin = Column(Boolean, default=False)
    is_enabled = Column(Boolean, default=True)

    # 阶段定义 (步骤条)
    # [{"key":"draft","label":"草稿","status":"draft","role":"..."}, ...]
    stages = Column(JSON, nullable=False, default=list)

    # 模块组装 (有序 tab 列表, 引用 WorkflowModule.name)
    # [{"module_name":"ai_chat","tab_key":"discuss","tab_label":"💬 讨论",
    #   "stage_statuses":["draft","discussing"],"role_name":"需求分析",
    #   "config":{"mode":"discuss","plan_panel":true,...}}, ...]
    modules = Column(JSON, nullable=False, default=list)

    # UI 文案 (project_noun, create_title, output_noun 等)
    ui_labels = Column(JSON, default=dict)

    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ======================== 命令授权管理 ========================

class CommandAuthRule(Base):
    """
    命令授权规则 — 预配置的命令自动审批/拒绝规则

    支持多种匹配方式 (精确/前缀/包含/正则), 可作用于全局或特定项目。
    scope=project 时挂靠具体 project_id; scope=global 时 project_id=null。
    """
    __tablename__ = "command_auth_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pattern = Column(String(500), nullable=False)                  # 命令匹配模式
    pattern_type = Column(String(20), nullable=False, default="prefix")  # prefix | exact | contains | regex
    scope = Column(String(20), nullable=False, default="global")   # global | project
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    action = Column(String(10), nullable=False, default="allow")   # allow | deny
    created_by = Column(String(100), default="")
    note = Column(String(500), default="")
    is_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", foreign_keys=[project_id])


class CommandAuditLog(Base):
    """
    命令执行审计日志 — 记录每次写命令的审批/执行结果

    method 字段记录审批来源: manual (用户手动), rule:N (规则N匹配),
    project_auto (项目级自动批准), session_cache (会话缓存命中)
    """
    __tablename__ = "command_audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    project_title = Column(String(200), default="")                # 冗余, 方便展示
    command = Column(Text, nullable=False)
    action = Column(String(20), nullable=False)                    # approved | rejected | timeout
    method = Column(String(100), default="manual")                 # manual | rule:123 | project_auto | session_cache
    scope = Column(String(20), default="once")                     # once | session | project | permanent
    operator = Column(String(100), default="")                     # 操作者 (用户名或 auto)
    created_at = Column(DateTime, default=datetime.utcnow)


class AiTask(Base):
    """
    AI 后台任务 — 持久化 AI 执行状态

    核心设计: AI 的发言（包括工具调用）作为后台任务执行，不依赖前端连接。
    前端通过订阅任务事件流获取实时进度，断开后可重连继续获取。
    """
    __tablename__ = "ai_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    task_type = Column(String(50), nullable=False, default="discuss")  # discuss / finalize_plan / auto_review
    status = Column(String(20), nullable=False, default="pending")     # pending / running / completed / failed / cancelled

    # 输入
    model = Column(String(100), default="")
    sender_name = Column(String(100), default="")
    input_message = Column(Text, default="")
    input_attachments = Column(JSON, default=list)
    max_tool_rounds = Column(Integer, default=15)
    regenerate = Column(Boolean, default=False)

    # 累积输出 (用于持久化 + 重连恢复)
    output_content = Column(Text, default="")
    thinking_content = Column(Text, default="")
    tool_calls_data = Column(JSON, default=list)
    token_usage = Column(JSON, nullable=True)
    error_message = Column(Text, default="")

    # 结果
    result_message_id = Column(Integer, nullable=True)  # 最终保存的 Message 的 ID

    # 时间
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    project = relationship("Project")


# ======================== MCP (Model Context Protocol) ========================

class MCPServer(Base):
    """
    MCP Server 配置 — 管理外部 MCP 服务接入

    每个 MCP Server 对应一个外部工具服务 (如 GitHub MCP Server),
    通过 stdio/sse/streamable_http 协议通信。
    """
    __tablename__ = "mcp_servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    slug = Column(String(50), nullable=False, unique=True)        # 唯一标识 (如 "github")
    name = Column(String(100), nullable=False)                    # 显示名 (如 "GitHub MCP Server")
    description = Column(Text, default="")
    icon = Column(String(10), default="🔌")

    # 传输配置
    transport = Column(String(20), nullable=False, default="stdio")  # stdio | sse | streamable_http
    command = Column(String(500), default="")                     # stdio: 启动命令
    args = Column(JSON, default=list)                             # stdio: 命令参数
    env_template = Column(JSON, default=dict)                     # 环境变量模板 (支持 {github_token} 占位符)
    url = Column(String(500), default="")                         # sse/http: 远程 URL

    # 权限映射: MCP tool_name → Studio permission_key (JSON object)
    permission_map = Column(JSON, default=dict)

    enabled = Column(Boolean, default=True)
    is_builtin = Column(Boolean, default=False)                   # 内置服务不可删除
    sort_order = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MCPAuditLog(Base):
    """
    MCP 调用审计日志 — 记录每次 MCP 工具调用

    用于安全审计、调用统计、故障排查
    """
    __tablename__ = "mcp_audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    server_slug = Column(String(50), nullable=False, index=True)  # MCP Server 标识
    tool_name = Column(String(100), nullable=False)               # MCP 工具名
    arguments = Column(JSON, default=dict)                        # 调用参数 (脱敏)
    result_preview = Column(Text, default="")                     # 结果预览 (截断)
    duration_ms = Column(Integer, default=0)                      # 耗时 (毫秒)
    success = Column(Boolean, default=True)                       # 是否成功
    error_message = Column(Text, default="")                      # 错误信息
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", foreign_keys=[project_id])