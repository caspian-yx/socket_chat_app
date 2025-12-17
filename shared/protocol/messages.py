from __future__ import annotations

import time
from typing import Any, Dict, Literal, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .commands import MsgType, normalize_command
from .errors import ProtocolError, StatusCode


def _default_id() -> str:
    return str(uuid4())


def _default_timestamp() -> int:
    return int(time.time())


class BaseMsg(BaseModel):
    """Base envelope shared by every command."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=_default_id, description="Globally unique identifier")
    type: Literal["request", "response", "event"] = Field(..., description="request / response / event")
    timestamp: int = Field(default_factory=_default_timestamp, description="Unix timestamp (seconds)")
    command: Union[MsgType, str] = Field(..., description="Command identifier such as auth/login")
    headers: Dict[str, Any] = Field(default_factory=dict, description="Metadata such as version/trace/client")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Command specific payload")

    @property
    def command_text(self) -> str:
        return normalize_command(self.command)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BaseMsg":
        try:
            return cls(**data)
        except ValidationError as exc:
            raise ProtocolError(StatusCode.BAD_REQUEST, message=f"Message validation failed: {exc}") from exc


class LoginPayload(BaseModel):
    username: str
    password: str
    client_info: Optional[Dict[str, Any]] = None


class LoginMsg(BaseMsg):
    command: MsgType = Field(default=MsgType.AUTH_LOGIN, frozen=True)
    payload: LoginPayload


class RegisterMsg(BaseMsg):
    command: MsgType = Field(default=MsgType.AUTH_REGISTER, frozen=True)
    payload: LoginPayload


class LoginAckPayload(BaseModel):
    status: int
    token: str
    user_id: str
    expires_in: int
    error_message: Optional[str] = None


class AuthAckMsg(BaseMsg):
    command: str = Field(pattern=r"auth/(login|refresh|register)_ack")
    payload: LoginAckPayload
    type: str = "response"


class LoginAckMsg(AuthAckMsg):
    command: MsgType = Field(default=MsgType.AUTH_LOGIN_ACK, frozen=True)


class RefreshAckMsg(AuthAckMsg):
    command: MsgType = Field(default=MsgType.AUTH_REFRESH_ACK, frozen=True)


class RegisterAckMsg(AuthAckMsg):
    command: MsgType = Field(default=MsgType.AUTH_REGISTER_ACK, frozen=True)


class PresenceListPayload(BaseModel):
    users: Optional[list[str]] = None
    status: Optional[int] = None


class PresenceListMsg(BaseMsg):
    command: MsgType = Field(default=MsgType.PRESENCE_LIST, frozen=True)
    payload: PresenceListPayload


class PresenceEventPayload(BaseModel):
    user_id: str
    state: str
    last_seen: Optional[int] = None


class PresenceEventMsg(BaseMsg):
    command: MsgType = Field(default=MsgType.PRESENCE_EVENT, frozen=True)
    payload: PresenceEventPayload
    type: str = "event"


class MessageSendPayload(BaseModel):
    conversation_id: str
    target: Dict[str, Any]
    content: Dict[str, Any]
    attachments: list[Dict[str, Any]] = Field(default_factory=list)


class MessageSendMsg(BaseMsg):
    command: MsgType = Field(default=MsgType.MESSAGE_SEND, frozen=True)
    payload: MessageSendPayload


class MessageEventPayload(BaseModel):
    conversation_id: str
    sender_id: str
    content: Dict[str, Any]
    message_id: str


class MessageEventMsg(BaseMsg):
    command: MsgType = Field(default=MsgType.MESSAGE_EVENT, frozen=True)
    payload: MessageEventPayload
    type: str = "event"


class HeartbeatMsg(BaseMsg):
    command: MsgType = Field(default=MsgType.PRESENCE_HEARTBEAT, frozen=True)
    payload: Dict[str, Any] = Field(default_factory=dict)
    type: str = "event"


class FileRequestPayload(BaseModel):
    target: Dict[str, Any]
    file_name: str
    file_size: int
    checksum: Optional[str] = None


class FileRequestMsg(BaseMsg):
    command: MsgType = Field(default=MsgType.FILE_REQUEST, frozen=True)
    payload: FileRequestPayload


class FileRequestAckPayload(BaseModel):
    status: int
    session_id: Optional[str] = None
    error_message: Optional[str] = None


class FileRequestAckMsg(BaseMsg):
    command: MsgType = Field(default=MsgType.FILE_REQUEST_ACK, frozen=True)
    payload: FileRequestAckPayload
    type: str = "response"


class FileAcceptPayload(BaseModel):
    session_id: str


class FileAcceptMsg(BaseMsg):
    command: MsgType = Field(default=MsgType.FILE_ACCEPT, frozen=True)
    payload: FileAcceptPayload


class FileAcceptAckMsg(FileRequestAckMsg):
    command: MsgType = Field(default=MsgType.FILE_ACCEPT_ACK, frozen=True)


class FileRejectMsg(BaseMsg):
    command: MsgType = Field(default=MsgType.FILE_REJECT, frozen=True)
    payload: FileAcceptPayload


class FileRejectAckMsg(FileRequestAckMsg):
    command: MsgType = Field(default=MsgType.FILE_REJECT_ACK, frozen=True)


class FileTransferEventPayload(BaseModel):
    session_id: str
    from_user: Optional[str] = None
    target: Optional[Dict[str, Any]] = None
    file_name: Optional[str] = None
    file_size: Optional[int] = None
    checksum: Optional[str] = None
    channel_host: Optional[str] = None
    channel_port: Optional[int] = None
    status: Optional[str] = None
    error_message: Optional[str] = None


class FileEventMsg(BaseMsg):
    command: MsgType
    payload: FileTransferEventPayload
    type: str = "event"
