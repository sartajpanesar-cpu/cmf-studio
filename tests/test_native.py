"""Native (structured) faces: codec on the spec's templates, the scene renderer, and install on the simulator."""

import asyncio
import base64
import datetime as dt
import struct
from pathlib import Path

import httpx
import numpy as np
import pytest

from cmfstudio.api.app import create_app
from cmfstudio.core.jobs import JobQueue
from cmfstudio.render import native_preview
from cmfstudio.render.native import NativeDial, NativeFormatError, crc32_raw, validate
from cmfstudio.render.native_face import TEMPLATE_DIR, NativeSpec, build
from cmfstudio.render.native_service import NativeService
from cmfstudio.store.db import Store

from .test_render import SYSTEM_TTF, needs_browser

needs_templates = pytest.mark.skipif(
    not (TEMPLATE_DIR / "362.bin").exists(), reason="spec templates (refs/) not present"
)
TEMPLATES = (288, 359, 362, 275, 290, 309)


def template(n: int) -> bytes:
    return (TEMPLATE_DIR / f"{n}.bin").read_bytes()


def test_crc32_raw_is_init0_no_xor():
    assert crc32_raw(b"") == 0
    assert crc32_raw(b"123456789") == 0x2DFD2D88  # CRC-32/JAMCRC init 0 variant; standard crc32 is 0xCBF43926


@needs_templates
@pytest.mark.parametrize("n", TEMPLATES)
def test_rebuild_keeps_every_pixel_and_chain(n):
    dial = NativeDial(template(n))
    out = dial.build()
    assert validate(out) == []
    again = NativeDial(out)
    assert len(again.tails) == len(dial.tails)
    for old, new in zip(dial.tails, again.tails, strict=True):
        for a, b in zip(dial.chain(old), again.chain(new), strict=True):
            assert (dial.decode(a) == again.decode(b)).all()


@needs_templates
def test_replace_grows_the_pool_and_repoints_tails():
    dial = NativeDial(template(362))
    (table, frames) = next((t, f) for t, f in dial.digit_sets() if not t.aod and f[0].w == 84)
    noisy = np.random.default_rng(1).integers(0, 255, (frames[1].h, frames[1].w, 4), dtype=np.uint8)
    size = dial.replace(frames[1], noisy)
    assert size > frames[1].len, "the new '1' is bigger than its old slot"
    out = NativeDial(dial.build())
    new_frames = next(f for t, f in out.digit_sets() if t.at == table.at)
    assert (out.decode(new_frames[1])[..., 3] == noisy[..., 3]).all()
    assert (out.decode(new_frames[2]) == NativeDial(template(362)).decode(frames[2])).all(), "neighbours intact"
    with pytest.raises(NativeFormatError):
        dial.replace(frames[0], noisy[:10])


def test_validate_catches_broken_files():
    assert validate(b"\0" * 10) == ["file too small"]
    if not (TEMPLATE_DIR / "362.bin").exists():
        return
    data = bytearray(NativeDial(template(362)).build())
    bad_footer = data[:-1] + b"\x99"
    assert "footer differs from header" in validate(bytes(bad_footer))
    overrun = bytearray(data)
    struct.pack_into("<H", overrun, 36 + 3 + 1, 0xFFF0)  # 0x21 screen claims more than the scene holds
    assert any("overruns" in p for p in validate(bytes(overrun)))


@needs_templates
def test_preview_renderer_draws_the_time_where_the_template_says():
    dial = NativeDial(template(362))
    values = native_preview.sample_values(dt.datetime(2026, 10, 9, 10, 12, 9))
    img = np.asarray(native_preview.render(dial, values))
    official = np.asarray(__import__("PIL.Image", fromlist=["Image"]).open(TEMPLATE_DIR / "362.png").convert("RGB"))
    # Steady: hours at x=12, minutes at x=282, y=141, 84×181 glyphs; the official render is at 10:12
    for box in ((12, 141, 180, 322), (282, 141, 450, 322)):
        x0, y0, x1, y1 = box
        ours = img[y0:y1, x0:x1, :3].astype(int)
        lit = ours.max(axis=2) > 128
        assert lit.mean() > 0.05 and (official[y0:y1, x0:x1].max(axis=2) > 128)[lit].mean() > 0.95


