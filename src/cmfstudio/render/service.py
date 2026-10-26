"""Faces inside watchd: preview, save, install, and re-render after each health sync.

A photo-dial install replaces the watch's single custom slot and makes it the active face, so the
automatic refresh only runs while the custom face is the one on screen (dial list from the watch),
and only when the pixels actually changed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..ble.link import WatchLink
from ..core.jobs import JobQueue
from ..store.db import Job, Store
from .bindings import Context
from .pipeline import Rendered, render
from .spec import FaceSpec

log = logging.getLogger(__name__)

ACTIVE_KEY = "active_face_id"
INSIGHT_KEY = "insight"


class FaceService:
    def __init__(self, store: Store, link: WatchLink, queue: JobQueue, faces_dir: Path) -> None:
        self.store = store
        self.link = link
        self.queue = queue
        self.faces_dir = faces_dir
        queue.on_done.append(self._job_done)

    def context(self) -> Context:
        return Context.from_store(
            self.store, battery=self.link.battery, insight=self.store.get_kv(INSIGHT_KEY, "") or ""
        )

    async def preview(self, spec: FaceSpec) -> Rendered:
        return await render(spec, self.context())

    def save(self, spec: FaceSpec) -> int:
        return self.store.save_face(spec.name, spec.model_dump_json(by_alias=True))

    def spec(self, face_id: int) -> FaceSpec:
        return FaceSpec.model_validate_json(self.store.get_face(face_id)["spec_json"])

    def active_id(self) -> int | None:
        value = self.store.get_kv(ACTIVE_KEY)
        return int(value) if value else None

    async def install(self, face_id: int, *, force: bool = False, auto: bool = False) -> dict[str, Any]:
        spec = self.spec(face_id)
        rendered = await self.preview(spec)
        if not force and rendered.digest == self.store.get_face(face_id)["last_digest"]:
            return {"skipped": "unchanged", "digest": rendered.digest}
        self.faces_dir.mkdir(parents=True, exist_ok=True)
        bin_path = self.faces_dir / f"{rendered.digest}.bin"
        bin_path.write_bytes(rendered.blob)
        (self.faces_dir / f"{rendered.digest}.png").write_bytes(rendered.preview_png)
        clock = spec.clock
        job = self.queue.submit(
            "install_face",
            {
                "bin_path": str(bin_path),
                "style": clock.style,
                "x": clock.x,
                "y": clock.y,
                "color565": clock.color565,
                "face_id": face_id,
                "digest": rendered.digest,
                "auto": auto,
            },
            ttl_s=3600,
        )
        self.store.set_kv(ACTIVE_KEY, str(face_id))
        return {"job": job.public(), "render": rendered.summary()}

    def _job_done(self, job: Job) -> None:
        if job.kind != "install_face" or "face_id" not in job.args or job.state != "done":
            return
        png = self.faces_dir / f"{job.args['digest']}.png"
        self.store.mark_face_installed(
            job.args["face_id"], job.args["digest"], png.read_bytes() if png.exists() else b""
        )

    async def refresh_after_sync(self) -> dict[str, Any] | None:
        face_id = self.active_id()
        if face_id is None:
            return None
        if self.spec(face_id).refresh != "on_sync":
            return None
        if self.link.custom_face_active is False:
            # the owner picked another face on the watch; re-installing would switch it back
            self.store.add_event("face", {"refresh": "skipped", "reason": "another face is active", "face_id": face_id})
            return None
        result = await self.install(face_id, auto=True)
        self.store.add_event("face", {"refresh": "skipped" if "skipped" in result else "queued", "face_id": face_id})
        return result
