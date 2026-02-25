# CLAUDE.md — AI-Studio (设计院) 项目指南

## 项目概述

AI-Studio（设计院）是一个 **AI 驱动的通用项目设计与需求迭代平台**。它提供了从需求讨论、方案设计、代码实施到部署上线的完整工作流，支持多角色 AI 对话、工具调用、实时协作等能力。

- **定位**: 独立部署的 AI 辅助开发平台，不绑定任何具体业务项目
- **核心能力**: AI 多轮对话 + 工具调用沙箱 + 工作流引擎 + GitHub 集成 + 实时协作

## 技术栈

| 层          | 技术                                                       |
| ----------- | ---------------------------------------------------------- |
| 后端框架    | FastAPI (Python 3.11+, 全异步)                             |
| 数据库      | SQLite + SQLAlchemy 2.0 async + aiosqlite (WAL 模式)      |
| 前端框架    | Vue 3 + TypeScript + Vite 5                                |
| UI 库       | Naive UI (暗色主题, 中文 locale)                           |
| 状态管理    | Pinia                                                      |
| HTTP 客户端 | axios (前端) / httpx (后端)                                |
| AI API      | 双后端: GitHub Models API + GitHub Copilot API             |
| 实时通信    | SSE (AI 任务事件流) + WebSocket (聊天/协作)                |
| 认证        | JWT (多源: admin / DB 用户 / 主项目 SSO)                   |
| 容器化      | Docker (Python 3.11-slim 基础镜像)                         |
| Token 估算  | tiktoken (cl100k_base, 可选依赖, 有 fallback)             |

## 项目结构

