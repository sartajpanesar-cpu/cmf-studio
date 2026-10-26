"""Automatic health sync: shortly after every (re)connect, then every ``interval_s`` while connected.

The watch keeps a sync cursor (findings 2026-10-25), so each sync is incremental and cheap; the Mac
is the only consumer, so data only lands if we fetch it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from ..ble.link import WatchLink
from .jobs import JobQueue

log = logging.getLogger(__name__)


class SyncScheduler:
    def __init__(
        self, queue: JobQueue, link: WatchLink, *, interval_s: float = 3600.0, after_connect_s: float = 15.0
    ) -> None:
        self.queue = queue
        self.link = link
        self.interval_s = interval_s
        self.after_connect_s = after_connect_s

    def _sync_pending(self) -> bool:
        return any(j.kind == "sync" for j in self.queue.store.open_jobs())

    def kick(self) -> None:
        if self._sync_pending():
            return
        log.info("scheduling health sync")
        self.queue.submit("sync", {"auto": True}, ttl_s=self.interval_s)

    async def run(self) -> None:
        while True:
            await self.link.connected.wait()
            await asyncio.sleep(self.after_connect_s)  # let login pushes and keepalive settle
            while self.link.connected.is_set():
                self.kick()
                # wake early if the link drops, so the next connect gets a prompt sync
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wait_disconnect(), self.interval_s)

    async def _wait_disconnect(self) -> None:
        while self.link.connected.is_set():
            await asyncio.sleep(1)
