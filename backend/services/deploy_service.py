"""
设计院 (Studio) - 部署流水线服务
处理构建、部署、健康检查和自动回滚
"""
import asyncio
import logging
import os
from datetime import datetime
from typing import Optional, Callable, Awaitable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.config import settings
from studio.backend.models import Deployment, DeployStatus, DeployType, Project, ProjectStatus
from studio.backend.services import snapshot_service

logger = logging.getLogger(__name__)

# 部署日志回调类型
LogCallback = Optional[Callable[[str], Awaitable[None]]]


async def _run_cmd(cmd: str, cwd: Optional[str] = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd or settings.workspace_path,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(), stderr.decode()


async def _log(deployment: Deployment, message: str, callback: LogCallback = None):
    """追加部署日志"""
    timestamp = datetime.utcnow().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    deployment.logs = (deployment.logs or "") + line + "\n"
    logger.info(f"[Deploy #{deployment.id}] {message}")
    if callback:
        await callback(line)


async def deploy_project(
    db: AsyncSession,
    project_id: Optional[int] = None,
    deploy_type: DeployType = DeployType.merge_deploy,
    log_callback: LogCallback = None,
) -> Deployment:
    """
    项目部署流水线:
    1. 创建部署前快照
    2. git pull
    3. docker compose build (配置的服务列表)
    4. docker compose up -d (配置的服务列表)
    5. 健康检查 (配置的端点)
    6. 失败则自动回滚
    """
    # 创建部署记录
    deployment = Deployment(
        project_id=project_id,
        deploy_type=deploy_type,
        status=DeployStatus.pending,
    )
    db.add(deployment)
    await db.flush()

    try:
        # ===== Step 1: 部署前快照 =====
        await _log(deployment, "📸 正在创建部署前快照...", log_callback)
        deployment.status = DeployStatus.building
        snapshot_before = await snapshot_service.create_snapshot(
            db, description=f"部署前快照 (Deploy #{deployment.id})", project_id=project_id
        )
        deployment.snapshot_before_id = snapshot_before.id
        await _log(deployment, f"   快照已创建: {snapshot_before.git_tag}", log_callback)

        # ===== Step 2: Git Pull =====
        await _log(deployment, f"📥 拉取最新代码 (git pull origin {settings.deploy_git_branch})...", log_callback)
        rc, stdout, stderr = await _run_cmd(f"git pull origin {settings.deploy_git_branch}")
        if rc != 0:
            await _log(deployment, f"   ⚠️ git pull 警告: {stderr.strip()}", log_callback)
        else:
            await _log(deployment, f"   ✅ {stdout.strip()}", log_callback)

        # ===== Step 3: Docker Build =====
        services = settings.deploy_services
        services_str = " ".join(services)
        await _log(deployment, f"🔨 构建 Docker 镜像 ({services_str})...", log_callback)
        deployment.status = DeployStatus.building

        for service in services:
            await _log(deployment, f"   构建 {service}...", log_callback)
            rc, stdout, stderr = await _run_cmd(
                f"docker compose build {service}",
                cwd=settings.workspace_path,
            )
            if rc != 0:
                raise RuntimeError(f"{service} 构建失败: {stderr}")
            await _log(deployment, f"   ✅ {service} 构建完成", log_callback)

        # ===== Step 4: 启动容器 =====
        await _log(deployment, "🚀 启动容器...", log_callback)
        deployment.status = DeployStatus.deploying

        # 安全约束: 只操作配置的部署服务
        rc, stdout, stderr = await _run_cmd(
            f"docker compose up -d {services_str}",
            cwd=settings.workspace_path,
        )
        if rc != 0:
            raise RuntimeError(f"容器启动失败: {stderr}")
        await _log(deployment, "   ✅ 容器已启动", log_callback)

        # ===== Step 5: 健康检查 =====
        await _log(deployment, f"🏥 健康检查 (超时 {settings.health_check_timeout}s)...", log_callback)

        # 等待容器启动
        await asyncio.sleep(10)

        healthy = await _health_check_with_retry(log_callback)

        if healthy:
            # 部署成功
            deployment.status = DeployStatus.healthy
            deployment.finished_at = datetime.utcnow()
            await _log(deployment, "✅ 部署成功! 健康检查通过", log_callback)

            # 创建部署后快照
            snapshot_after = await snapshot_service.create_snapshot(
                db, description=f"部署后快照 (Deploy #{deployment.id})", project_id=project_id
            )
            deployment.snapshot_after_id = snapshot_after.id

            # 更新项目状态
            if project_id:
                result = await db.execute(select(Project).where(Project.id == project_id))
                project = result.scalar_one_or_none()
                if project:
                    project.status = ProjectStatus.deployed
        else:
            # 部署失败 → 自动回滚
            await _log(deployment, "❌ 健康检查失败! 自动回滚中...", log_callback)
            deployment.status = DeployStatus.failed
            deployment.error_message = "健康检查未通过"

            rollback_result = await snapshot_service.rollback_to_snapshot(
                db, deployment.snapshot_before_id
            )
            if rollback_result["success"]:
                deployment.status = DeployStatus.rolled_back
                await _log(deployment, "🔄 已自动回滚到部署前状态", log_callback)
            else:
                await _log(deployment, f"⚠️ 自动回滚也失败: {rollback_result.get('error')}", log_callback)

            deployment.finished_at = datetime.utcnow()

    except Exception as e:
        logger.exception("部署流水线异常")
        deployment.status = DeployStatus.failed
        deployment.error_message = str(e)
        deployment.finished_at = datetime.utcnow()
        await _log(deployment, f"💥 部署异常: {str(e)}", log_callback)

        # 尝试自动回滚
        if deployment.snapshot_before_id:
            await _log(deployment, "🔄 尝试自动回滚...", log_callback)
            try:
                await snapshot_service.rollback_to_snapshot(db, deployment.snapshot_before_id)
                deployment.status = DeployStatus.rolled_back
                await _log(deployment, "🔄 已回滚", log_callback)
            except Exception as re:
                await _log(deployment, f"⚠️ 回滚失败: {str(re)}", log_callback)

    await db.flush()
    return deployment


async def _health_check_with_retry(log_callback: LogCallback = None) -> bool:
    """带重试的健康检查 (根据 settings.deploy_health_checks 配置)"""
    import httpx

    checks = settings.deploy_health_checks
    if not checks:
        # 未配置健康检查 → 默认视为成功
        if log_callback:
            await log_callback("   ℹ️ 未配置健康检查端点, 跳过")
        return True

    for attempt in range(settings.health_check_retries):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                all_ok = True
                status_parts = []

                for check in checks:
                    url = check.get("url", "")
                    name = check.get("name", url)
                    try:
                        resp = await client.get(url)
                        ok = resp.status_code == 200
                    except Exception:
                        ok = False
                    all_ok = all_ok and ok
                    status_parts.append(f"{name}={'✅' if ok else '❌'}")

                if all_ok:
                    return True

                msg = f"   尝试 {attempt + 1}/{settings.health_check_retries}: {' '.join(status_parts)}"
                if log_callback:
                    await log_callback(msg)

        except Exception as e:
            if log_callback:
                await log_callback(f"   尝试 {attempt + 1}/{settings.health_check_retries}: 连接失败 ({e})")

        await asyncio.sleep(settings.health_check_interval)

    return False
