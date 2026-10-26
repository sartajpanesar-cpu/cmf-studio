"""SQLite store. Schema per docs/02-architecture.md; migrations tracked with PRAGMA user_version.

Append new migrations to MIGRATIONS; never edit one that has shipped.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path.home() / "Library" / "Application Support" / "cmf-studio" / "cmfstudio.db"

MIGRATIONS: list[str] = [
    # 1: initial schema
    """
    CREATE TABLE raw_frames(
        id INTEGER PRIMARY KEY, received_at REAL NOT NULL, opcode TEXT NOT NULL,
        payload_hex TEXT NOT NULL, parser_version INTEGER);
    CREATE TABLE samples(
        metric TEXT NOT NULL, ts INTEGER NOT NULL, value REAL NOT NULL,
        source_frame_id INTEGER REFERENCES raw_frames(id), PRIMARY KEY(metric, ts));
    CREATE TABLE sleep_sessions(
        start_ts INTEGER PRIMARY KEY, end_ts INTEGER NOT NULL, stages_json TEXT NOT NULL,
        source_frame_id INTEGER REFERENCES raw_frames(id));
    CREATE TABLE workouts(
        start_ts INTEGER PRIMARY KEY, end_ts INTEGER NOT NULL, type TEXT, summary_json TEXT,
        source_frame_id INTEGER REFERENCES raw_frames(id));
    CREATE TABLE daily(date TEXT NOT NULL, metric TEXT NOT NULL, value REAL NOT NULL, PRIMARY KEY(date, metric));
    CREATE TABLE faces(
        id INTEGER PRIMARY KEY, name TEXT NOT NULL, spec_json TEXT NOT NULL,
        last_rendered_png BLOB, last_installed_at REAL);
    CREATE TABLE jobs(
        id INTEGER PRIMARY KEY, kind TEXT NOT NULL, args_json TEXT NOT NULL, state TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, finished_at REAL, error TEXT,
        result_json TEXT, not_after REAL);
    CREATE INDEX jobs_state ON jobs(state, id);
    CREATE TABLE events(id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, data_json TEXT NOT NULL);
    CREATE INDEX events_ts ON events(ts);
    """,
]

OPEN_JOB_STATES = ("queued", "running", "deferred")


@dataclass
class Job:
    id: int
    kind: str
    args: dict[str, Any]
    state: str
    attempts: int
    created_at: float
    finished_at: float | None
    error: str | None
    result: Any
    not_after: float | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Job:
        return cls(
            id=row["id"],
            kind=row["kind"],
            args=json.loads(row["args_json"]),
            state=row["state"],
            attempts=row["attempts"],
            created_at=row["created_at"],
            finished_at=row["finished_at"],
            error=row["error"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            not_after=row["not_after"],
        )

    def public(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


class Store:
    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)  # autocommit; explicit BEGIN where needed
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        if str(path) != ":memory:":
            self.db.execute("PRAGMA journal_mode = WAL")
        self.migrate()

    @property
    def version(self) -> int:
        return self.db.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self) -> None:
        for number in range(self.version + 1, len(MIGRATIONS) + 1):
            self.db.execute("BEGIN")
            try:
                # statement by statement: executescript() would commit our transaction early
                for statement in filter(str.strip, MIGRATIONS[number - 1].split(";")):
                    self.db.execute(statement)
                self.db.execute(f"PRAGMA user_version = {number}")
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def close(self) -> None:
        self.db.close()

    # -- events -----------------------------------------------------------------

    def add_event(self, kind: str, data: dict[str, Any], ts: float | None = None) -> None:
        self.db.execute(
            "INSERT INTO events(ts, kind, data_json) VALUES (?, ?, ?)",
            (ts or time.time(), kind, json.dumps(data)),
        )

    def events(self, limit: int = 100, kind: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM events" + (" WHERE kind = ?" if kind else "") + " ORDER BY id DESC LIMIT ?"
        rows = self.db.execute(sql, (kind, limit) if kind else (limit,)).fetchall()
        return [{"id": r["id"], "ts": r["ts"], "kind": r["kind"], "data": json.loads(r["data_json"])} for r in rows]

    # -- health data ------------------------------------------------------------

    def add_raw_frame(self, opcode: str, payload: bytes, parser_version: int | None) -> int:
        cur = self.db.execute(
            "INSERT INTO raw_frames(received_at, opcode, payload_hex, parser_version) VALUES (?, ?, ?, ?)",
            (time.time(), opcode, payload.hex(), parser_version),
        )
        return cur.lastrowid  # type: ignore[return-value]

    def upsert_samples(self, samples: list[Any], source_frame_id: int | None) -> int:
        """Insert-or-update on (metric, ts), so re-syncing the same data never duplicates. Returns new rows."""
        self.db.execute("BEGIN")
        try:
            new = 0
            for s in samples:
                exists = self.db.execute(
                    "SELECT 1 FROM samples WHERE metric = ? AND ts = ?", (s.metric, s.ts)
                ).fetchone()
                self.db.execute(
                    "INSERT INTO samples(metric, ts, value, source_frame_id) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(metric, ts) DO UPDATE SET "
                    "value = excluded.value, source_frame_id = excluded.source_frame_id",
                    (s.metric, s.ts, s.value, source_frame_id),
                )
                new += exists is None
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return new

    def upsert_sleep(self, session: Any, source_frame_id: int | None) -> None:
        stages = {
            "totals_s": session.totals_s,
            "unknown": session.unknown,
            "stages": [[st.ts, st.duration_s, st.stage] for st in session.stages],
        }
        self.db.execute(
            "INSERT INTO sleep_sessions(start_ts, end_ts, stages_json, source_frame_id) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(start_ts) DO UPDATE SET end_ts = excluded.end_ts, stages_json = excluded.stages_json, "
            "source_frame_id = excluded.source_frame_id",
            (session.start_ts, session.end_ts, json.dumps(stages), source_frame_id),
        )

    def samples(self, metric: str, start: int = 0, end: int = 2**31) -> list[tuple[int, float]]:
        rows = self.db.execute(
            "SELECT ts, value FROM samples WHERE metric = ? AND ts BETWEEN ? AND ? ORDER BY ts", (metric, start, end)
        )
        return [(r["ts"], r["value"]) for r in rows]

    def sample_counts(self) -> dict[str, int]:
        rows = self.db.execute("SELECT metric, count(*) AS n FROM samples GROUP BY metric")
        return {r["metric"]: r["n"] for r in rows}

    # -- jobs -------------------------------------------------------------------

    def add_job(self, kind: str, args: dict[str, Any], not_after: float | None = None) -> Job:
        cur = self.db.execute(
            "INSERT INTO jobs(kind, args_json, state, created_at, not_after) VALUES (?, ?, 'queued', ?, ?)",
            (kind, json.dumps(args), time.time(), not_after),
        )
        return self.get_job(cur.lastrowid)  # type: ignore[arg-type]

    def get_job(self, job_id: int) -> Job:
        row = self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return Job.from_row(row)

    def update_job(self, job_id: int, **fields: Any) -> None:
        if "result" in fields:
            fields["result_json"] = json.dumps(fields.pop("result"))
        if fields.get("state") in ("done", "failed", "expired"):
            fields.setdefault("finished_at", time.time())
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))

    def open_jobs(self) -> list[Job]:
        marks = ",".join("?" * len(OPEN_JOB_STATES))
        rows = self.db.execute(f"SELECT * FROM jobs WHERE state IN ({marks}) ORDER BY id", OPEN_JOB_STATES)
        return [Job.from_row(r) for r in rows]

    def recent_jobs(self, limit: int = 50) -> list[Job]:
        rows = self.db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [Job.from_row(r) for r in rows]
