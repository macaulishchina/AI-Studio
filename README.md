# AI-Studio (设计院)

> AI 驱动的通用项目设计与需求迭代平台

AI-Studio 提供了从需求讨论、方案设计、代码实施到部署上线的完整工作流。支持多角色 AI 对话、工具调用沙箱、工作流引擎、GitHub 集成和多人实时协作。

## ✨ 核心功能

- 🤖 **AI 多轮对话** — 支持 GitHub Models / Copilot / 第三方 OpenAI 兼容 API，流式输出 + 思维链展示
- 🛠 **工具调用沙箱** — AI 可读取代码、搜索、执行命令，内置安全沙箱和权限控制
- 🔄 **工作流引擎** — 数据驱动的项目生命周期管理，支持自定义阶段和模块
- 👥 **多角色协作** — 不同阶段自动切换 AI 角色 (需求分析、Bug 问诊、实现审查等)
- 🚀 **GitHub 集成** — 一键创建 Issue → Copilot Agent 实施 → PR 审查 → 部署
- 📸 **快照管理** — 代码 + 数据库 + Docker 镜像一体化快照，支持一键回滚
- 👨‍💻 **多用户系统** — 注册审批、角色权限 (admin/developer/viewer)
- 🌐 **实时协作** — WebSocket + SSE 双通道，多人同时查看 AI 对话进展

## 📋 前置要求

- **Python** 3.11+
- **Node.js** 18+ (含 npm)
- **Git**
- (可选) **Docker** — 用于容器化部署
- (可选) **系统音频/摄像头库** — 用于输入设备调试功能:
  - `libportaudio2` / `libasound2-dev` — PortAudio + ALSA (音频采集)
  - `libgl1` / `libglib2.0-0` — OpenCV 运行时
  - `v4l-utils` — V4L2 摄像头工具

  ```bash
  # Ubuntu/Debian 一键安装
  sudo apt install -y libportaudio2 libasound2-dev libgl1 libglib2.0-0 v4l-utils
  ```

## 🚀 快速开始

### 方式一: 开发模式 (推荐用于开发)

#### Windows

```bash
# 克隆项目
git clone <repo-url> AI-Studio
cd AI-Studio

# 无参数显示帮助
dev.bat

# 按目标启动
dev.bat backend
dev.bat frontend
dev.bat all
```

#### Linux / macOS

```bash
# 克隆项目
git clone <repo-url> AI-Studio
cd AI-Studio

# 添加执行权限
chmod +x dev.sh

# 无参数显示帮助
./dev.sh

# 按目标启动
./dev.sh backend
./dev.sh frontend
./dev.sh all

# Linux 后台运行 (tmux)
./dev.sh all --tmux
# 默认同一 window 左右分屏，同时显示后端和前端日志
```

启动后访问:
- **前端**: http://localhost:5174/studio/
- **后端 API 文档**: http://localhost:8002/studio-api/docs
- **默认管理员**: `admin` / `admin123`

#### 手动启动 (了解细节)

```bash
# 1. 安装 Python 依赖
pip install -r requirements.txt

# 2. 安装前端依赖
cd frontend && npm install && cd ..

# 3. 设置环境变量 (关键: PYTHONPATH)
#    项目使用 studio.backend.xxx 导入路径
#    直接将 PYTHONPATH 指向项目根目录即可

# Windows (CMD):
set PYTHONPATH=.
set STUDIO_DATA_PATH=./dev-data
set STUDIO_ADMIN_PASS=admin123

# Linux/macOS:
export PYTHONPATH=.
export STUDIO_DATA_PATH=./dev-data
export STUDIO_ADMIN_PASS=admin123

# 4. 启动后端
uvicorn studio.backend.main:app --host 0.0.0.0 --port 8002 --reload

# 5. 启动前端 (新终端)
cd AI-Studio/frontend
npm run dev
```

### 方式二: Docker 部署

