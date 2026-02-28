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

check_device_permissions() {
  # 检查音频/视频设备权限 (可选, 仅影响设备调试功能)
  local missing_groups=""
  if [ -e /dev/snd ] && ! id -nG | grep -qw audio; then
    missing_groups="audio"
  fi
  if [ -e /dev/video0 ] && ! id -nG | grep -qw video; then
    missing_groups="${missing_groups:+$missing_groups }video"
  fi
  if [ -n "$missing_groups" ]; then
    echo -e "${YELLOW}[WARN]${NC} 当前用户不在 ${missing_groups} 组, 设备调试功能可能受限"
    echo -e "       运行: ${GREEN}sudo usermod -aG ${missing_groups// /,} \$(whoami)${NC} 后重新登录"
  fi

  # 检查可选系统库 (优先使用 dpkg-query 判断是否已安装)
  has_portaudio=false
  if command -v dpkg-query >/dev/null 2>&1; then
    if dpkg-query -W -f='${Status}' libportaudio2 2>/dev/null | grep -q "install ok installed"; then
      has_portaudio=true
    fi
  fi
  if [ "$has_portaudio" = false ]; then
    if ldconfig -p 2>/dev/null | grep -q "libportaudio"; then
      has_portaudio=true
    fi
  fi
  if [ "$has_portaudio" = false ]; then
    echo -e "${YELLOW}[WARN]${NC} 未检测到 libportaudio2, 服务端音频采集不可用"
    echo -e "       安装: ${GREEN}sudo apt install libportaudio2 libasound2-dev${NC}"
  fi
}

check_frontend_deps() {
  echo -e "${BLUE}[Deps]${NC} 检查前端依赖..."
  if [ ! -d "$PROJECT_ROOT/frontend/node_modules" ]; then
    echo -e "${YELLOW}[INFO]${NC} 安装前端依赖..."
    (cd "$PROJECT_ROOT/frontend" && npm install)
  fi
}

# 启动后端前清理占用端口的旧进程
kill_port() {
  local port="$1"
  local pids pid pgid
  if ! command -v lsof >/dev/null 2>&1; then
    pids=$(ss -ltnp 2>/dev/null | awk -v P=":$port" '$4 ~ P { match($0,/pid=[0-9]+/); if (RSTART) { pid=substr($0,RSTART+4,RLENGTH-4); print pid }}' || true)
  else
    pids=$(lsof -ti :"$port" 2>/dev/null || true)
  fi

  if [ -n "$pids" ]; then
    echo -e "${YELLOW}[INFO]${NC} 端口 $port 被占用, 正在清理旧进程..."
    for pid in $pids; do
      pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)
      if [ -n "$pgid" ]; then
        [ -n "${SHOW_KILL_DETAILS:-}" ] && echo -e "  -> 发送 TERM 到进程组 -$pgid"
        kill -TERM -"$pgid" 2>/dev/null || true
        sleep 2
        if ps -p "$pid" >/dev/null 2>&1; then
          [ -n "${SHOW_KILL_DETAILS:-}" ] && echo -e "  -> 进程仍然存在，发送 KILL 到进程组 -$pgid"
          kill -KILL -"$pgid" 2>/dev/null || true
        fi
      else
        [ -n "${SHOW_KILL_DETAILS:-}" ] && echo -e "  -> 发送 TERM 到 PID $pid"
        kill -TERM "$pid" 2>/dev/null || true
        sleep 1
        if ps -p "$pid" >/dev/null 2>&1; then
          [ -n "${SHOW_KILL_DETAILS:-}" ] && echo -e "  -> PID $pid 未退出，发送 KILL"
          kill -KILL "$pid" 2>/dev/null || true
        fi
      fi
    done
    sleep 1
  fi
}

build_frontend() {
  echo -e "${BLUE}[Build]${NC} 构建前端..."
  (cd "$PROJECT_ROOT/frontend" && npm run build)
}

start_backend() {
  cd "$PROJECT_ROOT"
  kill_port "$DEPLOY_BACKEND_PORT"
  python3 -m uvicorn studio.backend.main:app --host "$DEPLOY_BACKEND_HOST" --port "$DEPLOY_BACKEND_PORT"
}

start_frontend() {
  cd "$PROJECT_ROOT/frontend"
  npm run preview -- --host "$DEPLOY_FRONTEND_HOST" --port "$DEPLOY_FRONTEND_PORT"
}

start_all_foreground() {
  cleanup() {
    # 防止重复打印（多次 SIGINT/SIGTERM 触发时）
    if [ "${_DEPLOY_CLEANING:-0}" = "1" ]; then
      return
    fi
    _DEPLOY_CLEANING=1
    echo ""
    echo -e "${YELLOW}⏹ 正在停止本地部署进程...${NC}"
    kill -- -$$ 2>/dev/null || true
    echo -e "${GREEN}✅ 已停止${NC}"
    exit 0
  }
  trap cleanup SIGINT SIGTERM

  kill_port "$DEPLOY_BACKEND_PORT"
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

  # 清理端口并构造后端启动命令
  kill_port "$DEPLOY_BACKEND_PORT"
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
      check_device_permissions
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
      check_device_permissions
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
