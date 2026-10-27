"""Draw a native face the way the firmware does, for previews (spec §11.10–§11.12, §16).

Covers what the digital templates use: static images (0x30, one image or pick-by-value from N), live numbers (0x60:
digit count / zero-pad from the 0x40 byte, glyphs placed left to right), auto-layout rows (0x68 + 0x48 frame) and
visibility conditions (0x02). Hands and rings (0x70/0x80/0x81) aren't drawn — the templates we offer have none.
Values come from ``values`` (data-source id → int); see ``sample_values``.
"""

from __future__ import annotations

import datetime as dt
import struct
from typing import Any

import numpy as np
from PIL import Image

from .native import CONTAINERS, NativeDial
from .spec import SIZE

SCREEN = {False: 0x21, True: 0x22}


def sample_values(now: dt.datetime | None = None, metrics: dict[str, Any] | None = None) -> dict[int, int]:
    """Data-source ids (§16) → values: the time/date now, plus today's health numbers when known."""
    now = now or dt.datetime.now()
    m = metrics or {}
    h12 = now.hour % 12 or 12
    v = {
        0x01: now.hour, 0x04: now.hour, 0x07: now.hour, 0x02: h12 // 10, 0x03: h12 % 10,
        0x05: now.hour // 10, 0x06: now.hour % 10, 0x08: now.hour // 10, 0x09: now.hour % 10,
        0x0B: now.minute, 0x0C: now.minute // 10, 0x0D: now.minute % 10,
        0x0F: now.second, 0x10: now.second // 10, 0x11: now.second % 10,
        0x13: int(now.hour >= 12), 0x15: now.month, 0x16: now.month, 0x17: now.day, 0x18: now.weekday(),
        0x73: 0,
    }  # fmt: skip
    steps, kcal, hr, battery = m.get("steps"), m.get("kcal"), m.get("hr"), m.get("battery")
    # 0x60 = calories per the spec author's editor (wfweb mock.ts) — the spec marks these ids contested
    for ids, value in (
        ((0x19, 0x26, 0x49), steps),
        ((0x1C, 0x1E, 0x48, 0x60), kcal),
        ((0x1A,), hr),
        ((0x24, 0x30), battery),
    ):
        for i in ids:
            if value is not None:
                v[i] = int(value)
    return v


