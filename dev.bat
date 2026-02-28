@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
set "TARGET=%~1"
set "PROJECT_ROOT=%~dp0"

if "%TARGET%"=="" goto :help
if /i "%TARGET%"=="help" goto :help
if /i "%TARGET%"=="-h" goto :help
if /i "%TARGET%"=="--help" goto :help

if /i not "%TARGET%"=="backend" if /i not "%TARGET%"=="frontend" if /i not "%TARGET%"=="all" (
    echo [ERROR] 未知参数: %TARGET%
    echo.
    goto :help
)

cd /d "%PROJECT_ROOT%"

if exist "%PROJECT_ROOT%.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%PROJECT_ROOT%.env") do (
        set "_LINE=%%A"
        if not "%%A"=="" if not "!_LINE:~0,1!"=="#" (
            set "%%A=%%B"
        )
    )
)

set "PYTHONPATH=%PROJECT_ROOT:~0,-1%"
if not defined STUDIO_DATA_PATH set "STUDIO_DATA_PATH=%PROJECT_ROOT%dev-data"
if not defined WORKSPACE_PATH set "WORKSPACE_PATH=%PROJECT_ROOT:~0,-1%"
if not defined STUDIO_ADMIN_USER set "STUDIO_ADMIN_USER=admin"
if not defined STUDIO_ADMIN_PASS set "STUDIO_ADMIN_PASS=admin123"
if not defined STUDIO_SECRET_KEY set "STUDIO_SECRET_KEY=dev-secret-key-not-for-production"

if not exist "%STUDIO_DATA_PATH%" mkdir "%STUDIO_DATA_PATH%"
if not exist "%STUDIO_DATA_PATH%\plans" mkdir "%STUDIO_DATA_PATH%\plans"
if not exist "%STUDIO_DATA_PATH%\db-backups" mkdir "%STUDIO_DATA_PATH%\db-backups"
if not exist "%STUDIO_DATA_PATH%\uploads" mkdir "%STUDIO_DATA_PATH%\uploads"

echo.
echo ============================================================
echo   AI-Studio 开发环境启动
echo ============================================================
echo   目标:        %TARGET%
echo   项目目录:    %PROJECT_ROOT:~0,-1%
echo   数据目录:    %STUDIO_DATA_PATH%
echo   管理员:      %STUDIO_ADMIN_USER% / %STUDIO_ADMIN_PASS%
echo ============================================================
echo.

if /i "%TARGET%"=="backend" goto :run_backend
if /i "%TARGET%"=="frontend" goto :run_frontend
if /i "%TARGET%"=="all" goto :run_all
goto :eof

:ensure_python_deps
echo [Deps] 检查 Python 依赖...
pip show fastapi >nul 2>&1
if errorlevel 1 (
    echo [INFO] 安装 Python 依赖...
    pip install -r "%PROJECT_ROOT%requirements.txt"
)
goto :eof

:check_device_deps
echo [Deps] 检查设备调试依赖...
pip show sounddevice >nul 2>&1
if errorlevel 1 (
    echo [WARN] sounddevice 未安装, 服务端音频采集不可用
    echo        安装: pip install sounddevice numpy
)
pip show opencv-python-headless >nul 2>&1
if errorlevel 1 (
    pip show opencv-python >nul 2>&1
    if errorlevel 1 (
        echo [WARN] opencv 未安装, 服务端摄像头不可用
        echo        安装: pip install opencv-python-headless
    )
)
goto :eof

:ensure_frontend_deps
echo [Deps] 检查前端依赖...
if not exist "%PROJECT_ROOT%frontend\node_modules" (
    echo [INFO] 安装前端依赖...
    cd /d "%PROJECT_ROOT%frontend"
    call npm install
    cd /d "%PROJECT_ROOT%"
)
goto :eof

:run_backend
call :ensure_python_deps
call :check_device_deps
cd /d "%PROJECT_ROOT%"
python -m uvicorn studio.backend.main:app --host 0.0.0.0 --port 8002 --reload --reload-dir "%PROJECT_ROOT%backend"
goto :eof

:run_frontend
call :ensure_frontend_deps
cd /d "%PROJECT_ROOT%frontend"
npm run dev -- --host 0.0.0.0
goto :eof

:run_all
call :ensure_python_deps
call :ensure_frontend_deps
call :check_device_deps
echo [INFO] 启动后端窗口...
start "AI-Studio Backend" cmd /k "chcp 65001 >nul && cd /d %PROJECT_ROOT% && set PYTHONPATH=%PYTHONPATH% && set STUDIO_DATA_PATH=%STUDIO_DATA_PATH% && set WORKSPACE_PATH=%WORKSPACE_PATH% && set STUDIO_ADMIN_USER=%STUDIO_ADMIN_USER% && set STUDIO_ADMIN_PASS=%STUDIO_ADMIN_PASS% && set STUDIO_SECRET_KEY=%STUDIO_SECRET_KEY% && python -m uvicorn studio.backend.main:app --host 0.0.0.0 --port 8002 --reload --reload-dir %PROJECT_ROOT%backend"
echo [INFO] 启动前端窗口...
start "AI-Studio Frontend" cmd /k "chcp 65001 >nul && cd /d %PROJECT_ROOT%frontend && npm run dev -- --host 0.0.0.0"
echo.
echo [OK] 已启动前后端
echo      前端: http://localhost:5174/studio/
echo      后端: http://localhost:8002/studio-api/docs
echo.
goto :eof

:help
echo AI-Studio 开发脚本 ^(Windows^)
echo.
echo 用法:
echo   dev.bat ^<backend^|frontend^|all^>
echo.
echo 参数:
echo   backend   启动后端 ^(FastAPI^)
echo   frontend  启动前端 ^(Vite^)
echo   all       同时启动前端和后端 ^(新窗口^)
echo.
echo 示例:
echo   dev.bat backend
echo   dev.bat frontend
echo   dev.bat all
