from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple


class InMemoryCache:
    """Very lightweight TTL cache for presence/messaging metadata."""

    def __init__(self) -> None:
        self._store: Dict[str, Tuple[float, Any]] = {}

    def set(self, key: str, value: Any, ttl: float = 60.0) -> None:
        self._store[key] = (time.time() + ttl, value)

    def get(self, key: str) -> Optional[Any]:
        record = self._store.get(key)
        if not record:
            return None
        expires_at, value = record
        if expires_at < time.time():
            self._store.pop(key, None)
            return None
        return value

    def clear(self) -> None:
        self._store.clear()
