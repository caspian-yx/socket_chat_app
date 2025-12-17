from __future__ import annotations

import asyncio
import logging
from typing import Dict

from client.core.network import NetworkClient
from client.core.session import ClientSession, SessionError
from shared.protocol import validator
from shared.protocol.commands import MsgType
from shared.protocol.errors import ProtocolError, StatusCode
from shared.protocol.messages import LoginMsg, RegisterMsg

logger = logging.getLogger(__name__)


class AuthManager:
    """Handle login/logout/refresh flows, wiring handlers with NetworkClient."""

    def __init__(self, network: NetworkClient, session: ClientSession) -> None:
        self.network = network
        self.session = session
        self._login_event = asyncio.Event()
        self._refresh_event = asyncio.Event()
        self.network.register_handler(MsgType.AUTH_LOGIN_ACK, self._handle_login_ack)
        self.network.register_handler(MsgType.AUTH_REGISTER_ACK, self._handle_login_ack)
        self.network.register_handler(MsgType.AUTH_REFRESH_ACK, self._handle_refresh_ack)

    async def login(self, username: str, password_hash: str) -> bool:
        self._login_event.clear()
        login_msg = LoginMsg(
            type="request",
            command=MsgType.AUTH_LOGIN,
            payload={"username": username, "password": password_hash, "client_info": self.session.client_info},
            headers=self.session.build_headers(require_auth=False),
        ).model_dump()
        # schema = validator.load_schema(login_msg["command"])
        schema = validator.load_schema(MsgType.AUTH_LOGIN.value)
        await self.network.send(login_msg, schema)
        try:
            await asyncio.wait_for(self._login_event.wait(), timeout=10)
        except asyncio.TimeoutError:
            raise SessionError(StatusCode.UNAUTHORIZED, message="Login timed out")
        return self.session.online

    async def register(self, username: str, password_hash: str) -> bool:
        self._login_event.clear()
        self.session.registration_success = False  # 重置注册标记
        register_msg = RegisterMsg(
            type="request",
            command=MsgType.AUTH_REGISTER,
            payload={"username": username, "password": password_hash, "client_info": self.session.client_info},
            headers=self.session.build_headers(require_auth=False),
        ).model_dump()
        schema = validator.load_schema(MsgType.AUTH_REGISTER.value)
        await self.network.send(register_msg, schema)
        try:
            await asyncio.wait_for(self._login_event.wait(), timeout=10)
        except asyncio.TimeoutError:
            raise SessionError(StatusCode.UNAUTHORIZED, message="Register timed out")
        # 注册成功返回 registration_success 标记
        return self.session.registration_success

    async def logout(self) -> None:
        await self.session.logout()

    async def refresh(self) -> bool:
        if self.session.is_token_valid():
            return True
        self._refresh_event.clear()
        if not await self.session.refresh_token():
            try:
                await asyncio.wait_for(self._refresh_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                return False
        return self.session.is_token_valid()

    async def _handle_login_ack(self, message: Dict) -> None:
        try:
            schema = validator.load_schema(message.get("command", MsgType.AUTH_LOGIN_ACK.value))
            validator.validate_msg(message, schema)
            await self.session.set_authenticated(message)
        except ProtocolError as exc:
            logger.error("Login ACK validation failed: %s", exc)
        finally:
            self._login_event.set()

    async def _handle_refresh_ack(self, message: Dict) -> None:
        try:
            await self.session.set_authenticated(message)
        except ProtocolError:
            logger.exception("Refresh ACK invalid")
        finally:
            self._refresh_event.set()