@needs_templates
def test_digit_colour_and_background_without_a_font():
    spec = NativeSpec(template=359, digit_color="#ff0000", background="#002040", background_bottom="#000000")
    result = asyncio.run(build(spec))
    dial = NativeDial(result.blob)
    main = [f for t, f in dial.digit_sets() if not t.aod]
    lit = dial.decode(main[0][8])
    assert (lit[lit[..., 3] > 200][:, :3] == [255, 0, 0]).all()
    bg = dial.decode(dial.backgrounds()[0])
    assert np.abs(bg[0, 233, :3].astype(int) - (0, 32, 64)).max() <= 4  # RGB565 rounding
    assert tuple(bg[-1, 233, :3]) == (0, 0, 0)


@needs_templates
@needs_browser
def test_font_glyphs_replace_every_digit_set():
    font = {"family": "Mine", "source": "local", "path": "data:font/ttf;base64," + _b64(SYSTEM_TTF)}
    if not Path(SYSTEM_TTF).exists():
        pytest.skip("system font not present")
    before = NativeDial(template(362))
    result = asyncio.run(build(NativeSpec(template=362, font=font, digit_color="#e8c66a")))
    after = NativeDial(result.blob)
    for (t0, f0), (_t1, f1) in zip(before.digit_sets(), after.digit_sets(), strict=True):
        for a, b in zip(f0, f1, strict=True):
            assert not (before.decode(a)[..., 3] == after.decode(b)[..., 3]).all(), f"set {t0.base:#x} unchanged"
    assert result.preview_png.startswith(b"\x89PNG")


def _b64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode() if Path(path).exists() else ""


# -- install over a stock face on the simulator ------------------------------------------------------------------


@pytest.fixture
async def native_api(paired_link, watch, tmp_path):
    store = Store(":memory:")
    queue = JobQueue(store, paired_link, retry_s=0.01)
    queue.start()
    native = NativeService(store, paired_link, queue, tmp_path / "faces")
    app = create_app(paired_link, queue, store, "t", tmp_path, native=native)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers={"Authorization": "Bearer t"}
    ) as c:
        yield c, watch, store
    await queue.stop()


async def wait_job(c, job_id):
    for _ in range(200):
        job = (await c.get(f"/jobs/{job_id}")).json()
        if job["state"] in ("done", "failed", "expired"):
            return job
        await asyncio.sleep(0.02)
    raise AssertionError("job didn't finish")


@needs_templates
async def test_install_adds_a_face_once_per_layout_until_full(native_api):
    c, watch, _store = native_api
    assert [d["id"] for d in (await c.get("/watch/dials")).json()["dials"]][:4] == [275, 352, 276, 280]
    face = (await c.post("/native/faces", json={"name": "gold", "template": 362, "digit_color": "#e8c66a"})).json()
    assert (await c.post(f"/native/faces/{face['id']}/install", params={"replace": 275})).status_code == 400

    r = (await c.post(f"/native/faces/{face['id']}/install", params={"confirm": True, "replace": 275})).json()
    assert r["replaces"] == {"id": 275, "name": "SlopeTime"} and r["installs_as"] == 362
    assert (await wait_job(c, r["job"]["id"]))["state"] == "done"
    assert 275 in watch.state.dials and watch.state.dials[-1] == 362, "added, like the real watch"
    listed = (await c.get("/watch/dials")).json()["dials"][-1]
    assert listed == {"id": 362, "name": "Steady", "active": True, "kind": "native"}

    again = await c.post(f"/native/faces/{face['id']}/install", params={"confirm": True})
    assert again.status_code == 409 and "already on the watch" in again.json()["detail"]

    for n, template in enumerate((359, 288)):  # fill the remaining slots (5 → 7)
        other = (await c.post("/native/faces", json={"name": f"f{n}", "template": template})).json()
        r = (await c.post(f"/native/faces/{other['id']}/install", params={"confirm": True})).json()
        if "job" in r:
            await wait_job(c, r["job"]["id"])
    assert len(watch.state.dials) == 7


