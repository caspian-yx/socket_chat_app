from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, Optional

from client.config import CLIENT_CONFIG
from shared.protocol import DEFAULT_VERSION
from shared.protocol.commands import MsgType
from shared.protocol.errors import ProtocolError, StatusCode
from shared.protocol.messages import AuthAckMsg

logger = logging.getLogger(__name__)


class SessionError(ProtocolError):
    pass


class ClientSession:
    """Holds authenticated session state and header helpers."""

    def __init__(self, network_client: "NetworkClient", config: Optional[Dict[str, Any]] = None) -> None:
        self.network = network_client
        self.config = config or CLIENT_CONFIG
        self.user_id: Optional[str] = None
        self.token: Optional[str] = None
        self.expires_at: float = 0.0
        self.online: bool = False
        self.registration_success: bool = False  # 标记注册是否成功
        self.client_info: Dict[str, str] = {"device": "windows", "version": "cli-0.1.0"}
        self.token_ttl: int = int(self.config.get("token_expiry", 3600))
        self._refresh_lock = asyncio.Lock()

    def build_headers(self, require_auth: bool = True, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        headers = {"version": DEFAULT_VERSION, "client": self.client_info["version"]}
        if require_auth:
            if not self.is_token_valid():
                raise SessionError(StatusCode.UNAUTHORIZED, message="Session expired or not authenticated")
            headers["Authorization"] = f"Bearer {self.token}"
        if extra:
            headers.update(extra)
        return headers

    def attach_headers(self, message: Dict[str, Any], require_auth: bool = True) -> Dict[str, Any]:
        msg = {**message}
        msg["headers"] = self.build_headers(require_auth=require_auth, extra=message.get("headers"))
        return msg

    async def set_authenticated(self, ack_message: Dict[str, Any]) -> None:
        ack = AuthAckMsg.from_dict(ack_message)
        payload = ack.payload
        if payload.status != int(StatusCode.SUCCESS):
            raise SessionError(StatusCode(payload.status), message=payload.error_message or "Login failed")

        # 检查token是否有效（注册响应可能不包含token）
        if not payload.token:
            # 注册成功但没有token，不设置在线状态
            self.online = False
            self.registration_success = True  # 标记注册成功
            self.user_id = payload.user_id  # 保存用户ID
            logger.info("Registration successful for %s, please login", payload.user_id)
            return

        self.user_id = payload.user_id
        self.token = payload.token
        self.expires_at = time.time() + payload.expires_in
        self.online = True
        self.registration_success = False  # 清除注册标记
        logger.info("Session authenticated for %s", self.user_id)

    def is_token_valid(self) -> bool:
        return bool(self.token) and time.time() < self.expires_at

    def is_online(self) -> bool:
        """Reflect current login state (token + flag)."""
        return self.online and self.is_token_valid()

    async def logout(self) -> None:
        if not self.online:
            return
        logout_msg = {
            "id": str(uuid.uuid4()),
            "type": "request",
            "timestamp": int(time.time()),
            "command": MsgType.AUTH_LOGOUT.value,
            "payload": {},
        }
        try:
            await self.network.send(self.attach_headers(logout_msg))
        except ProtocolError as exc:
            logger.warning("Logout message failed: %s", exc)
        self.clear()

    def clear(self) -> None:
        self.user_id = None
        self.token = None
        self.expires_at = 0
        self.online = False

    async def refresh_token(self) -> bool:
        if self.is_token_valid():
            return True
        async with self._refresh_lock:
            if self.is_token_valid():
                return True
            refresh_msg = {
                "id": str(uuid.uuid4()),
                "type": "request",
                "timestamp": int(time.time()),
                "command": MsgType.AUTH_REFRESH.value,
                "payload": {"token": self.token},
            }
            await self.network.send(self.attach_headers(refresh_msg, require_auth=False))
            return False  # actual refresh completion handled by AuthManager
