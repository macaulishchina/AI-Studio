"""
设计院 (Studio) - 快照服务
管理代码快照、数据库备份和回滚操作
"""
import asyncio
import logging
import os
import shutil
from datetime import datetime
from typing import Optional, Dict, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.config import settings
from studio.backend.models import Snapshot

logger = logging.getLogger(__name__)


async def _run_cmd(cmd: str, cwd: Optional[str] = None) -> tuple[int, str, str]:
    """运行 shell 命令"""
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd or settings.workspace_path,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(), stderr.decode()


async def create_snapshot(
    db: AsyncSession,
    description: str = "",
    project_id: Optional[int] = None,
) -> Snapshot:
    """
    创建代码快照:
    1. git tag
    2. 数据库备份
    3. Docker 镜像标记
    """
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    tag_name = f"snapshot-{timestamp}"

    # 1. 获取当前 commit
    rc, stdout, stderr = await _run_cmd("git rev-parse HEAD")
    commit_hash = stdout.strip() if rc == 0 else "unknown"

    # 2. 创建 git tag
    await _run_cmd(f'git tag -a {tag_name} -m "Snapshot: {description or tag_name}"')

    # 3. 备份数据库 (根据配置发现需要备份的 DB 文件)
    db_backup_paths = []
    db_files = settings.snapshot_db_paths
    if not db_files:
        # 自动发现: 查找 data/ 下的 .db 文件
        data_dir = os.path.join(settings.workspace_path, "data")
        if os.path.isdir(data_dir):
            db_files = [
                os.path.join("data", f)
                for f in os.listdir(data_dir) if f.endswith(".db")
            ]

    for db_rel_path in db_files:
        db_src = os.path.join(settings.workspace_path, db_rel_path)
        if os.path.exists(db_src):
            db_name = os.path.basename(db_rel_path).replace(".db", "")
            db_backup_filename = f"{db_name}-{timestamp}.db"
            bp = os.path.join(settings.db_backups_path, db_backup_filename)
            shutil.copy2(db_src, bp)
            db_backup_paths.append(bp)
            logger.info(f"数据库备份: {bp}")

    db_backup_path = ";".join(db_backup_paths) if db_backup_paths else ""
    if not db_backup_paths:
        logger.info("未发现数据库文件, 跳过备份")

    # 4. 标记 Docker 镜像 (根据配置的部署服务和镜像前缀)
    docker_tags = {}
    image_prefix = settings.docker_image_prefix
    if image_prefix:
        for service in settings.deploy_services:
            image_name = f"{image_prefix}-{service}"
            tag_cmd = f"docker tag {image_name}:latest {image_name}:{tag_name} 2>/dev/null || true"
            await _run_cmd(tag_cmd)
            docker_tags[service] = tag_name

    # 5. 保存快照记录
    snapshot = Snapshot(
        project_id=project_id,
        git_commit=commit_hash,
        git_tag=tag_name,
        docker_image_tags=docker_tags,
        db_backup_path=db_backup_path,
        description=description,
        is_healthy=True,
    )
    db.add(snapshot)
    await db.flush()
    await db.refresh(snapshot)

    logger.info(f"快照已创建: {tag_name} (commit: {commit_hash[:8]})")
    return snapshot


async def rollback_to_snapshot(
    db: AsyncSession,
    snapshot_id: int,
    restore_db: bool = False,
) -> Dict[str, Any]:
    """
    回滚到指定快照:
    1. git checkout tag
    2. 可选: 恢复数据库
    3. 重建并重启项目容器
    """
    result = await db.execute(select(Snapshot).where(Snapshot.id == snapshot_id))
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        return {"success": False, "error": "快照不存在"}

    logs = []

    try:
        # 1. Git checkout
        tag = snapshot.git_tag
        rc, stdout, stderr = await _run_cmd(f"git checkout {tag}")
        logs.append(f"git checkout {tag}: {'OK' if rc == 0 else stderr}")

        # 2. 可选恢复数据库 (支持多个 DB 备份, 用分号分隔)
        if restore_db and snapshot.db_backup_path:
            for bp in snapshot.db_backup_path.split(";"):
                bp = bp.strip()
                if bp and os.path.exists(bp):
                    # 推断原始目标路径: backup 文件名去掉时间戳
                    # e.g. "mydb-20240101-120000.db" → "data/mydb.db"
                    import re
                    db_basename = re.sub(r'-\d{8}-\d{6}\.db$', '.db', os.path.basename(bp))
                    db_target = os.path.join(settings.workspace_path, "data", db_basename)
                    shutil.copy2(bp, db_target)
                    logs.append(f"数据库已恢复: {bp} → {db_target}")

        # 3. 尝试用已标记的镜像直接启动（更快）
        image_tags = snapshot.docker_image_tags or {}
        image_prefix = settings.docker_image_prefix
        services_str = " ".join(settings.deploy_services)
        if image_tags and image_prefix:
            for service, tag_val in image_tags.items():
                image_name = f"{image_prefix}-{service}"
                retag_cmd = f"docker tag {image_name}:{tag_val} {image_name}:latest 2>/dev/null"
                rc, _, _ = await _run_cmd(retag_cmd)
                if rc == 0:
                    logs.append(f"镜像回退 {image_name}:{tag_val} → latest: OK")

            # 重启容器
            rc, stdout, stderr = await _run_cmd(
                f"docker compose up -d {services_str}",
                cwd=settings.workspace_path,
            )
            logs.append(f"容器重启: {'OK' if rc == 0 else stderr}")
        else:
            # 没有镜像标记, 重新构建
            rc, stdout, stderr = await _run_cmd(
                f"docker compose up -d --build {services_str}",
                cwd=settings.workspace_path,
            )
            logs.append(f"容器重建: {'OK' if rc == 0 else stderr}")

        # 4. 健康检查
        healthy = await _health_check()
        logs.append(f"健康检查: {'通过 ✅' if healthy else '失败 ❌'}")

        return {
            "success": healthy,
            "snapshot": tag,
            "logs": logs,
        }

    except Exception as e:
        logger.exception("回滚失败")
        logs.append(f"异常: {str(e)}")
        return {"success": False, "error": str(e), "logs": logs}


async def _health_check() -> bool:
    """检查主项目是否健康 (根据 settings.deploy_health_checks 配置)"""
    checks = settings.deploy_health_checks
    if not checks:
        logger.info("未配置健康检查端点, 跳过")
        return True

    for attempt in range(settings.health_check_retries):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                all_ok = True
                for check in checks:
                    url = check.get("url", "")
                    name = check.get("name", url)
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        logger.warning(f"{name} 健康检查失败 (尝试 {attempt + 1})")
                        all_ok = False
                        break

                if all_ok:
                    return True

                await asyncio.sleep(settings.health_check_interval)
        except Exception as e:
            logger.warning(f"健康检查异常 (尝试 {attempt + 1}): {e}")
            await asyncio.sleep(settings.health_check_interval)

    return False


# 需要导入 httpx (在函数体中使用)
import httpx  # noqa: E402
