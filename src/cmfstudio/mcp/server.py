"""MCP server: lets Claude (Code / Desktop) use the watch through watchd, with least privilege.

Exposed (docs/02-architecture.md "MCP tool surface"): watch_status, sync_now, query_health, notify, render_face
(preview only), list_faces, install_face (requires confirm). Never exposed: the AT shell, firmware tools,
pairing/reset, raw frames. Everything goes through watchd's localhost API; this process never touches Bluetooth.

Health data returned here enters the model's context, so query_health returns daily or hourly aggregates only,
never raw samples (docs/04-safety-policy.md).
"""

from __future__ import annotations

import base64
import datetime as dt
import os
from collections import defaultdict
from collections.abc import Callable
from statistics import mean
from typing import Any, Literal

import httpx
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from ..api.auth import get_token

DEFAULT_URL = os.environ.get("CMF_API", "http://127.0.0.1:8765")

INSTRUCTIONS = """Tools for the owner's CMF Watch Pro 2, via the local watchd daemon.
- Health data: query_health gives daily or hourly aggregates; use watch_status for battery/connection.
- notify shows a short notification on the wrist (title ≤ 20 chars, body ≤ 128); use it for things the owner asked to be
  told about, not chatter.
- Faces: render_face previews a FaceSpec; install_face replaces the watch's only custom face and needs confirm=true -
  only install when the owner has asked for that face.
There are deliberately no tools for pairing, firmware, factory settings or raw Bluetooth."""

DAILY_METRICS: dict[str, tuple[str, float]] = {  # name -> (daily key, scale)
    "steps": ("steps", 1),
    "kcal": ("calories", 1),
    "distance_km": ("distance_m", 0.001),
    "resting_hr": ("hr_resting", 1),
    "avg_hr": ("hr_avg", 1),
    "min_hr": ("hr_min", 1),
    "max_hr": ("hr_max", 1),
    "stress": ("stress_avg", 1),
    "spo2": ("spo2_avg", 1),
    "sleep_hours": ("sleep_s", 1 / 3600),
    "deep_sleep_hours": ("sleep_deep_s", 1 / 3600),
    "rem_sleep_hours": ("sleep_rem_s", 1 / 3600),
}
HOURLY_METRICS = {"hr": "hr", "stress": "stress", "spo2": "spo2", "steps": "steps_cum"}
MAX_HOURLY_DAYS = 7

Metric = Literal[
    "steps",
    "kcal",
    "distance_km",
    "resting_hr",
    "avg_hr",
    "min_hr",
    "max_hr",
    "stress",
    "spo2",
    "sleep_hours",
    "deep_sleep_hours",
    "rem_sleep_hours",
    "hr",
]


class WatchdError(ToolError):
    """A deliberate, model-visible tool error (MCP 2.x hides the text of any other exception)."""


def default_client() -> httpx.AsyncClient:
    token = get_token()
    if not token:
        raise WatchdError("watchd has no API token yet: start watchd once (`uv run watchd`)")
    return httpx.AsyncClient(base_url=DEFAULT_URL, headers={"Authorization": f"Bearer {token}"}, timeout=120)


