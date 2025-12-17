from __future__ import annotations

import time
from typing import Any, Dict

from server.core.connection_manager import ConnectionManager
from server.storage.memory import InMemoryRepository
from shared.protocol import DEFAULT_VERSION
from shared.protocol.commands import MsgType
from shared.protocol.errors import ProtocolError, StatusCode
from shared.utils.common import sha256_hex


class RoomService:
    def __init__(self, repository: InMemoryRepository, connection_manager: ConnectionManager) -> None:
        self.repository = repository
        self.connection_manager = connection_manager

    async def handle_create(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        self._ensure_authenticated(ctx)
        payload = message.get("payload", {})
        room_id = payload.get("room_id")
        encrypted = bool(payload.get("encrypted", False))
        password = payload.get("password")
        if not room_id:
            raise ProtocolError(StatusCode.BAD_REQUEST, message="Missing room_id")
        if encrypted and not password:
            raise ProtocolError(StatusCode.BAD_REQUEST, message="Encrypted room requires password")
        metadata = {}
        try:
            password_hash = sha256_hex(password) if password else None
            self.repository.create_room(room_id, ctx.user_id, encrypted, password_hash, metadata)
            details = self.repository.get_room(room_id) or {}
            return self._response(
                message,
                {
                    "status": int(StatusCode.SUCCESS),
                    "room_id": room_id,
                    "encrypted": encrypted,
                    "owner": ctx.user_id,
                    "created_at": details.get("created_at"),
                    "members": self.repository.list_room_members(room_id),
                },
            )
        except ValueError as exc:
            return self._response(
                message,
                {
                    "status": int(StatusCode.CONFLICT),
                    "room_id": room_id,
                    "error_message": str(exc),
                },
            )

    async def handle_join(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        self._ensure_authenticated(ctx)
        payload = message.get("payload", {})
        room_id = payload.get("room_id")
        password = payload.get("password")
        if not room_id:
            raise ProtocolError(StatusCode.BAD_REQUEST, message="Missing room_id")
        room = self.repository.get_room(room_id)
        if not room:
            return self._response(
                message,
                {"status": int(StatusCode.NOT_FOUND), "room_id": room_id, "error_message": "Room not found"},
            )
        if room.get("encrypted"):
            if not password:
                raise ProtocolError(StatusCode.FORBIDDEN, message="Password required")
            stored_hash = room.get("password_hash")
            if stored_hash != sha256_hex(password):
                return self._response(
                    message,
                    {
                        "status": int(StatusCode.FORBIDDEN),
                        "room_id": room_id,
                        "error_message": "Invalid password",
                    },
                )
        self.repository.add_member(room_id, ctx.user_id)
        members = self.repository.list_room_members(room_id)
        return self._response(
            message,
            {
                "status": int(StatusCode.SUCCESS),
                "room_id": room_id,
                "encrypted": room.get("encrypted", False),
                "owner": room.get("owner"),
                "created_at": room.get("created_at"),
                "members": members,
            },
        )

    async def handle_leave(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        self._ensure_authenticated(ctx)
        payload = message.get("payload", {})
        room_id = payload.get("room_id")
        if not room_id:
            raise ProtocolError(StatusCode.BAD_REQUEST, message="Missing room_id")
        if not self.repository.room_exists(room_id):
            return self._response(
                message,
                {"status": int(StatusCode.NOT_FOUND), "room_id": room_id, "error_message": "Room not found"},
            )
        self.repository.remove_member(room_id, ctx.user_id)
        return self._response(message, {"status": int(StatusCode.SUCCESS), "room_id": room_id})

    async def handle_list(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        self._ensure_authenticated(ctx)
        rooms = self.repository.list_rooms_for_user(ctx.user_id)
        return self._response(message, {"status": int(StatusCode.SUCCESS), "rooms": rooms})

    async def handle_members(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        self._ensure_authenticated(ctx)
        payload = message.get("payload", {})
        room_id = payload.get("room_id")
        if not room_id:
            raise ProtocolError(StatusCode.BAD_REQUEST, message="Missing room_id")
        if not self.repository.room_exists(room_id):
            return self._response(
                message,
                {"status": int(StatusCode.NOT_FOUND), "room_id": room_id, "error_message": "Room not found"},
            )
        members = self.repository.list_room_members(room_id)
        return self._response(
            message,
            {"status": int(StatusCode.SUCCESS), "room_id": room_id, "members": members},
        )

    async def handle_info(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        self._ensure_authenticated(ctx)
        payload = message.get("payload", {})
        room_id = payload.get("room_id")
        if not room_id:
            raise ProtocolError(StatusCode.BAD_REQUEST, message="Missing room_id")
        details = self.repository.get_room_details(room_id)
        if not details:
            return self._response(
                message,
                {"status": int(StatusCode.NOT_FOUND), "room_id": room_id, "error_message": "Room not found"},
            )
        return self._response(
            message,
            {
                "status": int(StatusCode.SUCCESS),
                "room_id": room_id,
                "owner": details.get("owner"),
                "created_at": details.get("created_at"),
                "encrypted": details.get("encrypted", False),
                "members": details.get("members", []),
            },
        )

    async def handle_kick(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        """群主踢出成员"""
        self._ensure_authenticated(ctx)
        payload = message.get("payload", {})
        room_id = payload.get("room_id")
        target_user_id = payload.get("user_id")

        if not room_id or not target_user_id:
            raise ProtocolError(StatusCode.BAD_REQUEST, message="Missing room_id or user_id")

        room = self.repository.get_room(room_id)
        if not room:
            return self._response(
                message,
                {"status": int(StatusCode.NOT_FOUND), "room_id": room_id, "error_message": "Room not found"},
            )

        # 检查权限：只有群主可以踢人
        if room.get("owner") != ctx.user_id:
            return self._response(
                message,
                {
                    "status": int(StatusCode.FORBIDDEN),
                    "room_id": room_id,
                    "error_message": "Only room owner can kick members",
                },
            )

        # 不能踢自己
        if target_user_id == ctx.user_id:
            return self._response(
                message,
                {
                    "status": int(StatusCode.BAD_REQUEST),
                    "room_id": room_id,
                    "error_message": "Cannot kick yourself",
                },
            )

        # 移除成员
        self.repository.remove_member(room_id, target_user_id)

        return self._response(
            message,
            {
                "status": int(StatusCode.SUCCESS),
                "room_id": room_id,
                "user_id": target_user_id,
                "members": self.repository.list_room_members(room_id),
            },
        )

    async def handle_delete(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        """群主解散群聊"""
        self._ensure_authenticated(ctx)
        payload = message.get("payload", {})
        room_id = payload.get("room_id")

        if not room_id:
            raise ProtocolError(StatusCode.BAD_REQUEST, message="Missing room_id")

        room = self.repository.get_room(room_id)
        if not room:
            return self._response(
                message,
                {"status": int(StatusCode.NOT_FOUND), "room_id": room_id, "error_message": "Room not found"},
            )

        # 检查权限：只有群主可以解散群聊
        if room.get("owner") != ctx.user_id:
            return self._response(
                message,
                {
                    "status": int(StatusCode.FORBIDDEN),
                    "room_id": room_id,
                    "error_message": "Only room owner can delete the room",
                },
            )

        # 删除房间
        self.repository.delete_room(room_id)

        return self._response(
            message,
            {
                "status": int(StatusCode.SUCCESS),
                "room_id": room_id,
            },
        )

    def _ensure_authenticated(self, ctx) -> None:
        if not ctx.is_authenticated():
            raise ProtocolError(StatusCode.UNAUTHORIZED, message="Not authenticated")

    def _response(self, request: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = (request.get("headers") or {}).copy()
        headers.setdefault("version", DEFAULT_VERSION)
        return {
            "id": request.get("id"),
            "type": "response",
            "timestamp": int(time.time()),
            "command": request.get("command"),
            "headers": headers,
            "payload": payload,
        }
