"""Native faces inside watchd: templates, preview, save, and install over a stock face's slot.

The spec calls a structured install (9075) a replace, but on fw 1.0.0.57 it ADDS the face (findings 2026-10-26),
under the layout's own id; installing a layout that's already on the watch is rejected, so each layout can be on the
watch once until that face is removed (there's no known delete command yet), and the watch holds 7 faces.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from ..ble.link import CUSTOM_DIAL_ID, WatchLink
from ..core.jobs import JobQueue
from ..store.db import Job, Store
from . import native_face, native_preview
from .native_face import TEMPLATES, NativeResult, NativeSpec

log = logging.getLogger(__name__)

# Stock dial names, from the protocol spec's Appendix A (refs/CMF-Watch-Pro-2-BLE-Protocol README).
DIAL_NAMES: dict[int, str] = {
    273: "Activity Mood",
    274: "Sun Circle",
    275: "SlopeTime",
    276: "Dichotomy",
    277: "Prismatic Time",
    280: "Multifunction",
    281: "Metaball",
    282: "Radar Sweep",
    283: "Radio",
    284: "Square",
    285: "Widgets",
    286: "Sundial",
    287: "Simple Dial",
    288: "Type",
    289: "Rotate",
    290: "Gradual",
    291: "Vertical",
    292: "City",
    293: "Stairs",
    294: "Sudoku",
    295: "Vortex",
    296: "Ladder",
    297: "Ray",
    298: "Eclectic",
    299: "Echo",
    300: "Mono Dial",
    301: "Orbit",
    302: "Calendar",
    303: "Space",
    304: "Elaborate 2",
    305: "Dots",
    306: "Large Number",
    307: "Sprung",
    308: "Sundial 2",
    309: "Gradient",
    310: "Glare",
    311: "Bold",
    312: "Disc",
    313: "Classical",
    314: "Fragment",
    315: "Infinite",
    316: "Trailing",
    317: "Disc 2",
    318: "Dominos",
    319: "One Line",
    320: "Orienteer",
    321: "Revolution",
    322: "Glare 2",
    323: "Dash",
    324: "Finesse",
    325: "Metric",
    326: "Chrono Master",
    327: "Digit Max",
    328: "Coherent",
    329: "Wheel",
    330: "Zenith",
    331: "Intersection",
    332: "Flux",
    333: "Circularity",
    334: "Globe of Time",
    335: "Time Phase",
    336: "Energetic",
    337: "Time Finder",
    338: "Chronos",
    339: "Suprematism",
    340: "Sport Mode",
    341: "Dual View",
    342: "Perfect Match",
    343: "Progress Day",
    344: "InfoMeter",
    345: "Time Dot",
    346: "Time Windmill",
    347: "Large Panel",
    348: "Tumbler",
    349: "Theatre",
    350: "Timeline",
    351: "Cyclopes",
    352: "Elegant Sweep",
    353: "Dual phase",
    354: "Dual",
    357: "Silhouette",
    358: "Asteroid",
    359: "Ring data",
    360: "Explorer",
    361: "TempoG",
    362: "Steady",
    363: "Vintage",
    364: "SportPulse",
    365: "Elegance",
    366: "Combo",
    367: "Complex Figure",
    368: "Function",
    369: "Solar System",
    370: "Hemisphere",
    371: "ActiveTrio",
    372: "Time Wheel",
    373: "Traditional Pointer",
    374: "Cirquary",
    375: "InfoHub",
    376: "Digits time",
}


def dial_name(dial_id: int) -> str:
    if dial_id == CUSTOM_DIAL_ID:
        return "Custom photo face"
    return DIAL_NAMES.get(dial_id, f"Dial {dial_id}")


class NativeService:
    def __init__(self, store: Store, link: WatchLink, queue: JobQueue, faces_dir: Path, photo_faces=None) -> None:
        self.store = store
        self.link = link
        self.queue = queue
        self.faces_dir = faces_dir
        self.photo_faces = photo_faces  # FaceService, for "use a Studio design as the background"
        queue.on_done.append(self._job_done)

    def values(self) -> dict[int, int]:
        ctx = self.photo_faces.context() if self.photo_faces else None
        m = ctx.metrics if ctx else {}
        return native_preview.sample_values(
            metrics={"steps": m.get("steps"), "kcal": m.get("kcal"), "hr": m.get("hr"), "battery": self.link.battery}
        )

    async def build(self, spec: NativeSpec) -> NativeResult:
        background = None
        if spec.background_face_id is not None and self.photo_faces is not None:
            rendered = await self.photo_faces.preview(self.photo_faces.spec(spec.background_face_id))
            background = rendered.watch
        return await native_face.build(spec, background, self.values())

    def save(self, spec: NativeSpec) -> int:
        return self.store.save_native_face(spec.name, spec.model_dump_json())

    def spec(self, face_id: int) -> NativeSpec:
        return NativeSpec.model_validate_json(self.store.get_native_face(face_id)["spec_json"])

    def native_ids(self) -> set[int]:
        return {f["dial_id"] for f in self.store.native_faces() if f["dial_id"] is not None}

    def dials(self) -> dict[str, Any]:
        """The watch's dial list, labelled: which is active, which are ours, which a native face may replace."""
        d = self.link.dials
        if not d:
            return {"known": False, "dials": []}
        ours = self.native_ids()
        return {
            "known": True,
            "max": d["max"],
            "dials": [
                {
                    "id": i,
                    "name": dial_name(i),
                    "active": i == d["active_id"],
                    "kind": "custom" if i == CUSTOM_DIAL_ID else "native" if i in ours else "stock",
                }
                for i in d["ids"]
            ],
        }

    def plan(self, face_id: int, replace: int | None) -> tuple[int, int]:
        """(old_id, new_id) for installing ``face_id``.

        What fw 1.0.0.57 does (findings 2026-10-26): a 9075 install with a stock face as old_id is accepted and
        **adds** the new face (old_id stays); re-using an id, or naming one of our own native faces as old_id, is
        rejected. So: old_id = the chosen (or first) stock face, new_id = the layout's own id, which must not be on
        the watch yet, and there must be a free slot."""
        d = self.link.dials
        if not d:
            raise LookupError("the watch hasn't reported its dial list yet (it does after connecting)")
        on_watch = list(d["ids"])
        stock = [i for i in on_watch if i != CUSTOM_DIAL_ID and i not in self.native_ids() and i not in TEMPLATES]
        if replace is not None:
            if replace not in stock:
                raise LookupError(f"{dial_name(replace)} can't be used (not a stock face on the watch)")
            old = replace
        elif stock:
            old = stock[0]
        else:
            raise LookupError("no stock face on the watch to install against")
        new = self.spec(face_id).template
        if new in on_watch:
            raise LookupError(
                f"a {dial_name(new)} face is already on the watch; the watch won't replace it — remove it first "
                "(Nothing X), or use another layout"
            )
        if len(on_watch) >= d.get("max", 7):
            raise LookupError(f"the watch is full ({len(on_watch)} of {d.get('max', 7)} faces); remove one first")
        return old, new

    async def install(self, face_id: int, replace: int | None = None, as_id: int | None = None) -> dict[str, Any]:
        old_id, new_id = self.plan(face_id, replace)
        if as_id is not None:  # research: choose the id explicitly (e.g. re-use the old one)
            new_id = as_id
        result = await self.build(self.spec(face_id))
        self.faces_dir.mkdir(parents=True, exist_ok=True)
        bin_path = self.faces_dir / f"native-{face_id}-{new_id}.bin"
        bin_path.write_bytes(result.blob)
        (self.faces_dir / f"native-{face_id}-{new_id}.png").write_bytes(result.preview_png)
        job = self.queue.submit(
            "install_native",
            {"bin_path": str(bin_path), "old_id": old_id, "new_id": new_id, "face_id": face_id},
            ttl_s=3600,
        )
        return {
            "job": job.public(),
            "replaces": {"id": old_id, "name": dial_name(old_id)},
            "installs_as": new_id,
            "render": result.summary(),
        }

    def _job_done(self, job: Job) -> None:
        if job.kind != "install_native" or job.state != "done":
            return
        png = Path(job.args["bin_path"]).with_suffix(".png")
        self.store.mark_native_installed(
            job.args["face_id"], job.args["new_id"], png.read_bytes() if png.exists() else b""
        )
        self.store.add_event("face", {"native": job.args["face_id"], "dial": job.args["new_id"]})


def preview_payload(result: NativeResult) -> dict[str, Any]:
    return {**result.summary(), "preview_png": base64.b64encode(result.preview_png).decode()}
