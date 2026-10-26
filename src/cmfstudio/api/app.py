"""watchd's localhost API. Every client (CLI, UI, MCP) goes through here; none touch Bluetooth.

Binds 127.0.0.1 only (watchd), requires the bearer token from the Keychain, and rejects foreign
Host headers so a web page can't reach it via DNS rebinding.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import datetime as dt
import hashlib
import io
import json
import secrets
import time
from pathlib import Path
from typing import Any, Literal

import anthropic
import httpx
from cmfkit.watchface import photo
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from ..ble.link import LinkError, LinkState, WatchLink
from ..core import claude
from ..core import settings as settings_mod
from ..core.jobs import JobQueue
from ..core.media import MediaService, MediaSettings
from ..core.rules import RulesEngine
from ..core.settings import (
    Alarm,
    Goals,
    HrAlerts,
    RawSetting,
    Reminder,
    SettingsError,
    SettingsService,
    Switch,
    Units,
    WriteResult,
)
from ..core.weather import WeatherService, WeatherSettings, geocode
from ..render import clock, describe, native_face
from ..render import html as render_html
from ..render.native import NativeFormatError
from ..render.native_face import NativeSpec
from ..render.native_service import NativeService, preview_payload
from ..render.service import FaceService
from ..render.spec import FaceSpec
from ..store.db import Job, Store
from ..sync import daily, health, summary
from ..sync.manager import SyncManager

# How long a synchronous call waits for its job before returning it still queued.
WAIT_S = 20.0
MAX_IMAGE_BYTES = 10 * 1024 * 1024


class NotifyIn(BaseModel):
    title: str = Field(max_length=200)
    body: str = Field(default="", max_length=1000)
    icon: int = Field(default=0xFF, ge=0, le=0xFF)
    ttl_s: float | None = Field(default=3600, gt=0, description="drop if not delivered within this time")
    wait: bool = True


class DescribeIn(BaseModel):
    description: str = Field(min_length=3, max_length=1000)
    current: FaceSpec | None = Field(default=None, description="if given, Claude edits this design instead")


async def call_claude(coro):
    """Map Claude failures to HTTP errors the UI/CLI can show as-is."""
    try:
        return await coro
    except claude.ClaudeUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except claude.ClaudeRefused as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except (anthropic.APIError, RuntimeError, ValueError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Claude request failed: {exc}") from exc


class InsightIn(BaseModel):
    text: str = Field(default="", max_length=80)


def create_app(
    link: WatchLink,
    queue: JobQueue,
    store: Store,
    token: str,
    faces_dir: Path,
    sync: SyncManager | None = None,
    faces: FaceService | None = None,
    ui_dir: Path | None = None,
    rules: RulesEngine | None = None,
    native: NativeService | None = None,
    weather: WeatherService | None = None,
    settings: SettingsService | None = None,
    media: MediaService | None = None,
) -> FastAPI:
    app = FastAPI(title="cmf-studio watchd", version="0.1.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    def auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, given = header.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(given.encode(), token.encode()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or wrong bearer token")

    async def run(job: Job, wait: bool) -> dict[str, Any]:
        if wait and link.connected.is_set():
            try:
                job = await queue.wait(job.id, WAIT_S)
            except TimeoutError:
                job = store.get_job(job.id)
        return job.public()

    @app.get("/status", dependencies=[Depends(auth)])
    async def get_status() -> dict[str, Any]:
        return {"link": link.status(), "pending_jobs": queue.pending(), "time": time.time()}

    @app.post("/notify", dependencies=[Depends(auth)])
    async def post_notify(body: NotifyIn) -> dict[str, Any]:
        job = queue.submit("notify", {"title": body.title, "body": body.body, "icon": body.icon}, ttl_s=body.ttl_s)
        return await run(job, body.wait)

    @app.post("/faces/install", dependencies=[Depends(auth)])
    async def post_face_install(
        request: Request,
        confirm: bool = Query(False, description="required: replaces the watch's single custom photo face"),
        name: str = Query("face"),
        style: int = Query(0, ge=0, le=4),
        x: int = Query(56, ge=0, le=465),
        y: int = Query(77, ge=0, le=465),
        color565: int = Query(0xFFFF, ge=0, le=0xFFFF),
        wait: bool = True,
    ) -> dict[str, Any]:
        if not confirm:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "installing replaces the watch's only custom photo face; resend with confirm=true",
            )
        data = await request.body()
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "send the image as the raw request body (≤10 MB)")
        try:
            blob = photo.build(Image.open(io.BytesIO(data)))
        except (UnidentifiedImageError, OSError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"not a readable image: {exc}") from exc
        faces_dir.mkdir(parents=True, exist_ok=True)
        bin_path = faces_dir / f"{hashlib.sha256(blob).hexdigest()[:16]}.bin"
        bin_path.write_bytes(blob)
        store.save_face(name, json.dumps({"type": "photo", "bin": str(bin_path)}))
        args = {"bin_path": str(bin_path), "style": style, "x": x, "y": y, "color565": color565}
        return await run(queue.submit("install_face", args), wait)

    def need_faces() -> FaceService:
        if faces is None:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "faces not configured")
        return faces

    @app.post("/faces/preview", dependencies=[Depends(auth)])
    async def post_face_preview(spec: FaceSpec) -> dict[str, Any]:
        """Render with today's data; returns checks and a base64 PNG of what the watch would show."""
        r = await need_faces().preview(spec)
        return {**r.summary(), "preview_png": base64.b64encode(r.preview_png).decode()}

    @app.post("/faces/html", dependencies=[Depends(auth)])
    async def post_face_html(spec: FaceSpec) -> dict[str, Any]:
        """The spec as HTML with today's data, for an instant in-browser preview (exact render: /faces/preview)."""
        c = spec.clock
        return {
            "html": render_html.build(spec, need_faces().context()),
            "clock_svg": clock.svg(c.style, c.x, c.y, c.color),
            "clock_box": clock.shape(c.style, c.x, c.y).box,
        }

    @app.get("/faces", dependencies=[Depends(auth)])
    async def get_faces() -> dict[str, Any]:
        svc = need_faces()
        return {"faces": store.faces(), "active_id": svc.active_id(), "watch_dials": link.dials}

    @app.post("/faces", dependencies=[Depends(auth)])
    async def post_face(spec: FaceSpec) -> dict[str, Any]:
        return {"id": need_faces().save(spec), "name": spec.name}

    @app.get("/faces/{face_id}", dependencies=[Depends(auth)])
    async def get_face(face_id: int) -> dict[str, Any]:
        try:
            row = store.get_face(face_id)
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such face") from None
        spec = json.loads(row["spec_json"])
        if "layers" not in spec:  # a raw photo install (`cmf face install IMAGE`), not a FaceSpec
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not an editable face")
        return {"id": row["id"], "spec": spec, "last_installed_at": row["last_installed_at"]}

    @app.post("/faces/{face_id}/install", dependencies=[Depends(auth)])
    async def post_face_id_install(face_id: int, confirm: bool = False, force: bool = False) -> dict[str, Any]:
        if not confirm:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "installing replaces the watch's only custom photo face; resend with confirm=true",
            )
        try:
            result = await need_faces().install(face_id, force=force)
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such face") from None
        if "job" in result and link.connected.is_set():
            try:
                job = await queue.wait(result["job"]["id"], WAIT_S)
                result["job"] = job.public()
            except TimeoutError:
                pass
        return result

    @app.post("/faces/describe", dependencies=[Depends(auth)])
    async def post_face_describe(body: DescribeIn) -> dict[str, Any]:
        """Claude designs a FaceSpec from a description; returns it with a preview. Nothing is installed."""
        svc = need_faces()
        if body.current is not None:
            spec = await call_claude(describe.edit_face(body.description, body.current, svc.context()))
        else:
            spec = await call_claude(describe.describe_face(body.description, svc.context()))
        r = await svc.preview(spec)
        return {
            "spec": spec.model_dump(by_alias=True),
            **r.summary(),
            "preview_png": base64.b64encode(r.preview_png).decode(),
        }

    @app.get("/health/summary", dependencies=[Depends(auth)])
    async def get_health_summary() -> dict[str, Any]:
        return {"summary": summary.latest(store)}

    @app.post("/health/summary", dependencies=[Depends(auth)])
    async def post_health_summary() -> dict[str, Any]:
        """Ask Claude for this week's recap. Sends daily aggregates only (see /health/summary/payload)."""
        return {"summary": await call_claude(summary.generate(store))}

    @app.get("/health/summary/payload", dependencies=[Depends(auth)])
    async def get_health_summary_payload() -> dict[str, Any]:
        """Exactly what the weekly summary sends to Claude, for inspection."""
        return summary.aggregates(store)

    @app.put("/insight", dependencies=[Depends(auth)])
    async def put_insight(body: InsightIn) -> dict[str, Any]:
        """The "insight" binding text (Claude writes it from Phase 4); re-renders on the next sync."""
        store.set_kv("insight", body.text or None)
        return {"insight": body.text}

    @app.post("/pair", dependencies=[Depends(auth)])
    async def post_pair(confirm: bool = False) -> dict[str, Any]:
        """Tier 2: re-pair (sends AT GETSECRET; owner must accept on the watch). Never exposed via MCP."""
        if not confirm:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "pairing needs confirm=true")
        if link.state is LinkState.CONNECTED:
            raise HTTPException(status.HTTP_409_CONFLICT, "already connected with a valid key")
        try:
            await link.pair()
        except (LinkError, TimeoutError, OSError) as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"pairing failed: {exc!r}") from exc
        with contextlib.suppress(TimeoutError):  # the supervisor reconnects with the new key
            await asyncio.wait_for(link.connected.wait(), 15)
        return {"paired": True, "link": link.status()}

    @app.post("/sync", dependencies=[Depends(auth)])
    async def post_sync(wait: bool = True) -> dict[str, Any]:
        """Fetch health data from the watch (ACTIVITY_FETCH). Read-only on the watch side."""
        job = queue.submit("sync", {}, ttl_s=6 * 3600)
        if wait and link.connected.is_set():
            try:
                job = await queue.wait(job.id, 660)  # a heavy sync can take minutes (spec: ~160 s)
            except TimeoutError:
                job = store.get_job(job.id)
        return job.public()

    @app.get("/samples", dependencies=[Depends(auth)])
    async def get_samples(
        metric: str, start: int = 0, end: int = 2**31 - 1, limit: int = Query(5000, ge=1, le=100_000)
    ) -> dict[str, Any]:
        rows = store.samples(metric, start, end)[-limit:]
        return {"metric": metric, "samples": rows}

    @app.get("/samples/summary", dependencies=[Depends(auth)])
    async def get_samples_summary() -> dict[str, Any]:
        rows = store.db.execute("SELECT metric, count(*) n, min(ts) first, max(ts) last FROM samples GROUP BY metric")
        sleep = store.db.execute("SELECT count(*), min(start_ts), max(end_ts) FROM sleep_sessions").fetchone()
        return {
            "metrics": {r["metric"]: {"count": r["n"], "first": r["first"], "last": r["last"]} for r in rows},
            "sleep_sessions": {"count": sleep[0], "first": sleep[1], "last": sleep[2]},
            "raw_frames": store.db.execute("SELECT count(*) FROM raw_frames").fetchone()[0],
        }

    @app.get("/daily", dependencies=[Depends(auth)])
    async def get_daily(days: int = Query(7, ge=1, le=3660)) -> dict[str, Any]:
        return {"days": daily.read(store, days)}

    @app.get("/health/series", dependencies=[Depends(auth)])
    async def get_health_series(days: int = Query(30, ge=1, le=3660)) -> dict[str, Any]:
        return {"days": health.series(store, days)}

    @app.get("/health/baselines", dependencies=[Depends(auth)])
    async def get_health_baselines(window: int = Query(28, ge=7, le=365)) -> dict[str, Any]:
        return health.baselines(store, window)

    @app.get("/health/intraday", dependencies=[Depends(auth)])
    async def get_health_intraday(date: str | None = None) -> dict[str, Any]:
        day = date or dt.date.today().isoformat()
        try:
            dt.date.fromisoformat(day)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "date must be YYYY-MM-DD") from None
        return health.intraday(store, day)

    @app.post("/reparse", dependencies=[Depends(auth)])
    async def post_reparse() -> dict[str, Any]:
        """Re-run parsers over stored raw frames and rebuild daily totals. Doesn't touch the watch."""
        if sync is None:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "sync not configured")
        return sync.reparse()

    @app.get("/jobs", dependencies=[Depends(auth)])
    async def get_jobs(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
        return [j.public() for j in store.recent_jobs(limit)]

    @app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
    async def get_job(job_id: int) -> dict[str, Any]:
        try:
            return store.get_job(job_id).public()
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job") from None

    @app.get("/events", dependencies=[Depends(auth)])
    async def get_events(limit: int = Query(100, ge=1, le=1000), kind: str | None = None) -> list[dict[str, Any]]:
        return store.events(limit, kind)

    # -- native faces (structured dials: firmware-drawn time in your font) -------------------------------------

    def need_native() -> NativeService:
        if native is None:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "native faces not configured")
        return native

    async def native_call(coro):  # type: ignore[no-untyped-def]
        try:
            return await coro
        except FileNotFoundError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        except NativeFormatError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @app.get("/native/templates", dependencies=[Depends(auth)])
    async def get_native_templates() -> list[dict[str, Any]]:
        return native_face.available_templates()

    @app.post("/native/preview", dependencies=[Depends(auth)])
    async def post_native_preview(spec: NativeSpec) -> dict[str, Any]:
        return preview_payload(await native_call(need_native().build(spec)))

    @app.get("/native/faces", dependencies=[Depends(auth)])
    async def get_native_faces() -> list[dict[str, Any]]:
        return [{**f, "spec": json.loads(f.pop("spec_json"))} for f in store.native_faces()]

    @app.post("/native/faces", dependencies=[Depends(auth)])
    async def post_native_face(spec: NativeSpec) -> dict[str, Any]:
        return {"id": need_native().save(spec)}

    @app.post("/native/faces/{face_id}/install", dependencies=[Depends(auth)])
    async def post_native_install(
        face_id: int,
        confirm: bool = Query(False, description="required: replaces a face on the watch"),
        replace: int | None = Query(None, description="stock dial id to replace (first install only)"),
    ) -> dict[str, Any]:
        if not confirm:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "installing replaces a face on the watch; send confirm=true"
            )
        try:
            need_native().spec(face_id)
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such native face") from None
        return await native_call(need_native().install(face_id, replace))

    @app.get("/watch/dials", dependencies=[Depends(auth)])
    async def get_watch_dials() -> dict[str, Any]:
        return need_native().dials()

    # -- weather + glance line -------------------------------------------------------------------------------

    def need_weather() -> WeatherService:
        if weather is None:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "weather not configured")
        return weather

    @app.get("/weather", dependencies=[Depends(auth)])
    async def get_weather() -> dict[str, Any]:
        w = need_weather()
        return {"settings": w.settings().model_dump(), "last": w.last(), "error": w.last_error}

    @app.put("/weather/settings", dependencies=[Depends(auth)])
    async def put_weather_settings(settings: WeatherSettings) -> dict[str, Any]:
        return need_weather().save(settings).model_dump()

    @app.get("/weather/places", dependencies=[Depends(auth)])
    async def get_weather_places(q: str = Query(min_length=2, max_length=80)) -> list[dict[str, Any]]:
        try:
            async with need_weather().client_factory() as client:
                return await geocode(client, q)
        except httpx.HTTPError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"place search failed: {exc!r}") from exc

    @app.post("/weather/push", dependencies=[Depends(auth)])
    async def post_weather_push() -> dict[str, Any]:
        try:
            return await need_weather().push()
        except LookupError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"forecast fetch failed: {exc!r}") from exc

    # -- watch settings (read-modify-write, every write verified by reading it back) ---------------------------

    def need_settings() -> SettingsService:
        if settings is None:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "settings not configured")
        return settings

    async def settings_call(coro):  # type: ignore[no-untyped-def]
        try:
            result = await coro
        except LinkError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"the watch isn't connected ({exc})") from exc
        except SettingsError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
        if isinstance(result, WriteResult):
            store.add_event("settings", {"ok": result.ok, "encoding": result.encoding, "tried": result.tried})
            if not result.ok:
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY, f"the watch didn't take the change (tried {result.tried})"
                )
            return result.public()
        return result

    @app.get("/settings", dependencies=[Depends(auth)])
    async def get_settings() -> dict[str, Any]:
        return await settings_call(need_settings().snapshot())

    @app.put("/settings/alarms", dependencies=[Depends(auth)])
    async def put_alarms(alarms: list[Alarm]) -> dict[str, Any]:
        return await settings_call(need_settings().set_alarms(alarms))

    @app.put("/settings/goals", dependencies=[Depends(auth)])
    async def put_goals(goals: Goals) -> dict[str, Any]:
        return await settings_call(need_settings().set_goals(goals))

    @app.put("/settings/switch/{name}", dependencies=[Depends(auth)])
    async def put_switch(name: Literal["dnd", "wrist_raise", "time_format"], body: Switch) -> dict[str, Any]:
        """``time_format``: on = 12-hour."""
        return await settings_call(need_settings().set_simple(name, body.on))

    @app.put("/settings/reminders/{name}", dependencies=[Depends(auth)])
    async def put_reminder(name: Literal["standing_reminder", "water_reminder"], body: Reminder) -> dict[str, Any]:
        return await settings_call(need_settings().set_reminder(name, body))

    @app.put("/settings/sports", dependencies=[Depends(auth)])
    async def put_sports(codes: list[int]) -> dict[str, Any]:
        return await settings_call(need_settings().set_sports(codes))

    @app.put("/settings/monitoring/{kind}", dependencies=[Depends(auth)])
    async def put_monitoring(kind: Literal["heart_rate", "spo2", "stress"], body: Switch) -> dict[str, Any]:
        return await settings_call(need_settings().set_monitoring(kind, body.on))

    @app.put("/settings/hr_alerts", dependencies=[Depends(auth)])
    async def put_hr_alerts(body: HrAlerts) -> dict[str, Any]:
        return await settings_call(need_settings().set_hr_alerts(body.low, body.high, body.sport_high, body.spo2_low))

    @app.put("/settings/units", dependencies=[Depends(auth)])
    async def put_units(body: Units) -> dict[str, Any]:
        return await settings_call(need_settings().set_units(body.metric, body.celsius))

    @app.get("/settings/written", dependencies=[Depends(auth)])
    async def get_written_settings() -> dict[str, Any]:
        """Write-only settings as last sent from here (the watch can't report them)."""
        return {k: json.loads(store.get_kv(k) or "null") for k in ("hr_alerts", "units", "monitoring", "goals_sent")}

    @app.post("/find-watch", dependencies=[Depends(auth)])
    async def post_find_watch() -> dict[str, Any]:
        try:
            return {"acked": await link.find_watch()}
        except LinkError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"the watch isn't connected ({exc})") from exc

    @app.post("/settings/raw", dependencies=[Depends(auth)])
    async def post_settings_raw(body: RawSetting) -> dict[str, Any]:
        """Research: write raw bytes to one settings group (SET 0001, Tier 1), then read it back. Settings groups
        only; never exposed over MCP. Used to learn write formats (research/findings.md)."""
        if body.name not in settings_mod.GROUPS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"settings groups only: {sorted(settings_mod.GROUPS)}")
        cmd1 = settings_mod.GROUPS[body.name]
        try:
            acked = await link.write_setting(cmd1, 0x0001, bytes.fromhex(body.hex), 0x0003)
            back = await link.read_setting(cmd1)
        except LinkError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        result = {
            "name": body.name,
            "wrote": body.hex.lower(),
            "acked": acked,
            "read_back": back.hex() if back else None,
        }
        store.add_event("settings_raw", result)
        return result

    @app.post("/settings/probe", dependencies=[Depends(auth)])
    async def post_settings_probe() -> dict[str, Any]:
        """Read every setting that has a GET, raw (Tier 0). For learning formats before writing them."""
        out: dict[str, Any] = {}
        for name, cmd1 in settings_mod.GROUPS.items():
            try:
                raw = await link.read_setting(cmd1)
            except LinkError as exc:
                raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
            out[name] = raw.hex() if raw is not None else None
        store.add_event("settings_probe", out)
        return out

    @app.get("/media", dependencies=[Depends(auth)])
    async def get_media() -> dict[str, Any]:
        if media is None:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "media not configured")
        return media.public()

    @app.put("/media/settings", dependencies=[Depends(auth)])
    async def put_media_settings(body: MediaSettings) -> dict[str, Any]:
        if media is None:
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "media not configured")
        media.save(body)
        if (body.show_now_playing or body.buttons_control_music) and link.connected.is_set():
            if body.show_now_playing:
                await media.push_if_changed()
            else:
                queue.submit("set_music", {"track": "CMF Studio", "artist": "", "state": 0}, ttl_s=60)
        return media.public()

    @app.get("/workouts", dependencies=[Depends(auth)])
    async def get_workouts(limit: int = Query(30, ge=1, le=500)) -> list[dict[str, Any]]:
        return [
            {**w, "sport_name": settings_mod.SPORTS.get(w["sport"], f"Sport {w['sport']}")}
            for w in store.workouts(limit)
        ]

    @app.get("/workouts/{start_ts}/route", dependencies=[Depends(auth)])
    async def get_workout_route(start_ts: int) -> dict[str, Any]:
        w = next((w for w in store.workouts(500) if w["start_ts"] == start_ts), None)
        if w is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such workout")
        return {"start_ts": start_ts, "points": store.route(w["start_ts"], w["end_ts"])}

    @app.get("/rules", dependencies=[Depends(auth)])
    async def get_rules() -> dict[str, Any]:
        if rules is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "rules engine not running")
        return rules.describe()

    @app.post("/rules/reload", dependencies=[Depends(auth)])
    async def reload_rules() -> dict[str, Any]:
        """Re-read rules.json (the owner edits it by hand; there is deliberately no API to write rules)."""
        if rules is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "rules engine not running")
        return rules.reload()

    if ui_dir is not None and (ui_dir / "index.html").exists():
        # the built Svelte app: static, no secrets; it gets the token from `cmf ui` (URL fragment)
        app.mount("/ui", StaticFiles(directory=ui_dir, html=True), name="ui")

        @app.get("/", include_in_schema=False)
        async def root() -> RedirectResponse:
            return RedirectResponse("/ui/")

    return app
