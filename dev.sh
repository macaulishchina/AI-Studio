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

start_backend() {
    cd "$PROJECT_ROOT"
    kill_port 8002
    python3 -m uvicorn studio.backend.main:app --host 0.0.0.0 --port 8002 --reload --reload-dir "$PROJECT_ROOT/backend"
}

start_frontend() {
    cd "$PROJECT_ROOT/frontend"
    npm run dev -- --host 0.0.0.0
}

start_all_foreground() {
    cleanup() {
        # 防止重复打印（多次 SIGINT/SIGTERM 触发时）
        if [ "${_DEV_CLEANING:-0}" = "1" ]; then
            return
        fi
        _DEV_CLEANING=1
        echo ""
        echo -e "${YELLOW}⏹ 正在停止服务...${NC}"
        # 杀整个进程组, 确保 uvicorn 子进程也一并退出
        kill -- -$$ 2>/dev/null || true
        echo -e "${GREEN}✅ 所有服务已停止${NC}"
        exit 0
    }
    trap cleanup SIGINT SIGTERM

    kill_port 8002
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

    # 清理端口并构造后端启动命令
    kill_port 8002
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
            check_device_permissions
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
            check_device_permissions
            if [ "$USE_TMUX" -eq 1 ]; then
                start_with_tmux
            else
                start_all_foreground
            fi
            ;;
    esac
}

main "$@"
