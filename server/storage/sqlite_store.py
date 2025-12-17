from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


class SQLiteStore:
    """SQLite-backed persistence layer for users, sessions, rooms, messages, and offline queue."""

    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.parent.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS presence (
                username TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                sender_id TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rooms (
                room_id TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                encrypted INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                password_hash TEXT,
                metadata TEXT
            );

            CREATE TABLE IF NOT EXISTS room_members (
                room_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                joined_at INTEGER NOT NULL,
                PRIMARY KEY (room_id, user_id),
                FOREIGN KEY (room_id) REFERENCES rooms(room_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS offline_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS files (
                session_id TEXT PRIMARY KEY,
                file_name TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                checksum TEXT,
                sender_id TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS friend_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_user TEXT NOT NULL,
                to_user TEXT NOT NULL,
                message TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(from_user, to_user)
            );

            CREATE TABLE IF NOT EXISTS friends (
                user1 TEXT NOT NULL,
                user2 TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (user1, user2),
                CHECK (user1 < user2)
            );
            """
        )
        self.conn.commit()
        self._ensure_room_columns()

    def _ensure_room_columns(self) -> None:
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(rooms)").fetchall()}
        required = {
            "password_hash": "TEXT",
            "metadata": "TEXT",
        }
        for column, ddl in required.items():
            if column not in columns:
                self.conn.execute(f"ALTER TABLE rooms ADD COLUMN {column} {ddl}")
        self.conn.commit()

    # --- User management -------------------------------------------------
    def get_user(self, username: str) -> Optional[Dict[str, str]]:
        row = self.conn.execute(
            "SELECT username, password_hash, created_at FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not row:
            return None
        return {"username": row["username"], "password": row["password_hash"], "created_at": row["created_at"]}

    def user_exists(self, username: str) -> bool:
        return self.conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is not None

    def create_user(self, username: str, password_hash: str) -> None:
        try:
            self.conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, password_hash, int(time.time())),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("User already exists") from exc

    # --- Session + Presence ----------------------------------------------
    def upsert_session(self, token: str, username: str, expires_in: int) -> None:
        now = int(time.time())
        expires_at = now + expires_in
        self.conn.execute(
            """
            INSERT INTO sessions (token, username, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(token) DO UPDATE SET
                username=excluded.username,
                created_at=excluded.created_at,
                expires_at=excluded.expires_at
            """,
            (token, username, now, expires_at),
        )
        self.conn.commit()

    def delete_session(self, token: str) -> None:
        self.conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        self.conn.commit()

    def update_presence(self, user_id: str, state: str) -> None:
        self.conn.execute(
            """
            INSERT INTO presence (username, state, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                state=excluded.state,
                updated_at=excluded.updated_at
            """,
            (user_id, state, int(time.time())),
        )
        self.conn.commit()

    def list_online_users(self) -> List[str]:
        rows = self.conn.execute(
            "SELECT username FROM presence WHERE state = ? ORDER BY username ASC",
            ("online",),
        ).fetchall()
        return [row["username"] for row in rows]

    # --- Messages --------------------------------------------------------
    def insert_message(self, conversation_id: str, sender_id: str, content: Dict[str, Any]) -> Dict[str, Any]:
        message_id = uuid4().hex
        timestamp = int(time.time())
        self.conn.execute(
            """
            INSERT INTO messages (message_id, conversation_id, sender_id, content, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (message_id, conversation_id, sender_id, json.dumps(content or {}), timestamp),
        )
        self.conn.commit()
        return {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "sender_id": sender_id,
            "content": content,
            "timestamp": timestamp,
        }

    # --- Rooms -----------------------------------------------------------
    def create_room(
        self,
        room_id: str,
        owner: str,
        encrypted: bool,
        password_hash: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = int(time.time())
        try:
            with self.conn:
                self.conn.execute(
                    """
                    INSERT INTO rooms (room_id, owner, encrypted, created_at, password_hash, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        room_id,
                        owner,
                        int(bool(encrypted)),
                        now,
                        password_hash,
                        json.dumps(metadata or {}),
                    ),
                )
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO room_members (room_id, user_id, joined_at)
                    VALUES (?, ?, ?)
                    """,
                    (room_id, owner, now),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Room already exists") from exc

    def room_exists(self, room_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM rooms WHERE room_id = ?", (room_id,)).fetchone() is not None

    def add_member(self, room_id: str, user_id: str) -> None:
        now = int(time.time())
        try:
            self.conn.execute(
                """
                INSERT INTO room_members (room_id, user_id, joined_at)
                VALUES (?, ?, ?)
                """,
                (room_id, user_id, now),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("Room not found") from exc

    def remove_member(self, room_id: str, user_id: str) -> None:
        self.conn.execute(
            "DELETE FROM room_members WHERE room_id = ? AND user_id = ?",
            (room_id, user_id),
        )
        self.conn.commit()

    def list_room_members(self, room_id: str) -> List[str]:
        rows = self.conn.execute(
            "SELECT user_id FROM room_members WHERE room_id = ? ORDER BY user_id ASC",
            (room_id,),
        ).fetchall()
        return [row["user_id"] for row in rows]

    def list_rooms_for_user(self, user_id: str) -> List[str]:
        rows = self.conn.execute(
            "SELECT room_id FROM room_members WHERE user_id = ? ORDER BY room_id ASC",
            (user_id,),
        ).fetchall()
        return [row["room_id"] for row in rows]

    def get_room(self, room_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT room_id, owner, encrypted, created_at, password_hash, metadata FROM rooms WHERE room_id = ?",
            (room_id,),
        ).fetchone()
        if not row:
            return None
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        return {
            "room_id": row["room_id"],
            "owner": row["owner"],
            "encrypted": bool(row["encrypted"]),
            "created_at": row["created_at"],
            "password_hash": row["password_hash"],
            "metadata": metadata,
        }

    def get_room_details(self, room_id: str) -> Optional[Dict[str, Any]]:
        room = self.get_room(room_id)
        if not room:
            return None
        members = self.list_room_members(room_id)
        room["members"] = members
        return room

    def update_room_metadata(self, room_id: str, metadata: Dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE rooms SET metadata = ? WHERE room_id = ?",
            (json.dumps(metadata or {}), room_id),
        )
        self.conn.commit()

    def get_room_password_hash(self, room_id: str) -> Optional[str]:
        row = self.conn.execute("SELECT password_hash FROM rooms WHERE room_id = ?", (room_id,)).fetchone()
        return row["password_hash"] if row else None

    def delete_room(self, room_id: str) -> None:
        """删除房间及其所有成员关系"""
        # 删除房间成员
        self.conn.execute("DELETE FROM room_members WHERE room_id = ?", (room_id,))
        # 删除房间本身
        self.conn.execute("DELETE FROM rooms WHERE room_id = ?", (room_id,))
        self.conn.commit()

    # --- Offline queue ---------------------------------------------------
    def enqueue_offline_message(self, user_id: str, message: Dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO offline_queue (user_id, message, created_at)
            VALUES (?, ?, ?)
            """,
            (user_id, json.dumps(message), int(time.time())),
        )
        self.conn.commit()

    def consume_offline_messages(self, user_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, message FROM offline_queue WHERE user_id = ? ORDER BY id ASC",
            (user_id,),
        ).fetchall()
        if not rows:
            return []
        ids = [row["id"] for row in rows]
        placeholders = ",".join("?" for _ in ids)
        self.conn.execute(f"DELETE FROM offline_queue WHERE id IN ({placeholders})", ids)
        self.conn.commit()
        return [json.loads(row["message"]) for row in rows]

    # --- File sessions ---------------------------------------------------
    def create_file_session(
        self,
        session_id: str,
        file_name: str,
        file_size: int,
        checksum: Optional[str],
        sender_id: str,
        target_type: str,
        target_id: str,
        status: str,
    ) -> Dict[str, Any]:
        now = int(time.time())
        self.conn.execute(
            """
            INSERT INTO files (session_id, file_name, file_size, checksum, sender_id, target_type, target_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, file_name, file_size, checksum, sender_id, target_type, target_id, status, now, now),
        )
        self.conn.commit()
        return self.get_file_session(session_id) or {}

    def update_file_session_status(self, session_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE files SET status = ?, updated_at = ? WHERE session_id = ?",
            (status, int(time.time()), session_id),
        )
        self.conn.commit()

    def get_file_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            """
            SELECT session_id, file_name, file_size, checksum, sender_id, target_type, target_id, status, created_at, updated_at
            FROM files WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return dict(row)

    # --- Friend management -----------------------------------------------
    def send_friend_request(self, from_user: str, to_user: str, message: Optional[str] = None) -> int:
        """Send a friend request. Returns request ID."""
        now = int(time.time())
        try:
            cursor = self.conn.execute(
                """
                INSERT INTO friend_requests (from_user, to_user, message, status, created_at, updated_at)
                VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (from_user, to_user, message, now, now),
            )
            self.conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            # Request already exists, update it
            self.conn.execute(
                """
                UPDATE friend_requests
                SET status = 'pending', message = ?, updated_at = ?
                WHERE from_user = ? AND to_user = ?
                """,
                (message, now, from_user, to_user),
            )
            self.conn.commit()
            row = self.conn.execute(
                "SELECT id FROM friend_requests WHERE from_user = ? AND to_user = ?",
                (from_user, to_user),
            ).fetchone()
            return row["id"] if row else 0

    def get_pending_friend_requests(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all pending friend requests for a user."""
        rows = self.conn.execute(
            """
            SELECT id, from_user, to_user, message, status, created_at, updated_at
            FROM friend_requests
            WHERE to_user = ? AND status = 'pending'
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_sent_friend_requests(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all friend requests sent by a user."""
        rows = self.conn.execute(
            """
            SELECT id, from_user, to_user, message, status, created_at, updated_at
            FROM friend_requests
            WHERE from_user = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def accept_friend_request(self, request_id: int) -> bool:
        """Accept a friend request and create friendship."""
        # Get request details
        row = self.conn.execute(
            "SELECT from_user, to_user FROM friend_requests WHERE id = ? AND status = 'pending'",
            (request_id,),
        ).fetchone()

        if not row:
            return False

        from_user, to_user = row["from_user"], row["to_user"]
        # Ensure user1 < user2 for consistent storage
        user1, user2 = (from_user, to_user) if from_user < to_user else (to_user, from_user)

        now = int(time.time())

        try:
            # Create friendship
            self.conn.execute(
                "INSERT INTO friends (user1, user2, created_at) VALUES (?, ?, ?)",
                (user1, user2, now),
            )

            # Update request status
            self.conn.execute(
                "UPDATE friend_requests SET status = 'accepted', updated_at = ? WHERE id = ?",
                (now, request_id),
            )

            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Already friends
            self.conn.execute(
                "UPDATE friend_requests SET status = 'accepted', updated_at = ? WHERE id = ?",
                (now, request_id),
            )
            self.conn.commit()
            return True

    def reject_friend_request(self, request_id: int) -> bool:
        """Reject a friend request."""
        result = self.conn.execute(
            "UPDATE friend_requests SET status = 'rejected', updated_at = ? WHERE id = ? AND status = 'pending'",
            (int(time.time()), request_id),
        )
        self.conn.commit()
        return result.rowcount > 0

    def delete_friend(self, user1: str, user2: str) -> bool:
        """Delete a friendship."""
        # Ensure user1 < user2
        if user1 > user2:
            user1, user2 = user2, user1

        result = self.conn.execute(
            "DELETE FROM friends WHERE user1 = ? AND user2 = ?",
            (user1, user2),
        )
        self.conn.commit()
        return result.rowcount > 0

    def list_friends(self, user_id: str) -> List[str]:
        """Get list of friend IDs for a user."""
        rows = self.conn.execute(
            """
            SELECT
                CASE
                    WHEN user1 = ? THEN user2
                    ELSE user1
                END as friend_id
            FROM friends
            WHERE user1 = ? OR user2 = ?
            """,
            (user_id, user_id, user_id),
        ).fetchall()
        return [row["friend_id"] for row in rows]

    def are_friends(self, user1: str, user2: str) -> bool:
        """Check if two users are friends."""
        if user1 > user2:
            user1, user2 = user2, user1

        row = self.conn.execute(
            "SELECT 1 FROM friends WHERE user1 = ? AND user2 = ?",
            (user1, user2),
        ).fetchone()
        return row is not None

