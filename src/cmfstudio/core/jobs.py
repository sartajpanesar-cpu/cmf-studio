"""Serialized job queue for watch operations, with an offline backlog.

Jobs are persisted (store.jobs) and run one at a time, only while the link is connected. A job
interrupted by a disconnect is deferred and retried on the next connect without using up an
attempt; other errors retry up to ``max_attempts``. Jobs past ``not_after`` expire instead of
running late. Open jobs are reloaded on restart.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..ble.link import NotConnectedError, WatchLink
from ..store.db import Job, Store

log = logging.getLogger(__name__)

Handler = Callable[[WatchLink, dict[str, Any]], Awaitable[Any]]


async def _notify(link: WatchLink, a: dict[str, Any]) -> dict[str, Any]:
    acked = await link.notify(a["title"], a.get("body", ""), a.get("icon", 0xFF))
    return {"acked": acked}


async def _info(link: WatchLink, _a: dict[str, Any]) -> dict[str, Any]:
    return await link.info()


async def _set_music(link: WatchLink, a: dict[str, Any]) -> None:
    await link.set_music(a["track"], a.get("artist", ""), a.get("state", 2))


async def _install_face(link: WatchLink, a: dict[str, Any]) -> dict[str, Any]:
    blob = Path(a["bin_path"]).read_bytes()
    activated = await link.install_photo_face(
        blob, a.get("style", 0), a.get("x", 56), a.get("y", 77), a.get("color565", 0xFFFF)
    )
    return {"activated": activated}


HANDLERS: dict[str, Handler] = {
    "notify": _notify,
    "info": _info,
    "set_music": _set_music,
    "install_face": _install_face,
}

TERMINAL = ("done", "failed", "expired")


class JobQueue:
    def __init__(
        self,
        store: Store,
        link: WatchLink,
        *,
        max_attempts: int = 3,
        retry_s: float = 5.0,
        handlers: dict[str, Handler] | None = None,
    ) -> None:
        self.store = store
        self._extra = handlers or {}
        self.link = link
        self.max_attempts = max_attempts
        self.retry_s = retry_s
        self._queue: collections.deque[int] = collections.deque()
        self._kick = asyncio.Event()
        self._waiters: dict[int, list[asyncio.Future[Job]]] = {}
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        for job in self.store.open_jobs():
            if job.state == "running":  # daemon died mid-job
                self.store.update_job(job.id, state="queued")
            self._queue.append(job.id)
        self._task = asyncio.create_task(self._work(), name="jobqueue")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def submit(self, kind: str, args: dict[str, Any], ttl_s: float | None = None) -> Job:
        if self._handler(kind) is None:
            raise ValueError(f"unknown job kind {kind!r}")
        job = self.store.add_job(kind, args, not_after=time.time() + ttl_s if ttl_s else None)
        self._queue.append(job.id)
        self._kick.set()
        return job

    async def wait(self, job_id: int, timeout: float | None = None) -> Job:
        job = self.store.get_job(job_id)
        if job.state in TERMINAL:
            return job
        fut: asyncio.Future[Job] = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(job_id, []).append(fut)
        return await asyncio.wait_for(fut, timeout)

    def _handler(self, kind: str) -> Handler | None:
        return self._extra.get(kind) or HANDLERS.get(kind)

    def pending(self) -> int:
        return len(self._queue)

    def _finish(self, job_id: int, **fields: Any) -> None:
        self.store.update_job(job_id, **fields)
        job = self.store.get_job(job_id)
        self.store.add_event("job", {"id": job_id, "kind": job.kind, "state": job.state, "error": job.error})
        for fut in self._waiters.pop(job_id, []):
            if not fut.done():
                fut.set_result(job)

    async def _work(self) -> None:
        while True:
            if not self._queue:
                self._kick.clear()
                await self._kick.wait()
                continue
            await self.link.connected.wait()
            job = self.store.get_job(self._queue[0])
            if job.not_after and time.time() > job.not_after:
                self._queue.popleft()
                self._finish(job.id, state="expired")
                continue
            attempts = job.attempts + 1
            self.store.update_job(job.id, state="running", attempts=attempts)
            try:
                handler = self._handler(job.kind)
                assert handler is not None
                result = await handler(self.link, job.args)
            except (NotConnectedError, ConnectionError) as exc:
                # watch went away mid-job: not the job's fault, don't count the attempt. Pause briefly
                # so the supervisor can notice the drop and clear link.connected.
                self.store.update_job(job.id, state="deferred", attempts=attempts - 1, error=repr(exc))
                await asyncio.sleep(0.1)
                continue
            except Exception as exc:
                log.warning("job %s (%s) attempt %d failed: %r", job.id, job.kind, attempts, exc)
                if attempts >= self.max_attempts:
                    self._queue.popleft()
                    self._finish(job.id, state="failed", error=repr(exc))
                else:
                    self.store.update_job(job.id, state="queued", error=repr(exc))
                    await asyncio.sleep(self.retry_s)
                continue
            self._queue.popleft()
            self._finish(job.id, state="done", result=result, error=None)
