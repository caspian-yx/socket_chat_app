from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Optional, TYPE_CHECKING

from server.core.connection_manager import ConnectionManager
from server.storage.memory import InMemoryRepository

if TYPE_CHECKING:
    from server.services.presence_service import PresenceService

logger = logging.getLogger(__name__)


class PresenceCleaner:
    """Periodically scans idle connections and updates presence state."""

    def __init__(
        self,
        connection_manager: ConnectionManager,
        repository: InMemoryRepository,
        timeout: int = 30,
        interval: int = 5,
        presence_service: Optional["PresenceService"] = None,
    ) -> None:
        self.connection_manager = connection_manager
        self.repository = repository
        self.timeout = timeout
        self.interval = interval
        self.presence_service = presence_service
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="presence-cleaner")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                expire_before = time.time() - self.timeout
                removed = self.connection_manager.cleanup_idle(expire_before)
                for user_id in removed:
                    self.repository.update_presence(user_id, "offline")
                    # 广播用户离线状态
                    if self.presence_service:
                        self._broadcast_offline(user_id)
                if removed:
                    logger.info("Cleaned up %s idle sessions", len(removed))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Presence cleaner failed: %s", exc)
            await asyncio.sleep(self.interval)

    def _broadcast_offline(self, user_id: str) -> None:
        """广播用户离线状态"""
        if not self.presence_service:
            return

        event_message = self.presence_service.broadcast_event(user_id, "offline")
        online_users = self.connection_manager.get_all_users()
        for online_user in online_users:
            if online_user != user_id:
                # 使用asyncio创建异步任务发送
                asyncio.create_task(
                    self.connection_manager.send_to_user(online_user, event_message)
                )
