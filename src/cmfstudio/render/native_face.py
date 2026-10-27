"""Native faces from templates: your font for every number the firmware draws, your colours, the template's layout.

A native face is a structured dial (render/native.py) built from one of the digital templates shipped with the
Pro 2 protocol spec (refs/…/wfweb/public/templates: store dial structures re-skinned with the spec author's own art,
read at runtime, never committed). Only images are swapped, so the template's layout, live data bindings and the
firmware's own clock logic stay intact:

- every digit set (hours, minutes, small numbers) is re-drawn in the chosen font, fitted to the template's glyph box;
- main-screen digits take the chosen colour; always-on (AOD) digits keep their dim template colour;
- the main-screen background becomes a colour, a top→bottom gradient, or a picture (e.g. a Studio design rendered
  without the firmware clock); layouts without a background image get one added (``native.add_background``); the
  always-on background is left as the template's.
- the face-picker thumbnail is the preview, scaled down.
"""

from __future__ import annotations

import asyncio
import io
import os
import struct
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

from . import html, native_preview, raster
from .native import NativeDial, NativeFormatError, add_background
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
    center_time: bool = Field(True, description="shift the hours:minutes row to the middle of the screen")
    tidy: bool = Field(True, description="centre the date, line the data widgets up with labels")


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
    data = template_path(spec.template).read_bytes()
    bg_rgba = _background(spec, background_image)
    plain_black = background_image is None and spec.background == "#000000" and not spec.background_bottom
    if bg_rgba is not None and not plain_black and not NativeDial(data).backgrounds():
        data = add_background(data)  # Steady / Type draw black behind the time: give them a background image
    dial = NativeDial(data)
    warnings: list[str] = []
    labels = await _apply_clock(dial, spec)

    if bg_rgba is not None:
        if labels:  # labels never change, so they live in the background picture
            bg_rgba = await _paint_labels(bg_rgba, labels, spec)
        for bg in dial.backgrounds():
            dial.replace(bg, bg_rgba)

    preview_png, preview = await asyncio.to_thread(_preview, dial, values or native_preview.sample_values())
    for thumb in dial.thumbnails():
        small = preview.convert("RGB").resize((thumb.w, thumb.h), Image.Resampling.LANCZOS)
        dial.replace(thumb, np.asarray(small.convert("RGBA")))
    blob = dial.build()
    return NativeResult(blob, preview_png, warnings, spec.template)


async def clock_overlay(spec: NativeSpec, values: dict[int, int] | None = None) -> bytes:
    """Just the firmware-drawn widgets (time, and date / data if shown) on a transparent canvas — for laying the
    native clock over a live design preview."""
    dial = NativeDial(template_path(spec.template).read_bytes())
    labels = await _apply_clock(dial, spec)
    img = native_preview.render(dial, values or native_preview.sample_values(), transparent=True)
    if labels:
        img = Image.fromarray(await _paint_labels(np.asarray(img).copy(), labels, spec), "RGBA")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def spec_from_clock(clock, name: str, background_face_id: int | None = None) -> NativeSpec:  # type: ignore[no-untyped-def]
    return NativeSpec(
        name=name[:60],
        template=clock.template,
        font=clock.font,
        weight=clock.weight,
        digit_color=clock.digit_color,
        background=None if background_face_id is not None else "#000000",
        background_face_id=background_face_id,
        show_date=clock.show_date,
        show_data=clock.show_data,
        center_time=clock.center_time,
        tidy=getattr(clock, "tidy", True),
    )


_GLYPHS: dict[str, list[np.ndarray]] = {}


async def _glyphs(font: FontRef, weight: int) -> list[np.ndarray]:
    key = f"{font.model_dump_json()}|{weight}"
    if key not in _GLYPHS:
        if len(_GLYPHS) > 24:
            _GLYPHS.clear()
        _GLYPHS[key] = await render_digits(font, weight)
    return _GLYPHS[key]


