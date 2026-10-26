"""Health sync: parsers (docs/protocol-sync.md layouts) and the fetch handshake against the sim."""

import asyncio
import struct

import pytest

from cmfstudio.core.jobs import JobQueue
from cmfstudio.store.db import Store
from cmfstudio.sync import parsers as p
from cmfstudio.sync.manager import SyncManager

T0 = 1_761_400_000  # 2025-10-25


def activity(*recs):  # (ts, steps_cum, distance_m, calories_cal)
    return b"".join(struct.pack("<IIII", *r) + bytes(16) for r in recs)


def pairs(*recs):  # (ts, value) 8-byte records
    return b"".join(struct.pack("<II", *r) for r in recs)


def sleep_frame(start, end, totals, records):
    head = struct.pack("<IIHHHHH", start, end, *totals, 7)
    return head + b"".join(struct.pack("<IHH", *r) for r in records)


# -- parsers ------------------------------------------------------------------


def test_activity_record():
    out = p.activity(activity((T0, 1200, 900, 45000)))
    assert out == [
        p.Sample("steps_cum", T0, 1200),
        p.Sample("distance_cum_m", T0, 900),
        p.Sample("calories_cum_cal", T0, 45000),
    ]


def test_eight_byte_metrics():
    assert p.heart_rate(pairs((T0, 61), (T0 + 60, 64))) == [p.Sample("hr", T0, 61), p.Sample("hr", T0 + 60, 64)]
    assert p.spo2(pairs((T0, 97)))[0].metric == "spo2"
    assert p.stress(pairs((T0, 33)))[0].value == 33


def test_resting_hr_spec_example():
    # spec §8.2 live example: 5e dc 29 6a 4e -> 78 bpm
    assert p.heart_rate_resting(bytes.fromhex("5edc296a4e")) == [p.Sample("hr_resting", 0x6A29DC5E, 78)]


def test_sleep_session():
    s = p.sleep(
        sleep_frame(T0, T0 + 3600, (1200, 1800, 300, 300), [(T0, 1200, 1), (T0 + 1200, 1800, 2), (T0 + 3000, 600, 4)])
    )
    assert (s.start_ts, s.end_ts, s.unknown) == (T0, T0 + 3600, 7)
    assert s.totals_s == {"deep": 1200, "core": 1800, "rem": 300, "awake": 300}
    assert [st.stage for st in s.stages] == ["deep", "core", "awake"]


@pytest.mark.parametrize(
    ("fn", "payload"),
    [(p.activity, bytes(31)), (p.heart_rate, bytes(9)), (p.heart_rate_resting, bytes(6)), (p.sleep, bytes(20))],
)
def test_bad_lengths_raise(fn, payload):
    with pytest.raises(p.ParseError):
        fn(payload)


# -- fetch against the sim ------------------------------------------------------


@pytest.fixture
def day(watch):
    watch.health_frames = [
        (p.ACTIVITY_DATA, activity(*[(T0 + 60 * i, 10 * i, 8 * i, 400 * i) for i in range(20)])),  # 640 B: multi-chunk
        (p.HEART_RATE, pairs((T0, 61), (T0 + 600, 72))),
        (p.HEART_RATE_RESTING, bytes.fromhex("5edc296a4e")),
        (p.SPO2, pairs((T0, 97))),
        (p.STRESS, pairs((T0, 33))),
        (p.SLEEP_DATA, sleep_frame(T0 - 8 * 3600, T0, (3600, 14400, 5400, 1800), [(T0 - 8 * 3600, 3600, 1)])),
        (p.WORKOUT_SUMMARY, bytes(54)),
    ]
    return watch


@pytest.fixture
def sync(paired_link):
    store = Store(":memory:")
    mgr = SyncManager(store, idle_s=0.3, ready_s=0.5)
    mgr.attach(paired_link)
    return mgr, store


async def test_fetch_stores_raw_then_parsed(sync, paired_link, day):
    mgr, store = sync
    res = await mgr.fetch(paired_link)
    assert res["since_ts"] == day.since_ts and res["flags"] == "00000001"
    assert res["new_samples"]["steps_cum"] == 20 and res["new_samples"]["hr"] == 2
    assert res["sleep_sessions"] == 1 and res["parse_errors"] == 0
    raw = store.db.execute("SELECT opcode, parser_version FROM raw_frames ORDER BY id").fetchall()
    assert len(raw) == 7
    assert dict(raw)["0057:0001"] is None  # workout summary kept raw, not parsed
    counts = store.sample_counts()
    assert counts == {
        "steps_cum": 20, "distance_cum_m": 20, "calories_cum_cal": 20,
        "hr": 2, "hr_resting": 1, "spo2": 1, "stress": 1,
    }  # fmt: skip
    assert store.samples("hr") == [(T0, 61.0), (T0 + 600, 72.0)]


async def test_resync_is_idempotent(sync, paired_link, day):
    mgr, store = sync
    await mgr.fetch(paired_link)
    before = store.sample_counts()
    res = await mgr.fetch(paired_link)  # watch resends everything
    assert store.sample_counts() == before
    assert sum(res["new_samples"].values()) == 0
    assert store.db.execute("SELECT count(*) FROM sleep_sessions").fetchone()[0] == 1


async def test_parse_error_keeps_raw_frame(sync, paired_link, watch):
    mgr, store = sync
    watch.health_frames = [(p.HEART_RATE, bytes(9))]
    res = await mgr.fetch(paired_link)
    assert res["parse_errors"] == 1
    assert store.db.execute("SELECT payload_hex FROM raw_frames").fetchone()[0] == "00" * 9
    assert store.events(kind="parse_error")


