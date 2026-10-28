"""Watch settings on the simulator (seeded with the owner's watch's GET replies), verified writes, find watch/phone."""

import asyncio

import httpx
import pytest

from cmfstudio.api.app import create_app
from cmfstudio.ble import sim as sim_mod
from cmfstudio.core import settings as s
from cmfstudio.core.findphone import FindPhone
from cmfstudio.core.jobs import JobQueue
from cmfstudio.store.db import Store


def test_parse_the_owner_watch_replies():
    alarms = s.parse_alarms(sim_mod.PROBED[0x0063])
    assert [a.model_dump() for a in alarms] == [{"time": "08:20", "enabled": False, "days": [], "label": ""}]
    goals = s.parse_goals(sim_mod.PROBED[0x005E])
    assert goals == {
        "steps": 5000,
        "distance_m": 4000,
        "kcal": 400,
        "sleep_min": 720,
        "exercise_min": 30,
        "stand_h": 12,
    }
    r = s.parse_reminder(sim_mod.PROBED[0x0060])
    assert (r.enabled, r.every_min, r.quiet_start) == (False, 60, None)
    assert (
        s.parse_sports(sim_mod.PROBED[0x00DC])[:3] == [0x02, 0x03, 0x01]
        and len(s.parse_sports(sim_mod.PROBED[0x00DC])) == 16
    )


def test_alarm_encoding():
    a = s.Alarm(time="7:05", days=["sat", "mon"], label="Gym")
    assert a.time == "07:05" and a.days == ["mon", "sat"] and a.mask == 0b100001
    rec = s.encode_alarms([a], "big")
    assert rec[:4] == (7 * 3600 + 5 * 60).to_bytes(4, "big") and rec[4:8] == bytes([0, 1, 0x21, 1])
    assert s.encode_alarms([s.Alarm(time="08:20", enabled=False)], "little")[:8] == sim_mod.PROBED[0x0063][:8]
    assert rec[8:11] == b"Gym" and len(rec) == 40
    with pytest.raises(ValueError):
        s.Alarm(time="25:00")


@pytest.fixture
async def api(paired_link, watch, tmp_path):
    store = Store(":memory:")
    queue = JobQueue(store, paired_link, retry_s=0.01)
    svc = s.SettingsService(paired_link, store)
    app = create_app(paired_link, queue, store, "t", tmp_path, settings=svc)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers={"Authorization": "Bearer t"}
    ) as c:
        yield c, watch, store


async def test_snapshot(api):
    c, *_ = api
    snap = (await c.get("/settings")).json()
    assert snap["alarms"][0]["time"] == "08:20" and snap["goals"]["steps"] == 5000
    assert snap["time_format"] == "12h" and snap["wrist_raise"] is True and snap["dnd"] is False
    assert snap["sports"][0] == {"code": 2, "name": "Outdoor running"}


async def test_alarms_write_is_verified_sorted(api):
    c, watch, store = api
    alarms = [{"time": "08:20", "enabled": False}, {"time": "06:45", "days": ["mon", "tue"], "label": "Run"}]
    r = (await c.put("/settings/alarms", json=alarms)).json()
    assert r["ok"] and r["encoding"] == "be"
    back = (await c.get("/settings")).json()["alarms"]
    assert [a["time"] for a in back] == ["06:45", "08:20"], "the watch keeps them sorted"
    assert back[0]["label"] == "Run" and back[0]["days"] == ["mon", "tue"]


async def test_failed_write_restores_in_the_write_format(api, monkeypatch):
    c, watch, _store = api
    before = watch.settings[0x0063]
    real = sim_mod._as_read
    calls = []

    def flaky(cmd1, written, current):  # the first write is lost, the restore goes through normally
        calls.append(written)
        return current if len(calls) == 1 else real(cmd1, written, current)

    monkeypatch.setattr(sim_mod, "_as_read", flaky)
    r = await c.put("/settings/alarms", json=[{"time": "09:00"}])
    assert r.status_code == 502
    assert watch.settings[0x0063] == before, "08:20 written back big-endian, so it reads back as 08:20 again"


