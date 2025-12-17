from __future__ import annotations

from server.storage import InMemoryRepository


def _repo(tmp_path):
    return InMemoryRepository(str(tmp_path / "repo.db"))


def test_store_message_persists(tmp_path):
    repo = _repo(tmp_path)
    payload = {"content": {"type": "text", "text": "hello"}}
    msg = repo.store_message("conversation-1", "alice", payload)

    assert msg["conversation_id"] == "conversation-1"
    assert msg["sender_id"] == "alice"
    assert msg["content"] == payload["content"]
    assert msg["message_id"]


def test_offline_queue_roundtrip(tmp_path):
    repo = _repo(tmp_path)
    event1 = {"id": "m1", "payload": {"text": "hello"}}
    event2 = {"id": "m2", "payload": {"text": "world"}}

    repo.enqueue_offline_message("alice", event1)
    repo.enqueue_offline_message("alice", event2)
    repo.enqueue_offline_message("bob", {"id": "m3"})

    queued = repo.consume_offline_messages("alice")
    assert queued == [event1, event2]
    assert repo.consume_offline_messages("alice") == []

    bob_messages = repo.consume_offline_messages("bob")
    assert len(bob_messages) == 1
    assert bob_messages[0]["id"] == "m3"


def test_room_persistence_with_password(tmp_path):
    repo = _repo(tmp_path)
    repo.create_room("room-1", "alice", True, "secret-hash", {"topic": "dev"})
    assert repo.room_exists("room-1")

    room = repo.get_room("room-1")
    assert room is not None
    assert room["owner"] == "alice"
    assert room["encrypted"] is True
    assert room["password_hash"] == "secret-hash"

    members = repo.list_room_members("room-1")
    assert members == ["alice"]

    details = repo.get_room_details("room-1")
    assert details["members"] == ["alice"]