```
AI-Studio/
├── __init__.py                # 根包 (使 studio.backend.xxx 导入生效)
├── Dockerfile                 # 生产部署镜像
├── requirements.txt           # Python 依赖
├── CLAUDE.md                  # 本文件
│
├── backend/                   # FastAPI 后端
│   ├── __init__.py
│   ├── main.py                # 应用入口, 生命周期, 路由注册, 自动迁移
│   ├── models.py              # SQLAlchemy ORM 模型 (575 行, 全部模型定义)
│   │
│   ├── core/                  # 核心基础设施
│   │   ├── config.py          # StudioSettings 配置类 (环境变量驱动)
│   │   ├── database.py        # 异步数据库引擎 + 会话工厂
│   │   ├── security.py        # 认证: JWT 签发/验证, 多源 auth, FastAPI Depends
│   │   ├── token_utils.py     # Token 计数 + 截断工具
│   │   ├── model_capabilities.py  # 模型能力检测 (vision/tools/reasoning)
│   │   └── project_types.py   # 项目类型/工作流定义 (DB 优先 + 硬编码 fallback)
│   │
│   ├── api/                   # API 路由层 (FastAPI Router)
│   │   ├── projects.py        # 项目 CRUD (/projects)
│   │   ├── discussion.py      # AI 讨论 + 流式对话 (/projects/{id}/discuss)
│   │   ├── implementation.py  # GitHub Issue → Copilot Agent 实施
│   │   ├── deployment.py      # 部署流水线 (merge + deploy)
│   │   ├── snapshots.py       # 代码快照 + 系统状态
│   │   ├── models_api.py      # AI 模型列表 + 能力查询
│   │   ├── model_config.py    # 自定义模型 + 能力覆盖配置
│   │   ├── copilot_auth_api.py# Copilot OAuth Device Flow
│   │   ├── studio_auth.py     # Studio 认证 (登录/验证)
│   │   ├── provider_api.py    # AI 服务提供商管理
│   │   ├── provider_presets.py# 预设第三方提供商 (DeepSeek/Qwen 等)
│   │   ├── roles.py           # AI 角色管理 (persona 定义)
│   │   ├── skills.py          # AI 技能管理 (capability 模块)
│   │   ├── tools.py           # AI 工具定义管理
│   │   ├── workflows.py       # 工作流模块 + 工作流定义
│   │   ├── tasks.py           # AI 后台任务管理 (SSE 事件流)
│   │   ├── users.py           # 用户管理 (注册/审批/权限)
│   │   ├── command_auth.py    # 命令授权规则 + 审计日志
│   │   ├── endpoint_probe.py  # 端点探测 (可用性检测)
│   │   └── ws.py              # WebSocket 端点
│   │
│   └── services/              # 业务逻辑层
│       ├── ai_service.py      # AI 核心服务 (876 行, 双 API 后端, SSE 流式)
│       ├── task_runner.py     # 后台任务执行器 (1072 行, 工具调用循环, 事件总线)
│       ├── tool_registry.py   # 工具注册 + 安全沙箱 (1056 行, 文件读写/搜索/命令)
│       ├── context_service.py # 上下文构建器 (自适应压缩, 项目感知)
│       ├── context_manager.py # 上下文窗口管理
│       ├── ws_hub.py          # WebSocket 广播中心 (房间管理)
│       ├── copilot_auth.py    # Copilot OAuth 凭据管理
│       ├── github_service.py  # GitHub API 封装
│       ├── deploy_service.py  # 部署执行服务
│       ├── snapshot_service.py# 快照管理服务
│       └── workspace_service.py# 工作区文件操作
│
└── frontend/                  # Vue 3 前端
    ├── package.json           # npm 依赖
    ├── vite.config.ts         # Vite 配置 (proxy → localhost:8002)
    ├── tsconfig.json
    ├── index.html
    └── src/
        ├── main.ts            # Vue 入口 (Pinia + NaiveUI + Router)
        ├── App.vue            # 根组件 (暗色主题, zhCN locale)
        ├── api/index.ts       # API 客户端 (axios, 全部接口定义)
        ├── router/index.ts    # 路由 (/studio/ 前缀, 登录守卫)
        ├── stores/            # Pinia 状态仓库
        ├── views/             # 页面组件
        │   ├── Dashboard.vue
        │   ├── ProjectList.vue
        │   ├── ProjectDetail.vue  # 核心: 工作流驱动的动态标签页
        │   ├── Settings.vue       # 设置页 (子路由)
        │   └── settings/          # 设置子页面
        ├── components/        # 复用组件
        │   ├── ChatPanel.vue  # AI 对话面板
        │   ├── PlanEditor.vue # 方案编辑器
        │   ├── ImplementPanel.vue
        │   ├── DeployPanel.vue
        │   └── SnapshotPanel.vue
        ├── composables/       # Vue 组合函数
        └── utils/             # 工具函数
```

## 核心架构设计

### 1. 工作流引擎 (Workflow Engine)

项目采用**数据驱动的工作流系统**，定义了从需求到部署的完整生命周期：

```
Project → project_type → Workflow → stages[] + modules[]
                                      ↓           ↓
                                   步骤条       动态标签页
                                      ↓
                                stage → Role (AI 人设)
```

- **Workflow**: 定义项目生命周期阶段和功能模块
- **WorkflowModule**: 可复用的功能面板 (ChatPanel, ImplementPanel 等)
- **Stage → Role 绑定**: 不同阶段自动切换 AI 角色 (如"讨论"阶段用"需求分析"角色)
- 支持 DB 配置 + 硬编码 fallback (project_types.py)

### 2. AI 服务双后端

```
ai_service.py
  ├── GitHub Models API (models.inference.ai.azure.com)
  │     └── 使用 GITHUB_TOKEN 认证
  └── GitHub Copilot API
        └── OAuth Device Flow 认证
        └── 模拟 VS Code 请求头 (copilot-billing, vscode-sessionid)
```

- 模型 ID 以 `copilot:` 前缀区分后端
- 支持第三方 OpenAI 兼容 API (通过 AIProvider 配置)
- 自动检测 reasoning 模型 (o1/o3/o4) 并调整参数

### 3. 后台任务系统

