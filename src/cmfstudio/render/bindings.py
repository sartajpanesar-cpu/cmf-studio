"""Resolve FaceSpec bindings from the store at render time.

    date:<strftime>      local time of the render, e.g. date:%a %d
    metric:<name>        today's value: steps, kcal, km, hr_resting, hr_avg, hr_max, stress, spo2, sleep,
                         battery, hr (latest reading)
    static:<text>        literal text
    insight              a short line (Claude-written in Phase 4; settable via the API meanwhile)

Data is baked into the image, so it is only as fresh as the last sync; the clock is live (firmware).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from ..store.db import Store
from ..sync import daily

UNBOUND = "–"


@dataclass
class Context:
    now: dt.datetime = field(default_factory=dt.datetime.now)
    metrics: dict[str, float] = field(default_factory=dict)
    insight: str = ""

    @classmethod
    def from_store(cls, store: Store, battery: int | None = None, insight: str = "") -> Context:
        today = daily.read(store, days=1).get(dt.date.today().isoformat(), {})
        m: dict[str, float] = {}
        for src, dst in (
            ("steps", "steps"),
            ("calories", "kcal"),
            ("hr_resting", "hr_resting"),
            ("hr_avg", "hr_avg"),
            ("hr_max", "hr_max"),
            ("stress_avg", "stress"),
            ("spo2_avg", "spo2"),
            ("sleep_s", "sleep"),
        ):
            if src in today:
                m[dst] = today[src]
        if "distance_m" in today:
            m["km"] = today["distance_m"] / 1000
        latest = store.db.execute("SELECT value FROM samples WHERE metric = 'hr' ORDER BY ts DESC LIMIT 1").fetchone()
        if latest:
            m["hr"] = latest[0]
        if battery is not None:
            m["battery"] = battery
        return cls(metrics=m, insight=insight)


def fmt_metric(name: str, value: float) -> str:
    if name == "sleep":
        return f"{int(value // 3600)}h{int(value % 3600 // 60):02d}"
    if name == "km":
        return f"{value:.1f}"
    if name == "battery":
        return f"{value:.0f}%"
    return f"{value:,.0f}"


def metric_value(bind: str, ctx: Context) -> float | None:
    kind, _, arg = bind.partition(":")
    return ctx.metrics.get(arg) if kind == "metric" else None


def resolve(bind: str, ctx: Context) -> str:
    kind, _, arg = bind.partition(":")
    if kind == "date":
        return ctx.now.strftime(arg or "%a %d")
    if kind == "metric":
        value = ctx.metrics.get(arg)
        return UNBOUND if value is None else fmt_metric(arg, value)
    if kind == "static":
        return arg
    if kind == "insight":
        return ctx.insight
    raise ValueError(f"unknown binding {bind!r}")


def describe(ctx: Context) -> dict[str, Any]:
    return {"now": ctx.now.isoformat(timespec="minutes"), "metrics": ctx.metrics, "insight": ctx.insight}
