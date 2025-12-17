from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Optional

from server.core.connection_manager import ConnectionManager
from server.storage.memory import InMemoryRepository

logger = logging.getLogger(__name__)


class OfflineDispatcher:
    """Delivers queued messages when users come back online."""

    def __init__(self, repository: InMemoryRepository, connection_manager: ConnectionManager) -> None:
        self.repository = repository
        self.connection_manager = connection_manager
        self._task: Optional[asyncio.Task] = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="offline-dispatcher")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def notify_user_online(self, user_id: str) -> None:
        if not user_id:
            return
        self._queue.put_nowait(user_id)

    async def _run(self) -> None:
        while True:
            user_id = await self._queue.get()
            try:
                await self._drain_user_queue(user_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Offline dispatcher failed for %s: %s", user_id, exc)
            finally:
                self._queue.task_done()

    async def _drain_user_queue(self, user_id: str) -> None:
        messages = self.repository.consume_offline_messages(user_id)
        if not messages:
            return
        logger.debug("Delivering %s offline messages to %s", len(messages), user_id)
        for event in messages:
            delivered = await self.connection_manager.send_to_user(user_id, event)
            if not delivered:
                # User went offline again; push remaining event back.
                self.repository.enqueue_offline_message(user_id, event)
                break
