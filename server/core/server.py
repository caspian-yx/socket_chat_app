from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Dict, Optional

from shared.protocol import DEFAULT_VERSION, encode_msg, framing, validator
from shared.protocol.commands import MsgType
from shared.protocol.errors import ProtocolError

from .connection import ConnectionContext
from .connection_manager import ConnectionManager
from .router import CommandRouter

logger = logging.getLogger(__name__)

DisconnectCallback = Callable[[ConnectionContext], Awaitable[None]]

ERROR_COMMAND_MAP = {
    MsgType.AUTH_LOGIN.value: MsgType.AUTH_LOGIN_ACK.value,
    MsgType.AUTH_REFRESH.value: MsgType.AUTH_REFRESH_ACK.value,
    MsgType.MESSAGE_SEND.value: MsgType.MESSAGE_ACK.value,
}


class SocketServer:
    def __init__(
        self,
        host: str,
        port: int,
        router: CommandRouter,
        connection_manager: ConnectionManager,
        on_disconnect: Optional[DisconnectCallback] = None
    ) -> None:
        self.host = host
        self.port = port
        self.router = router
        self.connection_manager = connection_manager
        self.on_disconnect = on_disconnect
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        logger.info("Server listening on %s:%s", self.host, self.port)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        ctx = ConnectionContext(reader=reader, writer=writer, peername=str(writer.get_extra_info("peername")))
        self.connection_manager.register(writer, ctx)
        try:
            while True:
                message: Dict[str, Any] = {}
                try:
                    message = await framing.async_decode_msg(reader)
                    schema = validator.load_schema(message.get("command", ""))
                    validator.validate_msg(message, schema)
                    ctx.touch()
                    response = await self.router.dispatch(message, ctx)
                    if response:
                        await self._send(writer, response)
                except ProtocolError as exc:
                    logger.warning("Protocol error for %s: %s", ctx.peername, exc)
                    await self._send(writer, _error_response(message, exc))
                except asyncio.IncompleteReadError:
                    raise
        except asyncio.IncompleteReadError:
            logger.info("Client %s disconnected", ctx.peername)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError) as exc:
            # Client disconnected unexpectedly (e.g., during voice call end)
            logger.info("Client %s connection reset: %s", ctx.peername, exc)
        except Exception as exc:
            logger.exception("Unhandled error: %s", exc)
        finally:
            # Call disconnect callback before cleanup
            if self.on_disconnect and ctx.user_id:
                try:
                    await self.on_disconnect(ctx)
                except Exception as e:
                    logger.error("Error in disconnect callback for %s: %s", ctx.user_id, e)

            try:
                writer.close()
                await writer.wait_closed()
            except Exception as e:
                logger.debug("Error during writer cleanup: %s", e)
            finally:
                self.connection_manager.unregister(writer)

    async def _send(self, writer: asyncio.StreamWriter, message: Dict[str, Any]) -> None:
        writer.write(encode_msg(message))
        await writer.drain()


def _error_response(request: Dict[str, Any], error: ProtocolError) -> Dict[str, Any]:
    req = request or {}
    headers = (req.get("headers") or {}).copy()
    headers.setdefault("version", DEFAULT_VERSION)
    command_key = req.get("command")
    command = ERROR_COMMAND_MAP.get(command_key, command_key)
    return {
        "id": req.get("id"),
        "type": "response",
        "timestamp": req.get("timestamp"),
        "command": command,
        "headers": headers,
        "payload": error.to_payload(),
    }
