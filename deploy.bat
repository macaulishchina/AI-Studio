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

if not exist "%PROJECT_ROOT%.env" (
    echo [ERROR] 未找到 .env 文件: %PROJECT_ROOT%.env
    echo        请先执行: copy .env.example .env
    exit /b 1
)

for /f "usebackq tokens=1,* delims==" %%A in ("%PROJECT_ROOT%.env") do (
    set "_LINE=%%A"
    if not "%%A"=="" if not "!_LINE:~0,1!"=="#" (
        set "%%A=%%B"
    )
)

set "PYTHONPATH=%PROJECT_ROOT:~0,-1%"
if not defined STUDIO_DATA_PATH set "STUDIO_DATA_PATH=%PROJECT_ROOT%dev-data"
if not defined WORKSPACE_PATH set "WORKSPACE_PATH=%PROJECT_ROOT:~0,-1%"
if not defined STUDIO_ADMIN_USER set "STUDIO_ADMIN_USER=admin"
if not defined STUDIO_ADMIN_PASS set "STUDIO_ADMIN_PASS=admin123"
if not defined STUDIO_SECRET_KEY set "STUDIO_SECRET_KEY=change-me-in-production"

if not defined DEPLOY_BACKEND_HOST set "DEPLOY_BACKEND_HOST=0.0.0.0"
if not defined DEPLOY_BACKEND_PORT set "DEPLOY_BACKEND_PORT=8002"
if not defined DEPLOY_FRONTEND_HOST set "DEPLOY_FRONTEND_HOST=0.0.0.0"
if not defined DEPLOY_FRONTEND_PORT set "DEPLOY_FRONTEND_PORT=4174"

if not exist "%STUDIO_DATA_PATH%" mkdir "%STUDIO_DATA_PATH%"
if not exist "%STUDIO_DATA_PATH%\plans" mkdir "%STUDIO_DATA_PATH%\plans"
if not exist "%STUDIO_DATA_PATH%\db-backups" mkdir "%STUDIO_DATA_PATH%\db-backups"
if not exist "%STUDIO_DATA_PATH%\uploads" mkdir "%STUDIO_DATA_PATH%\uploads"

echo.
echo ============================================================
echo   AI-Studio 本地部署
echo ============================================================
echo   目标:        %TARGET%
echo   项目目录:    %PROJECT_ROOT:~0,-1%
echo   数据目录:    %STUDIO_DATA_PATH%
echo   后端监听:    %DEPLOY_BACKEND_HOST%:%DEPLOY_BACKEND_PORT%
echo   前端监听:    %DEPLOY_FRONTEND_HOST%:%DEPLOY_FRONTEND_PORT%
echo ============================================================
echo.

if /i "%TARGET%"=="backend" goto :run_backend
if /i "%TARGET%"=="frontend" goto :run_frontend
if /i "%TARGET%"=="all" goto :run_all
goto :eof

:ensure_backend_deps
echo [Deps] 检查后端依赖...
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

:build_frontend
echo [Build] 构建前端...
cd /d "%PROJECT_ROOT%frontend"
call npm run build
cd /d "%PROJECT_ROOT%"
goto :eof

:run_backend
call :ensure_backend_deps
call :check_device_deps
cd /d "%PROJECT_ROOT%"
python -m uvicorn studio.backend.main:app --host %DEPLOY_BACKEND_HOST% --port %DEPLOY_BACKEND_PORT%
goto :eof

:run_frontend
call :ensure_frontend_deps
call :build_frontend
cd /d "%PROJECT_ROOT%frontend"
call npm run preview -- --host %DEPLOY_FRONTEND_HOST% --port %DEPLOY_FRONTEND_PORT%
goto :eof

:run_all
call :ensure_backend_deps
call :ensure_frontend_deps
call :check_device_deps
call :build_frontend

echo [INFO] 启动后端窗口...
start "AI-Studio Deploy Backend" cmd /k "chcp 65001 >nul && cd /d %PROJECT_ROOT% && set PYTHONPATH=%PYTHONPATH% && set STUDIO_DATA_PATH=%STUDIO_DATA_PATH% && set WORKSPACE_PATH=%WORKSPACE_PATH% && set STUDIO_ADMIN_USER=%STUDIO_ADMIN_USER% && set STUDIO_ADMIN_PASS=%STUDIO_ADMIN_PASS% && set STUDIO_SECRET_KEY=%STUDIO_SECRET_KEY% && python -m uvicorn studio.backend.main:app --host %DEPLOY_BACKEND_HOST% --port %DEPLOY_BACKEND_PORT%"

echo [INFO] 启动前端窗口...
start "AI-Studio Deploy Frontend" cmd /k "chcp 65001 >nul && cd /d %PROJECT_ROOT%frontend && npm run preview -- --host %DEPLOY_FRONTEND_HOST% --port %DEPLOY_FRONTEND_PORT%"

echo.
echo [OK] 本地部署已启动
echo      前端: http://localhost:%DEPLOY_FRONTEND_PORT%/studio/
echo      后端: http://localhost:%DEPLOY_BACKEND_PORT%/studio-api/docs
echo.
goto :eof

:help
echo AI-Studio 本地部署脚本 ^(Windows^)
echo.
echo 用法:
echo   deploy.bat ^<backend^|frontend^|all^>
echo.
echo 参数:
echo   backend   部署并启动后端 ^(uvicorn, 非 reload^)
echo   frontend  构建并启动前端预览 ^(vite preview^)
echo   all       同时部署前端和后端 ^(新窗口^)
echo.
echo 说明:
echo   - 需先配置项目根目录 .env
echo   - 未配置时可执行: copy .env.example .env
