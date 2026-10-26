"""watchd: the one process that owns Bluetooth. Runs the link, the job queue and the localhost API.

watchd                 # real watch (address from --address, config.json, or a scan)
watchd --sim           # cmfkit's simulated watch, separate database, in-memory keys
"""

from __future__ import annotations

import argparse
import asyncio
import faulthandler
import json
import logging
import signal
import sys
from pathlib import Path

import uvicorn

from .api.app import create_app
from .api.auth import ensure_token
from .ble.keys import KeychainKeyStore, MemoryKeyStore
from .ble.link import WatchLink
from .core.jobs import JobQueue
from .core.power import WakeWatcher
from .core.scheduler import SyncScheduler
from .render.service import FaceService
from .store.db import Store
from .sync.manager import SyncManager

APP_DIR = Path.home() / "Library" / "Application Support" / "cmf-studio"
CONFIG = APP_DIR / "config.json"
DEFAULT_PORT = 8765

log = logging.getLogger("watchd")


def load_config() -> dict:
    return json.loads(CONFIG.read_text()) if CONFIG.exists() else {}


def save_config(cfg: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2))


async def find_address() -> str:
    """Scan until exactly one CMF watch is visible, then remember it."""
    from .ble.transport import scan

    while True:
        found = await scan(10)
        if len(found) == 1:
            address, name = found[0]
            log.info("using %s (%s)", name, address)
            save_config({**load_config(), "address": address, "name": name})
            return address
        log.warning("scan found %d CMF watches; need exactly one (or pass --address). retrying", len(found))
        await asyncio.sleep(30)


async def serve(args: argparse.Namespace) -> None:
    if args.sim:
        from cmfkit.transport.sim import SimWatch

        sim = SimWatch(on_event=lambda m: log.info("[sim watch] %s", m))
        address, factory, keys = "SIM", (lambda _a: sim), MemoryKeyStore()
        db_path = args.db or APP_DIR / "sim.db"
    else:
        from .ble.transport import BleTransport

        address = args.address or load_config().get("address") or await find_address()
        factory, keys = BleTransport, KeychainKeyStore()
        db_path = args.db or APP_DIR / "cmfstudio.db"

    store = Store(db_path)
    link = WatchLink(address, factory, keys, keepalive_s=args.keepalive)
    link.subscribe(
        lambda e: store.add_event(e["kind"], {k: v for k, v in e.items() if k not in ("kind", "ts")}, e["ts"])
    )
    sync = SyncManager(store)
    sync.attach(link)
    queue = JobQueue(store, link, handlers={"sync": lambda lk, _args: sync.fetch(lk)})
    faces = FaceService(store, link, queue, APP_DIR / "faces")

    def after_job(job) -> None:
        if job.kind == "sync" and job.state == "done":
            refresh = asyncio.ensure_future(faces.refresh_after_sync())
            refresh.add_done_callback(lambda t: t.exception() and log.warning("face refresh failed: %r", t.exception()))

    queue.on_done.append(after_job)
    app = create_app(link, queue, store, ensure_token(), APP_DIR / "faces", sync, faces)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning"))

    def on_wake(slept_s: float) -> None:
        store.add_event("wake", {"slept_s": round(slept_s)})
        link.nudge()

    queue.start()
    link_task = asyncio.create_task(link.run(), name="link")
    wake_task = asyncio.create_task(WakeWatcher(on_wake).run(), name="wake")
    sync_task = asyncio.create_task(SyncScheduler(queue, link, interval_s=args.sync_every * 60).run(), name="sync")
    log.info("watchd up: %s, api http://127.0.0.1:%d, db %s", address, args.port, db_path)
    try:
        await server.serve()  # returns on SIGINT/SIGTERM
    finally:
        wake_task.cancel()
        sync_task.cancel()
        await link.stop()
        await queue.stop()
        await asyncio.wait_for(link_task, 10)
        store.close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="watchd")
    p.add_argument("--sim", action="store_true", help="use cmfkit's simulated watch")
    p.add_argument("--address", help="CoreBluetooth device UUID (default: config.json, else scan)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--db", type=Path)
    p.add_argument("--keepalive", type=float, default=60.0, help="seconds between battery keepalive polls")
    p.add_argument("--sync-every", type=float, default=60.0, help="minutes between automatic health syncs")
    p.add_argument("-v", "--verbose", action="count", default=0)
    args = p.parse_args(argv)
    logging.basicConfig(
        level=[logging.INFO, logging.DEBUG][min(args.verbose, 1)],
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # `kill -USR1 <pid>` dumps every thread's stack to the log (diagnosing hangs, findings 2026-10-25)
    faulthandler.register(signal.SIGUSR1, all_threads=True)
    if args.verbose < 2:
        logging.getLogger("bleak").setLevel(logging.INFO)  # per-advertisement chatter only with -vv
    asyncio.run(serve(args))


if __name__ == "__main__":
    main()
