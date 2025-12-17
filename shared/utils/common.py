from __future__ import annotations

import hashlib
import os
import time
from typing import Optional
from uuid import uuid4


def generate_message_id(prefix: Optional[str] = None) -> str:
    """Generate deterministic-looking message IDs."""
    base = uuid4().hex
    return f"{prefix}-{base}" if prefix else base


def utc_timestamp() -> int:
    """Current UTC timestamp in seconds."""
    return int(time.time())


def sha256_hex(data: str) -> str:
    """Convenience wrapper for hashing small secrets/passwords."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def random_token(length: int = 32) -> str:
    """Generate hex token."""
    return os.urandom(length // 2).hex()


__all__ = ["generate_message_id", "utc_timestamp", "sha256_hex", "random_token"]
