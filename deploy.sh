#!/usr/bin/env bash

set -euo pipefail

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
TARGET=""
USE_TMUX=0

show_help() {
  cat <<'EOF'
AI-Studio 本地部署脚本 (Linux/macOS)

用法:
  ./deploy.sh <backend|frontend|all> [--tmux]

参数:
  backend   部署并启动后端 (uvicorn, 非 reload)
  frontend  构建并启动前端预览 (vite preview)
  all       同时部署前端和后端
  --tmux    (仅 Linux) 在 tmux 会话中后台运行

依赖:
  1) 项目根目录存在 .env (可由 .env.example 复制)
  2) 需在 .env 中配置部署相关变量 (有默认值)

示例:
  ./deploy.sh backend
  ./deploy.sh frontend
  ./deploy.sh all
  ./deploy.sh all --tmux
EOF
}

parse_args() {
  if [ "$#" -eq 0 ]; then
    show_help
    exit 0
  fi

  for arg in "$@"; do
    case "$arg" in
      backend|frontend|all)
        if [ -n "$TARGET" ]; then
          echo "[ERROR] 只能指定一个部署目标: backend/frontend/all"
          exit 1
        fi
        TARGET="$arg"
        ;;
      --tmux)
        USE_TMUX=1
        ;;
      -h|--help|help)
        show_help
        exit 0
        ;;
      *)
        echo "[ERROR] 未知参数: $arg"
        show_help
        exit 1
        ;;
    esac
  done

  if [ -z "$TARGET" ]; then
    echo "[ERROR] 请指定部署目标: backend/frontend/all"
    show_help
    exit 1
  fi
}

load_env() {
  local env_file="$PROJECT_ROOT/.env"
  if [ ! -f "$env_file" ]; then
    echo "[ERROR] 未找到 .env 文件: $env_file"
    echo "       请先执行: cp .env.example .env"
    exit 1
  fi

  set -a
  . "$env_file"
  set +a
}

setup_env() {
  export PYTHONPATH="$PROJECT_ROOT"
  export STUDIO_DATA_PATH="${STUDIO_DATA_PATH:-$PROJECT_ROOT/dev-data}"
  export WORKSPACE_PATH="${WORKSPACE_PATH:-$PROJECT_ROOT}"
  export STUDIO_ADMIN_USER="${STUDIO_ADMIN_USER:-admin}"
  export STUDIO_ADMIN_PASS="${STUDIO_ADMIN_PASS:-admin123}"
  export STUDIO_SECRET_KEY="${STUDIO_SECRET_KEY:-change-me-in-production}"

  export DEPLOY_BACKEND_HOST="${DEPLOY_BACKEND_HOST:-0.0.0.0}"
  export DEPLOY_BACKEND_PORT="${DEPLOY_BACKEND_PORT:-8002}"
  export DEPLOY_FRONTEND_HOST="${DEPLOY_FRONTEND_HOST:-0.0.0.0}"
  export DEPLOY_FRONTEND_PORT="${DEPLOY_FRONTEND_PORT:-4174}"
  export DEPLOY_TMUX_SESSION="${DEPLOY_TMUX_SESSION:-ai-studio-deploy}"

  mkdir -p "$STUDIO_DATA_PATH/plans" "$STUDIO_DATA_PATH/db-backups" "$STUDIO_DATA_PATH/uploads"
}

check_backend_deps() {
  echo -e "${BLUE}[Deps]${NC} 检查后端依赖..."
  if ! python3 -c "import fastapi, uvicorn" >/dev/null 2>&1; then
    echo -e "${YELLOW}[INFO]${NC} 安装 Python 依赖..."
    pip3 install -r "$PROJECT_ROOT/requirements.txt"
  fi
}

check_frontend_deps() {
  echo -e "${BLUE}[Deps]${NC} 检查前端依赖..."
  if [ ! -d "$PROJECT_ROOT/frontend/node_modules" ]; then
    echo -e "${YELLOW}[INFO]${NC} 安装前端依赖..."
    (cd "$PROJECT_ROOT/frontend" && npm install)
  fi
}

build_frontend() {
  echo -e "${BLUE}[Build]${NC} 构建前端..."
  (cd "$PROJECT_ROOT/frontend" && npm run build)
}

start_backend() {
  cd "$PROJECT_ROOT"
  python3 -m uvicorn studio.backend.main:app \
    --host "$DEPLOY_BACKEND_HOST" \
    --port "$DEPLOY_BACKEND_PORT"
}

start_frontend() {
  cd "$PROJECT_ROOT/frontend"
  npm run preview -- --host "$DEPLOY_FRONTEND_HOST" --port "$DEPLOY_FRONTEND_PORT"
}

