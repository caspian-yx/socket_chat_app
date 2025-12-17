from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from client.core.network import NetworkClient
from client.core.session import ClientSession
from shared.protocol.commands import MsgType
from shared.protocol.errors import ProtocolError, StatusCode
from shared.protocol.framing import encode_chunk
from shared.protocol import validator

logger = logging.getLogger(__name__)


class FileTransferManager:
    """Handles file transfer negotiations and data channel operations."""

    def __init__(
        self,
        network: NetworkClient,
        session: ClientSession,
        ui_queue,
        storage_dir: Path,
        chunk_size: int = 65536,
    ) -> None:
        self.network = network
        self.session = session
        self.ui_queue = ui_queue
        self.chunk_size = chunk_size
        self.storage_dir = storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._pending_acks: Dict[str, asyncio.Future] = {}
        self._pending_requests: Dict[str, Dict[str, Any]] = {}
        self._sending_sessions: Dict[str, Dict[str, Any]] = {}
        self._receiving_sessions: Dict[str, Dict[str, Any]] = {}
        self.network.register_handler(MsgType.FILE_REQUEST_ACK, self._handle_ack)
        self.network.register_handler(MsgType.FILE_ACCEPT_ACK, self._handle_ack)
        self.network.register_handler(MsgType.FILE_REJECT_ACK, self._handle_ack)
        self.network.register_handler(MsgType.FILE_REQUEST, self._handle_request_event)
        self.network.register_handler(MsgType.FILE_ACCEPT, self._handle_accept_event)
        self.network.register_handler(MsgType.FILE_REJECT, self._handle_reject_event)
        self.network.register_handler(MsgType.FILE_COMPLETE, self._handle_complete_event)
        self.network.register_handler(MsgType.FILE_ERROR, self._handle_error_event)

    async def request_send_file(self, target_id: str, file_path: Path, target_type: str = "user") -> Dict[str, Any]:
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(file_path)
        file_size = file_path.stat().st_size
        checksum = self._sha256_file(file_path)
        payload = {
            "target": {"type": target_type, "id": target_id},
            "file_name": file_path.name,
            "file_size": file_size,
            "checksum": checksum,
        }
        response = await self._send_with_ack(MsgType.FILE_REQUEST, payload)
        status = int(response.get("status", StatusCode.SUCCESS))
        if status != int(StatusCode.SUCCESS):
            raise ProtocolError(StatusCode(status), message=response.get("error_message", "File request failed"))
        sessions = response.get("sessions")
        if not sessions:
            session_id = response.get("session_id")
            if session_id:
                sessions = [{"session_id": session_id, "target_id": target_id}]
        if not sessions:
            raise ProtocolError(StatusCode.INTERNAL_ERROR, message="No session information returned")
        summary = {"file_name": file_path.name, "file_size": file_size, "sessions": sessions}
        for session in sessions:
            session_id = session["session_id"]
            target = session.get("target_id", target_id)
            context = {
                "session_id": session_id,
                "file_path": file_path,
                "file_name": file_path.name,
                "file_size": file_size,
                "checksum": checksum,
                "target_id": target,
                "direction": "send",
                "bytes_transferred": 0,
            }
            self._sending_sessions[session_id] = context
            self._emit_event(
                {
                    "type": "request_sent",
                    "session_id": session_id,
                    "file_name": file_path.name,
                    "file_size": file_size,
                    "target_id": target,
                }
            )
        return summary

    async def accept_request(self, session_id: str, destination: Path) -> None:
        request = self._pending_requests.pop(session_id, None)
        if not request:
            raise ProtocolError(StatusCode.NOT_FOUND, message="Request not found")
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._receiving_sessions[session_id] = {
            "session_id": session_id,
            "file_name": request["file_name"],
            "file_size": request["file_size"],
            "from_user": request["from_user"],
            "save_path": destination,
            "direction": "receive",
            "bytes_transferred": 0,
        }
        await self._send_with_ack(MsgType.FILE_ACCEPT, {"session_id": session_id})

    async def reject_request(self, session_id: str) -> None:
        if session_id in self._pending_requests:
            await self._send_with_ack(MsgType.FILE_REJECT, {"session_id": session_id})
            self._pending_requests.pop(session_id, None)

    async def notify_complete(self, session_id: str) -> None:
        await self._send_message(MsgType.FILE_COMPLETE, {"session_id": session_id})

    async def notify_error(self, session_id: str, error_message: str) -> None:
        await self._send_message(MsgType.FILE_ERROR, {"session_id": session_id, "error_message": error_message})

    async def _handle_ack(self, message: Dict[str, Any]) -> None:
        future = self._pending_acks.pop(message.get("id"), None)
        if future and not future.done():
            future.set_result(message.get("payload") or {})

    async def _handle_request_event(self, message: Dict[str, Any]) -> None:
        payload = message.get("payload", {}) or {}
        session_id = payload.get("session_id")
        if not session_id:
            return
        self._pending_requests[session_id] = payload
        self._emit_event(
            {
                "type": "incoming_request",
                "session_id": session_id,
                "from_user": payload.get("from_user"),
                "file_name": payload.get("file_name"),
                "file_size": payload.get("file_size"),
            }
        )

    async def _handle_accept_event(self, message: Dict[str, Any]) -> None:
        payload = message.get("payload", {}) or {}
        session_id = payload.get("session_id")
        if not session_id:
            return
        host = payload.get("channel_host") or self.network.host
        if host in (None, "", "0.0.0.0", "::"):
            host = self.network.host
        port = payload.get("channel_port") or self.network.config.get("file_port")
        if session_id in self._sending_sessions:
            asyncio.create_task(self._start_sender_channel(session_id, host, int(port)))
        elif session_id in self._receiving_sessions:
            asyncio.create_task(self._start_receiver_channel(session_id, host, int(port)))

    async def _handle_reject_event(self, message: Dict[str, Any]) -> None:
        payload = message.get("payload", {}) or {}
        session_id = payload.get("session_id")
        if not session_id:
            return
        self._sending_sessions.pop(session_id, None)
        self._pending_requests.pop(session_id, None)
        self._emit_event(
            {"type": "rejected", "session_id": session_id, "from_user": payload.get("from_user")}
        )

    async def _handle_complete_event(self, message: Dict[str, Any]) -> None:
        payload = message.get("payload", {}) or {}
        session_id = payload.get("session_id")
        if not session_id:
            return
        self._emit_event({"type": "completed", "session_id": session_id})
        self._sending_sessions.pop(session_id, None)
        self._receiving_sessions.pop(session_id, None)

    async def _handle_error_event(self, message: Dict[str, Any]) -> None:
        payload = message.get("payload", {}) or {}
        session_id = payload.get("session_id")
        if not session_id:
            return
        error_message = payload.get("error_message", "transfer failed")
        self._emit_event({"type": "failed", "session_id": session_id, "error": error_message})
        self._sending_sessions.pop(session_id, None)
        self._receiving_sessions.pop(session_id, None)

    async def _start_sender_channel(self, session_id: str, host: str, port: int) -> None:
        context = self._sending_sessions.get(session_id)
        if not context:
            return
        file_path = context["file_path"]
        writer = None
        try:
            reader, writer = await self.network.open_file_channel(host, port)
            await self._send_handshake(writer, session_id, "sender")
            with file_path.open("rb") as fp:
                while True:
                    chunk = fp.read(self.chunk_size)
                    if not chunk:
                        break
                    writer.write(encode_chunk(0x01, chunk))
                    await writer.drain()
                    context["bytes_transferred"] += len(chunk)
                    self._emit_event(
                        {
                            "type": "progress",
                            "session_id": session_id,
                            "direction": "send",
                            "bytes": context["bytes_transferred"],
                            "total": context["file_size"],
                        }
                    )
            writer.write(encode_chunk(0x02, b""))
            await writer.drain()
            await self.notify_complete(session_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Sender channel failed for %s: %s", session_id, exc)
            await self.notify_error(session_id, str(exc))
        finally:
            if writer:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

    async def _start_receiver_channel(self, session_id: str, host: str, port: int) -> None:
        context = self._receiving_sessions.get(session_id)
        if not context:
            return
        save_path = context["save_path"]
        writer = None
        try:
            reader, writer = await self.network.open_file_channel(host, port)
            await self._send_handshake(writer, session_id, "receiver")
            with save_path.open("wb") as fp:
                while True:
                    header = await reader.readexactly(5)
                    chunk_type = header[0]
                    length = int.from_bytes(header[1:5], "little")
                    data = await reader.readexactly(length)
                    if chunk_type == 0x01:
                        fp.write(data)
                        context["bytes_transferred"] += len(data)
                        self._emit_event(
                            {
                                "type": "progress",
                                "session_id": session_id,
                                "direction": "receive",
                                "bytes": context["bytes_transferred"],
                                "total": context["file_size"],
                            }
                        )
                    elif chunk_type == 0x02:
                        break
                    elif chunk_type == 0x03:
                        raise RuntimeError("Sender reported failure")
            await self.notify_complete(session_id)
            self._emit_event({"type": "saved", "session_id": session_id, "path": str(save_path)})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Receiver channel failed for %s: %s", session_id, exc)
            await self.notify_error(session_id, str(exc))
        finally:
            if writer:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

    async def _send_with_ack(self, command: MsgType, payload: Dict[str, Any]) -> Dict[str, Any]:
        message = self._build_message(command, payload)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_acks[message["id"]] = future
        schema = validator.load_schema(command.value)
        await self.network.send(message, schema)
        return await asyncio.wait_for(future, timeout=15)

    async def _send_message(self, command: MsgType, payload: Dict[str, Any]) -> None:
        message = self._build_message(command, payload)
        schema = validator.load_schema(command.value)
        await self.network.send(message, schema)

    def _build_message(self, command: MsgType, payload: Dict[str, Any]) -> Dict[str, Any]:
        message = {
            "id": uuid4().hex,
            "type": "request",
            "timestamp": int(time.time()),
            "command": command.value,
            "headers": self.session.build_headers(),
            "payload": payload,
        }
        return message

    async def _send_handshake(self, writer, session_id: str, role: str) -> None:
        handshake = json.dumps(
            {"session_id": session_id, "role": role, "user_id": self.session.user_id or ""}
        ).encode("utf-8") + b"\n"
        writer.write(handshake)
        await writer.drain()

    def _emit_event(self, payload: Dict[str, Any]) -> None:
        try:
            self.ui_queue.put(("file", payload))
        except Exception:
            logger.debug("Failed to enqueue file event", exc_info=True)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        sha = hashlib.sha256()
        with path.open("rb") as fp:
            while True:
                block = fp.read(1024 * 1024)
                if not block:
                    break
                sha.update(block)
        return sha.hexdigest()
