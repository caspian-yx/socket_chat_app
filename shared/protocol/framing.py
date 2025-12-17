from __future__ import annotations

import asyncio
import json
from typing import Tuple

from .constants import ENCODING, FRAME_DELIMITER, MAX_PAYLOAD_SIZE
from .errors import ProtocolError, StatusCode


def encode_msg(msg: dict) -> bytes:
    """Encode message dict into bytes (JSON + delimiter)."""
    try:
        json_str = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ProtocolError(StatusCode.BAD_REQUEST, message=f"Encode failed: {exc}") from exc

    data = json_str.encode(ENCODING)
    if len(data) > MAX_PAYLOAD_SIZE:
        raise ProtocolError(StatusCode.BAD_REQUEST, message="Payload too large for control channel")
    return data + FRAME_DELIMITER


def decode_msg(data: bytes) -> dict:
    """Decode bytes into dictionary, stripping delimiter."""
    try:
        json_str = data.rstrip(FRAME_DELIMITER).decode(ENCODING)
        return json.loads(json_str)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(StatusCode.BAD_REQUEST, message=f"Decode failed: {exc}") from exc


async def async_decode_msg(reader: asyncio.StreamReader) -> dict:
    """Read a single frame from the stream and decode it."""
    data = await reader.readuntil(FRAME_DELIMITER)
    return decode_msg(data)


def encode_chunk(type_byte: int, payload: bytes) -> bytes:
    """
    Encode binary chunk for file transfer: 1 byte type + 4 bytes little-endian len + payload.
    """
    if not (0 <= type_byte <= 255):
        raise ProtocolError(StatusCode.BAD_REQUEST, message="Chunk type must be 0-255")
    length = len(payload).to_bytes(4, "little")
    return bytes([type_byte]) + length + payload


def decode_chunk(data: bytes) -> Tuple[int, bytes]:
    """Decode a TLV chunk."""
    if len(data) < 5:
        raise ProtocolError(StatusCode.BAD_REQUEST, message="Incomplete chunk header")
    type_byte = data[0]
    length = int.from_bytes(data[1:5], "little")
    payload = data[5 : 5 + length]
    if len(payload) != length:
        raise ProtocolError(StatusCode.BAD_REQUEST, message="Chunk payload truncated")
    return type_byte, payload