async def _apply_clock(dial: NativeDial, spec: NativeSpec) -> list[tuple[str, int, int]]:
    """Font, colour, accent, centring, hidden and tidied widgets — everything but the background. Returns the labels
    to paint under the data widgets."""
    glyphs = await _glyphs(spec.font, spec.weight) if spec.font else None

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
        if colour:  # our colour must win over the watch's accent tint (Type's time showed red, 2026-10-26)
            for body in dial.struct_bodies({f.off for f in frames}):
                dial.clear_accent(body)

    if spec.digit_color:
        _accent_to_colour(dial, _rgb(spec.digit_color))

    if spec.center_time:
        _center_time(dial)

    hidden = {"date"} if not spec.show_date else set()
    hidden |= {"data"} if not spec.show_data else set()
    if hidden:
        _hide(dial, hidden)
    if spec.tidy and (spec.show_date or spec.show_data):
        return _tidy(dial, spec.show_date, spec.show_data)
    return []


LABEL_PX = 13  # cap height of the labels under the data widgets


async def _paint_labels(rgba: np.ndarray, labels: list[tuple[str, int, int]], spec: NativeSpec) -> np.ndarray:
    """Draw each label (in the clock's font, digit colour at 75%) centred at (x, y) onto ``rgba``."""
    glyphs = await _texts(spec.font, spec.weight, sorted({t for t, _x, _y in labels}))
    tint = np.array(_rgb(spec.digit_color) if spec.digit_color else (200, 200, 200), np.float32)
    out = rgba.astype(np.float32)
    for text, cx, y in labels:
        a = glyphs[text]
        scale = LABEL_PX / max(1, a.shape[0])
        img = Image.fromarray(a).resize((max(1, round(a.shape[1] * scale)), LABEL_PX), Image.Resampling.LANCZOS)
        alpha = np.asarray(img).astype(np.float32) / 255 * 0.75
        h, w = alpha.shape
        x0 = cx - w // 2
        if x0 < 0 or y < 0 or x0 + w > SIZE or y + h > SIZE:
            continue
        region = out[y : y + h, x0 : x0 + w]
        region[..., :3] = tint * alpha[..., None] + region[..., :3] * (1 - alpha[..., None])
        region[..., 3] = np.maximum(region[..., 3], alpha * 255)
    return out.round().astype(np.uint8)


async def _texts(font: FontRef | None, weight: int, texts: list[str]) -> dict[str, np.ndarray]:
    """Each text drawn in ``font`` (or the system UI font), white alpha cropped to ink."""
    family = f"'{font.family}'," if font else ""
    spec = FaceSpec(name="labels", fonts=[font] if font else [])
    links, faces = html._font_css(spec, None)
    cells = "".join(f'<div class="c" style="top:{i * 120}px">{t}</div>' for i, t in enumerate(texts))
    page = (
        f"<!doctype html><html><head>{links}<style>{faces}html,body{{margin:0;background:transparent}}"
        f".c{{position:absolute;left:0;height:120px;width:900px;display:flex;align-items:center;padding-left:20px;"
        f"font:{weight} 80px {family}sans-serif;color:#fff;letter-spacing:4px}}</style></head>"
        f"<body>{cells}</body></html>"
    )
    png = await raster.render_png(page, size=(900, 120 * len(texts)), transparent=True)
    alpha = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))[..., 3]
    out = {}
    for i, t in enumerate(texts):
        cell = alpha[i * 120 : (i + 1) * 120]
        box = _ink_box(cell, 8) or (0, 0, 1, 1)
        out[t] = cell[box[1] : box[3], box[0] : box[2]]
    return out


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


def _accent_to_colour(dial: NativeDial, tint: tuple[int, int, int]) -> None:
    """Every main-screen widget the firmware would tint with its accent colour (meta[7] == 4, spec §11.14) gets the
    chosen digit colour baked in instead (alpha kept), and the flag cleared — so colons, weekday and month images
    match the digits instead of showing the watch's accent (red on the owner's watch)."""
    for t in dial.tails:
        body = t.at - 0x12
        if t.aod or dial.b[body - 3] != 0x01 or dial.b[body + 11] != 4:
            continue
        for a in dial.chain(t):
            if a.cf in (5, 13, 24):
                rgba = dial.decode(a).copy()
                rgba[..., :3] = tint
                dial.replace(a, rgba)
        dial.clear_accent(body)


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