```
TaskRunner (task_runner.py)
  ├── 异步任务执行 (与 HTTP 连接解耦)
  ├── 工具调用循环 (最多 N 轮)
  ├── 事件总线 (ProjectEventBus)
  │     ├── subscribe() → SSE 流 (断线重连 + 事件回放)
  │     └── broadcast() → 多用户实时同步
  └── 持久化到 AiTask 表 (崩溃恢复)
```

### 4. 工具调用沙箱

```
tool_registry.py
  ├── 权限体系: ask_user / read_source / read_config / search / tree /
  │             execute_readonly_command / execute_command
  ├── 安全策略: 路径逃逸防护 / 符号链接检测 / 敏感文件黑名单
  ├── 内置工具: read_file / search_text / list_directory / get_file_tree /
  │            read_config / execute_command / ask_user
  └── 可扩展: ToolDefinition 表 (builtin / command / http 执行器)
```

### 5. 实时通信

- **SSE**: AI 任务事件流 (`/projects/{id}/events`, `/tasks/{id}/stream`)
  - 协议事件: `content_delta`, `thinking_delta`, `tool_call`, `tool_result`, `done`, `error`
- **WebSocket**: 聊天协作 (`/ws/{project_id}`)
  - 协议: `new_message`, `ai_event`, `ai_start`, `ai_done`, `presence`, `typing`

## 关键约定

### Python 导入路径

所有后端代码使用**绝对导入**，以 `studio` 为顶级包：

```python
from studio.backend.core.config import settings
from studio.backend.core.database import async_session_maker
from studio.backend.models import Project, Message
from studio.backend.services.ai_service import stream_chat
```

运行时需确保 `PYTHONPATH` 包含 AI-Studio 的**父目录**（使 `studio` 包可被解析）。
Docker 中通过 `COPY . ./studio/` + `ENV PYTHONPATH=/app` 实现。

### API 路由前缀

- 所有后端 API: `/studio-api/...`
- 前端静态资源: `/studio/...`
- 健康检查: `GET /studio-api/health`
- API 文档: `/studio-api/docs` (Swagger), `/studio-api/redoc`

### 数据库

- SQLite 单文件: `{STUDIO_DATA_PATH}/studio.db`
- WAL 模式 (并发读写优化)
- 自动迁移: `main.py → _auto_migrate()` 启动时检查并添加缺失列
- 无需手动 migration 工具 (Alembic), 全部 ALTER TABLE 在启动时自动执行

### 认证体系

三种认证源 (优先级递减):
1. **Admin**: 环境变量 `STUDIO_ADMIN_USER` / `STUDIO_ADMIN_PASS`
2. **DB 用户**: 注册 → 审批 → 激活 (PBKDF2-SHA256, 角色: admin/developer/viewer)
3. **主项目 SSO**: 代理验证父项目 JWT (通过 `MAIN_API_URL`)

### 前端路由

- Base path: `/studio/`
- 登录页: `/studio/login`
- 仪表盘: `/studio/`
- 项目详情: `/studio/projects/:id`
- 设置页: `/studio/settings`

## 环境变量

| 变量                     | 说明                                   | 默认值                              |
| ------------------------ | -------------------------------------- | ----------------------------------- |
| `STUDIO_DATA_PATH`      | 数据存储目录 (SQLite, 计划, 备份)      | `/data`                             |
| `WORKSPACE_PATH`        | 代码工作区路径                         | `/workspace`                        |
| `GITHUB_TOKEN`          | GitHub API Token (Models API + 仓库)   | 空                                  |
| `GITHUB_REPO`           | GitHub 仓库 (owner/repo 格式)          | 空                                  |
| `GIT_CLONE_URL`         | 通用 Git 仓库克隆 URL (GitLab 等)      | 空                                  |
| `STUDIO_ADMIN_USER`     | 管理员用户名                           | `admin`                             |
| `STUDIO_ADMIN_PASS`     | 管理员密码 (空则自动生成)              | 自动生成                            |
| `STUDIO_SECRET_KEY`     | JWT 签名密钥 (空则自动生成)            | 自动生成                            |
| `STUDIO_TOKEN_EXPIRE_DAYS` | Token 有效天数                      | `7`                                 |
| `MAIN_API_URL`          | 主项目 API (SSO 验证, 留空禁用)        | 空                                  |
| `SSO_TOKEN_KEY`         | 主项目 localStorage JWT key            | `token`                             |
| `DEPLOY_SERVICES`       | 部署目标服务 (逗号分隔)               | `frontend,backend`                  |
| `DEPLOY_GIT_BRANCH`     | Git 主分支名                           | `master`                            |
| `DEPLOY_HEALTH_CHECKS`  | 健康检查端点 (name=url;name=url)       | 空                                  |
| `DOCKER_IMAGE_PREFIX`   | Docker 镜像前缀                        | 空                                  |
| `SNAPSHOT_DB_PATHS`     | 快照数据库路径 (逗号分隔)             | 空                                  |
| `PYTHONPATH`            | Python 模块搜索路径                    | Docker: `/app`                      |

