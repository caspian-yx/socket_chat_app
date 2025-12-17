from __future__ import annotations

import time
from typing import Any, Dict

from server.core.connection_manager import ConnectionManager
from server.storage.memory import InMemoryRepository
from shared.protocol.commands import MsgType
from shared.protocol.errors import ProtocolError, StatusCode


class MessageService:
    def __init__(self, repository: InMemoryRepository, connection_manager: ConnectionManager) -> None:
        self.repository = repository
        self.connection_manager = connection_manager

    async def handle_send(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        if not ctx.is_authenticated():
            raise ProtocolError(StatusCode.UNAUTHORIZED, message="Unauthenticated")
        payload = message.get("payload", {})
        convo_id = payload.get("conversation_id")
        if not convo_id:
            raise ProtocolError(StatusCode.BAD_REQUEST, message="Missing conversation_id")
        stored = self.repository.store_message(convo_id, ctx.user_id, payload)
        headers = message.get("headers", {}) or {}
        headers.setdefault("version", "1.0")
        ack = {
            "id": message["id"],
            "type": "response",
            "timestamp": int(time.time()),
            "command": MsgType.MESSAGE_ACK.value,
            "headers": headers,
            "payload": {"status": int(StatusCode.SUCCESS), "message_id": stored["message_id"]},
        }

        target = payload.get("target") or {}
        event = {
            "id": stored["message_id"],
            "type": "event",
            "timestamp": stored["timestamp"],
            "command": MsgType.MESSAGE_EVENT.value,
            "headers": headers,
            "payload": {
                "conversation_id": convo_id,
                "sender_id": ctx.user_id,
                "content": payload.get("content"),
                "message_id": stored["message_id"],
            },
        }
        if target.get("type") == "user":
            await self._deliver_to_user(target.get("id"), event)
        elif target.get("type") == "room":
            await self._deliver_to_room(ctx, target.get("id"), event)

        return ack

    async def _deliver_to_user(self, user_id: str, event: Dict[str, Any]) -> bool:
        if not user_id:
            return False
        delivered = await self.connection_manager.send_to_user(user_id, event)
        if not delivered:
            self.repository.enqueue_offline_message(user_id, event)
        return delivered

    async def _deliver_to_room(self, ctx, room_id: str, event: Dict[str, Any]) -> None:
        if not room_id:
            raise ProtocolError(StatusCode.BAD_REQUEST, message="Missing room_id in target")
        if not self.repository.room_exists(room_id):
            raise ProtocolError(StatusCode.NOT_FOUND, message="Room not found")
        members = self.repository.list_room_members(room_id)
        if ctx.user_id not in members:
            raise ProtocolError(StatusCode.FORBIDDEN, message="Sender not in room")
        for member in members:
            if member == ctx.user_id:
                continue
            await self._deliver_to_user(member, event)
