from __future__ import annotations

import time
import uuid
from typing import Any, Dict, Optional

from server.core.connection_manager import ConnectionManager
from server.storage import InMemoryRepository
from server.workers.file_channel import FileTransferServer
from shared.protocol import DEFAULT_VERSION
from shared.protocol.commands import MsgType
from shared.protocol.errors import ProtocolError, StatusCode


class FileService:
    """Coordinates file transfer handshake and status notifications."""

    def __init__(
        self,
        repository: InMemoryRepository,
        connection_manager: ConnectionManager,
        file_server: FileTransferServer,
        channel_host: str,
        channel_port: int,
    ) -> None:
        self.repository = repository
        self.connection_manager = connection_manager
        self.file_server = file_server
        self.channel_host = channel_host
        self.channel_port = channel_port

    async def handle_request(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        if not ctx.is_authenticated():
            raise ProtocolError(StatusCode.UNAUTHORIZED, message="Not authenticated")
        payload = message.get("payload", {}) or {}
        target = payload.get("target") or {}
        target_type = target.get("type", "user")
        target_id = target.get("id")
        if target_type not in {"user", "room"}:
            raise ProtocolError(StatusCode.NOT_IMPLEMENTED, message="Unsupported target type")
        if not target_id:
            raise ProtocolError(StatusCode.BAD_REQUEST, message="Missing target id")
        file_name = payload.get("file_name")
        file_size = payload.get("file_size")
        if not file_name or not file_size:
            raise ProtocolError(StatusCode.BAD_REQUEST, message="Missing file metadata")

        sessions = []
        if target_type == "user":
            session_id = uuid.uuid4().hex
            self.repository.create_file_session(
                session_id,
                file_name,
                int(file_size),
                payload.get("checksum"),
                ctx.user_id,
                target_type,
                target_id,
            )
            delivered = await self._notify_user(
                target_id,
                MsgType.FILE_REQUEST,
                {
                    "session_id": session_id,
                    "from_user": ctx.user_id,
                    "file_name": file_name,
                    "file_size": int(file_size),
                    "checksum": payload.get("checksum"),
                },
            )
            if not delivered:
                self.repository.update_file_session_status(session_id, "unreachable")
                return self._response(
                    message,
                    {
                        "status": int(StatusCode.NOT_FOUND),
                        "session_id": session_id,
                        "error_message": "Target user offline",
                    },
                    MsgType.FILE_REQUEST_ACK,
                )
            sessions.append({"session_id": session_id, "target_id": target_id})
        else:
            members = self.repository.list_room_members(target_id)
            payload_checksum = payload.get("checksum")
            for member in members:
                if member == ctx.user_id:
                    continue
                session_id = uuid.uuid4().hex
                self.repository.create_file_session(
                    session_id,
                    file_name,
                    int(file_size),
                    payload_checksum,
                    ctx.user_id,
                    "user",
                    member,
                )
                delivered = await self._notify_user(
                    member,
                    MsgType.FILE_REQUEST,
                    {
                        "session_id": session_id,
                        "from_user": ctx.user_id,
                        "target": {"type": "room", "id": target_id},
                        "file_name": file_name,
                        "file_size": int(file_size),
                        "checksum": payload_checksum,
                    },
                )
                if delivered:
                    sessions.append({"session_id": session_id, "target_id": member})
                else:
                    self.repository.update_file_session_status(session_id, "unreachable")
        if not sessions:
            return self._response(
                message,
                {"status": int(StatusCode.NOT_FOUND), "error_message": "No recipients available"},
                MsgType.FILE_REQUEST_ACK,
            )
        payload = {
            "status": int(StatusCode.SUCCESS),
            "sessions": sessions,
            "file_name": file_name,
            "file_size": int(file_size),
        }
        if target_type == "room":
            payload["room_id"] = target_id
        if len(sessions) == 1:
            payload["session_id"] = sessions[0]["session_id"]
        return self._response(
            message,
            payload,
            MsgType.FILE_REQUEST_ACK,
        )

    async def handle_accept(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        session = self._require_session(message, ctx)
        if ctx.user_id != session["target_id"]:
            raise ProtocolError(StatusCode.FORBIDDEN, message="Not allowed to accept")
        self.repository.update_file_session_status(session["session_id"], "accepted")
        self.file_server.prepare_session(session["session_id"], session["sender_id"], session["target_id"])
        event_payload = {
            "session_id": session["session_id"],
            "file_name": session["file_name"],
            "file_size": session["file_size"],
            "channel_host": self.channel_host,
            "channel_port": self.channel_port,
            "target_id": session["target_id"],
        }
        await self._notify_user(session["sender_id"], MsgType.FILE_ACCEPT, event_payload)
        await self._notify_user(session["target_id"], MsgType.FILE_ACCEPT, event_payload)
        return self._response(
            message,
            {"status": int(StatusCode.SUCCESS), "session_id": session["session_id"]},
            MsgType.FILE_ACCEPT_ACK,
        )

    async def handle_reject(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        session = self._require_session(message, ctx)
        if ctx.user_id != session["target_id"]:
            raise ProtocolError(StatusCode.FORBIDDEN, message="Not allowed to reject")
        self.repository.update_file_session_status(session["session_id"], "rejected")
        await self._notify_user(
            session["sender_id"],
            MsgType.FILE_REJECT,
            {"session_id": session["session_id"], "from_user": ctx.user_id},
        )
        return self._response(
            message,
            {"status": int(StatusCode.SUCCESS), "session_id": session["session_id"]},
            MsgType.FILE_REJECT_ACK,
        )

    async def handle_complete(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        session = self._require_session(message, ctx)
        await self.notify_channel_complete(session["session_id"])
        return self._response(
            message,
            {"status": int(StatusCode.SUCCESS), "session_id": session["session_id"]},
            MsgType.FILE_COMPLETE,
        )

    async def handle_error(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        session = self._require_session(message, ctx)
        error_message = (message.get("payload") or {}).get("error_message", "transfer failed")
        await self.notify_channel_error(session["session_id"], error_message)
        return self._response(
            message,
            {
                "status": int(StatusCode.SUCCESS),
                "session_id": session["session_id"],
                "error_message": error_message,
            },
            MsgType.FILE_ERROR,
        )

    async def notify_channel_complete(self, session_id: str) -> None:
        session = self.repository.get_file_session(session_id)
        if not session:
            return
        self.repository.update_file_session_status(session_id, "completed")
        payload = {"session_id": session_id, "status": "completed"}
        await self._notify_user(session["sender_id"], MsgType.FILE_COMPLETE, payload)
        await self._notify_user(session["target_id"], MsgType.FILE_COMPLETE, payload)

    async def notify_channel_error(self, session_id: str, error_message: str) -> None:
        session = self.repository.get_file_session(session_id)
        if not session:
            return
        self.repository.update_file_session_status(session_id, "error")
        payload = {"session_id": session_id, "error_message": error_message}
        await self._notify_user(session["sender_id"], MsgType.FILE_ERROR, payload)
        await self._notify_user(session["target_id"], MsgType.FILE_ERROR, payload)

    def _require_session(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        payload = message.get("payload", {}) or {}
        session_id = payload.get("session_id")
        if not session_id:
            raise ProtocolError(StatusCode.BAD_REQUEST, message="Missing session_id")
        session = self.repository.get_file_session(session_id)
        if not session:
            raise ProtocolError(StatusCode.NOT_FOUND, message="File session not found")
        if ctx.user_id not in (session["sender_id"], session["target_id"]):
            raise ProtocolError(StatusCode.FORBIDDEN, message="Not participant of session")
        return session

    async def _notify_user(self, user_id: Optional[str], command: MsgType, payload: Dict[str, Any]) -> bool:
        if not user_id:
            return False
        event = {
            "id": uuid.uuid4().hex,
            "type": "event",
            "timestamp": int(time.time()),
            "command": command.value,
            "headers": {"version": DEFAULT_VERSION},
            "payload": payload,
        }
        return await self.connection_manager.send_to_user(user_id, event)

    @staticmethod
    def _response(message: Dict[str, Any], payload: Dict[str, Any], command: MsgType) -> Dict[str, Any]:
        headers = (message.get("headers") or {}).copy()
        headers.setdefault("version", DEFAULT_VERSION)
        return {
            "id": message.get("id"),
            "type": "response",
            "timestamp": int(time.time()),
            "command": command.value,
            "headers": headers,
            "payload": payload,
        }
