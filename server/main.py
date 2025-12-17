from __future__ import annotations

import asyncio
import logging

from server.config import SERVER_CONFIG, load_server_config
from server.core import CommandRouter, ConnectionManager, SocketServer
from server.services import AuthService, FileService, MessageService, PresenceService, RoomService
from server.services.voice_service import VoiceService
from server.services.friend_service import FriendService
from server.storage import InMemoryRepository
from server.workers import FileTransferServer, OfflineDispatcher, PresenceCleaner
from shared.protocol.commands import MsgType


async def run_server() -> None:
    load_server_config()
    logging.basicConfig(level=SERVER_CONFIG["log_level"])

    repository = InMemoryRepository(SERVER_CONFIG["db_path"])
    connection_manager = ConnectionManager()
    offline_dispatcher = OfflineDispatcher(repository, connection_manager)
    offline_dispatcher.start()

    # 创建服务（注意顺序：presence_service需要先创建）
    presence_service = PresenceService(repository)

    presence_cleaner = PresenceCleaner(
        connection_manager,
        repository,
        timeout=SERVER_CONFIG["session_timeout"],
        interval=SERVER_CONFIG["presence_scan_interval"],
        presence_service=presence_service,
    )
    presence_cleaner.start()
    file_server = FileTransferServer(SERVER_CONFIG["host"], SERVER_CONFIG["file_port"])

    # 创建其他服务
    auth_service = AuthService(repository, connection_manager, offline_dispatcher, presence_service)
    room_service = RoomService(repository, connection_manager)
    message_service = MessageService(repository, connection_manager)
    file_service = FileService(
        repository,
        connection_manager,
        file_server,
        SERVER_CONFIG["host"],
        SERVER_CONFIG["file_port"],
    )
    voice_service = VoiceService(connection_manager, room_service)
    friend_service = FriendService(connection_manager, repository)
    file_server.set_callbacks(file_service.notify_channel_complete, file_service.notify_channel_error)

    router = CommandRouter()
    router.register(MsgType.AUTH_LOGIN, auth_service.handle_login)
    router.register(MsgType.AUTH_REGISTER, auth_service.handle_register)
    router.register(MsgType.AUTH_LOGOUT, auth_service.handle_logout)
    router.register(MsgType.AUTH_REFRESH, auth_service.handle_refresh)
    router.register(MsgType.PRESENCE_UPDATE, presence_service.handle_update)
    router.register(MsgType.PRESENCE_LIST, presence_service.handle_list)
    router.register(MsgType.ROOM_CREATE, room_service.handle_create)
    router.register(MsgType.ROOM_JOIN, room_service.handle_join)
    router.register(MsgType.ROOM_LEAVE, room_service.handle_leave)
    router.register(MsgType.ROOM_LIST, room_service.handle_list)
    router.register(MsgType.ROOM_MEMBERS, room_service.handle_members)
    router.register(MsgType.ROOM_INFO, room_service.handle_info)
    router.register(MsgType.ROOM_KICK, room_service.handle_kick)
    router.register(MsgType.ROOM_DELETE, room_service.handle_delete)
    router.register(MsgType.MESSAGE_SEND, message_service.handle_send)
    router.register(MsgType.FILE_REQUEST, file_service.handle_request)
    router.register(MsgType.FILE_ACCEPT, file_service.handle_accept)
    router.register(MsgType.FILE_REJECT, file_service.handle_reject)
    router.register(MsgType.FILE_COMPLETE, file_service.handle_complete)
    router.register(MsgType.FILE_ERROR, file_service.handle_error)
    router.register(MsgType.VOICE_CALL, voice_service.handle_call)
    router.register(MsgType.VOICE_ANSWER, voice_service.handle_answer)
    router.register(MsgType.VOICE_REJECT, voice_service.handle_reject)
    router.register(MsgType.VOICE_END, voice_service.handle_end)
    router.register(MsgType.VOICE_DATA, voice_service.handle_voice_data)
    router.register(MsgType.FRIEND_REQUEST, friend_service.handle_request)
    router.register(MsgType.FRIEND_ACCEPT, friend_service.handle_accept)
    router.register(MsgType.FRIEND_REJECT, friend_service.handle_reject)
    router.register(MsgType.FRIEND_DELETE, friend_service.handle_delete)
    router.register(MsgType.FRIEND_LIST, friend_service.handle_list)

    # Disconnect callback to cleanup voice calls when user disconnects
    async def on_user_disconnect(ctx):
        """Handle user disconnection, cleanup voice calls."""
        if ctx.user_id:
            await voice_service.user_disconnected(ctx.user_id, ctx)

    server = SocketServer(
        SERVER_CONFIG["host"],
        SERVER_CONFIG["port"],
        router,
        connection_manager,
        on_disconnect=on_user_disconnect
    )
    await server.start()
    await file_server.start()
    try:
        await asyncio.Event().wait()  # keep running
    finally:
        await offline_dispatcher.stop()
        await presence_cleaner.stop()
        await file_server.stop()


if __name__ == "__main__":
    asyncio.run(run_server())
