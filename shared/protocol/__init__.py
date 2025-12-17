"""
Shared protocol package that centralizes commands, message models, framing helpers,
and validation utilities for both client and server.
"""

from .commands import MsgType, commands_in_group, is_command, normalize_command
from .constants import DEFAULT_VERSION, ENCODING, FRAME_DELIMITER
from .errors import ErrorCode, ProtocolError, StatusCode
from .framing import async_decode_msg, decode_msg, encode_msg, encode_chunk, decode_chunk
from .messages import (
    AuthAckMsg,
    BaseMsg,
    FileAcceptAckMsg,
    FileAcceptMsg,
    FileEventMsg,
    FileRejectAckMsg,
    FileRejectMsg,
    FileRequestAckMsg,
    FileRequestMsg,
    HeartbeatMsg,
    LoginAckMsg,
    LoginMsg,
    MessageEventMsg,
    MessageSendMsg,
    PresenceEventMsg,
    PresenceListMsg,
    RefreshAckMsg,
    RegisterAckMsg,
    RegisterMsg,
)
from .validator import load_schema, validate_msg, validate_signature, validate_version

__all__ = [
    "MsgType",
    "commands_in_group",
    "is_command",
    "normalize_command",
    "DEFAULT_VERSION",
    "ENCODING",
    "FRAME_DELIMITER",
    "ErrorCode",
    "ProtocolError",
    "StatusCode",
    "encode_msg",
    "decode_msg",
    "async_decode_msg",
    "encode_chunk",
    "decode_chunk",
    "BaseMsg",
    "LoginMsg",
    "RegisterMsg",
    "AuthAckMsg",
    "LoginAckMsg",
    "RefreshAckMsg",
    "RegisterAckMsg",
    "HeartbeatMsg",
    "PresenceListMsg",
    "PresenceEventMsg",
    "MessageSendMsg",
    "MessageEventMsg",
    "FileRequestMsg",
    "FileRequestAckMsg",
    "FileAcceptMsg",
    "FileAcceptAckMsg",
    "FileRejectMsg",
    "FileRejectAckMsg",
    "FileEventMsg",
    "load_schema",
    "validate_msg",
    "validate_version",
    "validate_signature",
]
