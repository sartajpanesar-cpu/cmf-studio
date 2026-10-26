"""MCP server against watchd's API on the simulated watch."""

import datetime as dt
import json
import struct

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from cmfstudio.api.app import create_app
from cmfstudio.core.jobs import JobQueue
from cmfstudio.mcp.server import build_server
from cmfstudio.render.service import FaceService
from cmfstudio.store.db import Store
from cmfstudio.sync import parsers as p
from cmfstudio.sync.manager import SyncManager

from .test_render import needs_browser

ALLOWED = {"watch_status", "sync_now", "query_health", "notify", "render_face", "list_faces", "install_face"}


@pytest.fixture
async def mcp(paired_link, tmp_path):
    store = Store(":memory:")
    sync = SyncManager(store, idle_s=0.3)
    sync.attach(paired_link)
    queue = JobQueue(store, paired_link, retry_s=0.01, handlers={"sync": lambda lk, _a: sync.fetch(lk)})
    queue.start()
    faces = FaceService(store, paired_link, queue, tmp_path / "faces")
    app = create_app(paired_link, queue, store, "t", tmp_path, sync, faces)

    def client():
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers={"Authorization": "Bearer t"}
        )

    yield build_server(client), store
    await queue.stop()


async def call(server, name, **args):
    res = await server.call_tool(name, args)
    return res


def data(res):
    assert not res.is_error, res.content[0].text
    return json.loads(res.content[0].text)


async def test_tool_surface_is_exactly_the_allowed_set(mcp):
    server, _ = mcp
    names = {t.name for t in await server.list_tools()}
    assert names == ALLOWED
    forbidden = {"pair", "at", "shell", "firmware", "ota", "reset", "raw", "frame", "flash", "unbind"}
    assert not any(set(name.split("_")) & forbidden for name in names)


async def test_status_and_notify(mcp, watch):
    server, _ = mcp
    st = data(await call(server, "watch_status"))
    assert st["state"] == "connected" and st["battery_percent"] == 87 and not st["needs_pairing"]
    sent = data(await call(server, "notify", title="Build done", body="tests passed"))
    assert sent == {"delivered": True}
    assert watch.state.notifications[-1] == ("Build done", "tests passed")


async def test_sync_and_daily_query(mcp, watch):
    server, _ = mcp
    noon = int(dt.datetime.combine(dt.date.today(), dt.time(12)).timestamp())
    watch.health_frames = [
        (p.ACTIVITY_DATA, struct.pack("<IIII", noon, 4321, 3000, 150) + bytes(16)),
        (p.HEART_RATE, struct.pack("<IIII", noon, 60, noon + 300, 80)),
    ]
    res = data(await call(server, "sync_now"))
    assert res["state"] == "done" and res["new_samples"]["steps_cum"] == 1
    today = dt.date.today().isoformat()
    steps = data(await call(server, "query_health", metric="steps", start=today))
    assert steps["values"] == {today: 4321}
    km = data(await call(server, "query_health", metric="distance_km", start=today))
    assert km["values"] == {today: 3.0}


async def test_hourly_query_returns_aggregates_not_samples(mcp, watch):
    server, store = mcp
    base = int(dt.datetime.combine(dt.date.today(), dt.time(9)).timestamp())
    store.upsert_samples([p.Sample("hr", base + 60 * i, 60 + i % 10) for i in range(120)], None)  # 2 h of minutes
    res = data(await call(server, "query_health", metric="hr", start=dt.date.today().isoformat(), agg="hour"))
    assert len(res["values"]) == 2, "120 minute samples collapse to 2 hourly means"
    assert all(isinstance(v, float) for v in res["values"].values())


async def test_query_validation(mcp):
    server, _ = mcp
    long_ago = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    with pytest.raises(ToolError, match="at most 7 days"):
        await call(server, "query_health", metric="hr", start=long_ago, agg="hour")
    with pytest.raises(ToolError, match="daily metrics"):
        await call(server, "query_health", metric="hr", start=long_ago)  # hr has no daily value
    with pytest.raises(ToolError):
        await call(server, "query_health", metric="nope", start=long_ago)


async def test_install_requires_confirm(mcp, watch):
    server, store = mcp
    face_id = store.save_face("x", json.dumps({"name": "x", "layers": [{"type": "text", "text": "hi"}]}))
    with pytest.raises(ToolError, match="confirm=true"):
        await call(server, "install_face", face_id=face_id)
    assert watch.state.active_dial is None


@needs_browser
async def test_install_with_confirm_and_render(mcp, watch):
    server, store = mcp
    face_id = store.save_face("x", json.dumps({"name": "x", "layers": [{"type": "text", "text": "hi"}]}))
    assert data(await call(server, "install_face", face_id=face_id, confirm=True))["state"] == "done"
    assert watch.state.active_dial is not None
    res = await call(server, "render_face", spec={"name": "p", "layers": [{"type": "text", "bind": "metric:steps"}]})
    assert [c.type for c in res.content] == ["image", "text"] and "lit" in res.content[1].text


async def test_watchd_down_gives_a_clear_error():
    def down():
        return httpx.AsyncClient(base_url="http://127.0.0.1:9")  # nothing listens here

    with pytest.raises(ToolError, match="watchd isn't running"):
        await build_server(down).call_tool("watch_status", {})
