"""Health sync: store every data frame raw, then parse; drive the fetch handshake.

Data frames are captured whenever they arrive (a fetch or an unsolicited push), so nothing is lost if a
parser is wrong: raw frames are kept and can be re-parsed later. Sequence per docs/protocol-sync.md.
"""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from cmfkit.protocol import opcodes as ops
from cmfkit.protocol import payloads as pl
from cmfkit.protocol.frame import Message

from ..ble.link import WatchLink
from ..store.db import Store
from . import daily
from . import parsers as p

log = logging.getLogger(__name__)

ACK_READY, ACK_FINISHED = 0x01, 0x02


@dataclass
class FetchRun:
    started: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    ready: asyncio.Future[int] = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    since_ts: int | None = None
    flags: str | None = None
    frames: Counter = field(default_factory=Counter)
    new_samples: Counter = field(default_factory=Counter)
    sleep_sessions: int = 0
    parse_errors: int = 0
    min_ts: int | None = None

    def saw(self, ts: int) -> None:
        self.min_ts = ts if self.min_ts is None else min(self.min_ts, ts)

    def summary(self) -> dict[str, Any]:
        return {
            "since_ts": self.since_ts,
            "flags": self.flags,
            "frames": dict(self.frames),
            "new_samples": dict(self.new_samples),
            "sleep_sessions": self.sleep_sessions,
            "parse_errors": self.parse_errors,
            "seconds": round(time.monotonic() - self.started, 1),
        }


class SyncManager:
    def __init__(self, store: Store, *, idle_s: float = 30.0, total_s: float = 600.0, ready_s: float = 10.0) -> None:
        self.store = store
        self.idle_s = idle_s
        self.total_s = total_s
        self.ready_s = ready_s
        self._run: FetchRun | None = None

    def attach(self, link: WatchLink) -> None:
        link.add_message_hook(self.on_message)

    # -- capture ----------------------------------------------------------------

    def on_message(self, msg: Message) -> bool:
        run = self._run
        if msg.op == p.ACTIVITY_FETCH_ACK_1:
            code = msg.payload[0] if msg.payload else -1
            if run and not run.ready.done():
                run.ready.set_result(code)
            elif run and code == ACK_FINISHED:
                run.finished.set()
            else:
                log.info("unexpected ACTIVITY_FETCH_ACK_1 %s", msg.payload.hex())
            return True
        if msg.op == p.ACTIVITY_FETCH_ACK_2:
            if run and len(msg.payload) >= 8:
                run.since_ts = struct.unpack_from("<I", msg.payload)[0]
                run.flags = msg.payload[4:8].hex()
                run.last_activity = time.monotonic()
            return True
        if msg.op in p.DATA_OPS:
            self.ingest(msg.op, msg.payload, run)
            return True
        return False

    def ingest(self, op: ops.Op, payload: bytes, run: FetchRun | None = None) -> None:
        parsed = op not in p.RAW_ONLY
        # canonical "cmd1:cmd2" hex, so reparse() can always decode it
        frame_id = self.store.add_raw_frame(str(op), payload, p.PARSER_VERSION if parsed else None)  # raw first
        self._parse(op, payload, frame_id, run)

    def _parse(self, op: ops.Op, payload: bytes, frame_id: int, run: FetchRun | None) -> None:
        name = ops.name_of(op) if op in ops.NAMES else str(op)
        parsed = op not in p.RAW_ONLY
        if run:
            run.frames[name] += 1
            run.last_activity = time.monotonic()
        if not parsed:
            return
        try:
            if op == p.SLEEP_DATA:
                session = p.sleep(payload)
                self.store.upsert_sleep(session, frame_id)
                if run:
                    run.sleep_sessions += 1
                    run.saw(session.end_ts)
                return
            by_metric: dict[str, list[p.Sample]] = {}
            for sample in p.SAMPLE_PARSERS[op](payload):
                by_metric.setdefault(sample.metric, []).append(sample)
            for metric, samples in by_metric.items():
                new = self.store.upsert_samples(samples, frame_id)
                if run:
                    run.new_samples[metric] += new
                    run.saw(min(s.ts for s in samples))
        except p.ParseError as exc:
            log.warning("parse failed (raw frame %d kept): %s", frame_id, exc)
            self.store.add_event("parse_error", {"frame_id": frame_id, "op": str(op), "error": str(exc)})
            if run:
                run.parse_errors += 1

    # -- fetch ------------------------------------------------------------------

    async def fetch(self, link: WatchLink) -> dict[str, Any]:
        """Run one ACTIVITY_FETCH handshake and collect until the watch says it's finished."""
        if self._run:
            raise RuntimeError("a sync is already running")
        run = self._run = FetchRun()
        try:
            async with link.exclusive() as s:
                await s.send(p.ACTIVITY_FETCH_1, pl.POLL)
                code = await asyncio.wait_for(run.ready, self.ready_s)
                if code != ACK_READY:
                    raise RuntimeError(f"watch declined the fetch (ACK_1 code {code:#04x})")
                run.last_activity = time.monotonic()
                await s.send(p.ACTIVITY_FETCH_2, pl.POLL)
                while not run.finished.is_set():
                    try:
                        await asyncio.wait_for(run.finished.wait(), 1.0)
                    except TimeoutError:
                        now = time.monotonic()
                        if now - run.last_activity > self.idle_s:
                            raise TimeoutError(f"sync stalled: no data for {self.idle_s:.0f} s") from None
                        if now - run.started > self.total_s:
                            raise TimeoutError(f"sync exceeded {self.total_s:.0f} s") from None
            if run.min_ts is not None:
                daily.rebuild(self.store, run.min_ts)
            result = run.summary()
            self.store.add_event("sync", result)
            log.info("sync done: %s", result)
            return result
        finally:
            self._run = None

    def reparse(self) -> dict[str, Any]:
        """Re-run the current parsers over every stored raw frame (after a parser fix), then rebuild `daily`."""
        run = FetchRun()
        rows = self.store.db.execute("SELECT id, opcode, payload_hex FROM raw_frames ORDER BY id").fetchall()
        for row in rows:
            cmd1, cmd2 = (int(x, 16) for x in row["opcode"].split(":"))
            op = ops.Op(cmd1, cmd2)
            if op in p.RAW_ONLY or op not in p.DATA_OPS:
                continue
            self._parse(op, bytes.fromhex(row["payload_hex"]), row["id"], run)
            self.store.db.execute(
                "UPDATE raw_frames SET parser_version = ? WHERE id = ?", (p.PARSER_VERSION, row["id"])
            )
        days = daily.rebuild(self.store)
        return {**run.summary(), "days_rebuilt": len(days)}
