"""Rules engine on the simulated watch: triggers (sync, job, button) -> the closed set of actions."""

import asyncio
import datetime as dt
import json

import httpx
import pytest
from cmfkit.protocol import opcodes as ops

from cmfstudio.api.app import create_app
from cmfstudio.core.jobs import JobQueue
from cmfstudio.core.rules import DEFAULT_RULES, MUSIC_TRACK, RulesEngine, load
from cmfstudio.store.db import Store

PLAY_PAUSE = bytes.fromhex("0100")  # Pro 2 2-byte payloads (findings 2026-10-25); the sim sends cmfkit's 1-byte ones
NEXT = bytes.fromhex("0201")


def write(path, rules):
    path.write_text(json.dumps({"rules": rules}))
    return path


@pytest.fixture
async def setup(paired_link, tmp_path):
    store = Store(":memory:")
    refreshed, shortcuts = [], []

    async def boom(_link, _args):
        raise RuntimeError("nope")

    queue = JobQueue(
        store,
        paired_link,
        max_attempts=1,
        retry_s=0.01,
        handlers={"boom": boom, "sync": lambda _l, _a: asyncio.sleep(0)},
    )
    queue.start()

    async def refresh():
        refreshed.append(True)

    async def shortcut(name):
        shortcuts.append(name)

    def make(rules=None):
        path = write(tmp_path / "rules.json", rules) if rules is not None else tmp_path / "none.json"
        engine = RulesEngine(store, queue, path, refresh, paired_link.status, shortcut)
        paired_link.subscribe(engine.on_link_event)
        queue.on_done.append(engine.on_job_done)
        return engine

    yield make, store, queue, refreshed, shortcuts
    await queue.stop()


async def settle(queue, store, kind, n=1, timeout=2.0):
    async def poll():
        while len([j for j in store.recent_jobs(50) if j.kind == kind and j.state == "done"]) < n:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


def test_load_defaults_invalid_and_closed_action_set(tmp_path):
    assert load(tmp_path / "missing.json") == (DEFAULT_RULES, None)
    bad = write(tmp_path / "bad.json", [{"on": "button", "button": "next", "do": [{"action": "shell", "cmd": "rm"}]}])
    rules, err = load(bad)
    assert rules == DEFAULT_RULES and "shell" in err, "unknown actions are rejected, not ignored"
    (tmp_path / "junk.json").write_text("{")
    assert load(tmp_path / "junk.json")[1]
    ok = write(tmp_path / "ok.json", [{"on": "button", "button": "vol_up", "do": [{"action": "sync"}]}])
    assert load(ok)[0].rules[0].button == "vol_up"


async def test_default_rule_refreshes_face_after_sync(setup):
    make, store, queue, refreshed, _ = setup
    engine = make()
    assert not engine.describe()["from_file"] and not engine.wants_buttons
    queue.submit("sync", {})
    await settle(queue, store, "sync")
    await asyncio.sleep(0.05)
    assert refreshed == [True]


async def test_button_sends_music_info_then_notifies_with_todays_stats(setup, paired_link, watch):
    make, store, queue, _, _ = setup
    store.db.execute("INSERT INTO daily(date, metric, value) VALUES (?, 'steps', 4321)", (dt.date.today().isoformat(),))
    make(
        [
            {
                "on": "button",
                "button": "play_pause",
                "do": [{"action": "notify", "title": "Today", "body": "{steps} steps · RHR {hr_resting} · {battery}%"}],
            },
        ]
    )
    paired_link._emit("state", state="connected")  # what the link emits on each (re)connect
    await settle(queue, store, "set_music")
    assert watch.state.music[0] == MUSIC_TRACK

    watch._reply(ops.MUSIC_BUTTON, PLAY_PAUSE)
    watch._reply(ops.MUSIC_BUTTON, PLAY_PAUSE)  # within the cooldown: ignored
    await settle(queue, store, "notify")
    await asyncio.sleep(0.05)
    assert watch.state.notifications == [("Today", "4,321 steps · RHR - · 87%")]
    assert [e["data"]["button"] for e in store.events(10, "rule")] == ["play_pause"]


async def test_button_sync_and_shortcut(setup, watch):
    make, store, queue, _, shortcuts = setup
    make(
        [
            {
                "on": "button",
                "button": "next",
                "cooldown_s": 0,
                "do": [{"action": "shortcut", "name": "Focus On"}, {"action": "sync"}],
            }
        ]
    )
    watch._reply(ops.MUSIC_BUTTON, NEXT)
    await asyncio.sleep(0.1)
    assert shortcuts == ["Focus On"]
    await settle(queue, store, "sync")


async def test_failed_job_notifies_but_notify_jobs_never_trigger_rules(setup, watch):
    make, store, queue, _, _ = setup
    make(
        [
            {
                "on": "job",
                "state": "failed",
                "cooldown_s": 0,
                "do": [{"action": "notify", "title": "{job} failed", "body": "{error}"}],
            }
        ]
    )
    queue.submit("boom", {})
    await settle(queue, store, "notify")
    await asyncio.sleep(0.1)
    assert watch.state.notifications == [("boom failed", "RuntimeError('nope')")]
    assert len(store.events(10, "rule")) == 1


async def test_rules_api_reads_and_reloads_but_cannot_write(setup, paired_link, tmp_path):
    make, store, queue, _, _ = setup
    engine = make([{"on": "sync", "do": [{"action": "refresh_face"}]}])
    app = create_app(paired_link, queue, store, "t", tmp_path, rules=engine)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers={"Authorization": "Bearer t"}
    ) as c:
        got = (await c.get("/rules")).json()
        assert got["from_file"] and got["rules"] == [
            {"on": "sync", "cooldown_s": 2.0, "do": [{"action": "refresh_face"}]}
        ]
        write(engine.path, [{"on": "button", "button": "prev", "do": [{"action": "sync"}]}])
        assert (await c.post("/rules/reload")).json()["rules"][0]["button"] == "prev"
        assert (await c.put("/rules", json={})).status_code == 405
    await settle(queue, store, "set_music")  # a reload that adds button rules enables buttons right away
