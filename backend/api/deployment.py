"""
设计院 (Studio) - 部署管理 API
部署到主线、分支预览、部署日志
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.backend.core.database import get_db, async_session_maker
from studio.backend.models import (
    Project, ProjectStatus, Deployment, DeployStatus, DeployType,
)
from studio.backend.services import deploy_service, github_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/studio-api/projects", tags=["Deployment"])


# ==================== Schemas ====================

class DeploymentOut(BaseModel):
    id: int
    project_id: Optional[int]
    deploy_type: DeployType
    status: DeployStatus
    logs: str
    error_message: str
    started_at: datetime
    finished_at: Optional[datetime]

    class Config:
        from_attributes = True


class DeployRequest(BaseModel):
    skip_review: bool = False


# 活跃的 WebSocket 连接 (deployment_id → [ws])
_ws_connections: dict[int, list[WebSocket]] = {}


# ==================== 部署 ====================

@router.post("/{project_id}/deploy", response_model=DeploymentOut)
async def deploy_project(
    project_id: int,
    data: DeployRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    合并 PR (如果有) 并部署到主线
    - skip_review=False: 先确认 PR 已合并, 再部署
    - skip_review=True:  直接合并 + 部署
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 如果有 PR 且需要合并 (需要 GitHub 集成已配置)
    if project.github_pr_number and settings.github_repo and settings.github_token:
        try:
            pr = await github_service.get_pull(project.github_pr_number)
            if not pr.get("merged"):
                if data.skip_review:
                    # 直接合并
                    await github_service.merge_pull(
                        project.github_pr_number,
                        commit_message=f"[设计院] {project.title}",
                    )
                else:
                    raise HTTPException(
                        status_code=400,
                        detail="PR 尚未合并。请先 review 并合并 PR, 或使用 skip_review=true 直接部署",
                    )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"检查/合并 PR 失败: {e}")

    project.status = ProjectStatus.deploying
    project.updated_at = datetime.utcnow()

    deploy_type = DeployType.direct_deploy if data.skip_review else DeployType.merge_deploy

    # 异步日志回调
    async def ws_log_callback(message: str):
        deployment_id = getattr(ws_log_callback, "_deployment_id", None)
        if deployment_id and deployment_id in _ws_connections:
            for ws in _ws_connections[deployment_id]:
                try:
                    await ws.send_text(json.dumps({"type": "log", "message": message}))
                except Exception:
                    pass

    # 启动部署 (在后台任务中)
    deployment = Deployment(
        project_id=project_id,
        deploy_type=deploy_type,
        status=DeployStatus.pending,
    )
    db.add(deployment)
    await db.flush()
    await db.refresh(deployment)
    deployment_id = deployment.id

    # 后台执行部署
    asyncio.create_task(
        _run_deploy_background(deployment_id, project_id, deploy_type)
    )

    return deployment


async def _run_deploy_background(
    deployment_id: int,
    project_id: int,
    deploy_type: DeployType,
):
    """后台执行部署任务"""
    async with async_session_maker() as db:
        try:
            async def log_cb(msg: str):
                if deployment_id in _ws_connections:
                    for ws in _ws_connections[deployment_id]:
                        try:
                            await ws.send_text(json.dumps({"type": "log", "message": msg}))
                        except Exception:
                            pass

            result = await deploy_service.deploy_project(
                db, project_id, deploy_type, log_callback=log_cb
            )
            await db.commit()

            # 通知 WebSocket 部署完成
            if deployment_id in _ws_connections:
                for ws in _ws_connections[deployment_id]:
                    try:
                        await ws.send_text(json.dumps({
                            "type": "done",
                            "status": result.status.value,
                        }))
                    except Exception:
                        pass

        except Exception as e:
            logger.exception(f"后台部署任务异常: {e}")
            await db.rollback()


# ==================== 部署日志 (WebSocket) ====================

@router.websocket("/{project_id}/deploy-ws/{deployment_id}")
async def deployment_websocket(
    websocket: WebSocket,
    project_id: int,
    deployment_id: int,
):
    """实时部署日志 WebSocket"""
    await websocket.accept()

    if deployment_id not in _ws_connections:
        _ws_connections[deployment_id] = []
    _ws_connections[deployment_id].append(websocket)

    try:
        # 先发送已有日志
        async with async_session_maker() as db:
            result = await db.execute(
                select(Deployment).where(Deployment.id == deployment_id)
            )
            deployment = result.scalar_one_or_none()
            if deployment and deployment.logs:
                await websocket.send_text(json.dumps({
                    "type": "history",
                    "logs": deployment.logs,
                }))

        # 保持连接
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if deployment_id in _ws_connections:
            _ws_connections[deployment_id].remove(websocket)
            if not _ws_connections[deployment_id]:
                del _ws_connections[deployment_id]


# ==================== 部署记录 ====================

@router.get("/{project_id}/deployments", response_model=List[DeploymentOut])
async def list_deployments(
    project_id: int,
    db: AsyncSession = Depends(get_db),
):
    """获取项目的部署记录"""
    result = await db.execute(
        select(Deployment)
        .where(Deployment.project_id == project_id)
        .order_by(Deployment.started_at.desc())
    )
    return result.scalars().all()
