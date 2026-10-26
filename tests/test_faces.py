"""Faces in watchd: save/install/skip-unchanged, refresh after sync, API — against the sim watch."""

import base64
import time

import httpx
import pytest

from cmfstudio.api.app import create_app
from cmfstudio.ble.link import parse_dial_list
from cmfstudio.core.jobs import JobQueue
from cmfstudio.render.service import FaceService
from cmfstudio.render.spec import FaceSpec
from cmfstudio.store.db import Store
from cmfstudio.sync import daily
from cmfstudio.sync.parsers import Sample

from .test_render import needs_browser

pytestmark = needs_browser

SPEC = FaceSpec.model_validate(
    {
        "name": "steps",
        "layers": [{"type": "text", "bind": "metric:steps", "size": 90}, {"type": "ring", "bind": "metric:steps"}],
        "clock": {"style": 2, "x": 100, "y": 60, "color": "#ff0000"},
    }
)
CUSTOM_ACTIVE = parse_dial_list(bytes.fromhex("0104050713010000600100001401000018010000ffffffff"))
STORE_FACE_ACTIVE = parse_dial_list(bytes.fromhex("0100050713010000600100001401000018010000ffffffff"))


@pytest.fixture
async def faces(paired_link, tmp_path):
    store = Store(":memory:")
    q = JobQueue(store, paired_link, retry_s=0.01)
    q.start()
    svc = FaceService(store, paired_link, q, tmp_path / "faces")
    yield svc
    await q.stop()


def add_steps(store, steps):
    store.upsert_samples([Sample("steps_cum", int(time.time()) - 60, steps)], None)
    daily.rebuild(store, int(time.time()) - 3600)


async def test_install_then_skip_unchanged(faces, watch):
    face_id = faces.save(SPEC)
    res = await faces.install(face_id)
    job = await faces.queue.wait(res["job"]["id"], 10)
    assert job.state == "done" and job.result == {"activated": True}
    assert watch.state.active_dial is not None
    assert job.args["color565"] == 0xF800 and job.args["style"] == 2  # clock settings from the spec
    row = faces.store.get_face(face_id)
    assert row["last_digest"] == res["render"]["digest"] and row["last_rendered_png"]
    assert faces.active_id() == face_id
    assert (await faces.install(face_id))["skipped"] == "unchanged"


async def test_failed_install_is_not_marked(faces, monkeypatch):
    from cmfstudio.core import jobs as jobsmod

    async def boom(_link, _args):
        raise RuntimeError("watch said 0a")

    monkeypatch.setitem(jobsmod.HANDLERS, "install_face", boom)
    face_id = faces.save(SPEC)
    res = await faces.install(face_id)
    assert (await faces.queue.wait(res["job"]["id"], 10)).state == "failed"
    assert faces.store.get_face(face_id)["last_digest"] is None


async def test_refresh_after_sync_reinstalls_only_when_pixels_change(faces, paired_link):
    face_id = faces.save(SPEC)
    await faces.queue.wait((await faces.install(face_id))["job"]["id"], 10)
    paired_link.dials = CUSTOM_ACTIVE
    assert (await faces.refresh_after_sync())["skipped"] == "unchanged"
    add_steps(faces.store, 4321)
    res = await faces.refresh_after_sync()
    assert (await faces.queue.wait(res["job"]["id"], 10)).state == "done"
    assert res["render"]["context"]["metrics"]["steps"] == 4321


async def test_refresh_skipped_when_another_face_is_active(faces, paired_link):
    face_id = faces.save(SPEC)
    await faces.queue.wait((await faces.install(face_id))["job"]["id"], 10)
    paired_link.dials = STORE_FACE_ACTIVE
    add_steps(faces.store, 999)
    assert await faces.refresh_after_sync() is None
    assert faces.store.events(kind="face")[0]["data"]["reason"] == "another face is active"


async def test_manual_refresh_faces_are_left_alone(faces):
    face_id = faces.save(SPEC.model_copy(update={"refresh": "manual"}))
    faces.store.set_kv("active_face_id", str(face_id))
    assert await faces.refresh_after_sync() is None


async def test_faces_api(faces, paired_link, tmp_path):
    app = create_app(paired_link, faces.queue, faces.store, "t", tmp_path, faces=faces)
    auth = {"Authorization": "Bearer t"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
        body = SPEC.model_dump(by_alias=True)
        prev = (await c.post("/faces/preview", json=body, headers=auth)).json()
        assert base64.b64decode(prev["preview_png"])[:8] == b"\x89PNG\r\n\x1a\n"
        assert prev["checks"]["background_black"]
        face_id = (await c.post("/faces", json=body, headers=auth)).json()["id"]
        assert (await c.post(f"/faces/{face_id}/install", headers=auth)).status_code == 400
        res = (await c.post(f"/faces/{face_id}/install", params={"confirm": True}, headers=auth)).json()
        assert res["job"]["state"] == "done"
        loaded = (await c.get(f"/faces/{face_id}", headers=auth)).json()
        assert FaceSpec.model_validate(loaded["spec"]) == SPEC
        assert (await c.get("/faces/999", headers=auth)).status_code == 404
        listing = (await c.get("/faces", headers=auth)).json()
        assert listing["active_id"] == face_id and listing["faces"][0]["name"] == "steps"
        assert (await c.put("/insight", json={"text": "slept well"}, headers=auth)).status_code == 200
        assert faces.store.get_kv("insight") == "slept well"
        live = (await c.post("/faces/html", json=body, headers=auth)).json()
        assert live["html"].startswith("<!doctype html>") and "<svg" in live["html"]  # ring track
        assert live["clock_svg"].count("<circle") > 30 and 'fill="#ff0000"' in live["clock_svg"]
        assert len(live["clock_box"]) == 4
        bad = await c.post("/faces/preview", json={"name": "x", "background": "red"}, headers=auth)
        assert bad.status_code == 422


def test_migration_2_dedupes_face_names(tmp_path):
    import sqlite3

    from cmfstudio.store import db as dbmod

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    for statement in filter(str.strip, dbmod.MIGRATIONS[0].split(";")):
        old.execute(statement)
    old.execute("PRAGMA user_version = 1")
    old.execute("INSERT INTO faces(name, spec_json) VALUES ('a', '1'), ('a', '2'), ('b', '3')")
    old.commit()
    old.close()
    store = Store(path)
    assert store.version == 3  # migrated all the way
    assert [(f["name"]) for f in store.faces()] == ["a", "b"]
    assert store.db.execute("SELECT spec_json FROM faces WHERE name='a'").fetchone()[0] == "2"


async def test_install_marks_custom_face_active_without_waiting_for_watch(faces, paired_link):
    paired_link.dials = STORE_FACE_ACTIVE  # what the watch reported at login, before the install
    face_id = faces.save(SPEC)
    await faces.queue.wait((await faces.install(face_id))["job"]["id"], 10)
    assert paired_link.custom_face_active is True
    add_steps(faces.store, 2222)
    res = await faces.refresh_after_sync()  # must not skip as "another face is active"
    assert res and "job" in res
