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


SYSTEM_TTF = "/System/Library/Fonts/Supplemental/Apple Chancery.ttf"
SYSTEM_OTF = "/System/Library/Fonts/Supplemental/NotoSansJavanese-Regular.otf"


@needs_browser
@pytest.mark.parametrize("font_path", [SYSTEM_TTF, SYSTEM_OTF])
def test_local_font_is_used(font_path, tmp_path):
    """A local font must actually change the glyphs (not silently fall back), also after inlining."""
    from pathlib import Path

    from cmfstudio.render.html import inline_assets
    from cmfstudio.render.pipeline import render

    if not Path(font_path).exists():
        pytest.skip("system font not present")
    text = {"type": "text", "text": "Hello 123 Quartz", "size": 44}
    # same family name, font not loaded: identical to the page fallback (see the control test below)
    plain = FaceSpec.model_validate({"name": "plain", "layers": [{**text, "font": "Mine"}]})
    custom = FaceSpec.model_validate(
        {
            "name": "custom",
            "fonts": [{"family": "Mine", "source": "local", "path": font_path}],
            "layers": [{**text, "font": "Mine"}],
        }
    )
    inlined = inline_assets(custom, "/")
    assert inlined.fonts[0].path.startswith("data:font/")
    a = np.asarray(asyncio.run(render(plain, CTX, tmp_path)).watch).astype(int)
    b = np.asarray(asyncio.run(render(inlined, CTX, tmp_path)).watch).astype(int)
    assert (np.abs(a - b).sum(axis=-1) > 60).mean() > 0.005, "glyphs identical: font didn't load"


@needs_browser
def test_unloaded_font_falls_back_identically(tmp_path):
    """Control for test_local_font_is_used: naming a font that isn't loaded changes nothing."""
    from cmfstudio.render.pipeline import render

    text = {"type": "text", "text": "Hello 123 Quartz", "size": 44}
    plain = FaceSpec.model_validate({"name": "plain", "layers": [text]})
    missing = FaceSpec.model_validate({"name": "missing", "layers": [{**text, "font": "NotLoaded"}]})
    a = np.asarray(asyncio.run(render(plain, CTX, tmp_path)).watch).astype(int)
    b = np.asarray(asyncio.run(render(missing, CTX, tmp_path)).watch).astype(int)
    assert (np.abs(a - b).sum(axis=-1) > 60).mean() < 0.0005


def test_clock_styles_match_measured_footprints():
    """Footprints measured from owner photos with the anchor at (80, 120) (findings 2026-10-26), ±12 px."""
    from cmfstudio.render import clock

    measured = {0: (78, 150, 258, 420), 1: (82, 140, 219, 410), 2: (103, 140, 393, 230), 3: (86, 139, 390, 294),
                4: (89, 144, 284, 424)}  # fmt: skip
    for style, box in measured.items():
        got = clock.shape(style, 80, 120).box
        assert all(abs(a - b) <= 16 for a, b in zip(got, box, strict=True)), (style, got, box)
    assert clock.shape(1, 80, 120).date and not clock.shape(0, 80, 120).date
    moved = clock.shape(2, 180, 220).box
    assert moved[0] - clock.shape(2, 80, 120).box[0] == 100  # follows the anchor (assumption, see findings)


def test_style3_matches_three_photographed_anchors():
    """Style 3 at (20,60), (80,120), (160,220): digits start at anchor + ~(20, 18) (findings 2026-10-26)."""
    from cmfstudio.render import clock

    for (ax, ay), (x0, y0) in {(20, 60): (39, 76), (80, 120): (103, 140), (160, 220): (180, 240)}.items():
        box = clock.shape(2, ax, ay).box
        assert abs(box[0] - x0) <= 6 and abs(box[1] - y0) <= 6, ((ax, ay), box)
        assert abs((box[2] - box[0]) - 290) <= 8
    # one-row styles left-align "1": its first dot is in the slot's first column
    xs = [cx for cx, cy, r in clock.shape(2, 0, 0, __import__("datetime").datetime(2026, 1, 1, 11, 27)).dots]
    assert min(xs) < 20 + 14


# -- hardening (docs/07-threat-model.md): specs also arrive from the API and MCP ---------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"layers": [{"type": "text", "text": "x", "font": "A'};x:url(//evil)"}]},
        {"fonts": [{"family": 'Inter"><script>', "source": "google"}]},
        {"layers": [{"type": "image", "src": 'data:image/png;base64,AA" onerror="x'}]},
    ],
)
def test_spec_rejects_markup_in_fonts_and_data_uris(bad):
    with pytest.raises(ValueError):
        FaceSpec.model_validate({"name": "x", **bad})


def test_local_files_only_from_the_spec_folder_and_only_assets(tmp_path):
    (tmp_path / "secret.txt").write_text("hunter2")
    Image.new("RGB", (2, 2)).save(tmp_path / "ok.png")
    sub = tmp_path / "face"
    sub.mkdir()

    def page(src, base):
        return html.build(FaceSpec.model_validate({"name": "x", "layers": [{"type": "image", "src": src}]}), CTX, base)

    for src in ("../secret.txt", "../ok.png", str(tmp_path / "ok.png")):
        with pytest.raises(ValueError):
            page(src, sub)
    with pytest.raises(ValueError, match="only data: URIs"):
        page("ok.png", None)  # API / MCP renders
    assert "data:image/png;base64," in page("ok.png", tmp_path)


@needs_browser
def test_render_runs_no_scripts_and_reaches_no_other_hosts(tmp_path):
    hits = []
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    page = (
        f'<html><body style="background:#000"><img src="{url}/img">'
        f'<script>fetch("{url}/js"); document.body.style.background="#fff"</script></body></html>'
    )
    from cmfstudio.render import raster

    png = asyncio.run(raster.render_png(page))
    srv.shutdown()
    assert hits == [], "no request may leave the render page except to Google Fonts"
    assert Image.open(io.BytesIO(png)).convert("RGB").getpixel((233, 233)) == (0, 0, 0), "script must not run"
