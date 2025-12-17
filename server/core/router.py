from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Dict, Optional, TYPE_CHECKING

from shared.protocol.commands import MsgType, normalize_command

if TYPE_CHECKING:
    from .connection import ConnectionContext

Handler = Callable[[Dict[str, Any], "ConnectionContext"], Awaitable[Optional[Dict[str, Any]]]]


class CommandRouter:
    """Maps protocol commands to async handlers."""

    def __init__(self) -> None:
        self._handlers: Dict[str, Handler] = {}

    def register(self, command: MsgType, handler: Handler) -> None:
        self._handlers[normalize_command(command)] = handler

    async def dispatch(self, message: Dict[str, Any], ctx: "ConnectionContext") -> Optional[Dict[str, Any]]:
        command = message.get("command")
        handler = self._handlers.get(command)
        if handler:
            return await handler(message, ctx)
        return None