def render(dial: NativeDial, values: dict[int, int], aod: bool = False, transparent: bool = False) -> Image.Image:
    """``transparent``: no backdrop and no full-screen background images — just the widgets."""
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0 if transparent else 255))
    backgrounds = {a.off for a in dial.backgrounds(aod)} if transparent else set()
    tails = {t.at: t for t in dial.tails}
    b = dial.b

    def struct_at(body: int) -> dict[str, Any]:
        x, y = dial.position(body)  # includes pending moves
        w, h = struct.unpack_from("<HH", b, body + 4)
        return {"x": x, "y": y, "w": w, "h": h, "src": b[body + 13], "tail": tails.get(body + 0x12)}

    def children(off: int, end: int):
        while off < end:
            tag, n = b[off], struct.unpack_from("<H", b, off + 1)[0]
            yield tag, off + 3, n
            off += 3 + n

    def visible(body: int, n: int) -> bool:
        for tag, cbody, cn in children(body, body + n):
            if tag == 0x02:
                return _condition(b[cbody : cbody + cn], values)
        return True

    def node_images(body: int, n: int) -> tuple[dict[str, Any] | None, list[Image.Image], int | None]:
        st, digits = None, None
        for tag, cbody, _cn in children(body, body + n):
            if tag == 0x01:
                st = struct_at(cbody)
            elif tag == 0x40:
                digits = b[cbody]
        if not st or not st["tail"]:
            return st, [], digits
        chain = dial.chain(st["tail"])
        if any(a.off in backgrounds for a in chain):
            return st, [], digits
        return st, [_image(dial, a) for a in chain], digits

    def draw_node(tag: int, body: int, n: int, origin: tuple[int, int] | None = None) -> tuple[int, int]:
        """Draw one 0x30/0x60 node; returns its drawn size. ``origin`` overrides x/y (auto-layout)."""
        if not visible(body, n):
            return 0, 0
        st, imgs, digits = node_images(body, n)
        if not st or not imgs:
            return 0, 0
        x, y = origin or (st["x"], st["y"])
        if tag == 0x60:
            return _draw_number(canvas, imgs, values.get(st["src"], 0), digits, x, y)
        img = imgs[min(values.get(st["src"], 0), len(imgs) - 1)] if len(imgs) > 1 else imgs[0]
        _composite(canvas, img, x, y)
        return img.width, img.height

    def draw_group(body: int, n: int) -> None:
        frame = None
        items = []
        for tag, cbody, cn in children(body, body + n):
            if tag == 0x48:
                fx, fy, fw, fh, gap = struct.unpack_from("<hhHHH", b, cbody)
                frame = (fx, fy, fw, fh, gap)
            elif tag in (0x30, 0x60) and visible(cbody, cn):
                items.append((tag, cbody, cn))
        if not frame:
            return
        fx, fy, fw, fh, gap = frame
        sizes = [_measure(tag, cbody, cn) for tag, cbody, cn in items]
        total = sum(w for w, _ in sizes) + gap * max(0, len(sizes) - 1)
        x = fx + max(0, (fw - total) // 2)
        for (tag, cbody, cn), (w, h) in zip(items, sizes, strict=True):
            draw_node(tag, cbody, cn, (x, fy + max(0, (fh - h) // 2)))
            x += w + gap

    def _measure(tag: int, body: int, n: int) -> tuple[int, int]:
        st, imgs, digits = node_images(body, n)
        if not st or not imgs:
            return 0, 0
        if tag == 0x60:
            text = _digits_text(values.get(st["src"], 0), digits)
            return sum(imgs[int(c)].width for c in text), imgs[0].height
        img = imgs[min(values.get(st["src"], 0), len(imgs) - 1)]
        return img.width, img.height

    def walk(off: int, end: int, in_screen: bool) -> None:
        for tag, body, n in children(off, end):
            if tag == 0x20:
                walk(body, body + n, False)
            elif tag in (0x21, 0x22):
                if tag == SCREEN[aod]:
                    walk(body, body + n, True)
            elif not in_screen or tag == 0x28:
                continue
            elif tag == 0x68:
                if visible(body, n):
                    draw_group(body, n)
            elif tag in (0x30, 0x60):
                draw_node(tag, body, n)
            elif tag in CONTAINERS:
                continue  # hands / rings / complication slots: not drawn by the preview

    walk(36, dial.first_asset, False)
    return canvas


def _condition(raw: bytes, values: dict[int, int]) -> bool:
    """§11.12: equality entries are OR-ed; hide / >= / <= entries must all hold. Unknown ops are ignored."""
    count = raw[0]
    eq: list[bool] = []
    rest = True
    for k in range(count):
        cid, op = raw[1 + 5 * k], raw[2 + 5 * k] & 0x7F
        val = int.from_bytes(raw[3 + 5 * k : 6 + 5 * k], "little", signed=True)
        v = values.get(cid, 0)
        if op in (0x01, 0x03):
            eq.append(v == val)
        elif op == 0x02:
            rest &= v != val
        elif op == 0x05:
            rest &= v >= val
        elif op == 0x06:
            rest &= v <= val
    return (any(eq) if eq else True) and rest


def _digits_text(value: int, digits_byte: int | None) -> str:
    """§11.10: low nibble = digit count (0 → 7), bit 7 = zero-pad; value % 10^n, most significant first."""
    flag = digits_byte or 0
    n = (flag & 0x0F) or 7
    value = abs(value) % (10**n)
    return str(value).zfill(n) if flag & 0x80 else str(value)


def _draw_number(
    canvas: Image.Image, imgs: list[Image.Image], value: int, digits_byte: int | None, x: int, y: int
) -> tuple[int, int]:
    text = _digits_text(value, digits_byte)
    cx = x
    for ch in text:
        g = imgs[int(ch)]
        _composite(canvas, g, cx, y)
        cx += g.width
    return cx - x, imgs[0].height


def _composite(canvas: Image.Image, img: Image.Image, x: int, y: int) -> None:
    """alpha_composite, clipped at the canvas edge (elements may hang off-screen, §11.8)."""
    left, top = max(0, -x), max(0, -y)
    right, bottom = min(img.width, SIZE - x), min(img.height, SIZE - y)
    if right > left and bottom > top:
        canvas.alpha_composite(img.crop((left, top, right, bottom)), (x + left, y + top))


def _image(dial: NativeDial, asset) -> Image.Image:
    rgba = dial.decode(asset)
    if asset.cf == 13:  # alpha mask, tinted by the firmware; white in the preview
        rgba = rgba.copy()
        rgba[..., :3] = 255
    return Image.fromarray(np.ascontiguousarray(rgba), "RGBA")
