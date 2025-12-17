from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ConnectionContext:
    reader: any  # asyncio.StreamReader
    writer: any  # asyncio.StreamWriter
    peername: str
    user_id: Optional[str] = None
    session_token: Optional[str] = None
    last_seen: float = field(default_factory=time.time)

    def mark_authenticated(self, user_id: str, token: str) -> None:
        self.user_id = user_id
        self.session_token = token
        self.touch()

    def touch(self) -> None:
        self.last_seen = time.time()

    def is_authenticated(self) -> bool:
        return self.user_id is not None
