import asyncio
import time

import pytest

from cmfstudio.ble.link import LinkState
from cmfstudio.core import jobs as jobsmod
from cmfstudio.core.jobs import JobQueue
from cmfstudio.store.db import Store

from .conftest import wait_state


@pytest.fixture
async def queue(paired_link):
    q = JobQueue(Store(":memory:"), paired_link, retry_s=0.01)
    q.start()
    yield q
    await q.stop()


async def test_notify_job_runs(queue, watch):
    job = queue.submit("notify", {"title": "CMF Studio", "body": "queued"})
    done = await queue.wait(job.id, 2)
    assert done.state == "done" and done.result == {"acked": True}
    assert watch.state.notifications[-1] == ("CMF Studio", "queued")
    assert queue.store.events(kind="job")[0]["data"]["state"] == "done"


async def test_offline_backlog_runs_on_reconnect(queue, paired_link, watch):
    watch.reachable = False
    watch.drop()
    await wait_state(paired_link, LinkState.DISCONNECTED)
    job = queue.submit("notify", {"title": "while away"})
    await asyncio.sleep(0.1)
    assert queue.store.get_job(job.id).state in ("queued", "deferred")
    watch.reachable = True
    done = await queue.wait(job.id, 2)
    assert done.state == "done" and done.attempts == 1
    assert watch.state.notifications[-1] == ("while away", "")


async def test_jobs_run_in_submission_order(queue, watch):
    ids = [queue.submit("notify", {"title": f"n{i}"}).id for i in range(3)]
    for i in ids:
        await queue.wait(i, 2)
    assert [t for t, _ in watch.state.notifications[-3:]] == ["n0", "n1", "n2"]


async def test_expired_job_is_not_run(queue, watch):
    job = queue.store.add_job("notify", {"title": "stale"}, not_after=time.time() - 1)
    queue._queue.append(job.id)
    queue._kick.set()
    assert (await queue.wait(job.id, 2)).state == "expired"
    assert ("stale", "") not in watch.state.notifications


async def test_open_jobs_survive_restart(paired_link, watch):
    store = Store(":memory:")
    leftover = store.add_job("notify", {"title": "from last run"})
    store.update_job(leftover.id, state="running")  # daemon died mid-job
    q = JobQueue(store, paired_link)
    q.start()
    try:
        assert (await q.wait(leftover.id, 2)).state == "done"
    finally:
        await q.stop()


async def test_failing_job_gives_up_after_max_attempts(queue, monkeypatch):
    async def boom(_link, _args):
        raise RuntimeError("watch said no")

    monkeypatch.setitem(jobsmod.HANDLERS, "notify", boom)
    done = await queue.wait(queue.submit("notify", {"title": "x"}).id, 2)
    assert done.state == "failed" and done.attempts == 3 and "watch said no" in done.error


def test_unknown_kind_rejected(paired_link):
    q = JobQueue(Store(":memory:"), paired_link)
    with pytest.raises(ValueError):
        q.submit("factory_reset", {})
