"""The Mac's music on the watch: the watch's music card shows what Spotify / Apple Music is playing, and the watch's
music buttons control them (play/pause, next, previous) and the Mac's volume.

Volume: the watch draws its own 0-16 bar from the level the host last sent and moves it one step per press, expecting
the host to apply the step and echo the new level (findings 2026-10-25). So each press moves the Mac one watch step
(100/16 %) and the Mac's level goes back to the watch in MUSIC_INFO_SET.

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
VOLUME_MAX = 16  # the watch's scale

# (script) -> stdout. Injected in tests.
Runner = Callable[[list[str]], Awaitable[tuple[int, str]]]

_STATE = """
tell application "{app}"
    if player state is stopped then return "stopped"
    set t to current track
    return (player state as text) & linefeed & (name of t) & linefeed & (artist of t)
end tell
"""
_GET_VOLUME = "output volume of (get volume settings)"
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
        self._sent: tuple[str, str, int, int] | None = None
        self._title: tuple[str, str, int] = ("CMF Studio", "", 0)  # what the music card shows now
        self.volume = VOLUME_MAX // 2  # last level sent, on the watch's 0-16 scale
        self._volume_lock = asyncio.Lock()  # held keys repeat ~4-8/s; apply presses one at a time
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

    async def mac_volume(self) -> int | None:
        """The Mac's output volume on the watch's 0-16 scale, or None (e.g. an output macOS can't set)."""
        code, out = await self.run(["osascript", "-e", _GET_VOLUME])
        if code or not out.isdigit():
            return None
        return round(int(out) * VOLUME_MAX / 100)

    async def push_if_changed(self) -> bool:
        info = await self.poll()
        if info is None:
            self._title = ("CMF Studio", "", 0)
        else:
            self._title = (
                info["track"][:64] or info["app"],
                info["artist"][:64],
                2 if info["state"] == "playing" else 1,
            )
        level = await self.mac_volume()
        if level is not None:
            self.volume = level
        return self._push()

    def _push(self, ttl_s: float = 30) -> bool:
        key = (*self._title, self.volume)
        if key == self._sent:
            return False
        self._sent = key
        track, artist, state = self._title
        self.queue.submit(
            "set_music", {"track": track, "artist": artist, "state": state, "volume": self.volume}, ttl_s=ttl_s
        )
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
                self._push(ttl_s=60)
            return
        if event["kind"] != "button" or not s.buttons_control_music:
            return
        task = asyncio.ensure_future(self.command(event["name"]))
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.warning("music button failed: %r", task.exception())

    async def command(self, button: str) -> str | None:
        if button in ("vol_up", "vol_down"):
            async with self._volume_lock:
                level = await self.mac_volume()
                if level is None:
                    log.warning("volume button: can't read the Mac's output volume")
                    return None
                level = max(0, min(VOLUME_MAX, level + (1 if button == "vol_up" else -1)))
                code, _ = await self.run(
                    ["osascript", "-e", f"set volume output volume {round(level * 100 / VOLUME_MAX)}"]
                )
                if code:
                    log.warning("volume button: osascript exited %d", code)
                    return None
                self.volume = level
                self._push()  # echo the new level so the watch's bar follows the Mac
            return "volume"
        verb = _COMMANDS.get(button)
        if verb is None:
            return None
        info = self.now or await self.poll()
        if info is None:
            return None
        code, _ = await self.run(["osascript", "-e", f'tell application "{info["app"]}" to {verb}'])
        if code:
            log.warning("music button: osascript exited %d for %s", code, info["app"])
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
