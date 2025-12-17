from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from client.core.network import NetworkClient
from client.core.session import ClientSession
from client.storage.local_db import LocalDatabase
from shared.protocol import validator
from shared.protocol.commands import MsgType
from shared.protocol.messages import MessageSendMsg

logger = logging.getLogger(__name__)


class MessagingManager:
    """Messaging feature facade: sending, receiving, persistence."""

    def __init__(self, network: NetworkClient, session: ClientSession, storage: LocalDatabase) -> None:
        self.network = network
        self.session = session
        self.storage = storage
        self._incoming_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        network.register_handler(MsgType.MESSAGE_EVENT, self._handle_event)
        network.register_handler(MsgType.MESSAGE_ACK, self._handle_ack)

    async def send_text(self, conversation_id: str, target_id: str, text: str, reply_to: Optional[Dict[str, Any]] = None) -> None:
        content: Dict[str, Any] = {"type": "text", "text": text}
        if reply_to:
            content["reply_to"] = reply_to
        payload = {
            "conversation_id": conversation_id,
            "target": {"type": "user", "id": target_id},
            "content": content,
        }
        msg = MessageSendMsg(
            type="request",
            timestamp=int(time.time()),
            payload=payload,
            headers=self.session.build_headers(),
        ).model_dump()
        schema = validator.load_schema(msg["command"])
        await self.network.send(msg, schema)
        self.storage.save_outbound_message(msg)

    async def send_room_text(self, room_id: str, text: str, conversation_id: Optional[str] = None, reply_to: Optional[Dict[str, Any]] = None) -> None:
        convo = conversation_id or room_id
        content: Dict[str, Any] = {"type": "text", "text": text}
        if reply_to:
            content["reply_to"] = reply_to
        payload = {
            "conversation_id": convo,
            "target": {"type": "room", "id": room_id},
            "content": content,
        }
        msg = MessageSendMsg(
            type="request",
            timestamp=int(time.time()),
            payload=payload,
            headers=self.session.build_headers(),
        ).model_dump()
        schema = validator.load_schema(msg["command"])
        await self.network.send(msg, schema)
        self.storage.save_outbound_message(msg)

    async def _handle_ack(self, message: Dict[str, Any]) -> None:
        logger.debug("Message ACK: %s", message.get("payload"))

    async def _handle_event(self, message: Dict[str, Any]) -> None:
        await self._incoming_queue.put(message)
        self.storage.save_inbound_message(message)

    async def next_message(self, timeout: Optional[float] = None) -> Optional[Dict[str, Any]]:
        try:
            return await asyncio.wait_for(self._incoming_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