def build_server(client_factory: Callable[[], httpx.AsyncClient] = default_client) -> MCPServer:
    server = MCPServer("cmf-watch", instructions=INSTRUCTIONS)

    async def call(method: str, path: str, **kw: Any) -> Any:
        try:
            async with client_factory() as c:
                r = await c.request(method, path, **kw)
        except httpx.ConnectError as exc:
            raise WatchdError(
                "watchd isn't running (start it: `uv run watchd` or `bash scripts/launchd.sh install`)"
            ) from exc
        if r.status_code >= 400:
            detail = (
                r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text
            )
            raise WatchdError(f"watchd said {r.status_code}: {detail}")
        return r.json()

    read_only = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    @server.tool(annotations=read_only)
    async def watch_status() -> dict[str, Any]:
        """Connection state, battery, firmware and what face the watch is showing."""
        st = await call("GET", "/status")
        link = st["link"]
        dials = link.get("dials") or {}
        return {
            "state": link["state"],
            "battery_percent": link["battery"],
            "firmware": link["firmware"],
            "connected_since": link["connected_since"],
            "last_keepalive": link["last_keepalive_at"],
            "pending_jobs": st["pending_jobs"],
            "showing_custom_face": dials.get("active_id") == 0xFFFFFFFF if dials else None,
            "needs_pairing": link["state"] == "needs_pair",
        }

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True))
    async def sync_now() -> dict[str, Any]:
        """Fetch new health data from the watch now (normally automatic every hour)."""
        job = await call("POST", "/sync")
        if job["state"] != "done":
            return {"state": job["state"], "note": "the watch isn't connected; it will sync when it reconnects"}
        res = job["result"]
        return {"state": "done", "new_samples": res["new_samples"], "sleep_sessions": res["sleep_sessions"]}

    @server.tool(annotations=read_only)
    async def query_health(
        metric: Metric,
        start: str,
        end: str | None = None,
        agg: Literal["day", "hour"] = "day",
    ) -> dict[str, Any]:
        """Health aggregates between two local dates (YYYY-MM-DD, end inclusive, default today).

        agg="day": one value per day for any metric. agg="hour": hourly means (hourly step counts) for hr, stress,
        spo2 or steps, at most 7 days. Raw minute-level samples are never returned.
        """
        d0 = dt.date.fromisoformat(start)
        d1 = dt.date.fromisoformat(end) if end else dt.date.today()
        if d1 < d0:
            raise WatchdError("end is before start")
        if agg == "day":
            if metric not in DAILY_METRICS:
                raise WatchdError(f"daily metrics: {', '.join(DAILY_METRICS)} (use agg='hour' for hr)")
            key, scale = DAILY_METRICS[metric]
            days = (dt.date.today() - d0).days + 1
            rows = (await call("GET", "/health/series", params={"days": max(1, min(days, 3660))}))["days"]
            values = {
                r["date"]: round(r[key] * scale, 2)
                for r in rows
                if key in r and d0.isoformat() <= r["date"] <= d1.isoformat()
            }
            return {"metric": metric, "agg": "day", "values": values, "days_without_data": _missing(d0, d1, values)}
        if metric not in HOURLY_METRICS:
            raise WatchdError(f"hourly metrics: {', '.join(HOURLY_METRICS)}")
        if (d1 - d0).days >= MAX_HOURLY_DAYS:
            raise WatchdError(f"hourly queries cover at most {MAX_HOURLY_DAYS} days")
        t0 = int(dt.datetime.combine(d0, dt.time()).timestamp())
        t1 = int(dt.datetime.combine(d1 + dt.timedelta(days=1), dt.time()).timestamp()) - 1
        raw = (await call("GET", "/samples", params={"metric": HOURLY_METRICS[metric], "start": t0, "end": t1}))[
            "samples"
        ]
        return {"metric": metric, "agg": "hour", "values": _hourly(metric, raw)}

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False))
    async def notify(title: str, body: str = "") -> dict[str, Any]:
        """Show a notification on the watch. Title up to 20 characters, body up to 128 (longer text is cut)."""
        job = await call("POST", "/notify", json={"title": title[:200], "body": body[:1000], "ttl_s": 3600})
        if job["state"] == "done":
            return {"delivered": bool(job["result"]["acked"])}
        return {"delivered": False, "state": job["state"], "note": "queued until the watch reconnects (expires in 1 h)"}

    @server.tool(annotations=read_only)
    async def list_faces() -> dict[str, Any]:
        """Saved watch faces (id, name, last installed) and which one is active."""
        res = await call("GET", "/faces")
        faces = [{"id": f["id"], "name": f["name"], "last_installed_at": f["last_installed_at"]} for f in res["faces"]]
        return {"faces": faces, "active_id": res["active_id"]}

    @server.tool(annotations=read_only, structured_output=False)  # image content can't be structured output
    async def render_face(spec: dict[str, Any]) -> list[Any]:
        """Preview a FaceSpec (see docs/06) as the watch would show it, with today's data. Installs nothing."""
        res = await call("POST", "/faces/preview", json=spec)
        checks = res["checks"]
        summary = (
            f"{res['name']}: {res['bytes'] / 1024:.0f} KB, {checks['lit_fraction']:.0%} of the screen lit"
            + "".join(f"\nwarning: {w}" for w in checks["warnings"])
        )
        return [Image(data=base64.b64decode(res["preview_png"]), format="png"), summary]

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False))
    async def install_face(face_id: int, confirm: bool = False) -> dict[str, Any]:
        """Install a saved face on the watch. It REPLACES the watch's only custom face, so confirm must be true,
        and only when the owner asked for this face to be installed."""
        if not confirm:
            raise WatchdError(
                "installing replaces the watch's only custom face; call again with confirm=true "
                "only if the owner asked for it"
            )
        res = await call("POST", f"/faces/{face_id}/install", params={"confirm": True, "force": True})
        return {"state": res["job"]["state"], "name": res["render"]["name"]}

    return server


def _missing(d0: dt.date, d1: dt.date, values: dict[str, float]) -> list[str]:
    days = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    return [d for d in days if d not in values]


def _hourly(metric: str, raw: list[list[float]]) -> dict[str, float]:
    """Hourly means (steps: hourly increments of the cumulative counter), keyed 'YYYY-MM-DD HH:00'."""
    out: dict[str, float] = {}
    if metric == "steps":
        prev = 0.0
        prev_day = None
        for ts, cum in raw:
            stamp = dt.datetime.fromtimestamp(ts)
            if stamp.date() != prev_day:
                prev, prev_day = 0.0, stamp.date()
            key = stamp.strftime("%Y-%m-%d %H:00")
            out[key] = out.get(key, 0) + max(0.0, cum - prev)
            prev = max(prev, cum)
        return out
    buckets: dict[str, list[float]] = defaultdict(list)
    for ts, v in raw:
        buckets[dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:00")].append(v)
    return {k: round(mean(v), 1) for k, v in sorted(buckets.items())}


def main() -> None:
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)  # keep Claude Code's MCP log readable
    build_server().run("stdio")


if __name__ == "__main__":
    main()
