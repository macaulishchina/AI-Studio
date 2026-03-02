"""
Dogi (多吉) - FastAPI 主入口
AI 驱动的通用对话与项目协作平台
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core.database import init_db
from backend.api.projects import router as projects_router
from backend.api.discussion import router as discussion_router
from backend.api.implementation import router as implementation_router
from backend.api.deployment import router as deployment_router
from backend.api.snapshots import router as snapshots_router, system_router
from backend.api.models_api import router as models_router
from backend.api.model_config import router as model_config_router
from backend.api.copilot_auth_api import router as copilot_auth_router
from backend.api.studio_auth import router as studio_auth_router
from backend.api.endpoint_probe import router as endpoint_probe_router
from backend.api.provider_api import router as provider_router, seed_providers
from backend.api.roles import router as roles_router
from backend.api.skills import router as skills_router
from backend.api.tools import router as tools_router
from backend.api.workflows import module_router as workflow_module_router, workflow_router as workflow_router
from backend.api.tasks import project_router as tasks_project_router, task_router as tasks_router
from backend.api.ws import router as ws_router
from backend.api.users import router as users_router
from backend.api.command_auth import router as command_auth_router
from backend.api.workspace_dirs import router as workspace_dirs_router
from backend.api.mcp import router as mcp_router, seed_mcp_servers
from backend.api.conversations import router as conversations_router
from backend.api.observability import router as observability_router
from backend.api.voice import router as voice_router
from backend.api.camera import router as camera_router
from backend.api.stt import router as stt_router
from backend.api.antigravity_auth_api import router as antigravity_auth_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动/关闭生命周期"""
    logger.info("🐕 Dogi 启动中...")
    await init_db()
    logger.info("✅ 数据库初始化完成")

    # 自动迁移: 先于数据加载, 确保新列已存在
    await _auto_migrate()

    # 加载能力覆盖到内存
    from backend.api.model_config import load_capability_overrides_to_cache
    from backend.api.models_api import load_pricing_overrides_from_db
    await load_capability_overrides_to_cache()
    await load_pricing_overrides_from_db()

    # 种子数据: AI 服务提供商
    await seed_providers()

    # 种子数据: 内置角色
    from backend.api.roles import seed_roles
    await seed_roles()

    # 种子数据: 内置技能定义
    from backend.api.skills import seed_skills
    await seed_skills()

    # 种子数据: 内置工具定义
    from backend.api.tools import seed_tools
    await seed_tools()

    # 加载工具定义到内存缓存 (必须在 seed_tools 之后)
    from backend.services.tool_registry import load_tools_from_db
    await load_tools_from_db()

    # 种子数据: 工作流模块 + 工作流
    from backend.api.workflows import seed_workflow_modules, seed_workflows, load_workflows_to_cache
    await seed_workflow_modules()
    await seed_workflows()
    await load_workflows_to_cache()

    # 一次性迁移: 为 role_id=NULL 的旧项目设置默认角色
    await _migrate_null_role_projects()

    # 一次性迁移: 为旧项目的 tool_permissions 添加 ask_user
    await _migrate_ask_user_permission()

    # 恢复残留的 AI 任务 (服务重启时标记 running→failed)
    from backend.services.task_runner import TaskManager
    await TaskManager.recover_stale_tasks()

    # 同步活跃工作目录: DB 中的活跃目录 → settings.workspace_path
    await _sync_active_workspace()

    # MCP 框架初始化
    await seed_mcp_servers()
    from backend.services.mcp.registry import MCPServerRegistry
    await MCPServerRegistry.get_instance().load_from_db()

    # 加载 DB 持久化的系统配置到 settings
    await _load_studio_config()

    yield

    # ── 关闭所有活跃的硬件 SSE 流 (防止 shutdown 阻塞热更新) ──
    try:
        from backend.api.voice import shutdown_all_streams as voice_shutdown
        voice_shutdown()
    except Exception:
        pass
    try:
        from backend.api.camera import shutdown_all_streams as camera_shutdown
        camera_shutdown()
    except Exception:
        pass

    # 关闭 MCP 连接
    from backend.services.mcp.client_manager import MCPClientManager
    await MCPClientManager.get_instance().disconnect_all()

    logger.info("🐕 Dogi 关闭")


