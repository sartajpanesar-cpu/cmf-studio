"""Weather for the watch's weather screen, plus a "glance" line in its city field.

WEATHER_SET_1 (FFFF:906B, 199 bytes) — layout from the Pro 2 spec §9 and Gadgetbridge's Pro 2 driver:

    7 × day   (9 B): condition · temp · max · min · humidity · aqi u16 BE · uv · wind   (today first: temp = now)
    24 × hour (2 B): temp · condition   (the hours after now)
    city      (32 B): UTF-8, the watch scrolls past ~10 characters (≤ 30 bytes used)
    7 × sun   (8 B): sunrise u32 LE · sunset u32 LE (epoch seconds)

Temperatures are ``(°C + 100) & 0xFF``. Condition icons (observed by Gadgetbridge): 1 sun, 2 cloud, 3 overcast,
4 showers, 6 fog, 9 thunder, 11 snow, 14 sleet, 19 hot, 20 cold, 21 wind, 22 night haze, 23 clear night, 24 cloudy
night, 25 haze, 26 sun with cloud, 0 = no data.

The forecast comes from Open-Meteo (no account, no key): only the configured place's coordinates, rounded to two
decimals (~1 km), leave the Mac. The glance line is the place name, a fixed text, or the first line printed by one
of the owner's macOS Shortcuts (e.g. one that returns the next calendar event — Shortcuts asks for calendar access,
watchd never touches the calendar itself).
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from ..store.db import Store

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
SETTINGS_KEY = "weather_settings"
LAST_KEY = "weather_last"
CITY_BYTES = 30
REFRESH_S = 30 * 60

# WMO weather code (Open-Meteo) -> CMF icon
_WMO: dict[int, int] = {0: 1, 1: 26, 2: 26, 3: 3, 45: 6, 48: 6, 95: 9, 96: 9, 99: 9, 66: 14, 67: 14}
_WMO |= {c: 4 for c in (51, 53, 55, 56, 57, 61, 63, 65, 80, 81, 82)}
_WMO |= {c: 11 for c in (71, 73, 75, 77, 85, 86)}
_NIGHT = {1: 23, 26: 24, 25: 22}


def cmf_condition(wmo: int | None, day: bool = True) -> int:
    icon = _WMO.get(int(wmo), 0) if wmo is not None else 0
    return icon if day else _NIGHT.get(icon, icon)


def temp_byte(celsius: float | None) -> int:
    return 1 if celsius is None else (round(celsius) + 100) & 0xFF  # 1 = -99 °C: "no data"


class Place(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class WeatherSettings(BaseModel):
    enabled: bool = False
    place: Place | None = None
    glance: Literal["place", "text", "shortcut"] = "place"
    glance_text: str = Field("", max_length=60)
    glance_shortcut: str = Field("", max_length=100)


@dataclass
class Forecast:
    now_temp: float | None
    now_code: int | None
    is_day: bool
    humidity: int
    wind: float
    days: list[dict[str, Any]]  # date, code, max, min, sunrise, sunset, uv
    hours: list[tuple[int, float | None, int | None, bool]]  # epoch, temp, code, is_day
    fetched_at: float = field(default_factory=time.time)

    def summary(self) -> dict[str, Any]:
        today = self.days[0] if self.days else {}
        return {
            "now_c": self.now_temp,
            "icon": cmf_condition(self.now_code, self.is_day),
            "max_c": today.get("max"),
            "min_c": today.get("min"),
            "fetched_at": self.fetched_at,
        }


async def geocode(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    r = await client.get(GEOCODE_URL, params={"name": query, "count": 5, "format": "json"})
    r.raise_for_status()
    return [
        {
            "name": ", ".join(p for p in (g.get("name"), g.get("admin1"), g.get("country_code")) if p),
            "latitude": round(g["latitude"], 2),
            "longitude": round(g["longitude"], 2),
        }
        for g in r.json().get("results") or []
    ]


async def fetch(client: httpx.AsyncClient, place: Place) -> Forecast:
    r = await client.get(
        FORECAST_URL,
        params={
            "latitude": round(place.latitude, 2),
            "longitude": round(place.longitude, 2),
            "current": "temperature_2m,weather_code,relative_humidity_2m,wind_speed_10m,is_day",
            "hourly": "temperature_2m,weather_code,is_day",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,uv_index_max",
            "timezone": "auto",
            "timeformat": "unixtime",
            "forecast_days": 7,
        },
    )
    r.raise_for_status()
    return parse_forecast(r.json())


def parse_forecast(data: dict[str, Any]) -> Forecast:
    cur, hourly, daily = data.get("current", {}), data.get("hourly", {}), data.get("daily", {})
    days = [
        {
            "date": t,
            "code": daily["weather_code"][i],
            "max": daily["temperature_2m_max"][i],
            "min": daily["temperature_2m_min"][i],
            "sunrise": daily["sunrise"][i],
            "sunset": daily["sunset"][i],
            "uv": daily.get("uv_index_max", [None] * (i + 1))[i],
        }
        for i, t in enumerate(daily.get("time", []))
    ]
    hours = [
        (t, hourly["temperature_2m"][i], hourly["weather_code"][i], bool(hourly.get("is_day", [1] * (i + 1))[i]))
        for i, t in enumerate(hourly.get("time", []))
    ]
    return Forecast(
        now_temp=cur.get("temperature_2m"),
        now_code=cur.get("weather_code"),
        is_day=bool(cur.get("is_day", 1)),
        humidity=int(cur.get("relative_humidity_2m") or 0),
        wind=float(cur.get("wind_speed_10m") or 0),
        days=days,
        hours=hours,
    )


def payload(f: Forecast, city: str, now: float | None = None) -> bytes:
    now = time.time() if now is None else now
    out = bytearray()
    for i in range(7):
        d = f.days[i] if i < len(f.days) else None
        if d is None:
            out += bytes([0, 1, 1, 1, 0]) + struct.pack(">H", 0) + bytes([0, 0])
            continue
        if i == 0:
            out += bytes([cmf_condition(f.now_code, f.is_day), temp_byte(f.now_temp)])
            humidity, wind = f.humidity, f.wind
        else:
            out += bytes([cmf_condition(d["code"]), temp_byte(d["max"])])
            humidity, wind = 0, 0
        uv = int(d.get("uv") or 0)
        out += bytes([temp_byte(d["max"]), temp_byte(d["min"]), min(100, humidity)])
        out += struct.pack(">H", 0) + bytes([min(255, uv), min(255, round(wind))])
    next_hour = (int(now) // 3600 + 1) * 3600
    later = [h for h in f.hours if h[0] >= next_hour][:24]
    for _t, temp, code, day in later:
        out += bytes([temp_byte(temp), cmf_condition(code, day)])
    out += bytes([1, 0]) * (24 - len(later))
    out += city_field(city)
    for i in range(7):
        d = f.days[i] if i < len(f.days) else None
        out += struct.pack("<II", int(d["sunrise"]), int(d["sunset"])) if d else struct.pack("<II", 0, 0)
    assert len(out) == 199, len(out)
    return bytes(out)


def city_field(text: str) -> bytes:
    raw = text.encode("utf-8")[:CITY_BYTES]
    while raw:  # don't leave half a multi-byte character at the cut
        try:
            raw.decode("utf-8")
            break
        except UnicodeDecodeError:
            raw = raw[:-1]
    return raw.ljust(32, b"\0")


async def run_shortcut_output(name: str, timeout: float = 30) -> str:
    """First line a named macOS Shortcut prints (argv, no shell)."""
    proc = await asyncio.create_subprocess_exec(
        "shortcuts", "run", name, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout)
    if proc.returncode:
        raise RuntimeError(f"shortcut {name!r} exited {proc.returncode}: {err.decode(errors='replace')[:200]}")
    return next((line.strip() for line in out.decode("utf-8", "replace").splitlines() if line.strip()), "")


class WeatherService:
    """Keeps the watch's weather screen current while weather is enabled (every 30 min and on reconnect)."""

    def __init__(self, store: Store, queue, client_factory=None, shortcut=run_shortcut_output) -> None:  # type: ignore[no-untyped-def]
        self.store = store
        self.queue = queue
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=15))
        self.shortcut = shortcut
        self.last_error: str | None = None

    def settings(self) -> WeatherSettings:
        raw = self.store.get_kv(SETTINGS_KEY)
        return WeatherSettings.model_validate_json(raw) if raw else WeatherSettings()

    def save(self, settings: WeatherSettings) -> WeatherSettings:
        self.store.set_kv(SETTINGS_KEY, settings.model_dump_json())
        return settings

    def last(self) -> dict[str, Any] | None:
        raw = self.store.get_kv(LAST_KEY)
        return json.loads(raw) if raw else None

    async def glance(self, s: WeatherSettings) -> str:
        if s.glance == "text" and s.glance_text:
            return s.glance_text
        if s.glance == "shortcut" and s.glance_shortcut:
            try:
                return (await self.shortcut(s.glance_shortcut)) or (s.place.name if s.place else "")
            except Exception as exc:  # a broken Shortcut shouldn't stop the weather
                log.warning("glance shortcut failed: %r", exc)
                self.last_error = f"glance shortcut: {exc}"
        return s.place.name.split(",")[0] if s.place else ""

    async def push(self) -> dict[str, Any]:
        """Fetch the forecast and queue it for the watch. Raises if weather isn't set up."""
        s = self.settings()
        if not s.place:
            raise LookupError("choose a place for the weather first")
        async with self.client_factory() as client:
            forecast = await fetch(client, s.place)
        city = await self.glance(s)
        job = self.queue.submit("weather", {"payload": payload(forecast, city).hex()}, ttl_s=REFRESH_S)
        last = {**forecast.summary(), "glance": city, "place": s.place.name, "job_id": job.id}
        self.store.set_kv(LAST_KEY, json.dumps(last))
        self.last_error = None
        return {**last, "job": job.public()}

    async def run(self, link) -> None:  # type: ignore[no-untyped-def]
        """Background loop: push when weather is enabled, on (re)connect and every REFRESH_S."""
        while True:
            await link.connected.wait()
            if self.settings().enabled:
                try:
                    await self.push()
                except Exception as exc:
                    self.last_error = repr(exc)
                    log.warning("weather refresh failed: %r", exc)
            deadline = time.monotonic() + REFRESH_S
            while time.monotonic() < deadline and link.connected.is_set():
                await asyncio.sleep(5)
            if not link.connected.is_set():
                await asyncio.sleep(1)
