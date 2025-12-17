from __future__ import annotations

import asyncio
import logging

import contextlib

from client.features import AuthManager, MessagingManager, PresenceManager, RoomManager
from shared.protocol.errors import ProtocolError
from shared.utils.common import sha256_hex

logger = logging.getLogger(__name__)


class ChatCLI:
    """Simple async CLI driving the managers."""

    def __init__(self, auth: AuthManager, messaging: MessagingManager, presence: PresenceManager, rooms: RoomManager) -> None:
        self.auth = auth
        self.messaging = messaging
        self.presence = presence
        self.rooms = rooms
        self._receiver_task: asyncio.Task | None = None
        self._receiver_stop = asyncio.Event()

    async def run(self) -> None:
        logger.info("CLI ready. Type 'help' for commands.")
        loop = asyncio.get_event_loop()
        self._receiver_stop.clear()
        self._receiver_task = asyncio.create_task(self._print_incoming(), name="cli-incoming-printer")
        try:
            while True:
                cmd = await loop.run_in_executor(None, input, "> ")
                parts = cmd.strip().split()
                if not parts:
                    continue
                match parts[0]:
                    case "help":
                        self._show_help()
                    case "login":
                        await self._handle_login(parts)
                    case "register":
                        await self._handle_register(parts)
                    case "room":
                        await self._handle_room(parts[1:])
                    case "send":
                        await self._handle_send(parts)
                    case "send_room":
                        await self._handle_send_room(parts)
                    case "presence":
                        roster = await self.presence.request_roster()
                        print("Online:", roster)
                    case "quit":
                        await self.auth.logout()
                        break
                    case _:
                        print("Unknown command")
        finally:
            self._receiver_stop.set()
            if self._receiver_task:
                self._receiver_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._receiver_task

    def _show_help(self) -> None:
        print(
            "Commands: register <user> <password>, login <user> <password>, "
            "room <create|join|leave|list|members> ..., send <conversation> <target> <text>, "
            "send_room <room_id> <text>, presence, quit"
        )

    async def _handle_login(self, parts: list[str]) -> None:
        if len(parts) < 3:
            print("Usage: login <username> <password>")
            return
        username, password = parts[1], parts[2]
        try:
            await self.auth.login(username, sha256_hex(password))
            if self.auth.session.is_online():
                print(f"Logged in as {username}")
            else:
                print("Login failed, please check credentials.")
        except ProtocolError as exc:
            logger.warning("Login failed: %s", exc)
            print(f"Login failed: {exc.message}")
        except Exception as exc:
            logger.error("Unexpected login error: %s", exc)
            print(f"Login failed unexpectedly: {exc}")

    async def _handle_register(self, parts: list[str]) -> None:
        if len(parts) < 3:
            print("Usage: register <username> <password>")
            return
        username, password = parts[1], parts[2]
        try:
            await self.auth.register(username, sha256_hex(password))
            if self.auth.session.is_online():
                print(f"Registered and logged in as {username}")
            else:
                print("Register failed, please check logs.")
        except ProtocolError as exc:
            logger.warning("Register failed: %s", exc)
            print(f"Register failed: {exc.message}")
        except Exception as exc:
            logger.error("Unexpected register error: %s", exc)
            print(f"Register failed unexpectedly: {exc}")

    async def _handle_send(self, parts: list[str]) -> None:
        if len(parts) < 4:
            print("Usage: send <conversation> <target_id> <text>")
            return
        try:
            await self.messaging.send_text(parts[1], parts[2], " ".join(parts[3:]))
        except ProtocolError as exc:
            logger.warning("Send failed: %s", exc)
            print(f"Send failed: {exc.message}")
        except Exception as exc:
            logger.error("Unexpected send error: %s", exc)
            print(f"Send failed unexpectedly: {exc}")

    async def _handle_send_room(self, parts: list[str]) -> None:
        if len(parts) < 3:
            print("Usage: send_room <room_id> <text>")
            return
        room_id = parts[1]
        text = " ".join(parts[2:])
        try:
            await self.messaging.send_room_text(room_id, text)
        except ProtocolError as exc:
            logger.warning("Send room failed: %s", exc)
            print(f"Send room failed: {exc.message}")
        except Exception as exc:
            logger.error("Unexpected room send error: %s", exc)
            print(f"Send room failed unexpectedly: {exc}")

    async def _handle_room(self, args: list[str]) -> None:
        if not args:
            print("Usage: room <create|join|leave|list|members> ...")
            return
        action = args[0]
        try:
            if action == "create":
                if len(args) < 2:
                    print("Usage: room create <room_id> [encrypted]")
                    return
                room_id = args[1]
                encrypted = len(args) > 2 and args[2].lower() in {"1", "true", "yes", "on"}
                result = await self.rooms.create_room(room_id, encrypted)
                print(f"Room {room_id} created. Members: {result.get('members', [])}")
            elif action == "join":
                if len(args) < 2:
                    print("Usage: room join <room_id>")
                    return
                room_id = args[1]
                result = await self.rooms.join_room(room_id)
                print(f"Joined room {room_id}. Members: {result.get('members', [])}")
            elif action == "leave":
                if len(args) < 2:
                    print("Usage: room leave <room_id>")
                    return
                room_id = args[1]
                await self.rooms.leave_room(room_id)
                print(f"Left room {room_id}.")
            elif action == "list":
                rooms = await self.rooms.list_rooms()
                print("Your rooms:", rooms)
            elif action == "members":
                if len(args) < 2:
                    print("Usage: room members <room_id>")
                    return
                room_id = args[1]
                members = await self.rooms.list_members(room_id)
                print(f"Members of {room_id}: {members}")
            else:
                print("Unknown room command.")
        except ProtocolError as exc:
            logger.warning("Room command failed: %s", exc)
            print(f"Room command failed: {exc.message}")
        except Exception as exc:
            logger.error("Unexpected room command error: %s", exc)
            print(f"Room command failed unexpectedly: {exc}")

    async def _print_incoming(self) -> None:
        while not self._receiver_stop.is_set():
            try:
                msg = await self.messaging.next_message(timeout=1.0)
                if not msg:
                    continue
                payload = msg.get("payload", {})
                content = payload.get("content", {})
                text = content.get("text") or content
                sender = payload.get("sender_id", "unknown")
                convo = payload.get("conversation_id", "n/a")
                print(f"\n[{convo}] {sender}: {text}")
                print("> ", end="", flush=True)
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                logger.error("Incoming printer error: %s", exc)
                await asyncio.sleep(1)
