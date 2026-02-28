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
AI-Studio 开发脚本 (Linux/macOS)

用法:
  ./dev.sh <backend|frontend|all> [--tmux]

参数:
  backend   启动后端 (FastAPI)
  frontend  启动前端 (Vite)
  all       启动前端 + 后端
    --tmux    (仅 Linux) 在 tmux 会话中后台运行并可随时 attach 查看
                        当目标为 all 时，默认同一 window 左右分屏同时显示前后端

示例:
  ./dev.sh backend
  ./dev.sh frontend
  ./dev.sh all
  ./dev.sh all --tmux
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

load_env_file() {
    local env_file="$PROJECT_ROOT/.env"
    if [ -f "$env_file" ]; then
        set -a
        . "$env_file"
        set +a
    fi
}

setup_env() {
    export PYTHONPATH="$PROJECT_ROOT"
    export STUDIO_DATA_PATH="${STUDIO_DATA_PATH:-$PROJECT_ROOT/dev-data}"
    export WORKSPACE_PATH="${WORKSPACE_PATH:-$PROJECT_ROOT}"
    export STUDIO_ADMIN_USER="${STUDIO_ADMIN_USER:-admin}"
    export STUDIO_ADMIN_PASS="${STUDIO_ADMIN_PASS:-admin123}"
    export STUDIO_SECRET_KEY="${STUDIO_SECRET_KEY:-dev-secret-key-not-for-production}"

    mkdir -p "$STUDIO_DATA_PATH/plans" "$STUDIO_DATA_PATH/db-backups" "$STUDIO_DATA_PATH/uploads"
}

check_python_deps() {
    echo -e "${BLUE}[Deps]${NC} 检查 Python 依赖..."
    if ! python3 -c "import fastapi" >/dev/null 2>&1; then
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

start_backend() {
    cd "$PROJECT_ROOT"
    python3 -m uvicorn studio.backend.main:app \
        --host 0.0.0.0 --port 8002 \
        --reload --reload-dir "$PROJECT_ROOT/backend"
}

start_frontend() {
    cd "$PROJECT_ROOT/frontend"
    npm run dev -- --host 0.0.0.0
}

start_all_foreground() {
    cleanup() {
        echo ""
        echo -e "${YELLOW}⏹ 正在停止服务...${NC}"
        kill "$BACKEND_PID" 2>/dev/null || true
        kill "$FRONTEND_PID" 2>/dev/null || true
        echo -e "${GREEN}✅ 所有服务已停止${NC}"
        exit 0
    }
    trap cleanup SIGINT SIGTERM

    (cd "$PROJECT_ROOT" && python3 -m uvicorn studio.backend.main:app --host 0.0.0.0 --port 8002 --reload --reload-dir "$PROJECT_ROOT/backend") &
    BACKEND_PID=$!
    sleep 2
    (cd "$PROJECT_ROOT/frontend" && npm run dev -- --host 0.0.0.0) &
    FRONTEND_PID=$!

    echo -e "${GREEN}✅ 开发环境已启动${NC}"
    echo "  前端: http://localhost:5174/studio/"
    echo "  后端: http://localhost:8002/studio-api/docs"
    echo "  按 Ctrl+C 停止"
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

    local session="ai-studio-dev"
    if tmux has-session -t "$session" 2>/dev/null; then
        echo "[ERROR] tmux 会话 '$session' 已存在，请先处理后重试"
        echo "  查看: tmux attach -t $session"
        echo "  删除: tmux kill-session -t $session"
        exit 1
    fi

    local backend_cmd="cd '$PROJECT_ROOT' && export PYTHONPATH='$PYTHONPATH' STUDIO_DATA_PATH='$STUDIO_DATA_PATH' WORKSPACE_PATH='$WORKSPACE_PATH' STUDIO_ADMIN_USER='$STUDIO_ADMIN_USER' STUDIO_ADMIN_PASS='$STUDIO_ADMIN_PASS' STUDIO_SECRET_KEY='$STUDIO_SECRET_KEY' && python3 -m uvicorn studio.backend.main:app --host 0.0.0.0 --port 8002 --reload --reload-dir '$PROJECT_ROOT/backend'"
    local frontend_cmd="cd '$PROJECT_ROOT/frontend' && npm run dev -- --host 0.0.0.0"

    case "$TARGET" in
        backend)
            tmux new-session -d -s "$session" -n backend "$backend_cmd"
            ;;
        frontend)
            tmux new-session -d -s "$session" -n frontend "$frontend_cmd"
            ;;
        all)
            tmux new-session -d -s "$session" -n dev "$backend_cmd"
            tmux split-window -h -t "$session":dev "$frontend_cmd"
            tmux select-layout -t "$session":dev even-horizontal
            tmux select-pane -t "$session":dev.0
            ;;
    esac

    echo -e "${GREEN}✅ tmux 后台会话已创建: $session${NC}"
    echo "  进入会话: tmux attach -t $session"
    echo "  查看窗口: tmux list-windows -t $session"
    echo "  结束会话: tmux kill-session -t $session"
}

main() {
    parse_args "$@"
    load_env_file
    setup_env

    echo ""
    echo "============================================================"
    echo -e "  ${GREEN}AI-Studio (设计院) — 开发模式${NC}"
    echo "============================================================"
    echo "  目标:        $TARGET"
    echo "  项目目录:    $PROJECT_ROOT"
    echo "  数据目录:    $STUDIO_DATA_PATH"
    echo "  管理员:      $STUDIO_ADMIN_USER / $STUDIO_ADMIN_PASS"
    echo "============================================================"
    echo ""

    case "$TARGET" in
        backend)
            check_python_deps
            if [ "$USE_TMUX" -eq 1 ]; then
                start_with_tmux
            else
                start_backend
            fi
            ;;
        frontend)
            check_frontend_deps
            if [ "$USE_TMUX" -eq 1 ]; then
                start_with_tmux
            else
                start_frontend
            fi
            ;;
        all)
            check_python_deps
            check_frontend_deps
            if [ "$USE_TMUX" -eq 1 ]; then
                start_with_tmux
            else
                start_all_foreground
            fi
            ;;
    esac
}

main "$@"
