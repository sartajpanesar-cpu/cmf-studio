"""Diagnostic: which repeated requests does the watch answer within one session?

watchd's battery keepalive gets no reply after the first one in a session, although the link stays
up (findings 2026-10-25). Hypotheses: the watch drops byte-identical frames (static IV => a repeated
request encrypts identically), or answers each GET only once per session.

Run by the owner with watchd STOPPED (one BLE owner) and iPhone Bluetooth off:
    uv run python scripts/probe_replies.py --address <uuid> | tee research/logs/probe-1.log
    uv run python scripts/probe_replies.py --sim
Writes to the watch: TIME (login and step 8) and three notifications. Uses the Keychain key; never pairs.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from cmfkit.protocol import opcodes as ops
from cmfkit.protocol import payloads as pl

from cmfstudio.ble.keys import KeychainKeyStore
from cmfstudio.ble.link import StudioSession


async def probe(session: StudioSession, label: str, op, payload: bytes, reply, timeout: float = 4) -> bool:
    t0 = time.monotonic()
    try:
        msg = await session.request(op, payload, reply, timeout=timeout)
        print(f"  ANSWERED  {label:<44} {1000 * (time.monotonic() - t0):5.0f} ms  {msg.payload.hex()[:24]}", flush=True)
        return True
    except TimeoutError:
        print(f"  NO REPLY  {label:<44} (waited {timeout:.0f} s)", flush=True)
        return False


async def main(args: argparse.Namespace) -> int:
    if args.sim:
        from cmfkit.transport.sim import SimWatch

        transport = SimWatch()
    else:
        from cmfstudio.ble.transport import BleTransport

        transport = BleTransport(args.address)
    await transport.connect()
    session = StudioSession(transport)
    session.on_message(lambda m: print(f"  (unsolicited {ops.name_of(m.op)} {m.payload.hex()[:24]})", flush=True))
    try:
        if args.sim:
            await session.pair()
        else:
            key = KeychainKeyStore().get(args.address)
            if not key:
                print("no key in Keychain; run `cmf pair` via watchd first", file=sys.stderr)
                return 1
            await session.login(key)
        print("logged in; settling 3 s", flush=True)
        await asyncio.sleep(3)
        b, fw, sn = ops.BATTERY, ops.FIRMWARE_VERSION_GET, ops.SERIAL_NUMBER_GET
        print("1-5: repeated GETs (identical frames)")
        await probe(session, "1 BATTERY", b, pl.POLL, ops.BATTERY)
        await probe(session, "2 BATTERY again (identical frame)", b, pl.POLL, ops.BATTERY)
        await probe(session, "3 FIRMWARE_VERSION_GET", fw, pl.POLL, ops.FIRMWARE_VERSION_RET)
        await probe(session, "4 FIRMWARE_VERSION_GET again", fw, pl.POLL, ops.FIRMWARE_VERSION_RET)
        await probe(session, "5 SERIAL_NUMBER_GET", sn, pl.POLL, ops.SERIAL_NUMBER_RET)
        print("6: idle 20 s, then BATTERY")
        await asyncio.sleep(20)
        await probe(session, "6 BATTERY after 20 s idle", b, pl.POLL, ops.BATTERY)
        print("7: BATTERY with a different (non-A5) payload byte")
        await probe(session, "7 BATTERY payload 00", b, b"\x00", ops.BATTERY)
        print("8: fresh TIME, then BATTERY")
        await session.send(ops.TIME, pl.time_sync())
        await asyncio.sleep(0.5)
        await probe(session, "8 BATTERY after new TIME", b, pl.POLL, ops.BATTERY)
        print("9-11: notifications (each has a seconds timestamp; 10 and 11 are byte-identical)")
        await probe(
            session, "9 notify 'probe A'", ops.APP_NOTIFICATION, pl.notification("probe", "A"), ops.APP_NOTIFICATION_ACK
        )
        await asyncio.sleep(1.1)
        same = pl.notification("probe", "B", when=int(time.time()))
        await probe(session, "10 notify 'probe B'", ops.APP_NOTIFICATION, same, ops.APP_NOTIFICATION_ACK)
        await probe(
            session, "11 notify 'probe B' identical bytes", ops.APP_NOTIFICATION, same, ops.APP_NOTIFICATION_ACK
        )
        print("done; watch the screen: how many probe notifications appeared (A, B, B)?")
        return 0
    finally:
        await transport.disconnect()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--sim", action="store_true")
    g.add_argument("--address")
    sys.exit(asyncio.run(main(p.parse_args())))
