from __future__ import annotations

import asyncio
import logging

from client.config import CLIENT_CONFIG, load_config
from client.core import ClientSession, NetworkClient
from client.features import AuthManager, MessagingManager, PresenceManager, RoomManager
from client.storage import LocalDatabase
from client.ui import ChatCLI


async def run_client() -> None:
    load_config()
    logging.basicConfig(level=CLIENT_CONFIG["log_level"])
    network = NetworkClient()
    session = ClientSession(network)
    db = LocalDatabase(CLIENT_CONFIG["local_db_path"])
    auth = AuthManager(network, session)
    messaging = MessagingManager(network, session, db)
    presence = PresenceManager(network, session)
    rooms = RoomManager(network, session)
    cli = ChatCLI(auth, messaging, presence, rooms)

    await network.connect()
    await cli.run()


if __name__ == "__main__":
    asyncio.run(run_client())
