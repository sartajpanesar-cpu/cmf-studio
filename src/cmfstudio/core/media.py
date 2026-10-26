"""The Mac's music on the watch: the watch's music card shows what Spotify / Apple Music is playing, and the watch's
music buttons control them (play/pause, next, previous) and the Mac's volume.

Players are asked over AppleScript (``osascript``), only when their app is already running (``pgrep``), so nothing
is launched; macOS asks once whether watchd may control them (Automation permission). Off by default; settings in kv
``media_settings``. Button presses still reach the rules engine too — if rules.json binds the same buttons, both run.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from ..store.db import Store

log = logging.getLogger(__name__)

SETTINGS_KEY = "media_settings"
POLL_S = 3.0
PLAYERS = ("Spotify", "Music")
VOLUME_STEP = 6

# (script) -> stdout. Injected in tests.
Runner = Callable[[list[str]], Awaitable[tuple[int, str]]]

_STATE = """
tell application "{app}"
    if player state is stopped then return "stopped"
    set t to current track
    return (player state as text) & linefeed & (name of t) & linefeed & (artist of t)
end tell
"""
_COMMANDS = {"play_pause": "playpause", "next": "next track", "prev": "previous track"}


class MediaSettings(BaseModel):
    show_now_playing: bool = False
    buttons_control_music: bool = False


async def run_cmd(argv: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _err = await asyncio.wait_for(proc.communicate(), 10)
    return proc.returncode or 0, out.decode("utf-8", "replace").strip()


class MediaService:
    def __init__(self, store: Store, queue, run: Runner = run_cmd) -> None:  # type: ignore[no-untyped-def]
        self.store = store
        self.queue = queue
        self.run = run
        self.now: dict[str, Any] | None = None  # last seen {app, state, track, artist}
        self._sent: tuple[str, str, int] | None = None
        self._tasks: set[asyncio.Task] = set()

    def settings(self) -> MediaSettings:
        raw = self.store.get_kv(SETTINGS_KEY)
        return MediaSettings.model_validate_json(raw) if raw else MediaSettings()

    def save(self, s: MediaSettings) -> MediaSettings:
        self.store.set_kv(SETTINGS_KEY, s.model_dump_json())
        self._sent = None  # re-send on the next poll
        return s

    # -- reading the players ------------------------------------------------------------------------------------

    async def running(self) -> list[str]:
        out = []
        for app in PLAYERS:
            code, _ = await self.run(["pgrep", "-xq", app])
            if code == 0:
                out.append(app)
        return out

    async def poll(self) -> dict[str, Any] | None:
        """What's playing (a playing player wins over a paused one), or None."""
        best: dict[str, Any] | None = None
        for app in await self.running():
            code, out = await self.run(["osascript", "-e", _STATE.format(app=app)])
            if code or not out or out == "stopped":
                continue
            state, _, rest = out.partition("\n")
            track, _, artist = rest.partition("\n")
            info = {"app": app, "state": state.strip(), "track": track.strip(), "artist": artist.strip()}
            if best is None or (info["state"] == "playing" and best["state"] != "playing"):
                best = info
        self.now = best
        return best

    async def push_if_changed(self) -> bool:
        info = await self.poll()
        if info is None:
            key = ("CMF Studio", "", 0)
        else:
            key = (info["track"][:64] or info["app"], info["artist"][:64], 2 if info["state"] == "playing" else 1)
        if key == self._sent:
            return False
        self._sent = key
        self.queue.submit("set_music", {"track": key[0], "artist": key[1], "state": key[2]}, ttl_s=30)
        return True

    # -- buttons --------------------------------------------------------------------------------------------------

    def on_link_event(self, event: dict[str, Any]) -> None:
        s = self.settings()
        if (
            event["kind"] == "state"
            and event.get("state") == "connected"
            and (s.buttons_control_music or s.show_now_playing)
        ):
            self._sent = None
            if not s.show_now_playing:  # the watch only reports buttons after some music info (findings 2026-10-25)
                self.queue.submit("set_music", {"track": "CMF Studio", "artist": "", "state": 0}, ttl_s=60)
            return
        if event["kind"] != "button" or not s.buttons_control_music:
            return
        task = asyncio.ensure_future(self.command(event["name"]))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def command(self, button: str) -> str | None:
        if button in ("vol_up", "vol_down"):
            delta = VOLUME_STEP if button == "vol_up" else -VOLUME_STEP
            script = f"set volume output volume ((output volume of (get volume settings)) + {delta})"
            await self.run(["osascript", "-e", script])
            return "volume"
        verb = _COMMANDS.get(button)
        if verb is None:
            return None
        info = self.now or await self.poll()
        if info is None:
            return None
        await self.run(["osascript", "-e", f'tell application "{info["app"]}" to {verb}'])
        self._sent = None  # show the new track promptly
        return info["app"]

    # -- loop -----------------------------------------------------------------------------------------------------

    async def run_loop(self, link) -> None:  # type: ignore[no-untyped-def]
        while True:
            await link.connected.wait()
            if self.settings().show_now_playing:
                try:
                    await self.push_if_changed()
                except Exception as exc:  # a player hiccup shouldn't stop the loop
                    log.debug("now playing: %r", exc)
            await asyncio.sleep(POLL_S)

    def public(self) -> dict[str, Any]:
        return {"settings": self.settings().model_dump(), "now": self.now}