```bash
# 构建镜像
docker build -t ai-studio .

# 运行 (数据持久化 + 可访问宿主机 Docker)
docker run -d \
  --name ai-studio \
  -p 8002:8002 \
  -v ai-studio-data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --group-add $(stat -c '%g' /var/run/docker.sock) \
  -e STUDIO_ADMIN_PASS=your-secure-password \
  -e GITHUB_TOKEN=ghp_xxxx \
  ai-studio

# 如需容器内使用 USB 摄像头/麦克风 (设备调试功能):
docker run -d \
  --name ai-studio \
  -p 8002:8002 \
  -v ai-studio-data:/data \
  --device /dev/video0 \
  --device /dev/snd \
  --group-add $(stat -c '%g' /dev/snd/timer) \
  --group-add $(stat -c '%g' /dev/video0) \
  -e STUDIO_ADMIN_PASS=your-secure-password \
  ai-studio

# 查看日志 (包含自动生成的管理员密码)
docker logs ai-studio
```

如果不需要在 Studio 中查看/操作宿主机其他容器，可去掉 `docker.sock` 挂载与 `--group-add`。

也可使用 `docker compose`（推荐，已提供 `docker-compose.yml`）:

```bash
# 让 compose 中 group_add 与 docker.sock 用户组一致
export DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)

# 启动
docker compose up -d --build

# 查看日志
docker compose logs -f ai-studio

# 停止
docker compose down
```

> ⚠️ Docker 模式仅启动后端 API。生产环境需要单独构建前端 (`npm run build`) 并通过 Nginx 等反向代理提供静态文件服务。

### 方式三: 本地部署脚本 (非 Docker)

适用于已有 Python/Node 环境、希望直接在本机部署运行。

```bash
# 1) 准备环境变量
cp .env.example .env

# 2) Linux/macOS
chmod +x deploy.sh
./deploy.sh
./deploy.sh backend
./deploy.sh frontend
./deploy.sh all

# Linux 后台 tmux 运行
./deploy.sh all --tmux

# 3) Windows
deploy.bat
deploy.bat backend
deploy.bat frontend
deploy.bat all
```

默认地址:
- 前端: http://localhost:4174/studio/
- 后端: http://localhost:8002/studio-api/docs

可在 `.env` 中调整端口:
- `DEPLOY_BACKEND_HOST` / `DEPLOY_BACKEND_PORT`
- `DEPLOY_FRONTEND_HOST` / `DEPLOY_FRONTEND_PORT`
- `DEPLOY_TMUX_SESSION` (仅 Linux)

## ⚙️ 配置

### 环境变量

复制 `.env.example` 为 `.env` 进行配置:

```bash
cp .env.example .env
```

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `STUDIO_DATA_PATH` | 数据存储路径 (SQLite, 方案, 备份) | `/data` |
| `WORKSPACE_PATH` | 代码工作区路径 | `/workspace` |
| `GITHUB_TOKEN` | GitHub Token (Models API + 仓库操作) | — |
| `GITHUB_REPO` | GitHub 仓库 (owner/repo) | — |
| `STUDIO_ADMIN_USER` | 管理员用户名 | `admin` |
| `STUDIO_ADMIN_PASS` | 管理员密码 (空则自动生成并打印) | 自动生成 |
| `STUDIO_SECRET_KEY` | JWT 签名密钥 (空则自动生成) | 自动生成 |
| `MAIN_API_URL` | 主项目 API (SSO 验证, 留空禁用) | — |

完整变量列表参见 [.env.example](.env.example)。

### AI 服务配置

AI-Studio 支持多种 AI 服务后端:

1. **GitHub Models API** — 需要 `GITHUB_TOKEN`，免费额度
2. **GitHub Copilot API** — 通过 OAuth Device Flow 登录，使用 Copilot 订阅配额
3. **第三方 API** — 在设置页面添加 OpenAI 兼容提供商 (DeepSeek, 通义千问, Ollama 等)

## 📁 项目结构

```
AI-Studio/
├── backend/              # FastAPI 后端
│   ├── main.py           # 应用入口 + 路由注册
│   ├── models.py         # ORM 数据模型
│   ├── core/             # 核心: 配置/数据库/认证
│   ├── api/              # API 路由 (RESTful)
│   └── services/         # 业务逻辑层
├── frontend/             # Vue 3 前端
│   ├── src/
│   │   ├── api/          # API 客户端
│   │   ├── views/        # 页面组件
│   │   ├── components/   # 复用组件
│   │   ├── stores/       # Pinia 状态仓库
│   │   └── composables/  # Vue 组合函数
│   └── vite.config.ts    # Vite 配置 (含 API 代理)
├── dev.bat               # Windows 开发启动脚本 (backend/frontend/all)
├── dev.sh                # Linux/macOS 开发启动脚本 (backend/frontend/all, 支持 --tmux)
├── deploy.bat            # Windows 本地部署脚本 (backend/frontend/all)
├── deploy.sh             # Linux/macOS 本地部署脚本 (backend/frontend/all, 支持 --tmux)
├── Dockerfile            # Docker 构建
├── requirements.txt      # Python 依赖
└── CLAUDE.md             # AI 开发助手指南
```

