from __future__ import annotations

import time
from typing import Any, Dict

from server.storage.memory import InMemoryRepository
from shared.protocol.commands import MsgType
from shared.protocol.errors import ProtocolError, StatusCode


class PresenceService:
    def __init__(self, repository: InMemoryRepository) -> None:
        self.repository = repository

    async def handle_update(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        payload = message.get("payload", {})
        state = payload.get("state")
        if not state:
            raise ProtocolError(StatusCode.BAD_REQUEST, message="Missing presence state")
        if not ctx.user_id:
            raise ProtocolError(StatusCode.UNAUTHORIZED, message="Not authenticated")
        self.repository.update_presence(ctx.user_id, state)
        return _ok_response(message, {})

    async def handle_list(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        if not ctx.user_id:
            raise ProtocolError(StatusCode.UNAUTHORIZED, message="Not authenticated")

        # 返回所有在线用户（用于查看可以添加谁为好友）
        # 权限检查在发送消息时进行
        all_online = self.repository.list_online_users()

        return _ok_response(message, {"users": all_online})

    def broadcast_event(self, user_id: str, state: str) -> Dict[str, Any]:
        return {
            "id": f"presence-{time.time()}",
            "type": "event",
            "timestamp": int(time.time()),
            "command": MsgType.PRESENCE_EVENT.value,
            "headers": {"version": "1.0"},
            "payload": {"user_id": user_id, "state": state, "last_seen": int(time.time())},
        }


def _ok_response(request: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    headers = request.get("headers", {}) or {}
    headers.setdefault("version", "1.0")
    return {
        "id": request["id"],
        "type": "response",
        "timestamp": int(time.time()),
        "command": request["command"],
        "headers": headers,
        "payload": {"status": int(StatusCode.SUCCESS), **payload},
    }
