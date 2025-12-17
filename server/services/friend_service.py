"""Friend management service for server."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, TYPE_CHECKING

from shared.protocol.commands import MsgType
from shared.protocol.errors import StatusCode
from shared.protocol import encode_msg

if TYPE_CHECKING:
    from server.core.connection import ConnectionContext
    from server.core.connection_manager import ConnectionManager
    from server.storage.memory import InMemoryRepository

logger = logging.getLogger(__name__)


class FriendService:
    """Manages friend relationships on the server."""

    def __init__(self, connection_manager: ConnectionManager, repository: InMemoryRepository) -> None:
        self.connection_manager = connection_manager
        self.repository = repository

    async def handle_request(self, message: Dict[str, Any], ctx: ConnectionContext) -> Dict[str, Any]:
        """Handle friend/request - send a friend request."""
        user_id = ctx.user_id
        if not user_id:
            return self._error_response(message, StatusCode.UNAUTHORIZED, "Not authenticated")

        payload = message.get("payload", {})
        target_id = payload.get("target_id")
        request_message = payload.get("message", "")

        if not target_id:
            return self._error_response(message, StatusCode.BAD_REQUEST, "Missing target_id")

        # Check if target user exists
        if not self.repository.user_exists(target_id):
            return self._error_response(message, StatusCode.NOT_FOUND, "User not found")

        # Check if already friends
        if self.repository.are_friends(user_id, target_id):
            return self._error_response(message, StatusCode.CONFLICT, "Already friends")

        # Cannot send request to self
        if user_id == target_id:
            return self._error_response(message, StatusCode.BAD_REQUEST, "Cannot send friend request to yourself")

        # Send friend request
        request_id = self.repository.send_friend_request(user_id, target_id, request_message)

        logger.info(f"User {user_id} sent friend request to {target_id}")

        # Notify target user about new friend request
        await self._notify_user(target_id, MsgType.FRIEND_EVENT.value, {
            "event_type": "new_request",
            "from_user": user_id,
            "request_id": request_id,
            "message": request_message
        })

        return self._ok_response(message, MsgType.FRIEND_REQUEST_ACK, {
            "request_id": request_id,
            "message": "Friend request sent"
        })

    async def handle_accept(self, message: Dict[str, Any], ctx: ConnectionContext) -> Dict[str, Any]:
        """Handle friend/accept - accept a friend request."""
        user_id = ctx.user_id
        if not user_id:
            return self._error_response(message, StatusCode.UNAUTHORIZED, "Not authenticated")

        payload = message.get("payload", {})
        request_id = payload.get("request_id")

        if not request_id:
            return self._error_response(message, StatusCode.BAD_REQUEST, "Missing request_id")

        # Get pending requests to verify this user is the recipient
        pending_requests = self.repository.get_pending_friend_requests(user_id)
        request = next((r for r in pending_requests if r["id"] == request_id), None)

        if not request:
            return self._error_response(message, StatusCode.NOT_FOUND, "Friend request not found or already processed")

        from_user = request["from_user"]

        # Accept the request
        success = self.repository.accept_friend_request(request_id)

        if not success:
            return self._error_response(message, StatusCode.SERVER_ERROR, "Failed to accept friend request")

        logger.info(f"User {user_id} accepted friend request from {from_user}")

        # Notify the requester that their request was accepted
        await self._notify_user(from_user, MsgType.FRIEND_EVENT.value, {
            "event_type": "request_accepted",
            "user_id": user_id,
            "request_id": request_id
        })

        return self._ok_response(message, MsgType.FRIEND_ACCEPT_ACK, {
            "friend_id": from_user,
            "message": "Friend request accepted"
        })

    async def handle_reject(self, message: Dict[str, Any], ctx: ConnectionContext) -> Dict[str, Any]:
        """Handle friend/reject - reject a friend request."""
        user_id = ctx.user_id
        if not user_id:
            return self._error_response(message, StatusCode.UNAUTHORIZED, "Not authenticated")

        payload = message.get("payload", {})
        request_id = payload.get("request_id")

        if not request_id:
            return self._error_response(message, StatusCode.BAD_REQUEST, "Missing request_id")

        # Get pending requests to verify this user is the recipient
        pending_requests = self.repository.get_pending_friend_requests(user_id)
        request = next((r for r in pending_requests if r["id"] == request_id), None)

        if not request:
            return self._error_response(message, StatusCode.NOT_FOUND, "Friend request not found or already processed")

        from_user = request["from_user"]

        # Reject the request
        success = self.repository.reject_friend_request(request_id)

        if not success:
            return self._error_response(message, StatusCode.SERVER_ERROR, "Failed to reject friend request")

        logger.info(f"User {user_id} rejected friend request from {from_user}")

        # Notify the requester that their request was rejected
        await self._notify_user(from_user, MsgType.FRIEND_EVENT.value, {
            "event_type": "request_rejected",
            "user_id": user_id,
            "request_id": request_id
        })

        return self._ok_response(message, MsgType.FRIEND_REJECT_ACK, {
            "message": "Friend request rejected"
        })

    async def handle_delete(self, message: Dict[str, Any], ctx: ConnectionContext) -> Dict[str, Any]:
        """Handle friend/delete - delete a friend."""
        user_id = ctx.user_id
        if not user_id:
            return self._error_response(message, StatusCode.UNAUTHORIZED, "Not authenticated")

        payload = message.get("payload", {})
        friend_id = payload.get("friend_id")

        if not friend_id:
            return self._error_response(message, StatusCode.BAD_REQUEST, "Missing friend_id")

        # Check if they are actually friends
        if not self.repository.are_friends(user_id, friend_id):
            return self._error_response(message, StatusCode.NOT_FOUND, "Not friends")

        # Delete the friendship
        success = self.repository.delete_friend(user_id, friend_id)

        if not success:
            return self._error_response(message, StatusCode.SERVER_ERROR, "Failed to delete friend")

        logger.info(f"User {user_id} deleted friend {friend_id}")

        # Notify the other user that they were unfriended
        await self._notify_user(friend_id, MsgType.FRIEND_EVENT.value, {
            "event_type": "friend_deleted",
            "user_id": user_id
        })

        return self._ok_response(message, MsgType.FRIEND_DELETE_ACK, {
            "message": "Friend deleted"
        })

    async def handle_list(self, message: Dict[str, Any], ctx: ConnectionContext) -> Dict[str, Any]:
        """Handle friend/list - get friend list and pending requests."""
        user_id = ctx.user_id
        if not user_id:
            return self._error_response(message, StatusCode.UNAUTHORIZED, "Not authenticated")

        # Get friends list
        friends = self.repository.list_friends(user_id)

        # Get pending friend requests (incoming)
        pending_requests = self.repository.get_pending_friend_requests(user_id)

        # Get sent friend requests
        sent_requests = self.repository.get_sent_friend_requests(user_id)

        logger.info(f"User {user_id} requested friend list: {len(friends)} friends, {len(pending_requests)} pending")

        return self._ok_response(message, MsgType.FRIEND_LIST_ACK, {
            "friends": friends,
            "pending_requests": pending_requests,
            "sent_requests": sent_requests
        })

    async def _notify_user(self, user_id: str, command: str, payload: Dict[str, Any]) -> None:
        """Send event to a specific user."""
        event = {
            "id": uuid.uuid4().hex[:8],
            "type": "event",
            "timestamp": int(time.time()),
            "command": command,
            "headers": {"version": "1.0"},
            "payload": payload
        }

        # Use connection_manager to send to user
        user_ctx = self.connection_manager.get_by_user(user_id)
        if user_ctx and user_ctx.writer:
            try:
                user_ctx.writer.write(encode_msg(event))
                await user_ctx.writer.drain()
                logger.debug(f"Sent {command} to user {user_id}")
            except Exception as e:
                logger.error(f"Failed to send {command} to user {user_id}: {e}")

    def _ok_response(self, request: Dict[str, Any], command: MsgType, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create success response with proper headers."""
        headers = request.get("headers", {}) or {}
        headers.setdefault("version", "1.0")
        return {
            "id": request["id"],
            "type": "response",
            "timestamp": int(time.time()),
            "command": command.value,
            "headers": headers,
            "payload": {"status": int(StatusCode.SUCCESS), **payload},
        }

    def _error_response(self, message: Dict[str, Any], status: StatusCode, error_msg: str) -> Dict[str, Any]:
        """Create error response with proper headers."""
        headers = message.get("headers", {}) or {}
        headers.setdefault("version", "1.0")
        return {
            "id": message.get("id"),
            "type": "response",
            "timestamp": int(time.time()),
            "command": message.get("command", "unknown"),
            "headers": headers,
            "payload": {
                "status": int(status),
                "error_message": error_msg
            }
        }
