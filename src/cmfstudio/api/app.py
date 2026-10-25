"""watchd's localhost API. Every client (CLI, UI, MCP) goes through here; none touch Bluetooth.

Binds 127.0.0.1 only (watchd), requires the bearer token from the Keychain, and rejects foreign
Host headers so a web page can't reach it via DNS rebinding.
"""

from __future__ import annotations

import hashlib
import io
import json
import secrets
import time
from pathlib import Path
from typing import Any

from cmfkit.watchface import photo
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from ..ble.link import LinkError, LinkState, WatchLink
from ..core.jobs import JobQueue
from ..store.db import Job, Store

# How long a synchronous call waits for its job before returning it still queued.
WAIT_S = 20.0
MAX_IMAGE_BYTES = 10 * 1024 * 1024


class NotifyIn(BaseModel):
    title: str = Field(max_length=200)
    body: str = Field(default="", max_length=1000)
    icon: int = Field(default=0xFF, ge=0, le=0xFF)
    ttl_s: float | None = Field(default=3600, gt=0, description="drop if not delivered within this time")
    wait: bool = True


def create_app(link: WatchLink, queue: JobQueue, store: Store, token: str, faces_dir: Path) -> FastAPI:
    app = FastAPI(title="cmf-studio watchd", version="0.1.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    def auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, given = header.partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(given.encode(), token.encode()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or wrong bearer token")

    async def run(job: Job, wait: bool) -> dict[str, Any]:
        if wait and link.connected.is_set():
            try:
                job = await queue.wait(job.id, WAIT_S)
            except TimeoutError:
                job = store.get_job(job.id)
        return job.public()

    @app.get("/status", dependencies=[Depends(auth)])
    async def get_status() -> dict[str, Any]:
        return {"link": link.status(), "pending_jobs": queue.pending(), "time": time.time()}

    @app.post("/notify", dependencies=[Depends(auth)])
    async def post_notify(body: NotifyIn) -> dict[str, Any]:
        job = queue.submit("notify", {"title": body.title, "body": body.body, "icon": body.icon}, ttl_s=body.ttl_s)
        return await run(job, body.wait)

    @app.post("/faces/install", dependencies=[Depends(auth)])
    async def post_face_install(
        request: Request,
        confirm: bool = Query(False, description="required: replaces the watch's single custom photo face"),
        name: str = Query("face"),
        style: int = Query(0, ge=0, le=4),
        x: int = Query(56, ge=0, le=465),
        y: int = Query(77, ge=0, le=465),
        color565: int = Query(0xFFFF, ge=0, le=0xFFFF),
        wait: bool = True,
    ) -> dict[str, Any]:
        if not confirm:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "installing replaces the watch's only custom photo face; resend with confirm=true",
            )
        data = await request.body()
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "send the image as the raw request body (≤10 MB)")
        try:
            blob = photo.build(Image.open(io.BytesIO(data)))
        except (UnidentifiedImageError, OSError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"not a readable image: {exc}") from exc
        faces_dir.mkdir(parents=True, exist_ok=True)
        bin_path = faces_dir / f"{hashlib.sha256(blob).hexdigest()[:16]}.bin"
        bin_path.write_bytes(blob)
        store.db.execute(
            "INSERT INTO faces(name, spec_json) VALUES (?, ?)",
            (name, json.dumps({"type": "photo", "bin": str(bin_path)})),
        )
        args = {"bin_path": str(bin_path), "style": style, "x": x, "y": y, "color565": color565}
        return await run(queue.submit("install_face", args), wait)

    @app.post("/pair", dependencies=[Depends(auth)])
    async def post_pair(confirm: bool = False) -> dict[str, Any]:
        """Tier 2: re-pair (sends AT GETSECRET; owner must accept on the watch). Never exposed via MCP."""
        if not confirm:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "pairing needs confirm=true")
        if link.state is LinkState.CONNECTED:
            raise HTTPException(status.HTTP_409_CONFLICT, "already connected with a valid key")
        try:
            await link.pair()
        except (LinkError, TimeoutError, OSError) as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"pairing failed: {exc!r}") from exc
        return {"paired": True, "link": link.status()}

    @app.get("/jobs", dependencies=[Depends(auth)])
    async def get_jobs(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
        return [j.public() for j in store.recent_jobs(limit)]

    @app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
    async def get_job(job_id: int) -> dict[str, Any]:
        try:
            return store.get_job(job_id).public()
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job") from None

    @app.get("/events", dependencies=[Depends(auth)])
    async def get_events(limit: int = Query(100, ge=1, le=1000), kind: str | None = None) -> list[dict[str, Any]]:
        return store.events(limit, kind)

    return app
