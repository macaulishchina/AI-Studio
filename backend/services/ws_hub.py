"""
WebSocket 实时广播中心

为每个项目维护一组 WebSocket 连接, 实现:
  1. 用户消息实时同步 (new_message)
  2. AI 流式输出广播 (ai_event)
  3. 在线用户感知 (presence)
  4. 消息变更通知 (messages_updated)

事件协议 (JSON):
  → 服务端推送:
    {"type": "new_message",      "message": {...}}         用户/AI 新消息
    {"type": "ai_event",         "event": {...}}           AI 流式事件 (content/thinking/tool_*/done 等)
    {"type": "ai_start",         "task_id": N, "sender": "..."}  AI 任务开始
    {"type": "ai_done",          "task_id": N}             AI 任务完成
    {"type": "presence",         "users": [...]}           在线用户列表
    {"type": "typing",           "user": "...", "active": bool}  打字指示器
    {"type": "messages_updated", "reason": "..."}          消息列表需要刷新
    {"type": "pong"}                                       心跳响应

  ← 客户端发送:
    {"type": "ping"}                                       心跳
    {"type": "typing", "active": true/false}               打字状态
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


@dataclass
class WsClient:
    """一个 WebSocket 连接"""
    ws: WebSocket
    username: str = "anonymous"
    nickname: str = "匿名"
    connected_at: float = field(default_factory=time.time)
    last_ping: float = field(default_factory=time.time)
    typing: bool = False


class ProjectRoom:
    """一个项目的 WebSocket 房间"""

    def __init__(self, project_id: int):
        self.project_id = project_id
        self.clients: List[WsClient] = []

    def add(self, client: WsClient):
        self.clients.append(client)

    def remove(self, client: WsClient):
        if client in self.clients:
            self.clients.remove(client)

    @property
    def user_count(self) -> int:
        return len(self.clients)

    def get_presence(self) -> list:
        """返回在线用户列表 (去重)"""
        seen: Set[str] = set()
        users = []
        for c in self.clients:
            if c.username not in seen:
                seen.add(c.username)
                users.append({
                    "username": c.username,
                    "nickname": c.nickname,
                    "typing": c.typing,
                })
        return users

    async def broadcast(self, data: dict, exclude: Optional[WsClient] = None):
        """广播 JSON 消息到房间内所有连接"""
        text = json.dumps(data, ensure_ascii=False, default=str)
        dead: List[WsClient] = []
        for client in self.clients:
            if client is exclude:
                continue
            try:
                await client.ws.send_text(text)
            except Exception:
                dead.append(client)
        for c in dead:
            self.remove(c)

    async def broadcast_presence(self):
        """广播在线用户列表"""
        await self.broadcast({
            "type": "presence",
            "users": self.get_presence(),
        })


class WsHub:
    """全局 WebSocket 中心 (单例)"""

    _rooms: Dict[int, ProjectRoom] = {}

    @classmethod
    def get_room(cls, project_id: int) -> ProjectRoom:
        if project_id not in cls._rooms:
            cls._rooms[project_id] = ProjectRoom(project_id)
        return cls._rooms[project_id]

    @classmethod
    def cleanup_empty(cls, project_id: int):
        room = cls._rooms.get(project_id)
        if room and room.user_count == 0:
            del cls._rooms[project_id]

    @classmethod
    async def broadcast_new_message(cls, project_id: int, message: dict):
        """广播新消息 (用户/AI)"""
        room = cls._rooms.get(project_id)
        if room:
            await room.broadcast({
                "type": "new_message",
                "message": message,
            })

    @classmethod
    async def broadcast_ai_event(cls, project_id: int, event: dict):
        """广播 AI 流式事件"""
        room = cls._rooms.get(project_id)
        if room:
            await room.broadcast({
                "type": "ai_event",
                "event": event,
            })

    @classmethod
    async def broadcast_ai_start(cls, project_id: int, task_id: int, sender: str = ""):
        """广播 AI 任务开始"""
        room = cls._rooms.get(project_id)
        if room:
            await room.broadcast({
                "type": "ai_start",
                "task_id": task_id,
                "sender": sender,
            })

    @classmethod
    async def broadcast_ai_done(cls, project_id: int, task_id: int):
        """广播 AI 任务完成"""
        room = cls._rooms.get(project_id)
        if room:
            await room.broadcast({
                "type": "ai_done",
                "task_id": task_id,
            })

    @classmethod
    async def broadcast_messages_updated(cls, project_id: int, reason: str = ""):
        """广播消息列表变更 (删除/总结/清空等)"""
        room = cls._rooms.get(project_id)
        if room:
            await room.broadcast({
                "type": "messages_updated",
                "reason": reason,
            })

    @classmethod
    def get_online_count(cls, project_id: int) -> int:
        room = cls._rooms.get(project_id)
        return room.user_count if room else 0

    @classmethod
    def get_all_rooms_info(cls) -> dict:
        return {
            pid: {"users": room.user_count, "presence": room.get_presence()}
            for pid, room in cls._rooms.items()
            if room.user_count > 0
        }