HOUR_IDS = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09}
MINUTE_IDS = {0x0B, 0x0C, 0x0D}


def _center_time(dial: NativeDial, width: int = SIZE) -> None:
    """Centre each screen's hours:minutes row horizontally (moves only the nodes on that row)."""
    b = dial.b
    tails = {t.at: t for t in dial.tails}

    def nodes(off: int, end: int, out: list) -> None:
        while off < end:
            tag, ln = b[off], int.from_bytes(b[off + 1 : off + 3], "little")
            cb = off + 3
            if tag in (0x30, 0x60):
                st = digits = None
                conds = False
                o = cb
                while o < cb + ln:
                    t2, l2 = b[o], int.from_bytes(b[o + 1 : o + 3], "little")
                    if t2 == 0x01:
                        st = o + 3
                    elif t2 == 0x40:
                        digits = b[o + 3]
                    elif t2 == 0x02:
                        conds = True
                    o += 3 + l2
                if st is not None and (tail := tails.get(st + 0x12)):
                    frames = dial.chain(tail)
                    out.append(
                        {
                            "tag": tag,
                            "st": st,
                            "src": b[st + 13],
                            "conds": conds,
                            "digits": digits,
                            "w": max(f.w for f in frames),
                            "h": frames[0].h,
                        }
                    )
            off = cb + ln

    def screens(off: int, end: int) -> None:
        while off < end:
            tag, ln = b[off], int.from_bytes(b[off + 1 : off + 3], "little")
            cb = off + 3
            if tag == 0x20:
                screens(cb, cb + ln)
            elif tag in (0x21, 0x22):
                found: list = []
                nodes(cb, cb + ln, found)
                _center_row(dial, found, width)
            off = cb + ln

    screens(36, dial.first_asset)


def _center_row(dial: NativeDial, found: list[dict], width: int) -> None:
    hours = [n for n in found if n["tag"] == 0x60 and n["src"] in HOUR_IDS]
    minutes = [n for n in found if n["tag"] == 0x60 and n["src"] in MINUTE_IDS]
    if not hours or not minutes:
        return
    _x, row_y = dial.position(hours[0]["st"])
    row = []
    for n in found:
        x, y = dial.position(n["st"])
        time_node = n["tag"] == 0x60 and n["src"] in TIME_IDS
        separator = n["tag"] == 0x30 and n["src"] == 0 and not n["conds"]  # e.g. the colon
        if abs(y - row_y) <= 12 and (time_node or separator):
            count = 2 if n["tag"] == 0x60 else 1  # hours / minutes always show two digits
            row.append((n["st"], x, y, n["w"] * count))
    left = min(x for _st, x, _y, _w in row)
    right = max(x + w for _st, x, _y, w in row)
    shift = round((width - (right - left)) / 2 - left)
    if shift:
        for st, x, y, _w in row:
            dial.move(st, x + shift, y)


# -- tidying the layout's date and data widgets --------------------------------------------------------------------

LABELS = {0x60: "KCAL", 0x1C: "KCAL", 0x1E: "KCAL", 0x48: "KCAL", 0x61: "KM", 0x22: "KM", 0x1A: "BPM", 0x19: "STEPS"}
DATA_ROW_Y = 318  # top of the tidied data row (below the time and date on every layout)