start_all_foreground() {
  cleanup() {
    echo ""
    echo -e "${YELLOW}⏹ 正在停止本地部署进程...${NC}"
    kill "$BACKEND_PID" 2>/dev/null || true
    kill "$FRONTEND_PID" 2>/dev/null || true
    echo -e "${GREEN}✅ 已停止${NC}"
    exit 0
  }
  trap cleanup SIGINT SIGTERM

  (cd "$PROJECT_ROOT" && python3 -m uvicorn studio.backend.main:app --host "$DEPLOY_BACKEND_HOST" --port "$DEPLOY_BACKEND_PORT") &
  BACKEND_PID=$!
  sleep 2
  (cd "$PROJECT_ROOT/frontend" && npm run preview -- --host "$DEPLOY_FRONTEND_HOST" --port "$DEPLOY_FRONTEND_PORT") &
  FRONTEND_PID=$!

  echo -e "${GREEN}✅ 本地部署已启动${NC}"
  echo "  前端: http://localhost:${DEPLOY_FRONTEND_PORT}/studio/"
  echo "  后端: http://localhost:${DEPLOY_BACKEND_PORT}/studio-api/docs"
  wait
}

start_with_tmux() {
  if [ "$(uname -s)" != "Linux" ]; then
    echo "[ERROR] --tmux 仅支持 Linux"
    exit 1
  fi
  if ! command -v tmux >/dev/null 2>&1; then
    echo "[ERROR] 未检测到 tmux，请先安装 tmux"
    exit 1
  fi

  if tmux has-session -t "$DEPLOY_TMUX_SESSION" 2>/dev/null; then
    echo "[ERROR] tmux 会话 '$DEPLOY_TMUX_SESSION' 已存在，请先处理后重试"
    echo "  查看: tmux attach -t $DEPLOY_TMUX_SESSION"
    echo "  删除: tmux kill-session -t $DEPLOY_TMUX_SESSION"
    exit 1
  fi

  local backend_cmd="cd '$PROJECT_ROOT' && export PYTHONPATH='$PYTHONPATH' STUDIO_DATA_PATH='$STUDIO_DATA_PATH' WORKSPACE_PATH='$WORKSPACE_PATH' STUDIO_ADMIN_USER='$STUDIO_ADMIN_USER' STUDIO_ADMIN_PASS='$STUDIO_ADMIN_PASS' STUDIO_SECRET_KEY='$STUDIO_SECRET_KEY' DEPLOY_BACKEND_HOST='$DEPLOY_BACKEND_HOST' DEPLOY_BACKEND_PORT='$DEPLOY_BACKEND_PORT' && python3 -m uvicorn studio.backend.main:app --host '$DEPLOY_BACKEND_HOST' --port '$DEPLOY_BACKEND_PORT'"
  local frontend_cmd="cd '$PROJECT_ROOT/frontend' && npm run preview -- --host '$DEPLOY_FRONTEND_HOST' --port '$DEPLOY_FRONTEND_PORT'"

  case "$TARGET" in
    backend)
      tmux new-session -d -s "$DEPLOY_TMUX_SESSION" -n backend "$backend_cmd"
      ;;
    frontend)
      tmux new-session -d -s "$DEPLOY_TMUX_SESSION" -n frontend "$frontend_cmd"
      ;;
    all)
      tmux new-session -d -s "$DEPLOY_TMUX_SESSION" -n deploy "$backend_cmd"
      tmux split-window -h -t "$DEPLOY_TMUX_SESSION":deploy "$frontend_cmd"
      tmux select-layout -t "$DEPLOY_TMUX_SESSION":deploy even-horizontal
      tmux select-pane -t "$DEPLOY_TMUX_SESSION":deploy.0
      ;;
  esac

  echo -e "${GREEN}✅ 本地部署 tmux 会话已创建: $DEPLOY_TMUX_SESSION${NC}"
  echo "  进入会话: tmux attach -t $DEPLOY_TMUX_SESSION"
  echo "  结束会话: tmux kill-session -t $DEPLOY_TMUX_SESSION"
}

main() {
  parse_args "$@"
  load_env
  setup_env

  echo ""
  echo "============================================================"
  echo -e "  ${GREEN}AI-Studio 本地部署${NC}"
  echo "============================================================"
  echo "  目标:         $TARGET"
  echo "  项目目录:     $PROJECT_ROOT"
  echo "  数据目录:     $STUDIO_DATA_PATH"
  echo "  后端监听:     ${DEPLOY_BACKEND_HOST}:${DEPLOY_BACKEND_PORT}"
  echo "  前端监听:     ${DEPLOY_FRONTEND_HOST}:${DEPLOY_FRONTEND_PORT}"
  echo "============================================================"
  echo ""

  case "$TARGET" in
    backend)
      check_backend_deps
      if [ "$USE_TMUX" -eq 1 ]; then
        start_with_tmux
      else
        start_backend
      fi
      ;;
    frontend)
      check_frontend_deps
      build_frontend
      if [ "$USE_TMUX" -eq 1 ]; then
        start_with_tmux
      else
        start_frontend
      fi
      ;;
    all)
      check_backend_deps
      check_frontend_deps
      build_frontend
      if [ "$USE_TMUX" -eq 1 ]; then
        start_with_tmux
      else
        start_all_foreground
      fi
      ;;
  esac
}

main "$@"
