import sqlite3

import pytest

from cmfstudio.store import db as dbmod
from cmfstudio.store.db import Store


def test_migrates_fresh_db_to_latest(tmp_path):
    store = Store(tmp_path / "x.db")
    assert store.version == len(dbmod.MIGRATIONS)
    tables = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"raw_frames", "samples", "sleep_sessions", "workouts", "daily", "faces", "jobs", "events"} <= tables


def test_migrate_is_idempotent_and_persistent(tmp_path):
    Store(tmp_path / "x.db").add_event("boot", {"n": 1})
    again = Store(tmp_path / "x.db")
    assert again.version == len(dbmod.MIGRATIONS)
    assert again.events()[0]["data"] == {"n": 1}


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "x.db"
    Store(path)
    monkeypatch.setattr(dbmod, "MIGRATIONS", [*dbmod.MIGRATIONS, "CREATE TABLE ok(a); CREATE TABLE broken("])
    with pytest.raises(sqlite3.Error):
        Store(path)
    plain = sqlite3.connect(path)
    assert plain.execute("PRAGMA user_version").fetchone()[0] == len(dbmod.MIGRATIONS) - 1
    assert plain.execute("SELECT count(*) FROM sqlite_master WHERE name='ok'").fetchone()[0] == 0


def test_samples_upsert_key_is_metric_ts():
    store = Store(":memory:")
    store.db.execute("INSERT INTO samples VALUES ('steps', 100, 5, NULL)")
    with pytest.raises(sqlite3.IntegrityError):
        store.db.execute("INSERT INTO samples VALUES ('steps', 100, 6, NULL)")


def test_job_lifecycle():
    store = Store(":memory:")
    job = store.add_job("notify", {"title": "hi"}, not_after=123.0)
    assert job.state == "queued" and job.args == {"title": "hi"} and job.not_after == 123.0
    store.update_job(job.id, state="done", result={"acked": True})
    done = store.get_job(job.id)
    assert done.state == "done" and done.result == {"acked": True} and done.finished_at
    assert store.open_jobs() == []
