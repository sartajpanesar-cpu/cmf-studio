"""The single owner of the watch connection: connect, login, keepalive, reconnect.

Rules from Phase 0 (research/findings.md):
- Never pair automatically (Tier 2). No key, or a key the watch rejects (Nothing X re-bound it),
  puts the link in NEEDS_PAIR until ``pair()`` is called explicitly.
- Stay connected after writes: run 4's notify vanished when cmfkit disconnected 300 ms after sending.
- Hold the link a few seconds after a face install (watch showed "failed" after an instant drop).
- fw 1.0.0.57 never answers BATTERY requests (probe-1). Battery arrives only in the push burst that
  follows each TIME, so we re-send TIME periodically; the keepalive is FIRMWARE_VERSION_GET, which
  is answered every time.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from cmfkit.protocol import opcodes as ops
from cmfkit.protocol import payloads as pl
from cmfkit.protocol.frame import FrameError, Message
from cmfkit.session import AuthError, Session
from cmfkit.transport.base import Channel, Transport

from . import buttons
from .frames import SafeFrameDecoder
from .keys import KeyStore

log = logging.getLogger(__name__)

# Phone model string sent at login. "CMF Watch 3 Pro" is cmfkit's default and verified accepted
# by the Pro 2 on fw 1.0.0.57 (live runs 1-5).
MODEL = "CMF Watch 3 Pro"

Event = dict[str, Any]


async def _wait_any(events: tuple[asyncio.Event, ...], timeout: float) -> None:
    waiters = [asyncio.ensure_future(e.wait()) for e in events]
    try:
        await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for w in waiters:
            w.cancel()


class LinkState(enum.StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    NEEDS_PAIR = "needs_pair"
    STOPPED = "stopped"


class LinkError(RuntimeError):
    pass


class NotConnectedError(LinkError):
    pass


class NotPairedError(LinkError):
    pass


class StaleKeyError(LinkError):
    """The watch rejected our stored key; usually Nothing X paired with it since."""


class StudioSession(Session):
    """cmfkit Session with our frame decoder and a raw-bytes hook."""

    def __init__(self, transport: Transport, raw_hook: Callable[[Channel, bytes], None] | None = None) -> None:
        super().__init__(transport, model=MODEL)
        self._decoder = SafeFrameDecoder()
        self._raw_hook = raw_hook

    def _on_notify(self, channel: Channel, data: bytes) -> None:
        if self._raw_hook:
            self._raw_hook(channel, data)
        super()._on_notify(channel, data)


class WatchLink:
    def __init__(
        self,
        address: str,
        transport_factory: Callable[[str], Transport],
        keys: KeyStore,
        *,
        keepalive_s: float = 60.0,
        settle_s: float = 3.0,
        backoff_s: tuple[float, ...] = (1, 2, 5, 10, 30, 60),
        needs_pair_retry_s: float = 300.0,
        time_sync_s: float = 900.0,
    ) -> None:
        self.address = address
        self._factory = transport_factory
        self._keys = keys
        self.keepalive_s = keepalive_s
        self.settle_s = settle_s
        self.backoff_s = backoff_s
        self.needs_pair_retry_s = needs_pair_retry_s
        self.time_sync_s = time_sync_s
        self._last_time_sync = 0.0

        self.state = LinkState.DISCONNECTED
        self.connected = asyncio.Event()
        self.battery: int | None = None
        self.firmware: str | None = None
        self.serial: str | None = None
        self.last_error: str | None = None
        self.connected_since: float | None = None

        self._transport: Transport | None = None
        self._session: StudioSession | None = None
        self._lock = asyncio.Lock()
        self._lost = asyncio.Event()
        self._wake = asyncio.Event()
        self._poke = asyncio.Event()
        self._attempt = 0
        self._stopping = False
        self._listeners: list[Callable[[Event], None]] = []

    # -- events ---------------------------------------------------------------

    def subscribe(self, listener: Callable[[Event], None]) -> None:
        self._listeners.append(listener)

    def _emit(self, kind: str, **data: Any) -> None:
        event = {"kind": kind, "ts": time.time(), **data}
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                log.exception("event listener failed")

    def _set_state(self, state: LinkState, error: str | None = None) -> None:
        if error is not None:
            self.last_error = error
        if state is self.state:
            return
        self.state = state
        log.info("link %s%s", state.value, f" ({error})" if error else "")
        if state is LinkState.CONNECTED:
            self.connected.set()
            self.connected_since = time.time()
        else:
            self.connected.clear()
            self.connected_since = None
        self._emit("state", state=state.value, error=error)

    def status(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "state": self.state.value,
            "battery": self.battery,
            "firmware": self.firmware,
            "serial": self.serial,
            "connected_since": self.connected_since,
            "last_error": self.last_error,
        }

    # -- supervisor -----------------------------------------------------------

    async def run(self) -> None:
        """Keep the link up until ``stop()``. Run as a long-lived task."""
        while not self._stopping:
            delay = 0.0
            try:
                await self._connect()
                self._attempt = 0
                await self._hold()
            except (NotPairedError, StaleKeyError) as exc:
                self._set_state(LinkState.NEEDS_PAIR, str(exc))
                delay = self.needs_pair_retry_s
            except Exception as exc:  # BLE errors come in many types; all mean "try again later"
                if self._stopping:
                    break
                log.warning("link down: %r", exc)
                self._set_state(LinkState.DISCONNECTED, repr(exc))
                delay = self.backoff_s[min(self._attempt, len(self.backoff_s) - 1)]
                self._attempt += 1
            finally:
                await self._teardown()
            if not self._stopping and delay:
                self._wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), delay)
        self._set_state(LinkState.STOPPED)

    def nudge(self) -> None:
        """Reconnect now / check the link now, e.g. after the Mac wakes (core.power.WakeWatcher)."""
        self._attempt = 0
        self._wake.set()
        self._poke.set()

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        self._lost.set()

    async def _connect(self) -> None:
        self._set_state(LinkState.CONNECTING)
        key = self._keys.get(self.address)
        if not key:
            raise NotPairedError("no pairing key in Keychain for this watch; run `cmf pair`")
        self._lost.clear()
        transport = self._factory(self.address)
        if hasattr(transport, "set_disconnect_handler"):
            transport.set_disconnect_handler(lambda: self._lost.set())
        self._transport = transport
        await transport.connect()
        session = StudioSession(transport, raw_hook=self._on_raw)
        session.on_message(self._on_message)
        self._session = session
        try:
            await session.login(key)
        except (AuthError, TimeoutError, FrameError) as exc:
            # Run 3: a re-bound watch answers with frames our key can't decrypt, then times out.
            # (The simulator instead fails to decode our frame: FrameError.)
            raise StaleKeyError(f"watch rejected the stored key ({exc}); re-pair with `cmf pair`") from exc
        self._last_time_sync = time.monotonic()  # login() ends with TIME
        self._set_state(LinkState.CONNECTED, error=None)
        async with self._lock:
            await self._refresh_info(session)

    async def _hold(self) -> None:
        """Return (by raising) when the link is lost; keepalive + periodic TIME meanwhile."""
        while not self._stopping:
            await _wait_any((self._lost, self._poke), self.keepalive_s)
            self._poke.clear()
            if self._stopping:
                return
            if self._lost.is_set():
                raise ConnectionError("watch disconnected")
            async with self._lock:
                assert self._session is not None
                if time.monotonic() - self._last_time_sync >= self.time_sync_s:
                    await self._sync_time(self._session)
                await self._session.firmware_version()  # answered every time, unlike BATTERY

    async def _teardown(self) -> None:
        self.connected.clear()
        transport, self._transport, self._session = self._transport, None, None
        if transport is not None:
            with contextlib.suppress(Exception):
                await transport.disconnect()

    async def _sync_time(self, session: Session) -> None:
        """Set the watch clock; the watch answers with a push burst that includes BATTERY."""
        await session.send(ops.TIME, pl.time_sync())
        self._last_time_sync = time.monotonic()

    async def _refresh_info(self, session: Session) -> None:
        self.firmware = await session.firmware_version()
        self.serial = await session.serial_number()

    def _on_raw(self, channel: Channel, data: bytes) -> None:
        if channel is Channel.SHELL:  # carries the AT GETSECRET reply; never log it
            return
        log.debug("raw %s %s", channel.value, data.hex())

    def _on_message(self, msg: Message) -> None:
        if msg.op == ops.MUSIC_BUTTON:
            event = buttons.decode(msg.payload)
            self._emit("button", name=event.name, raw=event.raw.hex())
        elif msg.op == ops.BATTERY and msg.payload:
            self.battery = msg.payload[0]
        else:
            self._emit("frame", op=ops.name_of(msg.op), payload=msg.payload.hex())

    # -- operations (serialized; callers get NotConnectedError while the watch is away) ---------

    @contextlib.asynccontextmanager
    async def _op(self) -> AsyncIterator[StudioSession]:
        if not self.connected.is_set():
            raise NotConnectedError(f"watch is {self.state.value}")
        async with self._lock:
            if self._session is None:
                raise NotConnectedError("watch disconnected")
            yield self._session

    async def info(self) -> dict[str, Any]:
        async with self._op() as s:
            await self._sync_time(s)  # triggers a fresh BATTERY push
            await self._refresh_info(s)
            await asyncio.sleep(0.3)
        return self.status()

    async def notify(self, title: str, body: str = "", icon: int = pl.ICONS["generic"]) -> bool:
        """Show a notification. Returns whether the watch ACKed it (0065:0003)."""
        async with self._op() as s:
            try:
                payload = pl.notification(title, body, icon)
                await s.request(ops.APP_NOTIFICATION, payload, ops.APP_NOTIFICATION_ACK, timeout=3)
                return True
            except TimeoutError:
                return False

    async def set_music(self, track: str, artist: str, state: int = pl.MUSIC_PLAYING) -> None:
        async with self._op() as s:
            await s.set_music(track, artist, state)

    async def install_photo_face(
        self, blob: bytes, style: int = 0, x: int = 56, y: int = 77, color565: int = 0xFFFF
    ) -> bool:
        """Upload a photo dial. NOTE: replaces the single custom photo slot (findings, run 4)."""
        async with self._op() as s:
            ok = await s.upload_photo_dial(blob, style, x, y, color565)
            await asyncio.sleep(self.settle_s)
            return ok

    async def pair(self) -> None:
        """Explicit first-time pairing (Tier 2). Only allowed while not connected.

        Reads AT GETSECRET (allowlisted), then the watch shows a prompt the owner must accept.
        """
        if self.connected.is_set():
            raise LinkError("already connected with a valid key; nothing to pair")
        async with self._lock:
            transport = self._factory(self.address)
            await transport.connect()
            try:
                session = StudioSession(transport)
                key = await session.pair()
            finally:
                with contextlib.suppress(Exception):
                    await transport.disconnect()
            self._keys.put(self.address, key)
        self._emit("paired")
        self._wake.set()
