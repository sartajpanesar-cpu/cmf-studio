"""FaceSpec → watch-ready photo dial: render, mask, check, dither, encode."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cmfkit.watchface import photo
from PIL import Image

from . import html, raster
from .bindings import Context, describe
from .spec import FaceSpec


@dataclass
class Rendered:
    spec: FaceSpec
    source_png: bytes  # straight from the browser
    watch: Image.Image  # masked + RGB565-dithered: what the watch shows (minus the live clock)
    preview_png: bytes
    blob: bytes  # photo-dial container for the install job
    checks: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        """Changes only when the pixels change: skip re-installing an identical face."""
        return hashlib.sha256(self.watch.tobytes()).hexdigest()[:16]

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.spec.name,
            "digest": self.digest,
            "bytes": len(self.blob),
            "checks": self.checks,
            "clock": self.spec.clock.model_dump(),
            "context": self.context,
        }


async def render(spec: FaceSpec, ctx: Context | None = None, base_dir: Path | str = ".") -> Rendered:
    ctx = ctx or Context()
    source = await raster.render_png(html.build(spec, ctx, base_dir))
    masked = raster.round_mask(Image.open(io.BytesIO(source)))
    watch = raster.dither_rgb565(masked)
    blob = photo.build(watch)
    photo.parse(blob)  # round-trip check before anything goes near the watch
    return Rendered(
        spec=spec,
        source_png=source,
        watch=watch,
        preview_png=raster.preview(watch, spec.clock),
        blob=blob,
        checks=raster.amoled_check(watch),
        context=describe(ctx),
    )