def _scan(dial: NativeDial) -> list[dict]:
    """Per screen: its top-level 0x30/0x60 nodes and its 0x68 groups (frame offset, children)."""
    b = dial.b
    tails = {t.at: t for t in dial.tails}

    def tlv(off: int, end: int):
        while off < end:
            tag, ln = b[off], int.from_bytes(b[off + 1 : off + 3], "little")
            yield tag, off + 3, ln
            off += 3 + ln

    def node(tag: int, body: int, n: int) -> dict | None:
        st, conds = None, False
        for t2, cb, _l2 in tlv(body, body + n):
            if t2 == 0x01:
                st = cb
            elif t2 == 0x02:
                conds = True
        tail = tails.get(st + 0x12) if st is not None else None
        if tail is None:
            return None
        frames = dial.chain(tail)
        return {
            "tag": tag,
            "st": st,
            "src": b[st + 13],
            "conds": conds,
            "frames": frames,
            "w": max(f.w for f in frames),
            "h": frames[0].h,
        }

    screens = []
    for tag, body, n in tlv(36, dial.first_asset):
        if tag != 0x20:
            continue
        for stag, sbody, sn in tlv(body, body + n):
            if stag not in (0x21, 0x22):
                continue
            screen = {"aod": stag == 0x22, "nodes": [], "groups": []}
            for ctag, cbody, cn in tlv(sbody, sbody + sn):
                if ctag in (0x30, 0x60) and (nd := node(ctag, cbody, cn)):
                    screen["nodes"].append(nd)
                elif ctag == 0x68:
                    group = {"frame": None, "children": []}
                    for gtag, gbody, gn in tlv(cbody, cbody + cn):
                        if gtag == 0x48:
                            group["frame"] = gbody
                        elif gtag in (0x30, 0x60) and (nd := node(gtag, gbody, gn)):
                            group["children"].append(nd)
                    if group["frame"] is not None:
                        screen["groups"].append(group)
            screens.append(screen)
    return screens


def _tidy(dial: NativeDial, show_date: bool, show_data: bool) -> list[tuple[str, int, int]]:
    """Centre the date row and weekday; lay the data widgets out in one even row with their placeholder icons
    blanked. Returns (label, centre x, y) for labels to paint under the data widgets."""
    labels: list[tuple[str, int, int]] = []
    for screen in _scan(dial):
        nodes = screen["nodes"]
        if show_date:
            dates = [n for n in nodes if n["tag"] == 0x60 and n["src"] in DATE_IDS]
            if dates:
                _, row_y = dial.position(dates[0]["st"])
                row = [
                    n
                    for n in nodes
                    if abs(dial.position(n["st"])[1] - row_y) <= 12
                    and ((n["tag"] == 0x60 and n["src"] in DATE_IDS) or (n["src"] == 0 and not n["conds"]))
                ]
                _centre_nodes(dial, row)
            for n in nodes:  # weekday / month pictures: one image each
                if n["tag"] == 0x30 and n["src"] in DATE_IDS:
                    _centre_nodes(dial, [n])
        if not show_data or screen["aod"]:
            continue
        data = [g for g in screen["groups"] if any(c["src"] not in (0, *TIME_IDS, *DATE_IDS) for c in g["children"])]
        if not data:
            continue
        b = dial.b
        widths = [struct.unpack_from("<H", b, g["frame"] + 4)[0] for g in data]
        gap = 28
        x = (SIZE - (sum(widths) + gap * (len(data) - 1))) // 2
        for g, w in zip(data, widths, strict=True):
            fh = struct.unpack_from("<H", b, g["frame"] + 6)[0]
            dial.move(g["frame"], x, DATA_ROW_Y)
            source = next((c["src"] for c in g["children"] if c["tag"] == 0x60), 0)
            labels.append((LABELS.get(source, ""), x + w // 2, DATA_ROW_Y + fh + 4))
            for c in g["children"]:  # the "–" placeholder icons (the store face's art was removed)
                if c["tag"] == 0x30 and len(c["frames"]) == 1:
                    a = c["frames"][0]
                    dial.replace(a, np.zeros((a.h, a.w, 4), dtype=np.uint8))
            x += w + gap
    return [lbl for lbl in labels if lbl[0]]


def _centre_nodes(dial: NativeDial, row: list[dict]) -> None:
    if not row:
        return
    spans = []
    for n in row:
        x, y = dial.position(n["st"])
        count = 2 if n["tag"] == 0x60 else 1
        spans.append((n["st"], x, y, n["w"] * count))
    left = min(x for _st, x, _y, _w in spans)
    right = max(x + w for _st, x, _y, w in spans)
    shift = round((SIZE - (right - left)) / 2 - left)
    for st, x, y, _w in spans:
        if shift:
            dial.move(st, x + shift, y)


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
