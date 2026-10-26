"""Native faces from templates: your font for every number the firmware draws, your colours, the template's layout.

A native face is a structured dial (render/native.py) built from one of the digital templates shipped with the
Pro 2 protocol spec (refs/…/wfweb/public/templates: store dial structures re-skinned with the spec author's own art,
read at runtime, never committed). Only images are swapped, so the template's layout, live data bindings and the
firmware's own clock logic stay intact:

- every digit set (hours, minutes, small numbers) is re-drawn in the chosen font, fitted to the template's glyph box;
- main-screen digits take the chosen colour; always-on (AOD) digits keep their dim template colour;
- the main-screen background becomes a colour, a top→bottom gradient, or a picture (e.g. a Studio design rendered
  without the firmware clock); the always-on background is left as the template's.
- the face-picker thumbnail is the preview, scaled down.
"""

from __future__ import annotations

import asyncio
import io
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from . import html, native_preview, raster
from .native import NativeDial, NativeFormatError
from .spec import SIZE, Color, FaceSpec, FontRef

TEMPLATE_DIR = Path(
    os.environ.get(
        "CMF_TEMPLATES",
        Path(__file__).resolve().parents[3]
        / "refs"
        / "CMF-Watch-Pro-2-BLE-Protocol"
        / "wfweb"
        / "public"
        / "templates",
    )
)
# id -> (name, description). Ids are the store dial ids the structures come from (spec Appendix A).
TEMPLATES: dict[int, tuple[str, str]] = {
    362: ("Steady", "huge hours and minutes either side, date and weekday between"),
    359: ("Ring Data", "time in the middle of a segmented ring, with steps and date"),
    288: ("Type", "big hours and minutes, the date above them"),
}
TemplateId = Literal[362, 359, 288]

CELL_W, CELL_H, FONT_PX = 400, 520, 360


class NativeSpec(BaseModel):
    name: str = Field("my-native-face", min_length=1, max_length=60)
    template: TemplateId = 362
    font: FontRef | None = Field(None, description="None keeps the template's digits")
    weight: int = Field(400, ge=100, le=900)
    digit_color: Color | None = Field(None, description="main-screen digits; None keeps the template colour")
    background: Color | None = Field("#000000", description="None keeps the template background (placeholder art)")
    background_bottom: Color | None = Field(None, description="set for a top→bottom gradient")
    background_face_id: int | None = Field(None, description="a saved Studio design as the background picture")
    show_date: bool = Field(True, description="the template's date / weekday widgets")
    show_data: bool = Field(False, description="steps, calories, heart rate, battery… (template placeholders)")


@dataclass
class NativeResult:
    blob: bytes
    preview_png: bytes
    warnings: list[str] = field(default_factory=list)
    template: int = 0

    def summary(self) -> dict:
        return {"template": self.template, "bytes": len(self.blob), "warnings": self.warnings}


def template_path(template: int) -> Path:
    path = TEMPLATE_DIR / f"{template}.bin"
    if template not in TEMPLATES or not path.exists():
        raise FileNotFoundError(f"template {template} not available (looked in {TEMPLATE_DIR})")
    return path


@lru_cache(maxsize=1)
def available_templates() -> list[dict]:
    out = []
    for t, (name, desc) in TEMPLATES.items():
        if (TEMPLATE_DIR / f"{t}.bin").exists():
            has_bg = bool(NativeDial((TEMPLATE_DIR / f"{t}.bin").read_bytes()).backgrounds())
            out.append({"id": t, "name": name, "description": desc, "has_background": has_bg})
    return out


