"""Phase 0 listen step: push a 'now playing' track so the watch's music controls are live,
then print every unsolicited frame for N seconds (phase0_live.sh timestamps the log).

Run with cmfkit's venv: refs/CMF-Open/.venv/bin/python scripts/phase0_listen.py (--sim | --address UUID)
Uses the auth key cmfkit stored under $XDG_CONFIG_HOME (phase0_live.sh sets that up); never pairs.
Writes to the watch: TIME (on login) and MUSIC_INFO_SET only.
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


async def main(args: argparse.Namespace) -> int:
    if args.sim:
        from cmfkit.transport.sim import BUTTONS, SimWatch

        transport = SimWatch()
    else:
        from cmfkit.transport.ble import BleTransport

        transport = BleTransport(args.address)
    await transport.connect()
    session = Session(transport)
    try:
        if args.sim:
            await session.pair()
        else:
            key = keystore.get(args.address)
            if not key:
                print("no stored auth key for this address; run the pair step first", file=sys.stderr)
                return 1
            await session.login(key)
        session.on_message(lambda m: print(f"{ops.name_of(m.op):<20} {m.payload.hex()}", flush=True))
        await session.set_music("CMF Studio", "press the buttons", pl.MUSIC_PLAYING)
        print(f"pushed now-playing track; listening {args.seconds:.0f}s", flush=True)
        print("  On the watch: open the music screen, press play/pause, next, prev, vol +/-;", flush=True)
        print("  then 'find phone' from the watch menu if it has one. Note the order you pressed them.", flush=True)
        if args.sim:
            for button in BUTTONS:
                transport.press(button)  # type: ignore[attr-defined]
        await asyncio.sleep(args.seconds)
        return 0
    finally:
        await transport.disconnect()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--sim", action="store_true")
    g.add_argument("--address")
    p.add_argument("--seconds", type=float, default=45)
    a = p.parse_args()
    # cmfkit frames at DEBUG; bleak's per-advertisement chatter stays at INFO
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("cmfkit").setLevel(logging.DEBUG)
    sys.exit(asyncio.run(main(a)))