@needs_templates
async def test_refuses_to_replace_the_custom_slot_or_unknown_dials(native_api):
    c, _watch, _store = native_api
    face = (await c.post("/native/faces", json={"name": "x", "template": 288})).json()
    for bad in (0xFFFFFFFF, 999):
        r = await c.post(f"/native/faces/{face['id']}/install", params={"confirm": True, "replace": bad})
        assert r.status_code == 409


@needs_templates
async def test_native_preview_endpoint(native_api):
    c, *_ = native_api
    r = (await c.post("/native/preview", json={"template": 359, "digit_color": "#00ff88"})).json()
    assert r["template"] == 359 and base64.b64decode(r["preview_png"]).startswith(b"\x89PNG")
    assert [t["id"] for t in (await c.get("/native/templates")).json()] == [362, 359, 288]


@needs_templates
def test_hiding_extras_blanks_their_images_only():
    dial = NativeDial(template(359))
    result = asyncio.run(build(NativeSpec(template=359, show_date=False, show_data=False)))
    out = NativeDial(result.blob)
    from cmfstudio.render.native_face import widgets

    for (cat, imgs), (_cat2, new_imgs) in zip(widgets(dial), widgets(out), strict=True):
        alpha_max = max(int(out.decode(out.assets[o])[..., 3].max()) for o in new_imgs)
        if cat in ("date", "data"):
            assert alpha_max == 0 or all(dial.assets[o].cf == 4 for o in imgs), f"{cat} widget still visible"
        elif cat == "time":
            before = max(int(dial.decode(dial.assets[o])[..., 3].max()) for o in imgs)
            assert alpha_max == before, "time widgets keep their pixels (AM/PM is blank in this template)"


@needs_templates
@pytest.mark.parametrize("n", [359, 362, 288])
def test_time_row_is_centred_on_both_screens(n):
    from cmfstudio.render.native_face import _center_time, _hide

    dial = NativeDial(template(n))
    _center_time(dial)
    _hide(dial, {"date", "data"})  # so every lit pixel is the time row
    out = NativeDial(dial.build())
    values = native_preview.sample_values(dt.datetime(2026, 1, 1, 20, 58))  # wide digits: ink ≈ the digit slots
    for aod in (False, True):
        img = np.asarray(native_preview.render(out, values, aod))[..., :3].astype(int)
        background = img[233, 2]  # a pixel left of the clock
        xs = np.argwhere(np.abs(img - background).max(axis=2) > 60)[:, 1]
        if len(xs):
            assert abs((xs.min() + xs.max()) / 2 - 233) <= 3, (n, aod, int(xs.min()), int(xs.max()))


@needs_templates
@pytest.mark.parametrize("n", [362, 288])
def test_add_background_to_layouts_without_one(n):
    from cmfstudio.render.native import add_background

    before = NativeDial(template(n))
    out = add_background(template(n))
    after = NativeDial(out)
    assert validate(out) == [] and len(after.backgrounds()) == 1 and len(after.tails) == len(before.tails) + 1
    with pytest.raises(NativeFormatError):
        add_background(out)  # only once
    # a picture background, end to end, survives the pool rebuild
    from PIL import Image as PILImage

    picture = PILImage.new("RGB", (466, 466), (200, 40, 40))
    result = asyncio.run(build(NativeSpec(template=n, digit_color="#ffffff"), background_image=picture))
    final = NativeDial(result.blob)
    bg = final.decode(final.backgrounds()[0])
    assert np.abs(bg[233, 233, :3].astype(int) - (200, 40, 40)).max() <= 12
    img = np.asarray(native_preview.render(final, native_preview.sample_values()))
    assert np.abs(img[233, 6, :3].astype(int) - (200, 40, 40)).max() <= 12, "preview draws the new background"
