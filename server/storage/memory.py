from __future__ import annotations

from typing import Any, Dict, List, Optional

from shared.utils.common import sha256_hex

from .sqlite_store import SQLiteStore


class InMemoryRepository:
    """
    Legacy name retained for compatibility with existing imports.
    The implementation now relies on SQLite for persistence.
    """

    def __init__(self, db_path: str) -> None:
        self.store = SQLiteStore(db_path)
        self._seed_default_users()

    def _seed_default_users(self) -> None:
        for username in ("alice", "bob"):
            if not self.store.user_exists(username):
                self.store.create_user(username, sha256_hex(username))

    # --- User APIs -------------------------------------------------------
    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        return self.store.get_user(username)

    def user_exists(self, username: str) -> bool:
        return self.store.user_exists(username)

    def create_user(self, username: str, password_hash: str) -> None:
        self.store.create_user(username, password_hash)

    # --- Session / Presence ----------------------------------------------
    def store_session(self, token: str, username: str, expires_in: int = 3600) -> None:
        self.store.upsert_session(token, username, expires_in)

    def delete_session(self, token: str) -> None:
        self.store.delete_session(token)

    def update_presence(self, user_id: str, state: str) -> None:
        self.store.update_presence(user_id, state)

    def list_online_users(self) -> List[str]:
        return self.store.list_online_users()

    # --- Messaging -------------------------------------------------------
    def store_message(self, conversation_id: str, sender_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.store.insert_message(conversation_id, sender_id, payload.get("content"))

    # --- Rooms -----------------------------------------------------------
    def create_room(
        self,
        room_id: str,
        owner: str,
        encrypted: bool = False,
        password_hash: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.store.create_room(room_id, owner, encrypted, password_hash, metadata)

    def room_exists(self, room_id: str) -> bool:
        return self.store.room_exists(room_id)

    def add_member(self, room_id: str, user_id: str) -> None:
        self.store.add_member(room_id, user_id)

    def remove_member(self, room_id: str, user_id: str) -> None:
        self.store.remove_member(room_id, user_id)

    def list_room_members(self, room_id: str) -> List[str]:
        return self.store.list_room_members(room_id)

    def list_rooms_for_user(self, user_id: str) -> List[str]:
        return self.store.list_rooms_for_user(user_id)

    def get_room(self, room_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_room(room_id)

    def get_room_details(self, room_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_room_details(room_id)

    def get_room_password_hash(self, room_id: str) -> Optional[str]:
        return self.store.get_room_password_hash(room_id)

    def update_room_metadata(self, room_id: str, metadata: Dict[str, Any]) -> None:
        self.store.update_room_metadata(room_id, metadata)

    def delete_room(self, room_id: str) -> None:
        self.store.delete_room(room_id)

    # --- Offline queue ---------------------------------------------------
    def enqueue_offline_message(self, user_id: str, message: Dict[str, Any]) -> None:
        self.store.enqueue_offline_message(user_id, message)

    def consume_offline_messages(self, user_id: str) -> List[Dict[str, Any]]:
        return self.store.consume_offline_messages(user_id)

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
        status: str = "pending",
    ) -> Dict[str, Any]:
        return self.store.create_file_session(
            session_id,
            file_name,
            file_size,
            checksum,
            sender_id,
            target_type,
            target_id,
            status,
        )

    def update_file_session_status(self, session_id: str, status: str) -> None:
        self.store.update_file_session_status(session_id, status)

    def get_file_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_file_session(session_id)

    # --- Friend management -----------------------------------------------
    def send_friend_request(self, from_user: str, to_user: str, message: Optional[str] = None) -> int:
        return self.store.send_friend_request(from_user, to_user, message)

    def get_pending_friend_requests(self, user_id: str) -> list[Dict[str, Any]]:
        return self.store.get_pending_friend_requests(user_id)

    def get_sent_friend_requests(self, user_id: str) -> list[Dict[str, Any]]:
        return self.store.get_sent_friend_requests(user_id)

    def accept_friend_request(self, request_id: int) -> bool:
        return self.store.accept_friend_request(request_id)

    def reject_friend_request(self, request_id: int) -> bool:
        return self.store.reject_friend_request(request_id)

    def delete_friend(self, user1: str, user2: str) -> bool:
        return self.store.delete_friend(user1, user2)

    def list_friends(self, user_id: str) -> list[str]:
        return self.store.list_friends(user_id)

    def are_friends(self, user1: str, user2: str) -> bool:
        return self.store.are_friends(user1, user2)

