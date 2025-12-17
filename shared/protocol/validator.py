from __future__ import annotations

import hashlib
import hmac
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import jsonschema

from .commands import MsgType, normalize_command
from .constants import DEFAULT_VERSION
from .errors import ErrorCode, ProtocolError, StatusCode

SCHEMA_DIR = Path(__file__).parent / "schemas"

# Mapping command -> schema filename (relative to SCHEMA_DIR)
SCHEMA_REGISTRY: Dict[str, str] = {
    MsgType.AUTH_LOGIN.value: "auth.login.json",
    MsgType.AUTH_LOGIN_ACK.value: "auth.login_ack.json",
    MsgType.AUTH_REGISTER.value: "auth.register.json",
    MsgType.AUTH_REGISTER_ACK.value: "auth.register_ack.json",
    MsgType.AUTH_REFRESH_ACK.value: "auth.refresh_ack.json",
    MsgType.PRESENCE_LIST.value: "presence.list.json",
    MsgType.PRESENCE_EVENT.value: "presence.event.json",
    MsgType.MESSAGE_SEND.value: "message.send.json",
    MsgType.MESSAGE_EVENT.value: "message.event.json",
    MsgType.FILE_REQUEST.value: "file.request.json",
    MsgType.FILE_REQUEST_ACK.value: "file.request_ack.json",
    MsgType.FILE_ACCEPT.value: "file.accept.json",
    MsgType.FILE_ACCEPT_ACK.value: "file.accept_ack.json",
    MsgType.FILE_REJECT.value: "file.reject.json",
    MsgType.FILE_REJECT_ACK.value: "file.reject_ack.json",
    MsgType.FILE_COMPLETE.value: "file.complete.json",
    MsgType.FILE_ERROR.value: "file.error.json",
    MsgType.ROOM_CREATE.value: "room.create.json",
    MsgType.ROOM_JOIN.value: "room.join.json",
    MsgType.ROOM_LEAVE.value: "room.leave.json",
    MsgType.ROOM_LIST.value: "room.list.json",
    MsgType.ROOM_MEMBERS.value: "room.members.json",
    MsgType.ROOM_INFO.value: "room.info.json",
    MsgType.ROOM_KICK.value: "room.kick.json",
    MsgType.ROOM_DELETE.value: "room.delete.json",
}

# shared/protocol/validator.py

def _schema_path(command: str) -> Optional[Path]:
    filename = SCHEMA_REGISTRY.get(command)
    if not filename:
        # 关键：支持 "auth/login_ack" → "auth.login_ack.json"
        dotted = command.replace("/", ".")
        filename = SCHEMA_REGISTRY.get(dotted)
        if not filename:
            return None
    path = SCHEMA_DIR / filename
    return path if path.exists() else None

@lru_cache(maxsize=16)
def load_schema(command: str) -> Optional[dict]:
    """Load JSON schema for command if present."""
    command_text = normalize_command(command)
    path = _schema_path(command_text)
    if not path:
        return None
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def validate_version(headers: Optional[Dict[str, Any]]) -> None:
    """Ensure headers declare supported protocol version."""
    version = (headers or {}).get("version", "0.0")
    if version != DEFAULT_VERSION:
        raise ProtocolError(
            StatusCode.UPGRADE_REQUIRED,
            ErrorCode.VERSION_MISMATCH,
            f"Protocol version mismatch: expected {DEFAULT_VERSION}, got {version}",
        )


def validate_signature(msg: Dict[str, Any], key: str, field: str = "payload.signature") -> None:
    """
    Validate HMAC signature stored under payload.signature (by default).
    `field` uses dotted-path notation (e.g., payload.signature / headers.signature).
    """
    sections = field.split(".")
    data = msg
    for section in sections[:-1]:
        data = data.get(section, {})
        if not isinstance(data, dict):
            raise ProtocolError(StatusCode.BAD_REQUEST, ErrorCode.SIGNATURE_INVALID, f"Invalid field path {field}")
    signature_key = sections[-1]
    expected_sig = data.get(signature_key)
    if not expected_sig:
        raise ProtocolError(StatusCode.BAD_REQUEST, ErrorCode.SIGNATURE_INVALID, "Missing signature")

    payload_str = json.dumps(msg.get("payload", {}), sort_keys=True, separators=(",", ":"))
    calc_sig = hmac.new(key.encode("utf-8"), payload_str.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc_sig, expected_sig):
        raise ProtocolError(StatusCode.UNAUTHORIZED, ErrorCode.SIGNATURE_INVALID, "Signature validation failed")


def validate_msg(msg: Dict[str, Any], schema: Optional[dict] = None) -> None:
    """Run standard validations (version + json-schema)."""
    validate_version(msg.get("headers"))
    if not schema:
        schema = load_schema(msg.get("command", ""))
    if schema:
        try:
            jsonschema.validate(instance=msg, schema=schema)
        except jsonschema.ValidationError as exc:
            raise ProtocolError(StatusCode.BAD_REQUEST, ErrorCode.PARAM_MISSING, f"Schema validation failed: {exc}") from exc


__all__ = ["load_schema", "validate_msg", "validate_signature", "validate_version"]
