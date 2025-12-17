"""Voice call service for server."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, Optional, Set, TYPE_CHECKING

from shared.protocol.commands import MsgType
from shared.protocol.errors import StatusCode
from shared.protocol import encode_msg, DEFAULT_VERSION

if TYPE_CHECKING:
    from server.core.connection import ConnectionContext
    from server.core.connection_manager import ConnectionManager
    from server.services.room_service import RoomService

logger = logging.getLogger(__name__)


class VoiceCall:
    """Represents an active voice call session."""

    def __init__(
        self,
        call_id: str,
        initiator_id: str,
        call_type: str,
        target_type: str,
        target_id: str,
    ):
        self.call_id = call_id
        self.initiator_id = initiator_id
        self.call_type = call_type  # "direct" or "group"
        self.target_type = target_type  # "user" or "room"
        self.target_id = target_id
        self.status = "ringing"  # ringing, connected, ended
        self.participants: Set[str] = {initiator_id}
        self.created_at = time.time()
        self.connected_at: Optional[float] = None
        self.ended_at: Optional[float] = None

    def add_participant(self, user_id: str) -> None:
        """Add a participant to the call."""
        self.participants.add(user_id)
        if self.status == "ringing":
            self.status = "connected"
            self.connected_at = time.time()

    def remove_participant(self, user_id: str) -> None:
        """Remove a participant from the call."""
        self.participants.discard(user_id)

    def end(self) -> None:
        """Mark call as ended."""
        self.status = "ended"
        self.ended_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "call_id": self.call_id,
            "initiator": self.initiator_id,
            "call_type": self.call_type,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "status": self.status,
            "participants": list(self.participants),
            "created_at": int(self.created_at),
            "connected_at": int(self.connected_at) if self.connected_at else None,
        }


class VoiceService:
    """Manages voice calls on the server."""

    def __init__(self, connection_manager: ConnectionManager, room_service: RoomService) -> None:
        self.connection_manager = connection_manager
        self.room_service = room_service
        self.active_calls: Dict[str, VoiceCall] = {}
        self.user_to_call: Dict[str, str] = {}  # user_id -> call_id mapping

    async def handle_call(self, message: Dict[str, Any], ctx: ConnectionContext) -> Dict[str, Any]:
        """Handle voice/call request."""
        user_id = ctx.user_id
        if not user_id:
            return self._error_response(message, StatusCode.UNAUTHORIZED, "Not authenticated")

        payload = message.get("payload", {})
        call_type = payload.get("call_type", "direct")
        target = payload.get("target", {})
        target_type = target.get("type")
        target_id = target.get("id")

        logger.info(f"[VOICE] handle_call: caller={user_id}, target_type={target_type}, target_id={target_id}, call_type={call_type}")

        if not target_type or not target_id:
            return self._error_response(message, StatusCode.BAD_REQUEST, "Invalid target")

        # Check if user is already in a call
        if user_id in self.user_to_call:
            return self._error_response(message, StatusCode.CONFLICT, "Already in a call")

        # Create new call
        call_id = message.get("id", uuid.uuid4().hex)
        call = VoiceCall(call_id, user_id, call_type, target_type, target_id)
        self.active_calls[call_id] = call
        self.user_to_call[user_id] = call_id

        logger.info(f"[VOICE] User {user_id} initiated call {call_id} to {target_type}:{target_id}")
        logger.info(f"[VOICE] All online users in connection_manager: {self.connection_manager.get_all_users()}")

        # Send acknowledgment to caller
        ack_response = {
            "id": message.get("id"),
            "type": "response",
            "timestamp": int(time.time()),
            "command": MsgType.VOICE_CALL_ACK.value,
            "headers": {"version": DEFAULT_VERSION},  # 添加headers
            "payload": {
                "status": 200,
                "call_id": call_id,
                "message": "Call initiated"
            }
        }

        # Notify target(s) about incoming call
        if target_type == "user":
            # Direct call to a user
            logger.info(f"[VOICE] Notifying user {target_id} about incoming call from {user_id}")
            await self._notify_user(ctx, target_id, "voice/event", {
                "event_type": "incoming",
                "call_id": call_id,
                "from_user": user_id,
                "call_type": call_type,
                "target": target
            })
        elif target_type == "room":
            # Group call to a room
            members = self._get_room_members(target_id)
            for member in members:
                if member != user_id:
                    await self._notify_user(ctx, member, "voice/event", {
                        "event_type": "incoming",
                        "call_id": call_id,
                        "from_user": user_id,
                        "call_type": "group",
                        "target": target
                    })

        return ack_response

    async def handle_answer(self, message: Dict[str, Any], ctx: ConnectionContext) -> Dict[str, Any]:
        """Handle voice/answer request."""
        user_id = ctx.user_id
        if not user_id:
            return self._error_response(message, StatusCode.UNAUTHORIZED, "Not authenticated")

        payload = message.get("payload", {})
        call_id = payload.get("call_id")

        call = self.active_calls.get(call_id)
        if not call:
            return self._error_response(message, StatusCode.NOT_FOUND, "Call not found")

        # 对于群聊通话，允许在connected状态下继续加入
        # 对于私聊通话，只能在ringing状态接听
        if call.call_type == "direct" and call.status != "ringing":
            return self._error_response(message, StatusCode.CONFLICT, "Call already answered or ended")

        if call.call_type == "group" and call.status == "ended":
            return self._error_response(message, StatusCode.CONFLICT, "Call has ended")

        # 保存旧状态，用于判断发送什么事件
        was_ringing = call.status == "ringing"

        # Add user to call（这会改变状态为connected）
        call.add_participant(user_id)
        self.user_to_call[user_id] = call_id

        logger.info(f"User {user_id} joined call {call_id}, total participants: {len(call.participants)}")

        # Notify all participants
        # 如果是从ringing变为connected（第一人接听），发送connected事件
        # 否则发送member_joined事件（后续人加入）
        event_type = "connected" if was_ringing else "member_joined"

        for participant in call.participants:
            await self._notify_user(ctx, participant, "voice/event", {
                "event_type": event_type,
                "call_id": call_id,
                "user_id": user_id,
                "members": list(call.participants)
            })

        return {
            "id": message.get("id"),
            "type": "response",
            "timestamp": int(time.time()),
            "command": MsgType.VOICE_ANSWER_ACK.value,
            "headers": {"version": DEFAULT_VERSION},
            "payload": {
                "status": 200,
                "call_id": call_id,
                "message": "Call connected"
            }
        }

    async def handle_reject(self, message: Dict[str, Any], ctx: ConnectionContext) -> Dict[str, Any]:
        """Handle voice/reject request."""
        user_id = ctx.user_id
        if not user_id:
            return self._error_response(message, StatusCode.UNAUTHORIZED, "Not authenticated")

        payload = message.get("payload", {})
        call_id = payload.get("call_id")

        call = self.active_calls.get(call_id)
        if not call:
            return self._error_response(message, StatusCode.NOT_FOUND, "Call not found")

        logger.info(f"User {user_id} rejected call {call_id}")

        # Notify caller about rejection
        await self._notify_user(ctx, call.initiator_id, "voice/event", {
            "event_type": "rejected",
            "call_id": call_id,
            "by_user": user_id
        })

        # For direct calls, end the call immediately
        if call.call_type == "direct":
            call.end()
            self._cleanup_call(call_id)

        return {
            "id": message.get("id"),
            "type": "response",
            "timestamp": int(time.time()),
            "command": MsgType.VOICE_REJECT_ACK.value,
            "headers": {"version": DEFAULT_VERSION},  # 添加headers
            "payload": {
                "status": 200,
                "call_id": call_id
            }
        }

    async def handle_end(self, message: Dict[str, Any], ctx: ConnectionContext) -> Dict[str, Any]:
        """Handle voice/end request."""
        user_id = ctx.user_id
        if not user_id:
            return self._error_response(message, StatusCode.UNAUTHORIZED, "Not authenticated")

        payload = message.get("payload", {})
        call_id = payload.get("call_id")

        call = self.active_calls.get(call_id)
        if not call:
            return self._error_response(message, StatusCode.NOT_FOUND, "Call not found")

        if user_id not in call.participants:
            return self._error_response(message, StatusCode.FORBIDDEN, "Not in this call")

        logger.info(f"[VOICE] User {user_id} ended call {call_id}")
        logger.info(f"[VOICE] Call participants before removal: {list(call.participants)}")
        logger.info(f"[VOICE] Call type: {call.call_type}")

        # 保存所有参与者列表（包括即将退出的用户），用于发送ended事件
        all_participants = list(call.participants)

        # Remove user from call
        call.remove_participant(user_id)
        self.user_to_call.pop(user_id, None)

        logger.info(f"[VOICE] Remaining participants after removal: {list(call.participants)}")

        # If group call and still has participants, notify others
        if call.call_type == "group" and len(call.participants) > 0:
            logger.info(f"[VOICE] Group call with remaining participants, notifying member_left")
            for participant in call.participants:
                await self._notify_user(ctx, participant, "voice/event", {
                    "event_type": "member_left",
                    "call_id": call_id,
                    "user_id": user_id,
                    "members": list(call.participants)
                })
        else:
            # Direct call or last participant left, end the call
            # 使用保存的all_participants列表，包括刚退出的用户
            logger.info(f"[VOICE] Direct call or last participant, sending 'ended' to {len(all_participants)} participant(s)")

            # 计算通话时长
            duration = 0
            if call.connected_at:
                duration = int(time.time() - call.connected_at)

            # 准备完整的通话结束信息
            call_end_payload = {
                "event_type": "ended",
                "call_id": call_id,
                "call_type": call.call_type,
                "target_type": call.target_type,
                "target_id": call.target_id,
                "participants": all_participants,
                "duration": duration,
                "initiator": call.initiator_id
            }

            for participant in all_participants:
                logger.info(f"[VOICE] Sending 'ended' event to participant: {participant}")
                await self._notify_user(ctx, participant, "voice/event", call_end_payload)

            call.end()
            self._cleanup_call(call_id)
            logger.info(f"[VOICE] Call {call_id} has been cleaned up")

        return {
            "id": message.get("id"),
            "type": "response",
            "timestamp": int(time.time()),
            "command": MsgType.VOICE_END_ACK.value,
            "headers": {"version": DEFAULT_VERSION},  # 添加headers
            "payload": {
                "status": 200,
                "call_id": call_id
            }
        }

    async def handle_voice_data(self, message: Dict[str, Any], ctx: ConnectionContext) -> Optional[Dict[str, Any]]:
        """Handle voice/data (audio packets)."""
        user_id = ctx.user_id
        if not user_id:
            return None

        payload = message.get("payload", {})
        call_id = payload.get("call_id")

        call = self.active_calls.get(call_id)
        if not call or user_id not in call.participants:
            return None

        # Forward audio data to all other participants
        for participant in call.participants:
            if participant != user_id:
                await self._notify_user(ctx, participant, "voice/data", payload)

        # No response needed for data packets
        return None

    async def _notify_user(self, ctx: ConnectionContext, user_id: str, command: str, payload: Dict[str, Any]) -> None:
        """Send event to a specific user."""
        event = {
            "id": uuid.uuid4().hex[:8],
            "type": "event",
            "timestamp": int(time.time()),
            "command": command,
            "headers": {"version": DEFAULT_VERSION},  # 修复：添加headers
            "payload": payload
        }

        # Use connection_manager to send to user
        user_ctx = self.connection_manager.get_by_user(user_id)
        logger.info(f"[VOICE] Trying to notify user {user_id} with command {command}, user_ctx found: {user_ctx is not None}")
        if user_ctx and user_ctx.writer:
            try:
                logger.info(f"[VOICE] Sending {command} event to user {user_id}: {payload}")
                user_ctx.writer.write(encode_msg(event))
                await user_ctx.writer.drain()
                logger.info(f"[VOICE] Successfully sent {command} to user {user_id}")
            except Exception as e:
                logger.error(f"[VOICE] Failed to send {command} to user {user_id}: {e}")
        else:
            logger.warning(f"[VOICE] User {user_id} not found or no writer available")

    def _get_room_members(self, room_id: str) -> list[str]:
        """Get members of a room."""
        try:
            # 使用 repository 直接获取房间成员列表
            from server.storage.memory import InMemoryRepository
            # 通过 connection_manager 获取 repository（实际使用时应从构造函数传入）
            # 或者直接使用 room_service.repository
            members = self.room_service.repository.list_room_members(room_id)
            return members
        except Exception as e:
            logger.error(f"Failed to get room members for {room_id}: {e}")
            return []

    def _cleanup_call(self, call_id: str) -> None:
        """Remove call from active calls and user mappings."""
        call = self.active_calls.pop(call_id, None)
        if call:
            for user_id in call.participants:
                self.user_to_call.pop(user_id, None)
            logger.info(f"Call {call_id} cleaned up")

    def _error_response(self, message: Dict[str, Any], status: StatusCode, error_msg: str) -> Dict[str, Any]:
        """Create error response."""
        return {
            "id": message.get("id"),
            "type": "response",
            "timestamp": int(time.time()),
            "command": message.get("command", "unknown"),
            "headers": {"version": DEFAULT_VERSION},  # 添加headers
            "payload": {
                "status": int(status),
                "error_message": error_msg
            }
        }

    def get_user_call(self, user_id: str) -> Optional[VoiceCall]:
        """Get the call a user is currently in."""
        call_id = self.user_to_call.get(user_id)
        return self.active_calls.get(call_id) if call_id else None

    def get_call(self, call_id: str) -> Optional[VoiceCall]:
        """Get call by ID."""
        return self.active_calls.get(call_id)

    async def user_disconnected(self, user_id: str, ctx: ConnectionContext) -> None:
        """Handle user disconnection, end their active call."""
        call_id = self.user_to_call.get(user_id)
        if call_id:
            call = self.active_calls.get(call_id)
            if call:
                logger.info(f"User {user_id} disconnected, ending call {call_id}")
                await self.handle_end(
                    {
                        "id": uuid.uuid4().hex,
                        "command": MsgType.VOICE_END.value,
                        "payload": {"call_id": call_id}
                    },
                    ctx
                )
