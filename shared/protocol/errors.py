from __future__ import annotations

from enum import IntEnum
from typing import Optional


class StatusCode(IntEnum):
    """HTTP-like status codes used across responses."""

    SUCCESS = 200
    ACCEPTED = 202
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    CONFLICT = 409
    TOO_MANY_REQUESTS = 429
    UPGRADE_REQUIRED = 426
    INTERNAL_ERROR = 500


class ErrorCode(IntEnum):
    """Domain specific error codes."""

    INVALID_TOKEN = 1001
    VERSION_MISMATCH = 1002
    SIGNATURE_INVALID = 1003
    PARAM_MISSING = 1004
    RATE_LIMITED = 1005
    USER_EXISTS = 1006


class ProtocolError(Exception):
    """Structured protocol exception carrying status + code + message."""

    def __init__(self, status: StatusCode, code: Optional[ErrorCode] = None, message: str = "") -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(f"{status.name} ({int(status)}): {message} (code={code.name if code else 'n/a'})")

    def to_payload(self) -> dict:
        """Map error into payload fragment consumable by clients."""
        return {
            "status": int(self.status),
            "error_code": int(self.code) if self.code is not None else None,
            "error_message": self.message,
        }


__all__ = ["StatusCode", "ErrorCode", "ProtocolError"]
