"""Phase 0 notify retest: send test notifications, stay connected, and log every raw frame.

Run with cmfkit's venv: refs/CMF-Open/.venv/bin/python scripts/phase0_notify.py (--sim | --address UUID)
Uses the auth key cmfkit stored under $XDG_CONFIG_HOME (phase0_live.sh sets that up); never pairs.
Writes to the watch: TIME (on login) and APP_NOTIFICATION only.

Run 4's `cmfkit notify` disconnected 300 ms after sending, got no APP_NOTIFICATION_ACK, and dropped one
frame it couldn't decrypt. This holds the link open and prints raw hex so that frame can be decoded.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from cmfkit import keystore
from cmfkit.protocol import opcodes as ops
from cmfkit.protocol import payloads as pl
from cmfkit.session import Session

TESTS = [
    ("CMF Studio", "test 1: generic icon", pl.ICONS["generic"]),
    ("CMF Studio", "test 2: WhatsApp icon", pl.ICONS["whatsapp"]),
]


async def main(args: argparse.Namespace) -> int:
    if args.sim:
        from cmfkit.transport.sim import SimWatch

        transport = SimWatch()
    else:
        from cmfkit.transport.ble import BleTransport

        transport = BleTransport(args.address)
    await transport.connect()
    session = Session(transport)
    decode = session._on_notify

    def raw(channel, data: bytes) -> None:
        print(f"RAW {channel.value:<7} {data.hex()}", flush=True)
        decode(channel, data)

    transport.set_notify_handler(raw)
    try:
        if args.sim:
            await session.pair()
        else:
            key = keystore.get(args.address)
            if not key:
                print("no stored auth key for this address; run the pair step first", file=sys.stderr)
                return 1
            await session.login(key)
        session.on_message(lambda m: print(f"MSG {ops.name_of(m.op):<20} {m.payload.hex()}", flush=True))
        await asyncio.sleep(2)  # let the post-login pushes settle
        for title, body, icon in TESTS:
            print(f"--> sending notification: {title} / {body} (icon {icon})  WATCH THE SCREEN", flush=True)
            await session.notify(title, body, icon)
            await asyncio.sleep(args.hold)
        return 0
    finally:
        await transport.disconnect()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--sim", action="store_true")
    g.add_argument("--address")
    p.add_argument("--hold", type=float, default=8, help="seconds to stay connected after each notification")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("cmfkit").setLevel(logging.DEBUG)
    sys.exit(asyncio.run(main(a)))
