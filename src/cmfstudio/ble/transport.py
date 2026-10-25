"""bleak transport that reports disconnects (cmfkit's BleTransport doesn't).

Implements cmfkit's ``Transport`` protocol so ``cmfkit.session.Session`` runs on it unchanged.
macOS CoreBluetooth addresses are per-Mac device UUIDs, not MACs.
"""

from __future__ import annotations

from collections.abc import Callable

from bleak import BleakClient, BleakScanner
from cmfkit.transport.base import Channel, NotifyHandler
from cmfkit.transport.ble import NAME_PREFIX, NOTIFY_UUIDS, WRITE_UUIDS


async def scan(timeout: float = 8.0) -> list[tuple[str, str]]:
    devices = await BleakScanner.discover(timeout=timeout)
    return [(d.address, d.name or "") for d in devices if (d.name or "").startswith(NAME_PREFIX)]


class BleTransport:
    def __init__(self, address: str) -> None:
        self.name = address
        self._client: BleakClient | None = None
        self._handler: NotifyHandler | None = None
        self._on_disconnect: Callable[[], None] | None = None

    @property
    def mtu(self) -> int:
        return self._client.mtu_size if self._client else 23

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    def set_notify_handler(self, handler: NotifyHandler) -> None:
        self._handler = handler

    def set_disconnect_handler(self, handler: Callable[[], None]) -> None:
        self._on_disconnect = handler

    async def connect(self) -> None:
        self._client = BleakClient(self.name, disconnected_callback=lambda _c: self._disconnected())
        await self._client.connect()
        available = {c.uuid for s in self._client.services for c in s.characteristics}
        for channel, uuid in NOTIFY_UUIDS.items():
            if uuid in available:
                await self._client.start_notify(uuid, self._callback(channel))

    def _callback(self, channel: Channel):
        def callback(_char, data: bytearray) -> None:
            if self._handler:
                self._handler(channel, bytes(data))

        return callback

    def _disconnected(self) -> None:
        if self._on_disconnect:
            self._on_disconnect()

    async def disconnect(self) -> None:
        if self._client:
            client, self._client = self._client, None
            await client.disconnect()

    async def write(self, channel: Channel, data: bytes) -> None:
        if not self._client:
            raise ConnectionError("not connected")
        await self._client.write_gatt_char(WRITE_UUIDS[channel], data, response=False)
