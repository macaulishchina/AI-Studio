#!/usr/bin/env bash
# ============================================================
#  AI-Studio (设计院) — Linux/macOS 一键启动开发环境
#  同时启动后端 (FastAPI) 和前端 (Vite) 开发服务器
# ============================================================

set -e

# 颜色
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "============================================================"
echo -e "  ${GREEN}AI-Studio (设计院) — 开发模式${NC}"
echo "============================================================"
echo ""

# ── 环境变量 ──
export PYTHONPATH="$PROJECT_ROOT"
export STUDIO_DATA_PATH="${STUDIO_DATA_PATH:-$PROJECT_ROOT/dev-data}"
export WORKSPACE_PATH="${WORKSPACE_PATH:-$PROJECT_ROOT}"
export STUDIO_ADMIN_USER="${STUDIO_ADMIN_USER:-admin}"
export STUDIO_ADMIN_PASS="${STUDIO_ADMIN_PASS:-admin123}"
export STUDIO_SECRET_KEY="${STUDIO_SECRET_KEY:-dev-secret-key-not-for-production}"

# ── 创建数据目录 ──
mkdir -p "$STUDIO_DATA_PATH/plans" "$STUDIO_DATA_PATH/db-backups" "$STUDIO_DATA_PATH/uploads"

echo "  项目目录:   $PROJECT_ROOT"
echo "  PYTHONPATH:  $PYTHONPATH"
echo "  数据目录:    $STUDIO_DATA_PATH"
echo "  管理员:      $STUDIO_ADMIN_USER / $STUDIO_ADMIN_PASS"
echo ""
echo "  后端地址:    http://localhost:8002"
echo "  前端地址:    http://localhost:5174/studio/"
echo "  API 文档:    http://localhost:8002/studio-api/docs"
echo "============================================================"
echo ""

# ── 检查 Python 依赖 ──
echo -e "${BLUE}[1/3]${NC} 检查 Python 依赖..."
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo -e "${YELLOW}[INFO]${NC} 安装 Python 依赖..."
    pip3 install -r "$PROJECT_ROOT/requirements.txt"
fi

# ── 检查前端依赖 ──
echo -e "${BLUE}[2/3]${NC} 检查前端依赖..."
if [ ! -d "$PROJECT_ROOT/frontend/node_modules" ]; then
    echo -e "${YELLOW}[INFO]${NC} 安装前端依赖..."
    cd "$PROJECT_ROOT/frontend" && npm install && cd "$PROJECT_ROOT"
fi

# ── 启动服务 ──
echo -e "${BLUE}[3/3]${NC} 启动服务..."
echo ""

# 清理函数
cleanup() {
    echo ""
    echo -e "${YELLOW}⏹ 正在停止服务...${NC}"
    kill $BACKEND_PID 2>/dev/null || true
    kill $FRONTEND_PID 2>/dev/null || true
    echo -e "${GREEN}✅ 所有服务已停止${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

# 启动后端
echo -e "  启动后端 (FastAPI)..."
cd "$PROJECT_ROOT"
python3 -m uvicorn studio.backend.main:app \
    --host 0.0.0.0 --port 8002 \
    --reload --reload-dir "$PROJECT_ROOT/backend" &
BACKEND_PID=$!

# 等待后端启动
sleep 2

# 启动前端
echo -e "  启动前端 (Vite)..."
cd "$PROJECT_ROOT/frontend"
npm run dev &
FRONTEND_PID=$!

echo ""
echo -e "${GREEN}✅ 开发环境已启动！${NC}"
echo "   访问地址: http://localhost:5174/studio/"
echo "   按 Ctrl+C 停止所有服务"
echo ""

# 等待子进程
wait
