from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Dict, List, Optional

from client.core.network import NetworkClient
from client.core.session import ClientSession
from shared.protocol import validator
from shared.protocol.commands import MsgType

logger = logging.getLogger(__name__)


class PresenceManager:
    """Track presence list / subscribe to events."""

    def __init__(self, network: NetworkClient, session: ClientSession) -> None:
        self.network = network
        self.session = session
        self._presence_event = asyncio.Event()
        self._latest_roster: List[str] = []
        network.register_handler(MsgType.PRESENCE_EVENT, self._handle_event)
        network.register_handler(MsgType.PRESENCE_LIST, self._handle_list_response)

    async def request_roster(self) -> List[str]:
        self._presence_event.clear()
        request = {
            "id": uuid.uuid4().hex,
            "type": "request",
            "timestamp": self._timestamp(),
            "command": MsgType.PRESENCE_LIST.value,
            "headers": self.session.build_headers(),
            "payload": {},
        }
        schema = validator.load_schema(request["command"])
        await self.network.send(request, schema)
        try:
            await asyncio.wait_for(self._presence_event.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
        return self._latest_roster

    async def _handle_event(self, message: Dict) -> None:
        payload = message.get("payload", {})
        roster = payload.get("users")
        if roster:
            self._latest_roster = roster
            self._presence_event.set()

    async def _handle_list_response(self, message: Dict) -> None:
        payload = message.get("payload", {})
        users = payload.get("users")
        if isinstance(users, list):
            self._latest_roster = users
            self._presence_event.set()

    @staticmethod
    def _timestamp() -> int:
        import time

        return int(time.time())
