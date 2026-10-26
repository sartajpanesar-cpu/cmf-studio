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
import struct
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from bleak.exc import BleakBluetoothNotAvailableError
from cmfkit.protocol import opcodes as ops
from cmfkit.protocol import payloads as pl
from cmfkit.protocol.frame import FrameError, Message
from cmfkit.protocol.opcodes import Op
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


CUSTOM_DIAL_ID = 0xFFFFFFFF  # the photo dial's id in the dial list (findings 2026-10-25)

# Structured ("native") dial install: REPLACES an existing dial's slot (spec §11.2 / §11.4).
WF_INIT2_STRUCTURED_REQUEST = Op(0xFFFF, 0x9075)
WF_INIT2_STRUCTURED_REPLY = Op(0xFFFF, 0xA075)
STRUCTURED_KIND = 0x02  # spec: "kind = 0x02/0x03" (meaning of 3 unknown)


def structured_dial_init(size: int, old_id: int, new_id: int, kind: int = STRUCTURED_KIND) -> bytes:
    """INIT2 for 9075: kind u8 ‖ old_id u32 LE ‖ new_id u32 LE ‖ file_len u32 LE (spec §11.4)."""
    return struct.pack("<BIII", kind, old_id, new_id, size)


def parse_dial_list(payload: bytes) -> dict[str, Any]:
    """DIAL_COMMAND_RET: result u8 ‖ active index u8 ‖ total u8 ‖ max u8 ‖ total × id u32 LE (spec §11.1)."""
    _result, active, total, maximum = payload[:4]
    ids = [int.from_bytes(payload[4 + 4 * i : 8 + 4 * i], "little") for i in range(total) if 8 + 4 * i <= len(payload)]
    return {
        "active_index": active,
        "active_id": ids[active] if active < len(ids) else None,
        "total": total,
        "max": maximum,
        "ids": ids,
    }


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

    async def upload_structured_dial(
        self, blob: bytes, old_id: int, new_id: int, progress: Callable[[int, int], None] | None = None
    ) -> int:
        """Replace dial ``old_id`` with a structured dial. Returns the watch's finish byte (1 = activated & saved,
        0x0a = stored but not activated / rejected)."""
        init1 = await self.request(ops.WF_INIT1_REQUEST, pl.POLL, ops.WF_INIT1_REPLY, channel=Channel.DATA)
        if not init1.payload or init1.payload[0] != 1:
            raise RuntimeError(f"watch refused watchface transfer: {init1.payload.hex()}")
        chunk_or_finish = self._expect(ops.WF_CHUNK_REQUEST, ops.WF_FINISH_REQUEST)
        init2 = await self.request(
            WF_INIT2_STRUCTURED_REQUEST,
            structured_dial_init(len(blob), old_id, new_id),
            WF_INIT2_STRUCTURED_REPLY,
            channel=Channel.DATA,
        )
        if not init2.payload or init2.payload[0] != 1:
            chunk_or_finish.cancel()
            raise RuntimeError(f"watch rejected the structured dial header: {init2.payload.hex()}")
        while True:
            msg = await asyncio.wait_for(chunk_or_finish, self.timeout * 4)
            if msg.op == ops.WF_FINISH_REQUEST:
                await self.send(ops.WF_FINISH_ACK, pl.POLL, channel=Channel.DATA)
                return msg.payload[0] if msg.payload else 0
            offset, length = pl.chunk_request(msg.payload)
            chunk_or_finish = self._expect(ops.WF_CHUNK_REQUEST, ops.WF_FINISH_REQUEST)
            await self.send(ops.WF_CHUNK_WRITE, blob[offset : offset + length], channel=Channel.DATA)
            if progress:
                progress(min(offset + length, len(blob)), len(blob))


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
        self.op_timeout_s = 15.0
        self.bt_off_poll_s = 2.0
        self.last_keepalive_at: float | None = None
        self._last_time_sync = 0.0

        self.state = LinkState.DISCONNECTED
        self.connected = asyncio.Event()
        self.battery: int | None = None
        self.dials: dict[str, Any] | None = None  # from DIAL_COMMAND_RET pushes (after login / TIME)
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
        self._hooks: list[Callable[[Message], bool]] = []

    # -- events ---------------------------------------------------------------

    def subscribe(self, listener: Callable[[Event], None]) -> None:
        self._listeners.append(listener)

    def add_message_hook(self, hook: Callable[[Message], bool]) -> None:
        """Raw watch->Mac messages not claimed by a pending request; return True to consume one."""
        self._hooks.append(hook)

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
            "last_keepalive_at": self.last_keepalive_at,
            "last_error": self.last_error,
            "dials": self.dials,
        }

    @property
    def custom_face_active(self) -> bool | None:
        """Is the watch showing the custom photo dial? None until the watch has reported its dial list."""
        if not self.dials:
            return None
        return self.dials["active_id"] == CUSTOM_DIAL_ID

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
            except BleakBluetoothNotAvailableError as exc:
                # Mac Bluetooth off: attempts fail in ~0.1 s, so poll quickly and reconnect as soon as it's back
                # instead of climbing the backoff ladder (findings 2026-10-25, watchd-C.log)
                if self._stopping:
                    break
                if self.state is not LinkState.DISCONNECTED:
                    log.warning("Bluetooth unavailable: %r", exc)
                self._set_state(LinkState.DISCONNECTED, repr(exc))
                self._attempt = 0
                delay = self.bt_off_poll_s
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
                # bound the whole exchange (write + reply): nothing in here may hang the supervisor
                async with asyncio.timeout(self.op_timeout_s):
                    if time.monotonic() - self._last_time_sync >= self.time_sync_s:
                        await self._sync_time(self._session)
                        log.info("heartbeat: connected, battery %s%%", self.battery)
                    await self._session.firmware_version()  # answered every time, unlike BATTERY
            self.last_keepalive_at = time.time()

    async def _teardown(self) -> None:
        self.connected.clear()
        transport, self._transport, self._session = self._transport, None, None
        if transport is not None:
            # CoreBluetooth may never confirm a disconnect for a link that died while the Mac slept
            with contextlib.suppress(Exception):
                await asyncio.wait_for(transport.disconnect(), self.op_timeout_s)

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
        for hook in self._hooks:
            try:
                if hook(msg):
                    return
            except Exception:
                log.exception("message hook failed for %s", ops.name_of(msg.op))
        if msg.op == ops.MUSIC_BUTTON:
            event = buttons.decode(msg.payload)
            self._emit("button", name=event.name, raw=event.raw.hex())
        elif msg.op == ops.BATTERY and msg.payload:
            self.battery = msg.payload[0]
        elif msg.op == ops.DIAL_COMMAND_RET and len(msg.payload) >= 4:
            self.dials = parse_dial_list(msg.payload)
            self._emit("frame", op=ops.name_of(msg.op), payload=msg.payload.hex())
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

    @contextlib.asynccontextmanager
    async def exclusive(self) -> AsyncIterator[StudioSession]:
        """Hold the link for a multi-step exchange (e.g. a health sync); keepalives wait meanwhile."""
        async with self._op() as s:
            yield s

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

    async def set_weather(self, payload: bytes) -> None:
        """WEATHER_SET_1 (FFFF:906B); the watch sends no reply (spec §9, Gadgetbridge)."""
        if len(payload) != 199:
            raise ValueError(f"weather payload must be 199 bytes, got {len(payload)}")
        async with self._op() as s:
            await s.send(ops.WEATHER_SET, payload)

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
        if ok:
            self._mark_custom_active()
        return ok

    async def install_native_face(self, blob: bytes, old_id: int, new_id: int) -> int:
        """Replace stock dial ``old_id`` with a structured dial saved as ``new_id``. Returns the finish byte."""
        if self.dials and old_id not in self.dials["ids"]:
            raise ValueError(f"dial {old_id} isn't on the watch")
        async with self._op() as s:
            result = await s.upload_structured_dial(blob, old_id, new_id)
            await asyncio.sleep(self.settle_s)
        if result == 1 and self.dials:
            ids = [new_id if i == old_id else i for i in self.dials["ids"]]
            index = ids.index(new_id)
            self.dials = {**self.dials, "ids": ids, "active_index": index, "active_id": new_id}
        return result

    def _mark_custom_active(self) -> None:
        """A successful photo install activates the custom dial, but the watch only re-reports its dial
        list with the next TIME push (≤15 min); update our copy now so refresh logic sees the truth."""
        if not self.dials:
            return
        ids = list(self.dials["ids"])
        if CUSTOM_DIAL_ID not in ids:
            ids.append(CUSTOM_DIAL_ID)
        index = ids.index(CUSTOM_DIAL_ID)
        self.dials = {**self.dials, "ids": ids, "total": len(ids), "active_index": index, "active_id": CUSTOM_DIAL_ID}

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
