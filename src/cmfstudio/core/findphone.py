"""Find phone, Mac edition: when the watch's "find phone" is used, the Mac plays a sound until it's stopped on the
watch (or after ``MAX_S``). The phone here is the Mac — the iPhone is only a fallback companion (CLAUDE.md)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

log = logging.getLogger(__name__)

SOUND = "/System/Library/Sounds/Glass.aiff"
MAX_S = 30


class FindPhone:
    def __init__(self, store=None, sound: str = SOUND, player: str = "afplay") -> None:  # type: ignore[no-untyped-def]
        self.store = store
        self.sound = sound
        self.player = player
        self._task: asyncio.Task | None = None

    @property
    def ringing(self) -> bool:
        return self._task is not None and not self._task.done()

    def on_link_event(self, event: dict[str, Any]) -> None:
        if event["kind"] != "find_phone":
            return
        if event.get("start"):
            self.start()
        else:
            self.stop()

    def start(self) -> None:
        if self.ringing:
            return
        self._task = asyncio.ensure_future(self._ring())
        if self.store is not None:
            self.store.add_event("find_phone", {"ringing": True})

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _ring(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + MAX_S
        while loop.time() < deadline:
            try:
                proc = await asyncio.create_subprocess_exec(self.player, self.sound)
            except OSError as exc:
                log.warning("find phone: can't play a sound: %r", exc)
                return
            try:
                await proc.wait()
            except asyncio.CancelledError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                raise
