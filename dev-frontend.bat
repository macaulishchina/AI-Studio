@echo off
chcp 65001 >nul
REM ============================================================
REM  AI-Studio — 仅启动前端 (Vite) 开发服务器
REM ============================================================

set "PROJECT_ROOT=%~dp0"

echo.
echo ============================================================
echo   AI-Studio Frontend (Vite) — 开发模式
echo ============================================================
echo   地址: http://localhost:5174/studio/
echo   代理: /studio-api -^> http://localhost:8002
echo ============================================================
echo.

if not exist "%PROJECT_ROOT%frontend\node_modules" (
    echo [INFO] 安装前端依赖...
    cd /d "%PROJECT_ROOT%frontend"
    call npm install
)

cd /d "%PROJECT_ROOT%frontend"
npm run dev
