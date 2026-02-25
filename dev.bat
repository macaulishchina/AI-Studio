@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
REM ============================================================
REM  AI-Studio — Windows 一键启动开发环境
REM  同时启动后端 (FastAPI) 和前端 (Vite) 开发服务器
REM ============================================================

echo.
echo ============================================================
echo   AI-Studio 开发环境启动
echo ============================================================
echo.

set "PROJECT_ROOT=%~dp0"
set "PARENT_DIR=%PROJECT_ROOT%.."

REM ── 检查 studio 包链接 ──
for %%I in ("%PROJECT_ROOT:~0,-1%") do set "FOLDER_NAME=%%~nxI"

if /I NOT "%FOLDER_NAME%"=="studio" (
    if not exist "%PARENT_DIR%\studio" (
        echo [INFO] 创建目录链接: %PARENT_DIR%\studio -^> %PROJECT_ROOT:~0,-1%
        mklink /J "%PARENT_DIR%\studio" "%PROJECT_ROOT:~0,-1%"
    )
)

REM ── 加载 .env 文件 ──
if exist "%PROJECT_ROOT%.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%PROJECT_ROOT%.env") do (
        set "_LINE=%%A"
        if not "%%A"=="" if not "!_LINE:~0,1!"=="#" (
            set "%%A=%%B"
        )
    )
)

REM ── 环境变量 (未在 .env 中设置时使用默认值) ──
set "PYTHONPATH=%PARENT_DIR%"
if not defined STUDIO_DATA_PATH set "STUDIO_DATA_PATH=%PROJECT_ROOT%dev-data"
if not defined WORKSPACE_PATH set "WORKSPACE_PATH=%PROJECT_ROOT:~0,-1%"
if not defined STUDIO_ADMIN_USER set "STUDIO_ADMIN_USER=admin"
if not defined STUDIO_ADMIN_PASS set "STUDIO_ADMIN_PASS=admin123"
if not defined STUDIO_SECRET_KEY set "STUDIO_SECRET_KEY=dev-secret-key-not-for-production"

REM ── 创建数据目录 ──
if not exist "%STUDIO_DATA_PATH%" mkdir "%STUDIO_DATA_PATH%"
if not exist "%STUDIO_DATA_PATH%\plans" mkdir "%STUDIO_DATA_PATH%\plans"
if not exist "%STUDIO_DATA_PATH%\db-backups" mkdir "%STUDIO_DATA_PATH%\db-backups"
if not exist "%STUDIO_DATA_PATH%\uploads" mkdir "%STUDIO_DATA_PATH%\uploads"

echo   项目目录:   %PROJECT_ROOT:~0,-1%
echo   PYTHONPATH:  %PYTHONPATH%
echo   数据目录:    %STUDIO_DATA_PATH%
echo   工作区:      %WORKSPACE_PATH%
echo   管理员:      %STUDIO_ADMIN_USER% / %STUDIO_ADMIN_PASS%
echo.
echo   后端地址:    http://localhost:8002
echo   前端地址:    http://localhost:5174/studio/
echo   API 文档:    http://localhost:8002/studio-api/docs
echo ============================================================
echo.

REM ── 检查 Python 依赖 ──
echo [1/3] 检查 Python 依赖...
pip show fastapi >nul 2>&1
if errorlevel 1 (
    echo [INFO] 安装 Python 依赖...
    pip install -r "%PROJECT_ROOT%requirements.txt"
)

REM ── 检查前端依赖 ──
echo [2/3] 检查前端依赖...
if not exist "%PROJECT_ROOT%frontend\node_modules" (
    echo [INFO] 安装前端依赖...
    cd /d "%PROJECT_ROOT%frontend"
    call npm install
    cd /d "%PROJECT_ROOT%"
)

REM ── 启动后端 (新窗口, 子进程自动继承当前环境变量) ──
echo [3/3] 启动服务...
echo.
echo   正在启动后端 (FastAPI)...
start "AI-Studio Backend" cmd /k "chcp 65001 >nul && cd /d %PARENT_DIR% && python -m uvicorn studio.backend.main:app --host 0.0.0.0 --port 8002 --reload --reload-dir %PROJECT_ROOT%backend"

REM ── 启动前端 (新窗口) ──
echo   正在启动前端 (Vite)...
start "AI-Studio Frontend" cmd /k "chcp 65001 >nul && cd /d %PROJECT_ROOT%frontend && npm run dev"

echo.
echo [OK] 开发环境已启动!
echo      后端窗口: "AI-Studio Backend"
echo      前端窗口: "AI-Studio Frontend"
echo      访问地址: http://localhost:5174/studio/
echo.
echo 按任意键关闭此窗口...
pause >nul
