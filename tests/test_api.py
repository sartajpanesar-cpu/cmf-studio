import asyncio
import io
import struct

import httpx
import pytest
from PIL import Image

from cmfstudio.api.app import create_app
from cmfstudio.ble.link import LinkState
from cmfstudio.core.jobs import JobQueue
from cmfstudio.store.db import Store
from cmfstudio.sync import parsers as p
from cmfstudio.sync.manager import SyncManager

from .conftest import wait_state

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (466, 466), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
async def api(start_link, tmp_path):
    """(client, link, store) for a not-yet-paired sim watch behind the API."""
    link = start_link()
    store = Store(":memory:")
    sync = SyncManager(store, idle_s=0.3)
    sync.attach(link)
    queue = JobQueue(store, link, retry_s=0.01, handlers={"sync": lambda lk, _a: sync.fetch(lk)})
    queue.start()
    app = create_app(link, queue, store, TOKEN, tmp_path / "faces", sync)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        yield client, link, store
    await queue.stop()


@pytest.fixture
async def paired_api(api):
    client, link, store = api
    await wait_state(link, LinkState.NEEDS_PAIR)
    r = await client.post("/pair", params={"confirm": True}, headers=AUTH)
    assert r.status_code == 200, r.text
    await wait_state(link, LinkState.CONNECTED)
    return api


async def test_requires_token(api):
    client, *_ = api
    assert (await client.get("/status")).status_code == 401
    assert (await client.get("/status", headers={"Authorization": "Bearer nope"})).status_code == 401
    assert (await client.get("/status", headers=AUTH)).status_code == 200


async def test_rejects_foreign_host(api):
    client, *_ = api
    r = await client.get("/status", headers={**AUTH, "Host": "evil.example"})
    assert r.status_code == 400


async def test_status_reports_link(paired_api):
    client, *_ = paired_api
    st = (await client.get("/status", headers=AUTH)).json()
    assert st["link"]["state"] == "connected" and st["link"]["firmware"] == "1.0.0.77"


async def test_notify_delivers_and_acks(paired_api, watch):
    client, *_ = paired_api
    r = await client.post("/notify", json={"title": "CMF Studio", "body": "via api"}, headers=AUTH)
    job = r.json()
    assert job["state"] == "done" and job["result"] == {"acked": True}
    assert watch.state.notifications[-1] == ("CMF Studio", "via api")


async def test_notify_while_away_is_queued(paired_api, watch):
    client, link, _ = paired_api
    watch.reachable = False
    watch.drop()
    await wait_state(link, LinkState.DISCONNECTED)
    job = (await client.post("/notify", json={"title": "later"}, headers=AUTH)).json()
    assert job["state"] == "queued"
    watch.reachable = True
    await wait_state(link, LinkState.CONNECTED)
    for _ in range(100):
        if (await client.get(f"/jobs/{job['id']}", headers=AUTH)).json()["state"] == "done":
            break
        await asyncio.sleep(0.01)
    assert watch.state.notifications[-1] == ("later", "")


async def test_face_install_requires_confirm(paired_api, watch):
    client, *_ = paired_api
    r = await client.post("/faces/install", content=png(), headers=AUTH)
    assert r.status_code == 400 and "confirm" in r.text
    assert watch.state.active_dial is None


async def test_face_install(paired_api, watch):
    client, _, store = paired_api
    r = await client.post("/faces/install", params={"confirm": True, "name": "t"}, content=png(), headers=AUTH)
    job = r.json()
    assert job["state"] == "done" and job["result"] == {"activated": True}
    assert watch.state.active_dial is not None
    assert store.db.execute("SELECT name FROM faces").fetchone()[0] == "t"


async def test_face_install_rejects_non_image(paired_api):
    client, *_ = paired_api
    r = await client.post("/faces/install", params={"confirm": True}, content=b"not an image", headers=AUTH)
    assert r.status_code == 400


async def test_pair_requires_confirm_and_refuses_when_connected(paired_api):
    client, *_ = paired_api
    assert (await client.post("/pair", headers=AUTH)).status_code == 400
    assert (await client.post("/pair", params={"confirm": True}, headers=AUTH)).status_code == 409


async def test_sync_endpoint_and_samples(paired_api, watch):
    client, *_ = paired_api
    watch.health_frames = [(p.HEART_RATE, struct.pack("<II", 1_761_400_000, 66))]
    job = (await client.post("/sync", headers=AUTH)).json()
    assert job["state"] == "done" and job["result"]["new_samples"] == {"hr": 1}
    r = (await client.get("/samples", params={"metric": "hr"}, headers=AUTH)).json()
    assert r["samples"] == [[1_761_400_000, 66.0]]
    summ = (await client.get("/samples/summary", headers=AUTH)).json()
    assert summ["metrics"]["hr"]["count"] == 1 and summ["raw_frames"] == 1


async def test_daily_endpoint(paired_api, watch):
    client, *_ = paired_api
    watch.health_frames = [(p.HEART_RATE, struct.pack("<II", 1_761_400_000, 66))]
    await client.post("/sync", headers=AUTH)
    days = (await client.get("/daily", params={"days": 400}, headers=AUTH)).json()["days"]
    assert any(m.get("hr_max") == 66 for m in days.values())
    assert (await client.post("/reparse", headers=AUTH)).json()["parse_errors"] == 0