## 开发命令

### 后端 (FastAPI)

```bash
# 开发模式启动 (热重载)
cd <project-parent-dir>
set PYTHONPATH=.
set STUDIO_DATA_PATH=./dev-data
uvicorn studio.backend.main:app --host 0.0.0.0 --port 8002 --reload

# 或使用开发脚本
python dev-start.py
```

### 前端 (Vite)

```bash
cd frontend
npm install
npm run dev        # 开发服务器 (端口 5174, 自动代理到后端 8002)
npm run build      # 生产构建
```

### Docker

```bash
docker build -t ai-studio .
docker run -p 8002:8002 -v studio-data:/data ai-studio
```

## 数据模型关系

```
StudioUser (用户)
Role (AI 角色) ←── Project.role_id
Skill (AI 技能) ←── Role.default_skills[]
ToolDefinition (工具定义)
AIProvider (AI 提供商)

Project (项目)
  ├── Message[] (讨论消息)
  ├── Deployment[] (部署记录)
  ├── AiTask[] (后台 AI 任务)
  └── CommandAuditLog[] (命令审计)

Workflow (工作流) ← Project.project_type
  └── WorkflowModule[] (功能模块, 通过 modules JSON 引用)

Snapshot (快照)
CustomModel (自定义模型配置)
ModelCapabilityOverride (模型能力覆盖)
CommandAuthRule (命令授权规则)
```

## 常见开发场景

### 新增 API 端点

1. 在 `backend/api/` 下新建或修改路由文件
2. 创建 `APIRouter`，使用 `/studio-api` 前缀或 tags
3. 在 `backend/main.py` 中 `app.include_router()` 注册
4. 前端 `frontend/src/api/index.ts` 中添加对应调用

### 新增数据库表/列

1. 在 `backend/models.py` 中添加 ORM 模型/列
2. 在 `backend/main.py → _auto_migrate()` 中添加 ALTER TABLE 语句
3. 重启后端即自动迁移 (无需 Alembic)

### 新增 AI 工具

1. 在 `backend/services/tool_registry.py` 中添加执行函数
2. 或通过 `ToolDefinition` 表配置 (executor_type: builtin/command/http)
3. 注意权限: 在 `TOOL_PERMISSIONS` 中注册权限 key

### 新增工作流模块

1. `WorkflowModule` 表定义模块 (component_key 映射前端组件)
2. `Workflow` 表组装模块到工作流
3. 前端 `ProjectDetail.vue` 的组件映射自动匹配

## 注意事项

- 后端端口: **8002** (Dockerfile CMD 和 Vite proxy 均配置此端口)
- 前端开发端口: **5174** (vite.config.ts)
- 前端 base path: `/studio/` (路由和静态资源)
- SQLite 在高并发写入时有限制，生产环境考虑 WAL 调优已内置
- `_auto_migrate()` 只支持 ADD COLUMN，不支持删列或改类型
- reasoning 模型 (o1/o3/o4) 使用特殊参数 (无 system message, max_completion_tokens)
