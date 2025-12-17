from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any, Dict, Optional, TYPE_CHECKING

from server.core.connection_manager import ConnectionManager
from server.storage.memory import InMemoryRepository
from shared.protocol import DEFAULT_VERSION
from shared.protocol.commands import MsgType
from shared.protocol.errors import ErrorCode, ProtocolError, StatusCode

if TYPE_CHECKING:
    from server.workers.offline import OfflineDispatcher
    from server.services.presence_service import PresenceService


class AuthService:
    def __init__(
        self,
        repository: InMemoryRepository,
        connection_manager: ConnectionManager,
        offline_dispatcher: Optional["OfflineDispatcher"] = None,
        presence_service: Optional["PresenceService"] = None,
    ) -> None:
        self.repository = repository
        self.connection_manager = connection_manager
        self.offline_dispatcher = offline_dispatcher
        self.presence_service = presence_service

    async def handle_login(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        payload = message.get("payload", {})
        username = payload.get("username")
        password = payload.get("password")
        if not username or not password:
            raise ProtocolError(StatusCode.BAD_REQUEST, message="Missing credentials")

        user = self.repository.get_user(username)
        if not user or user["password"] != password:
            return self._login_response(
                message,
                status=StatusCode.UNAUTHORIZED,
                token="",
                user_id="",
                expires=0,
                error="Invalid credentials",
                code=ErrorCode.INVALID_TOKEN,
            )

        token = secrets.token_hex(16)
        self.repository.store_session(token, username)
        ctx.mark_authenticated(username, token)
        self.connection_manager.bind_user(ctx)
        self.repository.update_presence(username, "online")
        self._notify_online(username)
        self._broadcast_presence(username, "online")

        return self._login_response(
            message,
            status=StatusCode.SUCCESS,
            token=token,
            user_id=username,
            expires=3600,
        )

    async def handle_logout(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        token = ctx.session_token
        user_id_to_broadcast = ctx.user_id  # 保存用户ID用于广播
        if token:
            self.repository.delete_session(token)
        ctx.session_token = None
        if ctx.user_id:
            self.connection_manager.unbind_user(ctx)
            self.repository.update_presence(ctx.user_id, "offline")
        ctx.user_id = None

        # 广播用户离线状态
        if user_id_to_broadcast:
            self._broadcast_presence(user_id_to_broadcast, "offline")

        headers = message.get("headers", {}) or {"version": DEFAULT_VERSION}
        headers.setdefault("version", DEFAULT_VERSION)
        return {
            "id": message["id"],
            "type": "response",
            "timestamp": int(time.time()),
            "command": MsgType.AUTH_LOGOUT.value,
            "headers": headers,
            "payload": {"status": int(StatusCode.SUCCESS)},
        }

    async def handle_refresh(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        if not ctx.session_token or not ctx.user_id:
            return self._login_response(
                message,
                status=StatusCode.UNAUTHORIZED,
                token="",
                user_id="",
                expires=0,
                error="Not authenticated",
                code=ErrorCode.INVALID_TOKEN,
                command=MsgType.AUTH_REFRESH_ACK.value,
            )
        new_token = secrets.token_hex(16)
        self.repository.store_session(new_token, ctx.user_id)
        self.repository.delete_session(ctx.session_token)
        ctx.mark_authenticated(ctx.user_id, new_token)
        self.connection_manager.bind_user(ctx)
        self._notify_online(ctx.user_id)
        return self._login_response(
            message,
            status=StatusCode.SUCCESS,
            token=new_token,
            user_id=ctx.user_id,
            expires=3600,
            command=MsgType.AUTH_REFRESH_ACK.value,
        )

    async def handle_register(self, message: Dict[str, Any], ctx) -> Dict[str, Any]:
        payload = message.get("payload", {})
        username = payload.get("username")
        password = payload.get("password")
        if not username or not password:
            raise ProtocolError(StatusCode.BAD_REQUEST, message="Missing credentials")
        if self.repository.user_exists(username):
            return self._login_response(
                message,
                status=StatusCode.CONFLICT,
                token="",
                user_id="",
                expires=0,
                error="User already exists",
                code=ErrorCode.USER_EXISTS,
                command=MsgType.AUTH_REGISTER_ACK.value,
            )
        try:
            self.repository.create_user(username, password)
        except ValueError:
            return self._login_response(
                message,
                status=StatusCode.CONFLICT,
                token="",
                user_id="",
                expires=0,
                error="User already exists",
                code=ErrorCode.USER_EXISTS,
                command=MsgType.AUTH_REGISTER_ACK.value,
            )

        # 注册成功：只创建账号，不设置在线状态、不广播上线
        # 用户需要通过登录才能正式上线
        return self._login_response(
            message,
            status=StatusCode.SUCCESS,
            token="",  # 不返回token
            user_id=username,  # 返回用户ID以便客户端显示
            expires=0,
            command=MsgType.AUTH_REGISTER_ACK.value,
        )

    def _login_response(
        self,
        message: Dict[str, Any],
        *,
        status: StatusCode,
        token: str,
        user_id: str,
        expires: int,
        error: str | None = None,
        code: ErrorCode | None = None,
        command: str | None = None,
    ) -> Dict[str, Any]:
        headers = (message.get("headers") or {}).copy()
        headers.setdefault("version", DEFAULT_VERSION)
        payload: Dict[str, Any] = {
            "status": int(status),
            "token": token,
            "user_id": user_id,
            "expires_in": expires,
        }
        if error:
            payload["error_message"] = error
        if code:
            payload["error_code"] = int(code)
        return {
            "id": message.get("id"),
            "type": "response",
            "timestamp": int(time.time()),
            "command": command or MsgType.AUTH_LOGIN_ACK.value,
            "headers": headers,
            "payload": payload,
        }

    def _notify_online(self, user_id: Optional[str]) -> None:
        if user_id and self.offline_dispatcher:
            self.offline_dispatcher.notify_user_online(user_id)

    def _broadcast_presence(self, user_id: str, state: str) -> None:
        """广播用户在线状态变化给所有在线用户"""
        if not self.presence_service:
            return

        # 生成presence事件消息
        event_message = self.presence_service.broadcast_event(user_id, state)

        # 广播给所有在线用户（除了自己）
        online_users = self.connection_manager.get_all_users()
        for online_user in online_users:
            if online_user != user_id:  # 不发给自己
                # 使用asyncio创建异步任务发送
                asyncio.create_task(
                    self.connection_manager.send_to_user(online_user, event_message)
                )
