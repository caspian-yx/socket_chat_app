from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class SessionConnection:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    user_id: str


@dataclass
class FileSessionState:
    session_id: str
    sender_id: str
    receiver_id: str
    sender: Optional[SessionConnection] = None
    receiver: Optional[SessionConnection] = None
    bridge_task: Optional[asyncio.Task] = None


CompletionCallback = Optional[Callable[[str], Awaitable[None]]]


class FileTransferServer:
    """Dedicated TCP server that bridges file chunks between sender and receiver."""

    def __init__(
        self,
        host: str,
        port: int,
        on_complete: CompletionCallback = None,
        on_error: CompletionCallback = None,
    ) -> None:
        self.host = host
        self.port = port
        self._server: Optional[asyncio.AbstractServer] = None
        self._sessions: Dict[str, FileSessionState] = {}
        self._on_complete = on_complete
        self._on_error = on_error

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        logger.info("File transfer server listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for session in list(self._sessions.values()):
            self._close_connection(session.sender)
            self._close_connection(session.receiver)
        self._sessions.clear()

    def prepare_session(self, session_id: str, sender_id: str, receiver_id: str) -> None:
        state = FileSessionState(session_id=session_id, sender_id=sender_id, receiver_id=receiver_id)
        self._sessions[session_id] = state
        logger.debug("Prepared file session %s from %s to %s", session_id, sender_id, receiver_id)

    def set_callbacks(self, on_complete: CompletionCallback = None, on_error: CompletionCallback = None) -> None:
        if on_complete is not None:
            self._on_complete = on_complete
        if on_error is not None:
            self._on_error = on_error

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        addr = writer.get_extra_info("peername")
        try:
            header = await reader.readline()
            data = json.loads(header.decode("utf-8"))
            session_id = data.get("session_id")
            role = data.get("role")
            user_id = data.get("user_id")
            if not session_id or role not in {"sender", "receiver"} or not user_id:
                raise ValueError("Invalid handshake")
            session = self._sessions.get(session_id)
            if not session:
                raise ValueError("Unknown session")
            if role == "sender" and user_id != session.sender_id:
                raise ValueError("Sender mismatch")
            if role == "receiver" and user_id != session.receiver_id:
                raise ValueError("Receiver mismatch")
            conn = SessionConnection(reader=reader, writer=writer, user_id=user_id)
            if role == "sender":
                session.sender = conn
            else:
                session.receiver = conn
            logger.debug("File session %s registered %s connection from %s", session_id, role, addr)
            if session.sender and session.receiver and not session.bridge_task:
                session.bridge_task = asyncio.create_task(self._bridge(session))
        except Exception as exc:
            logger.warning("File transfer handshake failed from %s: %s", addr, exc)
            writer.close()
            await writer.wait_closed()

    async def _bridge(self, session: FileSessionState) -> None:
        sender = session.sender
        receiver = session.receiver
        if not sender or not receiver:
            return
        logger.info("Starting file bridge for session %s", session.session_id)
        try:
            while True:
                chunk = await sender.reader.read(64 * 1024)
                if not chunk:
                    break
                receiver.writer.write(chunk)
                await receiver.writer.drain()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("File bridge error for %s: %s", session.session_id, exc)
            if self._on_error:
                await self._on_error(session.session_id)
        else:
            if self._on_complete:
                await self._on_complete(session.session_id)
        finally:
            self._close_connection(sender)
            self._close_connection(receiver)
            self._sessions.pop(session.session_id, None)
            logger.info("File session %s closed", session.session_id)

    @staticmethod
    def _close_connection(conn: Optional[SessionConnection]) -> None:
        if conn and not conn.writer.is_closing():
            conn.writer.close()