async def _auto_migrate():
    """轻量级自动迁移: 检查并添加缺失的列"""
    import aiosqlite
    from backend.core.config import settings
    db_path = settings.data_path + "/studio.db"
    try:
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA table_info(messages)")
            existing = {row[1] for row in await cursor.fetchall()}

            migrations = {
                "thinking_content": "ALTER TABLE messages ADD COLUMN thinking_content TEXT",
                "tool_calls": "ALTER TABLE messages ADD COLUMN tool_calls JSON",
                "parent_message_id": "ALTER TABLE messages ADD COLUMN parent_message_id INTEGER",
            }
            for col, sql in migrations.items():
                if col not in existing:
                    await db.execute(sql)
                    logger.info(f"✅ 自动迁移: 添加 messages.{col}")

            # projects 表迁移
            cursor2 = await db.execute("PRAGMA table_info(projects)")
            proj_cols = {row[1] for row in await cursor2.fetchall()}
            proj_migrations = {
                "ai_muted": "ALTER TABLE projects ADD COLUMN ai_muted BOOLEAN DEFAULT 0",
                "tool_permissions": "ALTER TABLE projects ADD COLUMN tool_permissions JSON DEFAULT '[\"read_source\", \"read_config\", \"search\", \"tree\"]'",
                "is_archived": "ALTER TABLE projects ADD COLUMN is_archived BOOLEAN DEFAULT 0",
                "archived_at": "ALTER TABLE projects ADD COLUMN archived_at DATETIME",
            }
            for col, sql in proj_migrations.items():
                if col not in proj_cols:
                    await db.execute(sql)
                    logger.info(f"✅ 自动迁移: 添加 projects.{col}")

            # projects.role_id 迁移
            proj_role_migrations = {
                "role_id": "ALTER TABLE projects ADD COLUMN role_id INTEGER REFERENCES roles(id)",
            }
            for col, sql in proj_role_migrations.items():
                if col not in proj_cols:
                    await db.execute(sql)
                    logger.info(f"✅ 自动迁移: 添加 projects.{col}")

            # projects: project_type + review 列迁移
            proj_type_migrations = {
                "project_type": "ALTER TABLE projects ADD COLUMN project_type VARCHAR(50) DEFAULT 'requirement'",
                "review_content": "ALTER TABLE projects ADD COLUMN review_content TEXT DEFAULT ''",
                "review_version": "ALTER TABLE projects ADD COLUMN review_version INTEGER DEFAULT 0",
                "workspace_dir": "ALTER TABLE projects ADD COLUMN workspace_dir VARCHAR(500)",
                "iteration_count": "ALTER TABLE projects ADD COLUMN iteration_count INTEGER DEFAULT 0",
            }
            for col, sql in proj_type_migrations.items():
                if col not in proj_cols:
                    await db.execute(sql)
                    logger.info(f"✅ 自动迁移: 添加 projects.{col}")

            # ai_providers 表迁移 (新表通过 init_db 创建, 此处处理列变动)
            try:
                cursor3 = await db.execute("PRAGMA table_info(ai_providers)")
                prov_cols = {row[1] for row in await cursor3.fetchall()}
                prov_migrations = {}
                for col, sql in prov_migrations.items():
                    if col not in prov_cols:
                        await db.execute(sql)
                        logger.info(f"✅ 自动迁移: 添加 ai_providers.{col}")
            except Exception:
                pass  # 表尚未创建, 跳过

            # model_capability_overrides 表迁移: 添加定价列
            try:
                cursor4 = await db.execute("PRAGMA table_info(model_capability_overrides)")
                cap_cols = {row[1] for row in await cursor4.fetchall()}
                cap_migrations = {
                    "premium_paid": "ALTER TABLE model_capability_overrides ADD COLUMN premium_paid FLOAT",
                    "premium_free": "ALTER TABLE model_capability_overrides ADD COLUMN premium_free FLOAT",
                }
                for col, sql in cap_migrations.items():
                    if col not in cap_cols:
                        await db.execute(sql)
                        logger.info(f"✅ 自动迁移: 添加 model_capability_overrides.{col}")
            except Exception:
                pass

            # ai_tasks 表迁移 (CREATE TABLE IF NOT EXISTS)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ai_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES projects(id),
                    task_type VARCHAR(50) NOT NULL DEFAULT 'discuss',
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    model VARCHAR(100) DEFAULT '',
                    sender_name VARCHAR(100) DEFAULT '',
                    input_message TEXT DEFAULT '',
                    input_attachments JSON DEFAULT '[]',
                    max_tool_rounds INTEGER DEFAULT 15,
                    regenerate BOOLEAN DEFAULT 0,
                    output_content TEXT DEFAULT '',
                    thinking_content TEXT DEFAULT '',
                    tool_calls_data JSON DEFAULT '[]',
                    token_usage JSON,
                    error_message TEXT DEFAULT '',
                    result_message_id INTEGER,
                    created_at DATETIME,
                    updated_at DATETIME,
                    completed_at DATETIME
                )
            """)
            logger.info("✅ ai_tasks 表就绪")

            # roles 表迁移: 添加 default_skills 列
            try:
                cursor_roles = await db.execute("PRAGMA table_info(roles)")
                role_cols = {row[1] for row in await cursor_roles.fetchall()}
                if "default_skills" not in role_cols:
                    await db.execute("ALTER TABLE roles ADD COLUMN default_skills JSON DEFAULT '[]'")
                    logger.info("✅ 自动迁移: 添加 roles.default_skills")
            except Exception:
                pass

            # skills 表: 如旧表结构不兼容 (如存在 role_prompt 列), 先 drop 再重建
            try:
                cursor_sk_check = await db.execute("PRAGMA table_info(skills)")
                sk_check_cols = {row[1] for row in await cursor_sk_check.fetchall()}
                if sk_check_cols and "role_prompt" in sk_check_cols:
                    # 旧表结构不兼容, 需要重建
                    await db.execute("DROP TABLE IF EXISTS skills")
                    logger.info("✅ 删除旧 skills 表 (结构不兼容)")
            except Exception:
                pass

            await db.execute("""
                CREATE TABLE IF NOT EXISTS skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(100) NOT NULL UNIQUE,
                    icon VARCHAR(10) DEFAULT '⚡',
                    description TEXT DEFAULT '',
                    category VARCHAR(50) DEFAULT 'general',
                    is_builtin BOOLEAN DEFAULT 0,
                    is_enabled BOOLEAN DEFAULT 1,
                    instruction_prompt TEXT NOT NULL DEFAULT '',
                    output_format TEXT DEFAULT '',
                    examples JSON DEFAULT '[]',
                    constraints JSON DEFAULT '[]',
                    recommended_tools JSON DEFAULT '[]',
                    tags JSON DEFAULT '[]',
                    sort_order INTEGER DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)
            # skills 表列迁移 (旧表可能缺失新列)
            try:
                cursor_sk = await db.execute("PRAGMA table_info(skills)")
                sk_cols = {row[1] for row in await cursor_sk.fetchall()}
                skill_col_migrations = {
                    "category": "ALTER TABLE skills ADD COLUMN category VARCHAR(50) DEFAULT 'general'",
                    "instruction_prompt": "ALTER TABLE skills ADD COLUMN instruction_prompt TEXT NOT NULL DEFAULT ''",
                    "output_format": "ALTER TABLE skills ADD COLUMN output_format TEXT DEFAULT ''",
                    "examples": "ALTER TABLE skills ADD COLUMN examples JSON DEFAULT '[]'",
                    "constraints": "ALTER TABLE skills ADD COLUMN constraints JSON DEFAULT '[]'",
                    "recommended_tools": "ALTER TABLE skills ADD COLUMN recommended_tools JSON DEFAULT '[]'",
                    "tags": "ALTER TABLE skills ADD COLUMN tags JSON DEFAULT '[]'",
                }
                for col, sql in skill_col_migrations.items():
                    if col not in sk_cols:
                        await db.execute(sql)
                        logger.info(f"✅ 自动迁移: 添加 skills.{col}")
            except Exception:
                pass
            logger.info("✅ skills 表就绪")

            # workspace_dirs 表 (工作目录管理)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS workspace_dirs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path VARCHAR(500) NOT NULL UNIQUE,
                    label VARCHAR(100) DEFAULT '',
                    is_active BOOLEAN DEFAULT 0 NOT NULL,
                    git_provider VARCHAR(20) DEFAULT 'github',
                    github_token VARCHAR(500) DEFAULT '',
                    github_repo VARCHAR(255) DEFAULT '',
                    gitlab_url VARCHAR(255) DEFAULT 'https://gitlab.com',
                    gitlab_token VARCHAR(500) DEFAULT '',
                    gitlab_repo VARCHAR(255) DEFAULT '',
                    svn_repo_url VARCHAR(500) DEFAULT '',
                    svn_username VARCHAR(255) DEFAULT '',
                    svn_password VARCHAR(500) DEFAULT '',
                    svn_trunk_path VARCHAR(255) DEFAULT 'trunk',
                    created_at DATETIME
                )
            """)
            # workspace_dirs 列迁移
            try:
                cursor_ws = await db.execute("PRAGMA table_info(workspace_dirs)")
                ws_cols = {row[1] for row in await cursor_ws.fetchall()}
                ws_col_migrations = {
                    "git_provider": "ALTER TABLE workspace_dirs ADD COLUMN git_provider VARCHAR(20) DEFAULT 'github'",
                    "github_token": "ALTER TABLE workspace_dirs ADD COLUMN github_token VARCHAR(500) DEFAULT ''",
                    "github_repo": "ALTER TABLE workspace_dirs ADD COLUMN github_repo VARCHAR(255) DEFAULT ''",
                    "gitlab_url": "ALTER TABLE workspace_dirs ADD COLUMN gitlab_url VARCHAR(255) DEFAULT 'https://gitlab.com'",
                    "gitlab_token": "ALTER TABLE workspace_dirs ADD COLUMN gitlab_token VARCHAR(500) DEFAULT ''",
                    "gitlab_repo": "ALTER TABLE workspace_dirs ADD COLUMN gitlab_repo VARCHAR(255) DEFAULT ''",
                    "svn_repo_url": "ALTER TABLE workspace_dirs ADD COLUMN svn_repo_url VARCHAR(500) DEFAULT ''",
                    "svn_username": "ALTER TABLE workspace_dirs ADD COLUMN svn_username VARCHAR(255) DEFAULT ''",
                    "svn_password": "ALTER TABLE workspace_dirs ADD COLUMN svn_password VARCHAR(500) DEFAULT ''",
                    "svn_trunk_path": "ALTER TABLE workspace_dirs ADD COLUMN svn_trunk_path VARCHAR(255) DEFAULT 'trunk'",
                }
                for col, sql in ws_col_migrations.items():
                    if col not in ws_cols:
                        await db.execute(sql)
                        logger.info(f"✅ 自动迁移: 添加 workspace_dirs.{col}")
            except Exception:
                pass
            logger.info("✅ workspace_dirs 表就绪")

            # ── MCP 相关表 ──────────────────────────────────────
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS mcp_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug VARCHAR(50) NOT NULL UNIQUE,
                    name VARCHAR(100) NOT NULL,
                    description TEXT DEFAULT '',
                    icon VARCHAR(10) DEFAULT '🔌',
                    transport VARCHAR(20) NOT NULL DEFAULT 'stdio',
                    command VARCHAR(500) DEFAULT '',
                    args JSON DEFAULT '[]',
                    env_template JSON DEFAULT '{}',
                    url VARCHAR(500) DEFAULT '',
                    permission_map JSON DEFAULT '{}',
                    enabled BOOLEAN DEFAULT 1,
                    is_builtin BOOLEAN DEFAULT 0,
                    sort_order INTEGER DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """))
            logger.info("✅ mcp_servers 表就绪")

            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS mcp_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_slug VARCHAR(50) NOT NULL,
                    tool_name VARCHAR(100) NOT NULL,
                    arguments JSON DEFAULT '{}',
                    result_preview TEXT DEFAULT '',
                    duration_ms INTEGER DEFAULT 0,
                    success BOOLEAN DEFAULT 1,
                    error_message TEXT DEFAULT '',
                    project_id INTEGER REFERENCES projects(id),
                    created_at DATETIME
                )
            """))
            # 为审计日志创建索引
            await db.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_mcp_audit_log_server_slug
                ON mcp_audit_log(server_slug)
            """))
            await db.execute(text("""
                CREATE INDEX IF NOT EXISTS ix_mcp_audit_log_created_at
                ON mcp_audit_log(created_at)
            """))
            logger.info("✅ mcp_audit_log 表就绪")

            # studio_config 键值配置表
            await db.execute(text("""
                CREATE TABLE IF NOT EXISTS studio_config (
                    key VARCHAR(100) PRIMARY KEY,
                    value TEXT DEFAULT '',
                    updated_at DATETIME
                )
            """))
            logger.info("✅ studio_config 表就绪")

            # ── conversations 表 (Dogi 独立对话) ──
            await db.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title VARCHAR(200) DEFAULT '新对话',
                    model VARCHAR(100) DEFAULT 'gpt-4o',
                    tool_permissions JSON DEFAULT '["ask_user","read_source","read_config","search","tree","execute_readonly_command"]',
                    role_id INTEGER REFERENCES roles(id),
                    memory_summary TEXT,
                    is_pinned BOOLEAN DEFAULT 0,
                    is_archived BOOLEAN DEFAULT 0,
                    created_by VARCHAR(100) DEFAULT 'user',
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """)
            logger.info("✅ conversations 表就绪")

            # messages 表: 添加 conversation_id 列
            try:
                cursor_msg = await db.execute("PRAGMA table_info(messages)")
                msg_cols = {row[1] for row in await cursor_msg.fetchall()}
                if "conversation_id" not in msg_cols:
                    await db.execute("ALTER TABLE messages ADD COLUMN conversation_id INTEGER REFERENCES conversations(id)")
                    logger.info("✅ 自动迁移: 添加 messages.conversation_id")
            except Exception:
                pass

            # ai_tasks 表: 添加 conversation_id 列
            try:
                cursor_at = await db.execute("PRAGMA table_info(ai_tasks)")
                at_cols = {row[1] for row in await cursor_at.fetchall()}
                if "conversation_id" not in at_cols:
                    await db.execute("ALTER TABLE ai_tasks ADD COLUMN conversation_id INTEGER REFERENCES conversations(id)")
                    logger.info("✅ 自动迁移: 添加 ai_tasks.conversation_id")
            except Exception:
                pass

            await db.commit()
    except Exception as e:
        logger.warning(f"⚠️ 自动迁移跳过: {e}")


async def _migrate_null_role_projects():
    """一次性迁移: 为旧项目设置 project_type + 设置缺少 role_id 的默认值"""
    from backend.core.database import async_session_maker
    from sqlalchemy import text
    try:
        async with async_session_maker() as db:
            # 1) 为 role_id=NULL 的旧项目设置默认角色
            row = (await db.execute(
                text("SELECT id FROM roles WHERE is_builtin = 1 AND is_enabled = 1 ORDER BY sort_order, id LIMIT 1")
            )).first()
            if row:
                default_id = row[0]
                result = await db.execute(
                    text("UPDATE projects SET role_id = :rid WHERE role_id IS NULL"),
                    {"rid": default_id},
                )
                if result.rowcount > 0:
                    logger.info(f"✅ 迁移 {result.rowcount} 个旧项目 → 默认角色 id={default_id}")

            # 2) 根据已有 role 设置 project_type
            # Bug 问诊 role → bug, 其余 → requirement
            bug_row = (await db.execute(
                text("SELECT id FROM roles WHERE name = 'Bug 问诊' LIMIT 1")
            )).first()
            bug_role_id = bug_row[0] if bug_row else -1
            result2 = await db.execute(
                text("UPDATE projects SET project_type = 'bug' WHERE role_id = :rid AND (project_type IS NULL OR project_type = 'requirement')"),
                {"rid": bug_role_id},
            )
            if result2.rowcount > 0:
                logger.info(f"✅ 迁移 {result2.rowcount} 个旧项目 → project_type=bug")

            # 确保所有项目都有 project_type
            await db.execute(
                text("UPDATE projects SET project_type = 'requirement' WHERE project_type IS NULL OR project_type = ''")
            )

            await db.commit()
    except Exception as e:
        logger.warning(f"⚠️ 旧项目迁移跳过: {e}")


app = FastAPI(
    title="Dogi (多吉)",
    description="AI 驱动的通用对话与项目协作平台",
    version="2.0.0",
    docs_url="/studio-api/docs",
    redoc_url="/studio-api/redoc",
    openapi_url="/studio-api/openapi.json",
    lifespan=lifespan,
)


async def _migrate_ask_user_permission():
    """一次性迁移: 为旧项目的 tool_permissions 添加 ask_user（默认开启）"""
    from backend.core.database import async_session_maker
    from sqlalchemy import text
    import json
    try:
        async with async_session_maker() as db:
            rows = (await db.execute(
                text("SELECT id, tool_permissions FROM projects")
            )).fetchall()
            count = 0
            for row in rows:
                pid, raw = row
                perms = json.loads(raw) if isinstance(raw, str) else (raw or [])
                if "ask_user" not in perms:
                    perms.insert(0, "ask_user")
                    await db.execute(
                        text("UPDATE projects SET tool_permissions = :val WHERE id = :pid"),
                        {"val": json.dumps(perms), "pid": pid},
                    )
                    count += 1
            if count > 0:
                await db.commit()
                logger.info(f"✅ 迁移 {count} 个旧项目: tool_permissions 添加 ask_user")
            else:
                await db.commit()
    except Exception as e:
        logger.warning(f"⚠️ ask_user 权限迁移跳过: {e}")


async def _sync_active_workspace():
    """启动时同步活跃工作目录到 settings（含该目录 GitHub 配置）。

    默认: DB 活跃目录优先。
    若设置 WORKSPACE_PATH_FORCE=true/1/on，则强制使用环境变量 WORKSPACE_PATH 并同步到 DB。
    """
    from backend.core.database import async_session_maker
    from sqlalchemy import text
    try:
        async with async_session_maker() as db:
            # 可选: 强制以环境变量作为活跃工作目录
            force_env = os.environ.get("WORKSPACE_PATH_FORCE", "").strip().lower() in {"1", "true", "yes", "on"}
            env_ws = settings.workspace_path
            if force_env and env_ws and env_ws != "/workspace":
                exists = (await db.execute(
                    text("SELECT id FROM workspace_dirs WHERE path = :path LIMIT 1"),
                    {"path": env_ws},
                )).first()

                # 先取消所有活跃
                await db.execute(text("UPDATE workspace_dirs SET is_active = 0"))

                if exists:
                    await db.execute(
                        text("UPDATE workspace_dirs SET is_active = 1 WHERE id = :id"),
                        {"id": exists[0]},
                    )
                else:
                    await db.execute(
                        text(
                            """
                            INSERT INTO workspace_dirs(path, label, is_active, git_provider, github_token, github_repo, gitlab_url, gitlab_token, gitlab_repo, svn_repo_url, svn_username, svn_password, svn_trunk_path, created_at)
                            VALUES(:path, :label, 1, :provider, :token, :repo, :gitlab_url, :gitlab_token, :gitlab_repo, :svn_repo_url, :svn_username, :svn_password, :svn_trunk_path, CURRENT_TIMESTAMP)
                            """
                        ),
                        {
                            "path": env_ws,
                            "label": os.path.basename(os.path.normpath(env_ws)) or env_ws,
                            "provider": settings.git_provider or "github",
                            "token": settings.github_token or "",
                            "repo": settings.github_repo or "",
                            "gitlab_url": settings.gitlab_url or "https://gitlab.com",
                            "gitlab_token": settings.gitlab_token or "",
                            "gitlab_repo": settings.gitlab_repo or "",
                            "svn_repo_url": settings.svn_repo_url or "",
                            "svn_username": settings.svn_username or "",
                            "svn_password": settings.svn_password or "",
                            "svn_trunk_path": settings.svn_trunk_path or "trunk",
                        },
                    )

                await db.commit()
                settings.workspace_path = env_ws
                logger.info(f"📂 活跃工作目录 (ENV 强制): {env_ws}")
                return

            # 检查 workspace_dirs 表是否存在
            row = (await db.execute(
                text("SELECT path, git_provider, github_token, github_repo, gitlab_url, gitlab_token, gitlab_repo, svn_repo_url, svn_username, svn_password, svn_trunk_path FROM workspace_dirs WHERE is_active = 1 LIMIT 1")
            )).first()
            if row:
                settings.workspace_path = row[0]
                settings.git_provider = row[1] or "github"
                settings.github_token = row[2] or ""
                settings.github_repo = row[3] or ""
                settings.gitlab_url = row[4] or "https://gitlab.com"
                settings.gitlab_token = row[5] or ""
                settings.gitlab_repo = row[6] or ""
                settings.svn_repo_url = row[7] or ""
                settings.svn_username = row[8] or ""
                settings.svn_password = row[9] or ""
                settings.svn_trunk_path = row[10] or "trunk"
                logger.info(f"📂 活跃工作目录 (DB): {row[0]}")
            else:
                logger.info(f"📂 活跃工作目录 (ENV): {settings.workspace_path}")
    except Exception as e:
        # 表可能不存在 (首次启动), 忽略
        logger.debug(f"工作目录同步跳过: {e}")


async def _load_studio_config():
    """启动时从 studio_config 表加载持久化配置，覆盖 settings 对应字段。

    DB 配置优先于 .env，这样用户在界面上保存的值重启后仍有效。
    """
    from backend.core.database import async_session_maker
    from sqlalchemy import text

    # 可覆盖的 settings 字段白名单
    ALLOWED_KEYS = {"github_token", "github_repo"}

    try:
        async with async_session_maker() as db:
            rows = (await db.execute(
                text("SELECT key, value FROM studio_config WHERE key IN ('github_token', 'github_repo')")
            )).all()
            for key, value in rows:
                if key in ALLOWED_KEYS and value:
                    setattr(settings, key, value)
                    logger.info(f"🔧 studio_config: {key} 已从 DB 加载")
    except Exception as e:
        logger.debug(f"studio_config 加载跳过: {e}")


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(projects_router)
app.include_router(discussion_router)
app.include_router(implementation_router)
app.include_router(deployment_router)
app.include_router(snapshots_router)
app.include_router(system_router)
app.include_router(models_router)
app.include_router(model_config_router)
app.include_router(copilot_auth_router)
app.include_router(studio_auth_router)
app.include_router(endpoint_probe_router)
app.include_router(provider_router)
app.include_router(roles_router)
app.include_router(skills_router)
app.include_router(tools_router)
app.include_router(workflow_module_router)
app.include_router(workflow_router)
app.include_router(tasks_project_router)
app.include_router(tasks_router)
app.include_router(ws_router)
app.include_router(users_router)
app.include_router(command_auth_router)
app.include_router(workspace_dirs_router)
app.include_router(mcp_router)
app.include_router(observability_router)
app.include_router(conversations_router)
app.include_router(voice_router)
app.include_router(camera_router)
app.include_router(stt_router)
app.include_router(antigravity_auth_router)


@app.get("/studio-api/health")
async def health_check():
    """Dogi 健康检查"""
    return {"status": "ok", "service": "dogi"}
