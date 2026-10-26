"""Weekly health summary written by Claude from daily aggregates only (never raw samples)."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from pydantic import BaseModel, Field

from ..core import claude
from ..store.db import Store
from . import health

KEY = "weekly_summary"

SYSTEM = """You write a short weekly health recap for the owner of a smartwatch, from daily aggregates they
recorded themselves. Be specific and plain: compare this week with the previous one and with their usual
range where the data allows, notice patterns (sleep, resting heart rate, activity, stress), and say when
there isn't enough data yet. You are not a doctor: no diagnoses, no alarm; suggest seeing a professional only
if something is clearly and persistently out of their normal range. Units: steps; kcal; distance in km; heart
rates in bpm; sleep in hours; stress index 1-100 (lower is calmer); SpO2 in %."""


class WeeklySummary(BaseModel):
    headline: str = Field(description="one sentence, at most 90 characters")
    observations: list[str] = Field(description="2-5 short bullet observations, each at most 140 characters")
    insight_line: str = Field(description="a short encouraging line for the watch face, at most 32 characters")


FIELDS = {
    "steps": ("steps", 0),
    "calories": ("kcal", 0),
    "distance_m": ("km", None),
    "hr_resting": ("resting_hr", 0),
    "hr_avg": ("avg_hr", 0),
    "stress_avg": ("stress", 0),
    "spo2_avg": ("spo2", 0),
    "sleep_s": ("sleep_h", None),
}


def aggregates(store: Store, days: int = 14) -> dict[str, Any]:
    """The exact payload sent to Claude: per-day totals/averages plus personal baselines. No raw samples."""
    rows = []
    for r in health.series(store, days):
        row: dict[str, Any] = {"date": r["date"]}
        for src, (name, digits) in FIELDS.items():
            if src in r:
                value = r[src]
                if src == "distance_m":
                    value = round(value / 1000, 2)
                elif src == "sleep_s":
                    value = round(value / 3600, 1)
                else:
                    value = round(value, digits)
                row[name] = value
        rows.append(row)
    base = health.baselines(store)
    baselines = {
        k: {"usual": round(v["baseline"], 1), "days_of_history": v["n"]}
        for k, v in base.items()
        if k != "_meta" and v.get("baseline") is not None
    }
    return {"days": rows, "baselines_28d": baselines}


async def generate(store: Store) -> dict[str, Any]:
    payload = aggregates(store)
    user = (
        "Daily aggregates for the last 14 days (oldest first; missing fields = no data):\n"
        + json.dumps(payload, separators=(",", ":"))
        + "\n\nWrite the recap for the most recent 7 days."
    )
    summary: WeeklySummary = await claude.parse(
        WeeklySummary, system=SYSTEM, user=user, effort="medium", max_tokens=8000
    )
    result = {**summary.model_dump(), "generated_at": dt.datetime.now().isoformat(timespec="minutes")}
    store.set_kv(KEY, json.dumps(result))
    return result


def latest(store: Store) -> dict[str, Any] | None:
    raw = store.get_kv(KEY)
    return json.loads(raw) if raw else None
