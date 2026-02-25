@echo off
REM ============================================================
REM  AI-Studio — 仅启动后端 (FastAPI) 开发服务器
REM ============================================================

set PROJECT_ROOT=%~dp0
set PARENT_DIR=%PROJECT_ROOT%..

for %%I in ("%PROJECT_ROOT:~0,-1%") do set FOLDER_NAME=%%~nxI

if /I NOT "%FOLDER_NAME%" == "studio" (
    if not exist "%PARENT_DIR%\studio" (
        mklink /J "%PARENT_DIR%\studio" "%PROJECT_ROOT:~0,-1%"
    )
)

set PYTHONPATH=%PARENT_DIR%
if not defined STUDIO_DATA_PATH set STUDIO_DATA_PATH=%PROJECT_ROOT%dev-data
if not defined WORKSPACE_PATH set WORKSPACE_PATH=%PROJECT_ROOT:~0,-1%
if not defined STUDIO_ADMIN_USER set STUDIO_ADMIN_USER=admin
if not defined STUDIO_ADMIN_PASS set STUDIO_ADMIN_PASS=admin123
if not defined STUDIO_SECRET_KEY set STUDIO_SECRET_KEY=dev-secret-key-not-for-production

if not exist "%STUDIO_DATA_PATH%" mkdir "%STUDIO_DATA_PATH%"
if not exist "%STUDIO_DATA_PATH%\plans" mkdir "%STUDIO_DATA_PATH%\plans"
if not exist "%STUDIO_DATA_PATH%\db-backups" mkdir "%STUDIO_DATA_PATH%\db-backups"
if not exist "%STUDIO_DATA_PATH%\uploads" mkdir "%STUDIO_DATA_PATH%\uploads"

echo.
echo ============================================================
echo   AI-Studio Backend (FastAPI) — 开发模式
echo ============================================================
echo   PYTHONPATH: %PYTHONPATH%
echo   数据目录:   %STUDIO_DATA_PATH%
echo   管理员:     %STUDIO_ADMIN_USER% / %STUDIO_ADMIN_PASS%
echo   地址:       http://localhost:8002
echo   API 文档:   http://localhost:8002/studio-api/docs
echo ============================================================
echo.

cd /d %PARENT_DIR%
python -m uvicorn studio.backend.main:app --host 0.0.0.0 --port 8002 --reload --reload-dir %PROJECT_ROOT%backend
