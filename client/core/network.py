from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Dict, Optional, Union

from client.config import CLIENT_CONFIG
from shared.protocol import DEFAULT_VERSION, framing, validator
from shared.protocol.commands import MsgType, normalize_command
from shared.protocol.errors import ProtocolError, StatusCode
from shared.protocol.messages import HeartbeatMsg

logger = logging.getLogger(__name__)

MessageHandler = Callable[[Dict[str, Any]], Awaitable[None]]


class NetworkError(ProtocolError):
    """Network level error surfaced to higher layers."""

    pass


class NetworkClient:
    """TCP client that handles reconnect, heartbeat, and basic routing."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or CLIENT_CONFIG
        self.host: str = self.config["server_host"]
        self.port: int = int(self.config["server_port"])
        self.heartbeat_interval: int = int(self.config["heartbeat_interval"])
        self.backoff: int = int(self.config["reconnect_backoff"])
        self.max_backoff: int = int(self.config["max_reconnect_backoff"])
        self.max_retries: int = int(self.config["max_reconnect_retries"])
        self.encryption_key: str = self.config.get("encryption_key") or ""

        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.connected: bool = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._handlers: Dict[str, MessageHandler] = {}

    async def connect(self) -> None:
        if self.connected:
            return

        retries = 0
        delay = self.backoff
        while retries <= self.max_retries:
            try:
                self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
                self.connected = True
                logger.info("Connected to %s:%s", self.host, self.port)
                self._receive_task = asyncio.create_task(self._receive_loop(), name="client-recv-loop")
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="client-heartbeat")
                return
            except (OSError, asyncio.TimeoutError) as exc:
                retries += 1
                logger.warning("Connect attempt %s failed: %s", retries, exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.max_backoff)
        raise NetworkError(StatusCode.INTERNAL_ERROR, message="Exceeded max reconnect attempts")

    async def close(self) -> None:
        self.connected = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._receive_task:
            self._receive_task.cancel()
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        logger.info("Network client closed")

    async def send(self, message: Dict[str, Any], schema: Optional[dict] = None) -> None:
        if not self.connected:
            await self.connect()
        try:
            validator.validate_msg(message, schema)
            payload = framing.encode_msg(message)
            assert self.writer is not None
            self.writer.write(payload)
            await self.writer.drain()
            logger.debug("Sent message %s (%s)", message.get("id"), message.get("command"))
        except ProtocolError:
            raise
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as exc:
            # 连接已断开，标记为未连接但不抛出异常（让调用者决定如何处理）
            logger.warning(f"Connection lost during send: {exc}")
            self.connected = False
            raise NetworkError(StatusCode.INTERNAL_ERROR, message=f"Connection lost: {exc}") from exc
        except Exception as exc:
            # 其他错误，记录但不立即标记为未连接（可能是临时错误）
            logger.warning(f"Send failed: {exc}")
            raise NetworkError(StatusCode.INTERNAL_ERROR, message=f"Send failed: {exc}") from exc

    def register_handler(self, command: Union[str, MsgType], handler: MessageHandler) -> None:
        command_text = normalize_command(command)
        self._handlers[command_text] = handler

    async def _receive_loop(self) -> None:
        assert self.reader is not None
        while True:
            try:
                raw = await framing.async_decode_msg(self.reader)
                if not raw:
                    raise EOFError("server closed connection")
                schema = validator.load_schema(raw.get("command", ""))
                validator.validate_msg(raw, schema)
                await self._dispatch(raw)
            except asyncio.CancelledError:
                break
            except ProtocolError as exc:
                logger.warning("Protocol error: %s", exc)
            except (EOFError, ConnectionError, OSError) as exc:
                logger.error("Receive loop terminated: %s", exc)
                self.connected = False
                break

    async def _dispatch(self, msg: Dict[str, Any]) -> None:
        command = msg.get("command")
        if command in self._handlers:
            try:
                await self._handlers[command](msg)
            except Exception as exc:
                logger.exception("Handler error for %s: %s", command, exc)
        else:
            logger.debug("No handler registered for %s", command)

    async def _heartbeat_loop(self) -> None:
        while True:
            if not self.connected:
                break
            try:
                heartbeat = HeartbeatMsg(
                    type="event",
                    command=MsgType.PRESENCE_HEARTBEAT,
                    payload={"seq": uuid.uuid4().hex, "ts": int(time.time())},
                    headers={"version": DEFAULT_VERSION},
                ).model_dump()
                await self.send(heartbeat, schema=validator.load_schema(heartbeat["command"]))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Heartbeat failed: %s", exc)
                await asyncio.sleep(self.heartbeat_interval)
                continue
            await asyncio.sleep(self.heartbeat_interval)

    async def open_file_channel(self, host: Optional[str] = None, port: Optional[int] = None):
        """Open separate TCP channel for binary/file transfer."""
        try:
            return await asyncio.open_connection(host or self.host, port or self.config["file_port"])
        except Exception as exc:
            raise NetworkError(StatusCode.INTERNAL_ERROR, message=f"File channel failed: {exc}") from exc
