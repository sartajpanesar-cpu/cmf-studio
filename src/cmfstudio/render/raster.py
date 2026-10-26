"""HTML → pixels → what the watch will actually show.

Playwright renders at 466×466, DPR 1. Then: round mask, AMOLED check, and RGB565 quantisation with a
4×4 Bayer ordered dither (gradients band badly at 5/6/5 bits otherwise). The preview is the quantised,
masked image, not the source PNG.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw
from playwright.async_api import async_playwright

from .spec import SIZE, Clock

BAYER4 = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], dtype=np.float32) / 16.0


async def render_png(page_html: str, timeout_ms: int = 15_000) -> bytes:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": SIZE, "height": SIZE}, device_scale_factor=1)
            await page.set_content(page_html, wait_until="networkidle", timeout=timeout_ms)
            await page.evaluate("document.fonts.ready")
            return await page.screenshot(type="png", omit_background=False)
        finally:
            await browser.close()


def _circle_mask(size: int = SIZE) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    return mask


def round_mask(img: Image.Image) -> Image.Image:
    rgb = img.convert("RGB").resize((SIZE, SIZE)) if img.size != (SIZE, SIZE) else img.convert("RGB")
    return Image.composite(rgb, Image.new("RGB", rgb.size), _circle_mask(rgb.width))


def dither_rgb565(img: Image.Image) -> Image.Image:
    """Ordered-dither to the RGB565 grid; the encoder's truncation then keeps exactly these values."""
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    h, w, _ = a.shape
    t = np.tile(BAYER4, (h // 4 + 1, w // 4 + 1))[:h, :w][..., None]
    step = np.array([8.0, 4.0, 8.0], dtype=np.float32)  # 5/6/5 bits
    q = np.floor((a + t * step) / step) * step
    q = np.clip(q, 0, 255)  # 255 >> 3 = 31: white keeps the top level
    # expand back to 8-bit like the panel does (replicate high bits) for an honest preview
    r = q[..., 0].astype(np.uint8) >> 3
    g = q[..., 1].astype(np.uint8) >> 2
    b = q[..., 2].astype(np.uint8) >> 3
    out = np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], axis=-1)
    return Image.fromarray(out.astype(np.uint8), "RGB")


def amoled_check(img: Image.Image) -> dict:
    """How much of the visible circle is lit. Pure black pixels are off on AMOLED (power, contrast)."""
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    luma = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    inside = np.asarray(_circle_mask(img.width)) > 0
    lum = luma[inside]
    lit = float((lum > 8).mean())
    bright = float((lum > 200).mean())
    background_black = bool(np.percentile(lum, 50) <= 8)
    warnings = []
    if not background_black:
        warnings.append("background isn't black: most of the screen is lit (battery, burn-in)")
    if lit > 0.5:
        warnings.append(f"{lit:.0%} of the screen is lit; AMOLED faces look and last best mostly black")
    if bright > 0.15:
        warnings.append(f"{bright:.0%} of the screen is near-white")
    return {
        "lit_fraction": round(lit, 3),
        "bright_fraction": round(bright, 3),
        "mean_luma": round(float(lum.mean()), 1),
        "background_black": background_black,
        "warnings": warnings,
    }


def preview(watch_img: Image.Image, clock: Clock | None = None) -> bytes:
    """PNG of the watch image with a faint bezel and a marker where the firmware clock will draw."""
    img = watch_img.copy()
    draw = ImageDraw.Draw(img, "RGBA")
    draw.ellipse((0, 0, SIZE - 1, SIZE - 1), outline=(80, 80, 80, 255), width=1)
    if clock is not None:
        # the firmware draws the live clock over the dial; show its measured style (preview only, not sent)
        from . import clock as clock_styles

        rgb = tuple(int(clock.color[i : i + 2], 16) for i in (1, 3, 5))
        shape = clock_styles.shape(clock.style, clock.x, clock.y)
        for cx, cy, r in shape.dots:
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*rgb, 255))
        if shape.date:
            cx, top, size, text = shape.date
            draw.text((cx, top + size / 2), text, fill=(*rgb, 255), anchor="mm", font_size=size * 0.8)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