async def build(
    spec: NativeSpec, background_image: Image.Image | None = None, values: dict[int, int] | None = None
) -> NativeResult:
    """``background_image`` (466×466) wins over the spec's background colours; ``values`` feed the preview."""
    dial = NativeDial(template_path(spec.template).read_bytes())
    warnings: list[str] = []
    glyphs = await render_digits(spec.font, spec.weight) if spec.font else None

    for table, frames in dial.digit_sets():
        colour = None if table.aod else spec.digit_color
        if glyphs is None and colour is None:
            continue
        originals = [dial.decode(f) for f in frames]
        tint = _rgb(colour) if colour else _dominant(originals)
        alphas = fit_digits(glyphs, originals) if glyphs else [o[..., 3] for o in originals]
        for frame, alpha in zip(frames, alphas, strict=True):
            rgba = np.zeros((frame.h, frame.w, 4), dtype=np.uint8)
            rgba[..., :3] = tint  # one colour everywhere: straight alpha, and it compresses far better
            rgba[..., 3] = alpha
            dial.replace(frame, rgba)

    hidden = {"date"} if not spec.show_date else set()
    hidden |= {"data"} if not spec.show_data else set()
    if hidden:
        _hide(dial, hidden)

    bg_rgba = _background(spec, background_image)
    if bg_rgba is not None:
        plain_black = background_image is None and spec.background == "#000000" and not spec.background_bottom
        if not dial.backgrounds() and not plain_black:
            warnings.append(f"{TEMPLATES[spec.template][0]} has no background image (the watch draws black behind it)")
        for bg in dial.backgrounds():
            dial.replace(bg, bg_rgba)

    preview_png, preview = await asyncio.to_thread(_preview, dial, values or native_preview.sample_values())
    for thumb in dial.thumbnails():
        small = preview.convert("RGB").resize((thumb.w, thumb.h), Image.Resampling.LANCZOS)
        dial.replace(thumb, np.asarray(small.convert("RGBA")))
    blob = dial.build()
    return NativeResult(blob, preview_png, warnings, spec.template)


# -- glyphs ---------------------------------------------------------------------------------------------------------


async def render_digits(font: FontRef, weight: int = 400) -> list[np.ndarray]:
    """'0'..'9' in ``font``, white on transparent, cropped to ink: 10 alpha arrays (uint8)."""
    spec = FaceSpec(name="glyphs", fonts=[font])
    links, faces = html._font_css(spec, None)
    cells = "".join(f'<div class="c" style="left:{i * CELL_W}px">{i}</div>' for i in range(10))
    page = (
        f"<!doctype html><html><head>{links}<style>{faces}"
        "html,body{margin:0;background:transparent}"
        f".c{{position:absolute;top:0;width:{CELL_W}px;height:{CELL_H}px;display:flex;align-items:center;"
        f"justify-content:center;font:{weight} {FONT_PX}px '{font.family}',sans-serif;color:#fff;"
        "line-height:1;font-variant-numeric:lining-nums}"
        f"</style></head><body>{cells}</body></html>"
    )
    png = await raster.render_png(page, size=(CELL_W * 10, CELL_H), transparent=True)
    alpha = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))[..., 3]
    out = []
    for i in range(10):
        cell = alpha[:, i * CELL_W : (i + 1) * CELL_W]
        box = _ink_box(cell, 8)
        if box is None:
            raise NativeFormatError(f"font {font.family!r} drew nothing for '{i}'")
        x0, y0, x1, y1 = box
        out.append(cell[y0:y1, x0:x1])
    return out


def fit_digits(glyphs: list[np.ndarray], originals: list[np.ndarray]) -> list[np.ndarray]:
    """Scale the font's digits (one shared scale) into the template's glyph boxes: as tall as the template's
    digits, bottoms aligned, each centred where the template's digit sits, never wider than its own image
    (some templates give every digit its own width)."""
    boxes = [_ink_box(o[..., 3], 40) or (0, 0, o.shape[1], o.shape[0]) for o in originals]
    top = min(b[1] for b in boxes)
    bottom = max(b[3] for b in boxes)
    tallest = max(g.shape[0] for g in glyphs)
    scale = min(
        [(bottom - top) / tallest]
        + [max(b[2] - b[0], o.shape[1] * 0.9) / g.shape[1] for g, o, b in zip(glyphs, originals, boxes, strict=True)]
    )
    out = []
    for g, o, b in zip(glyphs, originals, boxes, strict=True):
        h, w = o.shape[:2]
        gw, gh = max(1, round(g.shape[1] * scale)), max(1, round(g.shape[0] * scale))
        small = np.asarray(Image.fromarray(g).resize((gw, gh), Image.Resampling.LANCZOS))
        canvas = np.zeros((h, w), dtype=np.uint8)
        _paste(canvas, small, int(round((b[0] + b[2]) / 2 - gw / 2)), bottom - gh)
        out.append(canvas)
    return out


# -- which template widgets show ----------------------------------------------------------------------------------

TIME_IDS = set(range(0x01, 0x14)) | {0x70, 0x71, 0x72}  # hours, minutes, seconds, AM/PM, hand angles (spec §16)
DATE_IDS = {0x15, 0x16, 0x17, 0x18}


def widget_category(source: int, condition_ids: set[int]) -> str:
    """'time' (always kept), 'date', 'data', or 'static' (unbound decoration: kept)."""
    ids = ({source} | condition_ids) - {0}
    if not ids:
        return "static"
    if ids & TIME_IDS:
        return "time"
    if ids <= DATE_IDS:
        return "date"
    return "data"


