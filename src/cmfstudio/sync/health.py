"""Health views over stored data: per-day series, intraday detail, personal baselines and anomaly flags.

Baselines are robust (median and MAD over the previous ``window`` days, today excluded) so one odd day
doesn't move them. A flag needs at least MIN_DAYS of history and a robust z-score beyond Z_FLAG.
These are personal-trend hints, not medical judgements.
"""

from __future__ import annotations

import datetime as dt
from statistics import median
from typing import Any

from ..store.db import Store
from . import daily

MIN_DAYS = 7
Z_FLAG = 2.5
MAD_TO_SD = 1.4826

# metric -> (label, unit, which direction is notable)
BASELINE_METRICS: dict[str, tuple[str, str, str]] = {
    "hr_resting": ("Resting heart rate", "bpm", "high"),
    "sleep_s": ("Sleep", "s", "low"),
    "steps": ("Steps", "", "low"),
    "stress_avg": ("Stress", "", "high"),
    "hr_avg": ("Average heart rate", "bpm", "high"),
    "spo2_avg": ("Blood oxygen", "%", "low"),
}


def series(store: Store, days: int) -> list[dict[str, Any]]:
    """One row per local day (oldest first), including empty days so charts keep their spacing."""
    by_day = daily.read(store, days)
    today = dt.date.today()
    return [
        {"date": d, **by_day.get(d, {})}
        for d in ((today - dt.timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1))
    ]


def baselines(store: Store, window: int = 28) -> dict[str, Any]:
    rows = series(store, window + 1)
    history, today = rows[:-1], rows[-1]
    out: dict[str, Any] = {}
    for metric, (label, unit, notable) in BASELINE_METRICS.items():
        values = [r[metric] for r in history if metric in r]
        entry: dict[str, Any] = {"label": label, "unit": unit, "n": len(values), "today": today.get(metric)}
        if values:
            mid = median(values)
            spread = median(abs(v - mid) for v in values) * MAD_TO_SD
            entry.update(baseline=mid, spread=spread)
            if entry["today"] is not None and len(values) >= MIN_DAYS and spread > 0:
                z = (entry["today"] - mid) / spread
                entry["z"] = round(z, 2)
                if (notable == "high" and z >= Z_FLAG) or (notable == "low" and z <= -Z_FLAG):
                    entry["flag"] = f"{label} is unusually {'high' if z > 0 else 'low'} for you today"
        out[metric] = entry
    out["_meta"] = {"window_days": window, "min_days": MIN_DAYS, "z_flag": Z_FLAG}
    return out


def intraday(store: Store, day: str) -> dict[str, Any]:
    start, end = daily.day_bounds(day)
    hr = store.samples("hr", start, end)
    steps_cum = store.samples("steps_cum", start, end)
    hourly_steps = []
    prev = 0.0
    for ts, cum in steps_cum:
        if cum >= prev:  # cumulative within the day; a dip means a reset or a re-stamped "now" record
            hourly_steps.append([ts, cum - prev])
            prev = cum
    return {
        "date": day,
        "hr": hr,
        "steps": hourly_steps,
        "stress": store.samples("stress", start, end),
        "spo2": store.samples("spo2", start, end),
        "hr_resting": store.samples("hr_resting", start, end)[-1:],
    }
