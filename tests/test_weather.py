"""Weather + glance line: Open-Meteo parsing, the 199-byte WEATHER_SET_1 layout, and a push to the simulator."""

import asyncio
import struct
import time

import httpx
import pytest

from cmfstudio.api.app import create_app
from cmfstudio.core import weather as w
from cmfstudio.core.jobs import JobQueue
from cmfstudio.store.db import Store

NOW = 1_793_030_000  # 2026-10-26 ~09:53 UTC
HOUR = (NOW // 3600) * 3600


def fake_forecast() -> dict:
    return {
        "current": {"temperature_2m": 12.4, "weather_code": 2, "relative_humidity_2m": 71, "wind_speed_10m": 9.6,
                    "is_day": 1},
        "hourly": {
            "time": [HOUR + 3600 * i for i in range(30)],
            "temperature_2m": [10.0 + i * 0.5 for i in range(30)],
            "weather_code": [61] * 30,
            "is_day": [1] * 10 + [0] * 20,
        },
        "daily": {
            "time": [HOUR + 86400 * i for i in range(7)],
            "weather_code": [3, 0, 95, 71, 45, 61, 2],
            "temperature_2m_max": [14.2, 16, 11, 2, 9, 10, 13],
            "temperature_2m_min": [6.8, 7, 5, -3, 4, 5, 6],
            "sunrise": [NOW - 10_000 + 86400 * i for i in range(7)],
            "sunset": [NOW + 20_000 + 86400 * i for i in range(7)],
            "uv_index_max": [2.6, 3, 1, 0, 1, 2, 3],
        },
    }  # fmt: skip


def mock_client(log: list | None = None):
    def handler(req: httpx.Request) -> httpx.Response:
        if log is not None:
            log.append(req.url)
        if req.url.host == "geocoding-api.open-meteo.com":
            paris = {"name": "Paris", "admin1": "Ile-de-France", "country_code": "FR"}
            return httpx.Response(200, json={"results": [{**paris, "latitude": 48.85661, "longitude": 2.3522}]})
        return httpx.Response(200, json=fake_forecast())

    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_payload_layout():
    f = w.parse_forecast(fake_forecast())
    p = w.payload(f, "Standup 10:30 · Room 4", now=NOW)
    assert len(p) == 199
    assert p[0:4] == bytes([26, 112, 114, 107]), "today: sun+cloud (WMO 2), now 12°, max 14°, min 7° (+100)"
    assert p[4] == 71 and p[5:7] == b"\0\0" and p[7] == 2 and p[8] == 10
    assert p[9:13] == bytes([1, 116, 116, 107]), "tomorrow: clear, max, max, min"
    hours = p[63:111]
    assert hours[0:2] == bytes([110, 4]), "first hour after now: temp (10.5 → 10) then condition (rain)"
    assert p[111:143] == "Standup 10:30 · Room 4".encode().ljust(32, b"\0")
    assert struct.unpack_from("<II", p, 143) == (NOW - 10_000, NOW + 20_000)


def test_night_icons_and_no_data():
    assert w.cmf_condition(0, day=True) == 1 and w.cmf_condition(0, day=False) == 23
    assert w.cmf_condition(2, day=False) == 24 and w.cmf_condition(999) == 0 and w.cmf_condition(None) == 0
    assert w.temp_byte(-3) == 97 and w.temp_byte(None) == 1


def test_city_field_never_splits_a_character():
    field = w.city_field("é" * 40)
    assert len(field) == 32 and field.rstrip(b"\0").decode("utf-8") == "é" * 15


@pytest.fixture
async def weather_api(paired_link, watch, tmp_path):
    store = Store(":memory:")
    queue = JobQueue(store, paired_link, retry_s=0.01)
    queue.start()
    calls: list = []
    shortcut_runs: list = []

    async def shortcut(name):
        shortcut_runs.append(name)
        if name == "Watch broken":
            raise RuntimeError("nope")
        return "Dentist 3pm"

    svc = w.WeatherService(store, queue, client_factory=mock_client(calls), shortcut=shortcut)
    app = create_app(paired_link, queue, store, "t", tmp_path, weather=svc)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers={"Authorization": "Bearer t"}
    ) as c:
        yield c, watch, calls, shortcut_runs
    await queue.stop()


async def wait_job(c, job_id):
    for _ in range(100):
        job = (await c.get(f"/jobs/{job_id}")).json()
        if job["state"] in ("done", "failed", "expired"):
            return job
        await asyncio.sleep(0.02)
    raise AssertionError("job didn't finish")


async def test_place_search_and_push_with_glance(weather_api):
    c, watch, calls, runs = weather_api
    assert (await c.post("/weather/push")).status_code == 409, "no place yet"
    places = (await c.get("/weather/places", params={"q": "Paris"})).json()
    assert places == [{"name": "Paris, Ile-de-France, FR", "latitude": 48.86, "longitude": 2.35}]

    settings = {"enabled": True, "place": places[0], "glance": "shortcut", "glance_shortcut": "Watch next event"}
    assert (await c.put("/weather/settings", json=settings)).status_code == 200
    r = (await c.post("/weather/push")).json()
    assert r["glance"] == "Dentist 3pm" and runs == ["Watch next event"]
    assert (await wait_job(c, r["job"]["id"]))["state"] == "done"
    assert watch.state.weather_city == "Dentist 3pm"
    forecast_call = next(u for u in calls if u.host == "api.open-meteo.com")
    assert forecast_call.params["latitude"] == "48.86" and forecast_call.params["longitude"] == "2.35"

    # a broken Shortcut falls back to the place name instead of failing the weather
    bad = await c.put("/weather/settings", json={**settings, "glance_shortcut": "Delete all files"})
    assert bad.status_code == 422, "only Shortcuts named for the watch"
    await c.put("/weather/settings", json={**settings, "glance_shortcut": "Watch broken"})
    r = (await c.post("/weather/push")).json()
    assert r["glance"] == "Paris"
    state = (await c.get("/weather")).json()
    assert state["last"]["glance"] == "Paris" and state["settings"]["glance_shortcut"] == "Watch broken"


async def test_text_glance(weather_api):
    c, watch, *_ = weather_api
    place = {"name": "Paris, Ile-de-France, FR", "latitude": 48.86, "longitude": 2.35}
    settings = {"enabled": True, "place": place, "glance": "text", "glance_text": "Hello there"}
    await c.put("/weather/settings", json=settings)
    r = (await c.post("/weather/push")).json()
    await wait_job(c, r["job"]["id"])
    assert watch.state.weather_city == "Hello there"


def test_refresh_constant_is_sane():
    assert 10 * 60 <= w.REFRESH_S <= 3600 and time.time() > NOW - 10**9