async def test_goals_switches_reminders_sports(api):
    c, watch, _ = api
    r = (await c.put("/settings/goals", json={"steps": 8000, "distance_m": 6000, "kcal": 450})).json()
    assert r["acked"] and "unverified" in r["encoding"]
    assert (0x005E, 0x0001, bytes.fromhex("00001f400000177001c2")) in watch.written
    assert (await c.put("/settings/switch/dnd", json={"on": True})).json()["ok"]
    assert (await c.get("/settings")).json()["dnd"] is True
    reminder = {"enabled": True, "every_min": 45, "quiet_start": "12:00", "quiet_end": "13:30"}
    assert (await c.put("/settings/reminders/water_reminder", json=reminder)).json()["ok"]
    assert (await c.get("/settings")).json()["water_reminder"] == reminder
    assert (await c.put("/settings/sports", json=[1, 3, 5])).json()["ok"]
    assert [x["code"] for x in (await c.get("/settings")).json()["sports"]] == [1, 3, 5]
    assert (await c.put("/settings/sports", json=[])).status_code == 422


async def test_write_only_settings(api):
    c, watch, store = api
    assert (await c.put("/settings/monitoring/stress", json={"on": True})).json()["acked"]
    assert (0x009B, 0x0001, bytes([4, 1])) in watch.written
    assert (await c.put("/settings/hr_alerts", json={"low": 40, "high": 150})).status_code == 200
    assert (0xFFFF, 0x9059, bytes([1, 40, 150, 255, 0, 0, 0, 0, 0])) in watch.written
    assert (await c.put("/settings/units", json={"metric": False, "celsius": True})).json()["acked"]
    assert (await c.get("/settings/written")).json()["units"] == {"metric": False, "celsius": True}


async def test_find_watch(api):
    c, watch, _ = api
    before = watch.state.vibrations
    assert (await c.post("/find-watch")).json() == {"acked": True} and watch.state.vibrations == before + 1


async def test_find_phone_rings_until_stopped():
    finder = FindPhone(player="/bin/sleep", sound="5")
    finder.on_link_event({"kind": "find_phone", "start": True})
    await asyncio.sleep(0.05)
    assert finder.ringing
    finder.on_link_event({"kind": "find_phone", "start": False})
    await asyncio.sleep(0.05)
    assert not finder.ringing


async def test_tiles_are_read_only_and_parsed(api):
    c, *_ = api
    screens = (await c.get("/settings/tiles")).json()["screens"]
    assert [len(s) for s in screens] == [2, 3, 3, 3]
    assert screens[0][0] == {"id": 93, "sport": 0, "name": "Activity record"}
    assert screens[2][2] == {"id": 90, "sport": 2, "name": "Sport"}
    assert (await c.put("/settings/tiles", json=[])).status_code == 405, "no way to write tiles"


async def test_contacts_info_panel_with_backup(api):
    c, watch, store = api
    lines = [{"name": "Milk, eggs, bread"}, {"name": "Gate code", "number": "4821"}, {"name": "é" * 40}]
    r = (await c.put("/settings/contacts", json=lines)).json()
    assert r["ok"]
    got = (await c.get("/settings/contacts")).json()
    assert got["contacts"][1] == {"name": "Gate code", "number": "4821"} and got["contacts"][2]["name"] == "é" * 16
    assert got["backups"] == [], "the watch had no contacts: nothing to back up"
    await c.put("/settings/contacts", json=[{"name": "next"}])
    backups = (await c.get("/settings/contacts")).json()["backups"]
    assert backups[0]["contacts"][0]["name"] == "Milk, eggs, bread"
    assert (await c.put("/settings/contacts", json=[{"name": "x"}] * 21)).status_code == 422


async def test_clearing_contacts_writes_one_blank_record(api):
    c, watch, _ = api
    await c.put("/settings/contacts", json=[{"name": "temp"}])
    assert (await c.put("/settings/contacts", json=[])).json()["ok"]
    assert watch.settings[0x00D5] == bytes(57) and (await c.get("/settings/contacts")).json()["contacts"] == []
