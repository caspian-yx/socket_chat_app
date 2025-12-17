from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, List

from client.core.network import NetworkClient
from client.core.session import ClientSession
from shared.protocol import validator
from shared.protocol.commands import MsgType
from shared.protocol.errors import ProtocolError, StatusCode


class RoomManager:
    def __init__(self, network: NetworkClient, session: ClientSession) -> None:
        self.network = network
        self.session = session
        self._pending: Dict[str, asyncio.Future] = {}
        for command in (
            MsgType.ROOM_CREATE,
            MsgType.ROOM_JOIN,
            MsgType.ROOM_LEAVE,
            MsgType.ROOM_LIST,
            MsgType.ROOM_MEMBERS,
            MsgType.ROOM_INFO,
            MsgType.ROOM_KICK,
            MsgType.ROOM_DELETE,
        ):
            self.network.register_handler(command, self._handle_response)

    async def create_room(self, room_id: str, encrypted: bool = False, password: str | None = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"room_id": room_id, "encrypted": encrypted}
        if password:
            payload["password"] = password
        return await self._request(
            MsgType.ROOM_CREATE,
            payload,
        )

    async def join_room(self, room_id: str, password: str | None = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"room_id": room_id}
        if password:
            payload["password"] = password
        return await self._request(MsgType.ROOM_JOIN, payload)

    async def leave_room(self, room_id: str) -> Dict[str, Any]:
        return await self._request(MsgType.ROOM_LEAVE, {"room_id": room_id})

    async def list_rooms(self) -> List[str]:
        payload = await self._request(MsgType.ROOM_LIST, {})
        return payload.get("rooms", [])

    async def list_members(self, room_id: str) -> List[str]:
        payload = await self._request(MsgType.ROOM_MEMBERS, {"room_id": room_id})
        return payload.get("members", [])

    async def room_info(self, room_id: str) -> Dict[str, Any]:
        payload = await self._request(MsgType.ROOM_INFO, {"room_id": room_id})
        return payload

    async def kick_member(self, room_id: str, user_id: str) -> Dict[str, Any]:
        """群主踢出成员"""
        return await self._request(MsgType.ROOM_KICK, {"room_id": room_id, "user_id": user_id})

    async def delete_room(self, room_id: str) -> Dict[str, Any]:
        """群主解散群聊"""
        return await self._request(MsgType.ROOM_DELETE, {"room_id": room_id})

    async def _request(self, command: MsgType, payload: Dict[str, Any]) -> Dict[str, Any]:
        msg = {
            "id": str(uuid.uuid4()),
            "type": "request",
            "timestamp": int(time.time()),
            "command": command.value,
            "payload": payload,
        }
        msg = self.session.attach_headers(msg)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg["id"]] = future
        schema = validator.load_schema(command.value)
        await self.network.send(msg, schema)
        try:
            response = await asyncio.wait_for(future, timeout=10)
        finally:
            self._pending.pop(msg["id"], None)
        resp_payload = response.get("payload", {})
        status = resp_payload.get("status", StatusCode.SUCCESS)
        if int(status) != int(StatusCode.SUCCESS):
            raise ProtocolError(StatusCode(status), message=resp_payload.get("error_message", "Room operation failed"))
        return resp_payload

    async def _handle_response(self, message: Dict[str, Any]) -> None:
        future = self._pending.get(message.get("id"))
        if future and not future.done():
            future.set_result(message)
