from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Optional, Tuple

from shared.protocol import encode_msg

from .connection import ConnectionContext


class ConnectionManager:
    """Tracks active connections and allows sending events to authenticated users."""

    def __init__(self) -> None:
        self._by_writer: Dict[asyncio.StreamWriter, ConnectionContext] = {}
        self._by_user: Dict[str, ConnectionContext] = {}

    def register(self, writer: asyncio.StreamWriter, ctx: ConnectionContext) -> None:
        self._by_writer[writer] = ctx

    def bind_user(self, ctx: ConnectionContext) -> None:
        if ctx.user_id:
            self._by_user[ctx.user_id] = ctx

    def unbind_user(self, ctx: ConnectionContext) -> None:
        if ctx.user_id and self._by_user.get(ctx.user_id) is ctx:
            self._by_user.pop(ctx.user_id, None)

    def unregister(self, writer: asyncio.StreamWriter) -> Optional[ConnectionContext]:
        ctx = self._by_writer.pop(writer, None)
        if ctx:
            self.unbind_user(ctx)
        return ctx

    def get_by_user(self, user_id: str) -> Optional[ConnectionContext]:
        return self._by_user.get(user_id)

    def get_context_by_user(self, user_id: str) -> Optional[ConnectionContext]:
        """获取指定用户的连接上下文"""
        return self._by_user.get(user_id)

    def get_all_users(self) -> List[str]:
        """获取所有已认证的在线用户ID列表"""
        return list(self._by_user.keys())

    async def send_to_user(self, user_id: str, message: dict) -> bool:
        ctx = self._by_user.get(user_id)
        if not ctx:
            return False
        try:
            ctx.writer.write(encode_msg(message))
            await ctx.writer.drain()
            return True
        except Exception:
            return False

    def cleanup_idle(self, idle_before: float) -> Dict[str, ConnectionContext]:
        removed: Dict[str, ConnectionContext] = {}
        for writer, ctx in list(self._by_writer.items()):
            if ctx.last_seen < idle_before:
                removed_ctx = self.unregister(writer)
                if removed_ctx and removed_ctx.user_id:
                    removed[removed_ctx.user_id] = removed_ctx
        return removed