def widgets(dial: NativeDial) -> list[tuple[str, set[int]]]:
    """(category, image offsets) for every drawable node on both screens."""
    b, out = dial.b, []
    tails = {t.at: t for t in dial.tails}

    def node(body: int, n: int) -> None:
        src, conds, imgs = 0, set(), set()
        off = body
        while off < body + n:
            tag, ln = b[off], int.from_bytes(b[off + 1 : off + 3], "little")
            cb = off + 3
            if tag == 0x01:
                src = b[cb + 13]
                tail = tails.get(cb + 0x12)
                if tail:
                    imgs = {a.off for a in dial.chain(tail)}
            elif tag == 0x02:
                conds = {b[cb + 1 + 5 * k] for k in range(b[cb])}
            off = cb + ln
        if imgs:
            out.append((widget_category(src, conds), imgs))

    def walk(off: int, end: int) -> None:
        while off < end:
            tag, ln = b[off], int.from_bytes(b[off + 1 : off + 3], "little")
            cb = off + 3
            if tag in (0x20, 0x21, 0x22, 0x68):
                walk(cb, cb + ln)
            elif tag in (0x30, 0x60, 0x70, 0x80, 0x81, 0x85):
                node(cb, ln)
            off = cb + ln

    walk(36, dial.first_asset)
    return out


def _hide(dial: NativeDial, categories: set[str]) -> None:
    """Blank the images of widgets in ``categories`` (unless a shown widget uses the same image)."""
    hide, keep = set(), set()
    for category, imgs in widgets(dial):
        (hide if category in categories else keep).update(imgs)
    for off in hide - keep:
        a = dial.assets[off]
        rgba = np.zeros((a.h, a.w, 4), dtype=np.uint8)
        if a.cf == 4:
            rgba[..., 3] = 255  # opaque format: black
        dial.replace(a, rgba)


# -- background -----------------------------------------------------------------------------------------------------


def gradient(w: int, h: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> np.ndarray:
    t = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    rows = (np.array(top, np.float32) * (1 - t) + np.array(bottom, np.float32) * t).round().astype(np.uint8)
    rgba = np.empty((h, w, 4), dtype=np.uint8)
    rgba[..., :3] = rows[:, None, :]
    rgba[..., 3] = 255
    return rgba


def _background(spec: NativeSpec, image: Image.Image | None) -> np.ndarray | None:
    if image is not None:
        img = raster.round_mask(image.convert("RGB").resize((SIZE, SIZE)))
        return np.asarray(raster.dither_rgb565(img).convert("RGBA"))
    if spec.background:
        top = _rgb(spec.background)
        bottom = _rgb(spec.background_bottom) if spec.background_bottom else top
        img = Image.fromarray(gradient(SIZE, SIZE, top, bottom)[..., :3])
        return np.asarray(raster.dither_rgb565(img).convert("RGBA"))  # RGB565 bands a smooth gradient otherwise
    return None


# -- preview ----------------------------------------------------------------------------------------------------------


def _preview(dial: NativeDial, values: dict[int, int]) -> tuple[bytes, Image.Image]:
    img = raster.round_mask(native_preview.render(dial, values).convert("RGB"))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue(), img


# -- helpers ----------------------------------------------------------------------------------------------------------


def _ink_box(alpha: np.ndarray, threshold: int) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(alpha >= threshold)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _paste(canvas: np.ndarray, img: np.ndarray, left: int, top: int) -> None:
    h, w = canvas.shape[:2]
    gh, gw = img.shape[:2]
    sx0, sy0 = max(0, -left), max(0, -top)
    dx0, dy0 = max(0, left), max(0, top)
    dx1, dy1 = min(w, left + gw), min(h, top + gh)
    if dx1 > dx0 and dy1 > dy0:
        canvas[dy0:dy1, dx0:dx1] = np.maximum(
            canvas[dy0:dy1, dx0:dx1], img[sy0 : sy0 + dy1 - dy0, sx0 : sx0 + dx1 - dx0]
        )


def _rgb(colour: str) -> tuple[int, int, int]:
    return int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16)


def _dominant(frames: list[np.ndarray]) -> tuple[int, int, int]:
    px = np.concatenate(
        [f[f[..., 3] >= 200][:, :3] for f in frames if (f[..., 3] >= 200).any()] or [np.full((1, 3), 255)]
    )
    return tuple(int(v) for v in np.median(px, axis=0))  # type: ignore[return-value]
