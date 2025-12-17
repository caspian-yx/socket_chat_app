from __future__ import annotations

import ast
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class LocalDatabase:
    """Minimal SQLite-backed storage for messages."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                direction TEXT,
                conversation_id TEXT,
                payload TEXT,
                created_at INTEGER
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id)"
        )
        self.conn.commit()

    def save_outbound_message(self, message: Dict[str, Any]) -> None:
        self._insert("outbound", message)

    def save_inbound_message(self, message: Dict[str, Any]) -> None:
        self._insert("inbound", message)

    def _insert(self, direction: str, message: Dict[str, Any]) -> None:
        payload = message.get("payload") or {}
        conversation_id = payload.get("conversation_id") or message.get("conversation_id")
        if not conversation_id:
            target = payload.get("target") or {}
            conversation_id = target.get("id")
        if not conversation_id:
            return
        created_at = int(
            message.get("timestamp")
            or payload.get("timestamp")
            or time.time()
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO messages (id, direction, conversation_id, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                message.get("id"),
                direction,
                conversation_id,
                json.dumps(message, ensure_ascii=False),
                created_at,
            ),
        )
        self.conn.commit()

    def load_all_messages(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM messages ORDER BY created_at ASC"
        params: tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def recent_messages(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM messages ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def _row_to_record(self, row: sqlite3.Row) -> Dict[str, Any]:
        payload = row["payload"]
        data: Dict[str, Any]
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            try:
                data = ast.literal_eval(payload)
            except Exception:
                data = {}
        return {
            "id": row["id"],
            "direction": row["direction"],
            "conversation_id": row["conversation_id"],
            "created_at": row["created_at"],
            "message": data,
        }
