from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import Callable
from pathlib import Path

import pytest
from bleak.exc import BleakBluetoothNotAvailableError, BleakBluetoothNotAvailableReason
from cmfkit.protocol import opcodes as ops

from cmfstudio.ble.keys import MemoryKeyStore
from cmfstudio.ble.link import LinkState, WatchLink
from cmfstudio.ble.sim import StudioSim
from cmfstudio.sync import parsers

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def frames() -> dict:
    return json.loads((FIXTURES / "phase0_frames.json").read_text())


class FlakySim(StudioSim):
    """The Pro 2 simulator, able to drop the link the way CoreBluetooth reports it."""

    def __init__(self) -> None:
        super().__init__()
        self.up = False
        self.connects = 0
        self.reachable = True
        self.bt_off = False
        self._on_disconnect: Callable[[], None] | None = None
        # health sync emulation (docs/protocol-sync.md): frames sent after ACTIVITY_FETCH_2
        self.health_frames: list[tuple] = []
        self.fetch_ready_code = 0x01
        self.send_finish = True
        self.since_ts = 1_761_300_000

    def set_disconnect_handler(self, handler: Callable[[], None]) -> None:
        self._on_disconnect = handler

    async def connect(self) -> None:
        if self.bt_off:
            raise BleakBluetoothNotAvailableError(
                "Bluetooth device is turned off", BleakBluetoothNotAvailableReason.POWERED_OFF
            )
        if not self.reachable:
            raise OSError("watch not found")
        self.connects += 1
        self.up = True
        await super().connect()

    async def disconnect(self) -> None:
        self.up = False
        await super().disconnect()

    async def write(self, channel, data: bytes) -> None:
        if not self.up:
            raise ConnectionError("not connected")
        await super().write(channel, data)

    def _dispatch(self, msg) -> None:
        # Mirror the real Pro 2 (fw 1.0.0.57, research/logs/probe-1.log): BATTERY requests are never
        # answered; each TIME is followed by an unsolicited BATTERY push.
        if msg.op == ops.BATTERY:
            return
        if msg.op == parsers.ACTIVITY_FETCH_1:
            self._reply(parsers.ACTIVITY_FETCH_ACK_1, bytes([self.fetch_ready_code]))
            return
        if msg.op == parsers.ACTIVITY_FETCH_2:
            self._reply(parsers.ACTIVITY_FETCH_ACK_2, struct.pack("<I", self.since_ts) + b"\x00\x00\x00\x01")
            for op, payload in self.health_frames:
                self._reply(op, payload)
            if self.send_finish:
                self._reply(parsers.ACTIVITY_FETCH_ACK_1, b"\x02")
            return
        super()._dispatch(msg)
        if msg.op == ops.TIME:
            self._reply(ops.BATTERY, bytes([self.battery, 0]))

    def drop(self) -> None:
        self.up = False
        if self._on_disconnect:
            self._on_disconnect()


@pytest.fixture
def watch() -> FlakySim:
    return FlakySim()


@pytest.fixture
async def start_link(watch: FlakySim):
    """start_link(keys=None, **opts) -> running WatchLink on the sim; stopped at teardown."""
    running: list[tuple[WatchLink, asyncio.Task]] = []

    def start(keys: MemoryKeyStore | None = None, **kw) -> WatchLink:
        opts = {"keepalive_s": 0.05, "settle_s": 0, "backoff_s": (0.01,), "needs_pair_retry_s": 0.05} | kw
        link = WatchLink("SIM", lambda _addr: watch, keys or MemoryKeyStore(), **opts)
        running.append((link, asyncio.create_task(link.run())))
        return link

    yield start
    for link, task in running:
        await link.stop()
        await asyncio.wait_for(task, 2)


@pytest.fixture
async def paired_link(start_link):
    keys = MemoryKeyStore()
    link = start_link(keys)
    await wait_state(link, LinkState.NEEDS_PAIR)
    await link.pair()
    await wait_state(link, LinkState.CONNECTED)
    return link


async def wait_state(link: WatchLink, state: LinkState, timeout: float = 2.0) -> None:
    async def poll() -> None:
        while link.state is not state:
            await asyncio.sleep(0.005)

    await asyncio.wait_for(poll(), timeout)
