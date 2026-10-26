"""Per-day aggregates in `daily`, always rebuildable from `samples` and `sleep_sessions`.

Days are local (the Mac's timezone). The watch's activity counters are cumulative and reset at local
midnight (findings 2026-10-26: 0 at 01:00, unchanged across UTC midnight), so a day's total is the
largest cumulative value seen that day. Calories are kept in the watch's unit (looks like kcal on fw
1.0.0.57, unconfirmed), hence the neutral name.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from statistics import mean

from ..store.db import Store

CUMULATIVE = {"steps_cum": "steps", "distance_cum_m": "distance_m", "calories_cum_cal": "calories"}
STATS = {"hr": "hr", "spo2": "spo2", "stress": "stress"}


def local_day(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts).date().isoformat()


def day_bounds(day: str) -> tuple[int, int]:
    start = dt.datetime.combine(dt.date.fromisoformat(day), dt.time())
    return int(start.timestamp()), int((start + dt.timedelta(days=1)).timestamp()) - 1


def compute(store: Store, day: str) -> dict[str, float]:
    start, end = day_bounds(day)
    out: dict[str, float] = {}
    for metric, name in CUMULATIVE.items():
        values = [v for _, v in store.samples(metric, start, end)]
        if values:
            out[name] = max(values)
    for metric, name in STATS.items():
        values = [v for _, v in store.samples(metric, start, end)]
        if values:
            out[f"{name}_min"], out[f"{name}_max"] = min(values), max(values)
            out[f"{name}_avg"] = round(mean(values), 1)
    resting = store.samples("hr_resting", start, end)
    if resting:
        out["hr_resting"] = resting[-1][1]  # re-stamped each sync; the latest is the watch's current value
    # sleep is attributed to the day you wake up
    stage_totals: dict[str, float] = defaultdict(float)
    rows = store.db.execute(
        "SELECT start_ts, end_ts, stages_json FROM sleep_sessions WHERE end_ts BETWEEN ? AND ?", (start, end)
    ).fetchall()
    for row in rows:
        out["sleep_s"] = out.get("sleep_s", 0) + (row["end_ts"] - row["start_ts"])
        for stage, seconds in json.loads(row["stages_json"])["totals_s"].items():
            stage_totals[stage] += seconds
    for stage, seconds in stage_totals.items():
        out[f"sleep_{stage}_s"] = seconds
    return out


def rebuild(store: Store, since_ts: int | None = None) -> list[str]:
    """Recompute `daily` for every local day from ``since_ts`` (default: all data) to today."""
    first = store.db.execute(
        "SELECT min(t) FROM (SELECT min(ts) t FROM samples UNION ALL SELECT min(end_ts) FROM sleep_sessions)"
    ).fetchone()[0]
    if first is None:
        return []
    day = dt.date.fromisoformat(local_day(max(first, since_ts or first)))
    today = dt.date.today()
    days = []
    while day <= today:
        key = day.isoformat()
        values = compute(store, key)
        store.db.execute("BEGIN")
        try:
            store.db.execute("DELETE FROM daily WHERE date = ?", (key,))
            store.db.executemany(
                "INSERT INTO daily(date, metric, value) VALUES (?, ?, ?)", [(key, m, v) for m, v in values.items()]
            )
            store.db.execute("COMMIT")
        except Exception:
            store.db.execute("ROLLBACK")
            raise
        days.append(key)
        day += dt.timedelta(days=1)
    return days


def read(store: Store, days: int = 7) -> dict[str, dict[str, float]]:
    since = (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()
    rows = store.db.execute("SELECT date, metric, value FROM daily WHERE date >= ? ORDER BY date", (since,))
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        out[r["date"]][r["metric"]] = r["value"]
    return dict(out)