async def test_declined_fetch(sync, paired_link, watch):
    mgr, _ = sync
    watch.fetch_ready_code = 0x00
    with pytest.raises(RuntimeError, match="declined"):
        await mgr.fetch(paired_link)


async def test_stalled_fetch_times_out(sync, paired_link, watch):
    mgr, _ = sync
    watch.send_finish = False
    with pytest.raises(TimeoutError, match="stalled"):
        await mgr.fetch(paired_link)
    assert mgr._run is None


async def test_unsolicited_data_is_stored(sync, paired_link, watch):
    _, store = sync
    watch._reply(p.HEART_RATE, pairs((T0, 88)))
    await asyncio.sleep(0.05)
    assert store.samples("hr") == [(T0, 88.0)]


async def test_sync_job(paired_link, day):
    store = Store(":memory:")
    mgr = SyncManager(store, idle_s=0.3)
    mgr.attach(paired_link)
    q = JobQueue(store, paired_link, handlers={"sync": lambda link, _a: mgr.fetch(link)})
    q.start()
    try:
        done = await q.wait(q.submit("sync", {}).id, 3)
        assert done.state == "done" and done.result["new_samples"]["spo2"] == 1
    finally:
        await q.stop()


async def test_scheduler_syncs_after_connect_and_periodically(paired_link, day):
    from cmfstudio.core.scheduler import SyncScheduler

    store = Store(":memory:")
    mgr = SyncManager(store, idle_s=0.3)
    mgr.attach(paired_link)
    q = JobQueue(store, paired_link, handlers={"sync": lambda link, _a: mgr.fetch(link)})
    q.start()
    sched = SyncScheduler(q, paired_link, interval_s=0.3, after_connect_s=0.05)
    task = asyncio.create_task(sched.run())
    try:
        await asyncio.sleep(1.0)
        syncs = [j for j in store.recent_jobs() if j.kind == "sync"]
        assert len(syncs) >= 2 and all(j.state == "done" for j in syncs[1:])
        assert store.sample_counts()["hr"] == 2  # periodic re-syncs never duplicate
    finally:
        task.cancel()
        await q.stop()


async def test_scheduler_skips_when_sync_pending(paired_link):
    from cmfstudio.core.scheduler import SyncScheduler

    store = Store(":memory:")
    q = JobQueue(store, paired_link, handlers={"sync": lambda link, _a: asyncio.sleep(10)})
    sched = SyncScheduler(q, paired_link)
    sched.kick()
    sched.kick()
    assert len([j for j in store.open_jobs() if j.kind == "sync"]) == 1


# -- daily totals and reparse -------------------------------------------------


def local_ts(y, mo, d, h, mi=0):
    import datetime as dt

    return int(dt.datetime(y, mo, d, h, mi).timestamp())


def test_daily_takes_max_cumulative_per_local_day():
    from cmfstudio.sync import daily

    store = Store(":memory:")
    store.upsert_samples(
        [
            p.Sample("steps_cum", local_ts(2026, 10, 24, 22), 4000),
            p.Sample("steps_cum", local_ts(2026, 10, 24, 23), 4500),
            p.Sample("steps_cum", local_ts(2026, 10, 25, 1), 20),  # reset at local midnight
            p.Sample("steps_cum", local_ts(2026, 10, 25, 9), 900),
            p.Sample("hr", local_ts(2026, 10, 25, 9), 60),
            p.Sample("hr", local_ts(2026, 10, 25, 10), 90),
            p.Sample("hr_resting", local_ts(2026, 10, 25, 9), 63),
            p.Sample("hr_resting", local_ts(2026, 10, 25, 11), 61),
        ],
        None,
    )
    store.upsert_sleep(
        p.SleepSession(local_ts(2026, 10, 24, 23), local_ts(2026, 10, 25, 7), {"deep": 3600, "core": 18000}, 0), None
    )
    assert daily.compute(store, "2026-10-24")["steps"] == 4500
    d = daily.compute(store, "2026-10-25")
    assert d["steps"] == 900
    assert (d["hr_min"], d["hr_max"], d["hr_avg"]) == (60, 90, 75)
    assert d["hr_resting"] == 61  # latest
    assert d["sleep_s"] == 8 * 3600 and d["sleep_deep_s"] == 3600  # attributed to the wake-up day
    assert "sleep_s" not in daily.compute(store, "2026-10-24")


async def test_sync_rebuilds_daily(sync, paired_link, day):
    from cmfstudio.sync import daily

    mgr, store = sync
    await mgr.fetch(paired_link)
    by_day = daily.read(store, days=400)
    totals = by_day[daily.local_day(T0)]
    assert totals["steps"] == 190 and totals["hr_max"] == 72


async def test_reparse_repairs_samples_from_raw(sync, paired_link, day):
    mgr, store = sync
    await mgr.fetch(paired_link)
    store.db.execute("UPDATE samples SET value = -1 WHERE metric = 'hr'")
    store.db.execute("UPDATE raw_frames SET parser_version = 0")
    res = mgr.reparse()
    assert store.samples("hr") == [(T0, 61.0), (T0 + 600, 72.0)]
    assert res["parse_errors"] == 0 and res["days_rebuilt"] > 0
    assert {r[0] for r in store.db.execute("SELECT parser_version FROM raw_frames WHERE opcode != '0057:0001'")} == {
        p.PARSER_VERSION
    }