## 🔧 开发指南

### 后端开发

- 后端端口: **8002**
- API 路由前缀: `/studio-api/`
- 热重载: uvicorn `--reload` 监听 `backend/` 目录变动
- 数据库: 启动时自动创建表和迁移列 (无需 Alembic)
- 新增 API: 在 `backend/api/` 中创建路由 → `main.py` 注册

### 前端开发

- 开发端口: **5174**
- 基础路径: `/studio/`
- API 代理: Vite 将 `/studio-api` 代理到 `http://localhost:8002`
- UI 组件: Naive UI (已全局注册)
- 热更新: Vite HMR 自动生效

### 导入路径说明

后端使用 `studio.backend.xxx` 绝对导入路径:

```python
from studio.backend.core.config import settings
from studio.backend.models import Project
```

项目已内置 `studio/backend` 桥接包，`studio.backend.*` 会映射到项目根下的 `backend/` 目录。
因此只需将 `PYTHONPATH` 设为项目根（或在项目根目录执行启动命令）即可，无需再创建外部链接。

### 数据库

- SQLite 单文件: `{STUDIO_DATA_PATH}/studio.db`
- 开发数据在 `dev-data/` 目录 (已 gitignore)
- 自动迁移: 新增列在 `main.py → _auto_migrate()` 中定义
- 删库重来: 删除 `dev-data/studio.db` 重启即可

## � 输入设备调试

项目内置了输入设备调试页面 (`/studio/device-debug`)，支持：

- **服务端音频**: 设备枚举、ALSA/PipeWire 驱动检测、SSE 实时电平、录音测试
- **服务端摄像头**: V4L2 设备枚举、JPEG 快照、MJPEG 实时视频流
- **浏览器麦克风**: getUserMedia 设备枚举、波形可视化、Web Speech API 语音识别、录音
- **浏览器摄像头**: getUserMedia 预览、拍照

### Linux 权限配置

服务端音频/摄像头功能需要当前用户有 `audio` 和 `video` 组权限：

```bash
# 添加用户到 audio + video 组
sudo usermod -aG audio,video $(whoami)

# 重新登录使生效 (或 newgrp)
newgrp audio
newgrp video

# 验证
id  # 输出应包含 audio 和 video
```

### Docker 中访问设备

```bash
docker run ... \
  --device /dev/video0 \
  --device /dev/snd \
  --group-add $(stat -c '%g' /dev/snd/timer) \
  --group-add $(stat -c '%g' /dev/video0)
```

### Python 依赖

| 包 | 用途 | 系统依赖 |
| --- | --- | --- |
| `sounddevice` | 音频采集 | `libportaudio2` |
| `numpy` | 音频数据处理 | — |
| `opencv-python-headless` | 摄像头采集 | `libgl1`, `libglib2.0-0` |

> 这些包均为可选依赖，缺失时对应 API 会返回 `available: false` 而不会崩溃。

## �📝 常见问题

### Q: `ModuleNotFoundError: No module named 'studio'`

A: PYTHONPATH 未正确设置。使用开发脚本 (`dev.bat` / `dev.sh`) 会自动处理。手动启动时确保:
- PYTHONPATH 指向项目根目录

### Q: 前端 API 请求报 404

A: 确保后端已启动在 8002 端口。Vite 开发服务器会将 `/studio-api` 代理到后端。

### Q: 管理员密码忘了

A: 设置环境变量 `STUDIO_ADMIN_PASS=新密码` 后重启。或删除 `dev-data/studio.db` 重新初始化。

### Q: Docker 模式前端如何访问

A: Docker 只运行后端 API。需要:
1. `cd frontend && npm run build` 构建静态文件
2. 用 Nginx 托管 `frontend/dist/` 并反向代理 `/studio-api` 到后端

## 📄 License

[MIT](LICENSE)
