"""
WebSocket 端点 — 项目实时聊天

每个客户端连接到 /studio-api/ws/projects/{project_id}
可选 query 参数 ?token=xxx 进行身份认证
"""
import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Optional

from backend.core.security import decode_studio_token
from backend.services.ws_hub import WsHub, WsClient

logger = logging.getLogger(__name__)

router = APIRouter(tags=["WebSocket"])


@router.websocket("/studio-api/ws/projects/{project_id}")
async def project_ws(
    websocket: WebSocket,
    project_id: int,
    token: Optional[str] = Query(None),
):
    """
    项目实时聊天 WebSocket

    握手时通过 ?token= 认证用户身份.
    连接后:
      - 服务端推送: new_message, ai_event, ai_start, ai_done, presence, typing, messages_updated
      - 客户端发送: ping, typing
    """
    # 认证 (可选, 允许匿名但标记为 anonymous)
    username = "anonymous"
    nickname = "匿名"
    if token:
        user_info = decode_studio_token(token)
        if user_info:
            username = user_info.get("username", "anonymous")
            nickname = user_info.get("nickname", username)

    await websocket.accept()

    client = WsClient(
        ws=websocket,
        username=username,
        nickname=nickname,
    )

    room = WsHub.get_room(project_id)
    room.add(client)

    logger.info(f"🔌 WS 连接: {nickname}({username}) → 项目 {project_id} (在线: {room.user_count})")

    # 广播在线用户变更
    await room.broadcast_presence()

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=60)
            except asyncio.TimeoutError:
                # 超时发心跳, 如果客户端断了会抛出异常
                try:
                    await websocket.send_text(json.dumps({"type": "pong"}))
                except Exception:
                    break
                continue

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")

            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

            elif msg_type == "typing":
                client.typing = bool(data.get("active", False))
                # 广播打字状态到其他人
                await room.broadcast(
                    {
                        "type": "typing",
                        "user": nickname,
                        "username": username,
                        "active": client.typing,
                    },
                    exclude=client,
                )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"WS 异常: {e}")
    finally:
        client.typing = False
        room.remove(client)
        WsHub.cleanup_empty(project_id)
        logger.info(f"🔌 WS 断开: {nickname}({username}) ← 项目 {project_id} (在线: {room.user_count})")
        # 广播用户离开
        if room.user_count > 0:
            await room.broadcast_presence()
