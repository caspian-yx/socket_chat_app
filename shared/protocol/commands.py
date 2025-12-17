from __future__ import annotations

from enum import StrEnum
from typing import Dict, Iterable, Union


class MsgType(StrEnum):
    """
    Canonical command names shared by client/server.
    Use unique enum attribute names (AUTH_LOGIN, MESSAGE_SEND, ...) to avoid duplication.
    """

    # Auth domain
    AUTH_LOGIN = "auth/login"
    AUTH_LOGIN_ACK = "auth/login_ack"
    AUTH_REGISTER = "auth/register"
    AUTH_REGISTER_ACK = "auth/register_ack"
    AUTH_LOGOUT = "auth/logout"
    AUTH_REFRESH = "auth/refresh"
    AUTH_REFRESH_ACK = "auth/refresh_ack"
    AUTH_KICK = "auth/kick"

    # Presence domain
    PRESENCE_HEARTBEAT = "presence/heartbeat"
    PRESENCE_UPDATE = "presence/update"
    PRESENCE_LIST = "presence/list"
    PRESENCE_EVENT = "presence/event"

    # Messaging domain
    MESSAGE_SEND = "message/send"
    MESSAGE_ACK = "message/ack"
    MESSAGE_EVENT = "message/event"
    MESSAGE_HISTORY = "message/history"
    MESSAGE_OFFLINE = "message/offline"

    # File / attachment domain
    FILE_REQUEST = "file/request"
    FILE_REQUEST_ACK = "file/request_ack"
    FILE_ACCEPT = "file/accept"
    FILE_ACCEPT_ACK = "file/accept_ack"
    FILE_REJECT = "file/reject"
    FILE_REJECT_ACK = "file/reject_ack"
    FILE_COMPLETE = "file/complete"
    FILE_ERROR = "file/error"

    # Room domain
    ROOM_CREATE = "room/create"
    ROOM_JOIN = "room/join"
    ROOM_LEAVE = "room/leave"
    ROOM_LIST = "room/list"
    ROOM_MEMBERS = "room/members"
    ROOM_INFO = "room/info"
    ROOM_KICK = "room/kick"
    ROOM_DELETE = "room/delete"

    # Voice domain
    VOICE_CALL = "voice/call"
    VOICE_CALL_ACK = "voice/call_ack"
    VOICE_ANSWER = "voice/answer"
    VOICE_ANSWER_ACK = "voice/answer_ack"
    VOICE_REJECT = "voice/reject"
    VOICE_REJECT_ACK = "voice/reject_ack"
    VOICE_END = "voice/end"
    VOICE_END_ACK = "voice/end_ack"
    VOICE_DATA = "voice/data"
    VOICE_EVENT = "voice/event"

    # Friend domain
    FRIEND_REQUEST = "friend/request"
    FRIEND_REQUEST_ACK = "friend/request_ack"
    FRIEND_ACCEPT = "friend/accept"
    FRIEND_ACCEPT_ACK = "friend/accept_ack"
    FRIEND_REJECT = "friend/reject"
    FRIEND_REJECT_ACK = "friend/reject_ack"
    FRIEND_DELETE = "friend/delete"
    FRIEND_DELETE_ACK = "friend/delete_ack"
    FRIEND_LIST = "friend/list"
    FRIEND_LIST_ACK = "friend/list_ack"
    FRIEND_EVENT = "friend/event"


COMMAND_GROUPS: Dict[str, str] = {
    MsgType.AUTH_LOGIN.value: "auth",
    MsgType.AUTH_LOGIN_ACK.value: "auth",
    MsgType.AUTH_REGISTER.value: "auth",
    MsgType.AUTH_REGISTER_ACK.value: "auth",
    MsgType.AUTH_LOGOUT.value: "auth",
    MsgType.AUTH_REFRESH.value: "auth",
    MsgType.AUTH_REFRESH_ACK.value: "auth",
    MsgType.AUTH_KICK.value: "auth",
    MsgType.PRESENCE_HEARTBEAT.value: "presence",
    MsgType.PRESENCE_UPDATE.value: "presence",
    MsgType.PRESENCE_LIST.value: "presence",
    MsgType.PRESENCE_EVENT.value: "presence",
    MsgType.MESSAGE_SEND.value: "message",
    MsgType.MESSAGE_ACK.value: "message",
    MsgType.MESSAGE_EVENT.value: "message",
    MsgType.MESSAGE_HISTORY.value: "message",
    MsgType.MESSAGE_OFFLINE.value: "message",
    MsgType.FILE_REQUEST.value: "file",
    MsgType.FILE_REQUEST_ACK.value: "file",
    MsgType.FILE_ACCEPT.value: "file",
    MsgType.FILE_ACCEPT_ACK.value: "file",
    MsgType.FILE_REJECT.value: "file",
    MsgType.FILE_REJECT_ACK.value: "file",
    MsgType.FILE_COMPLETE.value: "file",
    MsgType.FILE_ERROR.value: "file",
    MsgType.ROOM_CREATE.value: "room",
    MsgType.ROOM_JOIN.value: "room",
    MsgType.ROOM_LEAVE.value: "room",
    MsgType.ROOM_LIST.value: "room",
    MsgType.ROOM_MEMBERS.value: "room",
    MsgType.ROOM_INFO.value: "room",
    MsgType.ROOM_KICK.value: "room",
    MsgType.ROOM_DELETE.value: "room",
    MsgType.VOICE_CALL.value: "voice",
    MsgType.VOICE_CALL_ACK.value: "voice",
    MsgType.VOICE_ANSWER.value: "voice",
    MsgType.VOICE_ANSWER_ACK.value: "voice",
    MsgType.VOICE_REJECT.value: "voice",
    MsgType.VOICE_REJECT_ACK.value: "voice",
    MsgType.VOICE_END.value: "voice",
    MsgType.VOICE_END_ACK.value: "voice",
    MsgType.VOICE_DATA.value: "voice",
    MsgType.VOICE_EVENT.value: "voice",
    MsgType.FRIEND_REQUEST.value: "friend",
    MsgType.FRIEND_REQUEST_ACK.value: "friend",
    MsgType.FRIEND_ACCEPT.value: "friend",
    MsgType.FRIEND_ACCEPT_ACK.value: "friend",
    MsgType.FRIEND_REJECT.value: "friend",
    MsgType.FRIEND_REJECT_ACK.value: "friend",
    MsgType.FRIEND_DELETE.value: "friend",
    MsgType.FRIEND_DELETE_ACK.value: "friend",
    MsgType.FRIEND_LIST.value: "friend",
    MsgType.FRIEND_LIST_ACK.value: "friend",
    MsgType.FRIEND_EVENT.value: "friend",
}


def normalize_command(command: Union[str, MsgType]) -> str:
    """Convert enum/string into canonical command text."""
    return command.value if isinstance(command, MsgType) else str(command)


def is_command(value: str) -> bool:
    """Check if `value` is a known command."""
    try:
        MsgType(value)
        return True
    except ValueError:
        return False


def commands_in_group(group: str) -> Iterable[str]:
    """Yield commands belonging to the specified logical domain."""
    for command, grp in COMMAND_GROUPS.items():
        if grp == group:
            yield command


__all__ = [
    "MsgType",
    "COMMAND_GROUPS",
    "normalize_command",
    "is_command",
    "commands_in_group",
]
