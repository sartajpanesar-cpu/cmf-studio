"""Detect the Mac waking from sleep without extra dependencies.

On macOS ``time.monotonic`` is ``mach_absolute_time()``, which stops while the machine sleeps;
wall-clock time doesn't. A tick whose wall-clock gap far exceeds its monotonic gap means we just
woke, so the link should reconnect now instead of waiting out its backoff.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

log = logging.getLogger(__name__)


class WakeWatcher:
    def __init__(
        self,
        on_wake: Callable[[float], None],
        *,
        tick_s: float = 5.0,
        jump_s: float = 20.0,
        wall: Callable[[], float] = time.time,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self.on_wake = on_wake
        self.tick_s = tick_s
        self.jump_s = jump_s
        self._wall = wall
        self._mono = mono

    async def run(self) -> None:
        wall, mono = self._wall(), self._mono()
        while True:
            await asyncio.sleep(self.tick_s)
            now_wall, now_mono = self._wall(), self._mono()
            slept = (now_wall - wall) - (now_mono - mono)
            wall, mono = now_wall, now_mono
            if slept > self.jump_s:
                log.info("Mac woke after ~%.0f s asleep", slept)
                self.on_wake(slept)
