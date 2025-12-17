"""Friend management feature for client."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, Optional, TYPE_CHECKING

from shared.protocol.commands import MsgType

if TYPE_CHECKING:
    from client.core import NetworkClient, ClientSession


class FriendsManager:
    """Manages friend relationships on the client side."""

    def __init__(self, network: NetworkClient, session: ClientSession) -> None:
        self.network = network
        self.session = session
        self._friends: list[str] = []
        self._pending_requests: list[Dict[str, Any]] = []
        self._sent_requests: list[Dict[str, Any]] = []
        self._pending: Dict[str, asyncio.Future] = {}

        # Register handlers for friend-related responses (ACK commands)
        for command in (
            MsgType.FRIEND_REQUEST_ACK,
            MsgType.FRIEND_ACCEPT_ACK,
            MsgType.FRIEND_REJECT_ACK,
            MsgType.FRIEND_DELETE_ACK,
            MsgType.FRIEND_LIST_ACK,
        ):
            self.network.register_handler(command, self._handle_response)

    async def _request(self, command: MsgType, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send a request and wait for response."""
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
        await self.network.send(msg)
        response = await asyncio.wait_for(future, timeout=10.0)
        return response.get("payload", {})

    async def _handle_response(self, message: Dict[str, Any]) -> None:
        """Handle response messages."""
        msg_id = message.get("id")
        if msg_id and msg_id in self._pending:
            future = self._pending.pop(msg_id)
            if not future.done():
                future.set_result(message)

    async def send_friend_request(self, target_id: str, message: str = "") -> Dict[str, Any]:
        """Send a friend request to another user."""
        return await self._request(
            MsgType.FRIEND_REQUEST,
            {
                "target_id": target_id,
                "message": message
            }
        )

    async def accept_friend_request(self, request_id: int) -> Dict[str, Any]:
        """Accept a friend request."""
        response = await self._request(
            MsgType.FRIEND_ACCEPT,
            {
                "request_id": request_id
            }
        )
        # Refresh friend list after accepting
        await self.refresh_friends()
        return response

    async def reject_friend_request(self, request_id: int) -> Dict[str, Any]:
        """Reject a friend request."""
        response = await self._request(
            MsgType.FRIEND_REJECT,
            {
                "request_id": request_id
            }
        )
        # Refresh friend list after rejecting
        await self.refresh_friends()
        return response

    async def delete_friend(self, friend_id: str) -> Dict[str, Any]:
        """Delete a friend."""
        response = await self._request(
            MsgType.FRIEND_DELETE,
            {
                "friend_id": friend_id
            }
        )
        # Refresh friend list after deleting
        await self.refresh_friends()
        return response

    async def refresh_friends(self) -> Dict[str, Any]:
        """Refresh friend list and pending requests."""
        payload = await self._request(MsgType.FRIEND_LIST, {})

        self._friends = payload.get("friends", [])
        self._pending_requests = payload.get("pending_requests", [])
        self._sent_requests = payload.get("sent_requests", [])

        return {
            "friends": self._friends,
            "pending_requests": self._pending_requests,
            "sent_requests": self._sent_requests
        }

    def get_friends(self) -> list[str]:
        """Get cached friend list."""
        return self._friends

    def get_pending_requests(self) -> list[Dict[str, Any]]:
        """Get cached pending friend requests."""
        return self._pending_requests

    def get_sent_requests(self) -> list[Dict[str, Any]]:
        """Get cached sent friend requests."""
        return self._sent_requests

    def is_friend(self, user_id: str) -> bool:
        """Check if a user is in friend list."""
        return user_id in self._friends
