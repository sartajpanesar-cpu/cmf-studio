import asyncio
import datetime as dt
import io
import struct

import numpy as np
import pytest
from cmfkit.watchface import photo
from PIL import Image
from pydantic import ValidationError

from cmfstudio.render import html, raster
from cmfstudio.render.bindings import Context, resolve
from cmfstudio.render.spec import FaceSpec

CTX = Context(now=dt.datetime(2026, 10, 26, 8, 30), metrics={"steps": 5234, "sleep": 27000, "km": 3.456}, insight="hi")


def chromium_available() -> bool:
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


needs_browser = pytest.mark.skipif(not chromium_available(), reason="playwright chromium not installed")


# -- spec + bindings ------------------------------------------------------------


def test_spec_parses_doc_example_shape():
    spec = FaceSpec.model_validate(
        {
            "name": "deco",
            "layers": [
                {"type": "text", "bind": "date:%a %d", "maxWidth": 300, "color": "#E8C66A"},
                {"type": "ring", "bind": "metric:steps", "startDeg": 0},
            ],
            "clock": {"color": "#00ff00"},
        }
    )
    assert spec.layers[0].max_width == 300 and spec.layers[0].color == "#e8c66a"
    assert spec.clock.color565 == 0x07E0


@pytest.mark.parametrize(
    "bad", [{"name": ""}, {"name": "x", "background": "red"}, {"name": "x", "clock": {"style": 5}}]
)
def test_spec_rejects_bad_input(bad):
    with pytest.raises(ValidationError):
        FaceSpec.model_validate(bad)


def test_bindings():
    assert resolve("date:%a %d", CTX) == "Mon 26"
    assert resolve("metric:steps", CTX) == "5,234"
    assert resolve("metric:sleep", CTX) == "7h30"
    assert resolve("metric:km", CTX) == "3.5"
    assert resolve("metric:spo2", CTX) == "–"  # no data yet
    assert resolve("static:STEPS", CTX) == "STEPS"
    assert resolve("insight", CTX) == "hi"
    with pytest.raises(ValueError):
        resolve("nope:x", CTX)


def test_html_escapes_text_and_inlines_local_assets(tmp_path):
    img = tmp_path / "dot.png"
    Image.new("RGB", (2, 2), (255, 0, 0)).save(img)
    spec = FaceSpec.model_validate(
        {"name": "x", "layers": [{"type": "text", "text": "<b>&"}, {"type": "image", "src": "dot.png"}]}
    )
    page = html.build(spec, CTX, tmp_path)
    assert "&lt;b&gt;&amp;" in page and "<b>" not in page
    assert "data:image/png;base64," in page and "file://" not in page


def test_ring_fraction_follows_metric():
    spec = FaceSpec.model_validate({"name": "x", "layers": [{"type": "ring", "bind": "metric:steps", "max": 10468}]})
    page = html.build(spec, CTX, ".")
    circ = 2 * 3.141592653589793 * 215
    assert f'stroke-dasharray="{circ * 0.5:.2f}' in page  # 5234 / 10468 = half


# -- raster -------------------------------------------------------------------------


def test_dither_lands_on_rgb565_grid_and_keeps_extremes():
    grad = np.tile(np.linspace(0, 255, 466, dtype=np.float32), (466, 1))
    img = Image.fromarray(np.stack([grad] * 3, -1).astype(np.uint8), "RGB")
    out = np.asarray(raster.dither_rgb565(img)).astype(int)
    r, g = out[..., 0], out[..., 1]
    # every pixel is an 8-bit expansion of a 5/6/5 value
    assert np.array_equal(r, ((r >> 3) << 3) | (r >> 5))
    assert np.array_equal(g, ((g >> 2) << 2) | (g >> 6))
    assert out[:, 0].max() == 0 and out[:, -1].min() == 255  # black and white survive
    # ordered dither mixes neighbouring levels instead of hard bands
    assert len(np.unique(r[:, 200])) >= 2


def test_dithered_image_survives_encoder_exactly():
    img = Image.new("RGB", (466, 466), (123, 200, 17))
    watch = raster.dither_rgb565(raster.round_mask(img))
    decoded = photo.parse(photo.build(watch)).full.convert("RGB")
    a, b = np.asarray(watch).astype(int), np.asarray(decoded).astype(int)
    inside = np.asarray(raster._circle_mask()) > 0
    assert np.abs(a[inside] - b[inside]).max() <= 4  # cmfkit's 8-bit expansion may differ in the low bits


def test_amoled_check_flags_bright_faces():
    assert raster.amoled_check(raster.round_mask(Image.new("RGB", (466, 466))))["warnings"] == []
    white = raster.amoled_check(raster.round_mask(Image.new("RGB", (466, 466), (255, 255, 255))))
    assert not white["background_black"] and len(white["warnings"]) == 3


def test_round_mask_blacks_out_corners():
    out = raster.round_mask(Image.new("RGB", (466, 466), (255, 255, 255)))
    assert out.getpixel((0, 0)) == (0, 0, 0) and out.getpixel((233, 233)) == (255, 255, 255)


# -- full pipeline (needs chromium) -----------------------------------------------


@needs_browser
def test_render_pipeline_end_to_end(tmp_path):
    from cmfstudio.render.pipeline import render

    spec = FaceSpec.model_validate(
        {
            "name": "t",
            "layers": [
                {"type": "text", "bind": "metric:steps", "size": 80, "color": "#ff0000"},
                {"type": "ring", "bind": "metric:steps", "max": 10468, "color": "#00ff00"},
            ],
        }
    )
    r = asyncio.run(render(spec, CTX, tmp_path))
    assert r.watch.size == (466, 466) and r.watch.getpixel((0, 0)) == (0, 0, 0)
    reds = np.asarray(r.watch)[..., 0] > 200
    assert reds[180:290, 100:370].any(), "the steps text should be drawn mid-screen"
    assert np.asarray(r.watch)[..., 1][:30].max() > 200, "the ring starts at 12 o'clock"
    assert photo.parse(r.blob).full.size == (466, 466)
    assert r.checks["background_black"]
    Image.open(io.BytesIO(r.preview_png)).verify()
    struct.unpack("<I", r.blob[4:8])
