"""Claude features with a fake client: no network, no cost."""

import datetime as dt
import json
from types import SimpleNamespace

import httpx
import pytest

from cmfstudio.api.app import create_app
from cmfstudio.core import claude
from cmfstudio.core.jobs import JobQueue
from cmfstudio.render import describe
from cmfstudio.render.bindings import Context
from cmfstudio.render.service import FaceService
from cmfstudio.render.spec import FaceSpec
from cmfstudio.store.db import Store
from cmfstudio.sync import daily, summary
from cmfstudio.sync.parsers import Sample

from .test_render import needs_browser

AUTH = {"Authorization": "Bearer t"}


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def parse(self, **kw):
        self.calls.append(kw)
        return self.response


def fake_client(monkeypatch, response):
    messages = FakeMessages(response)
    monkeypatch.setattr(claude, "client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=messages)))
    return messages


def ok(parsed):
    return SimpleNamespace(stop_reason="end_turn", stop_details=None, parsed_output=parsed)


# -- core.claude ------------------------------------------------------------------


async def test_parse_sends_model_effort_and_fallbacks(monkeypatch):
    m = fake_client(monkeypatch, ok(summary.WeeklySummary(headline="h", observations=["o"], insight_line="i")))
    out = await claude.parse(summary.WeeklySummary, system="s", user="u")
    assert out.headline == "h"
    call = m.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["output_config"] == {"effort": "medium"}
    assert call["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in call["betas"]
    assert "temperature" not in call and "thinking" not in call


async def test_refusal_is_reported(monkeypatch):
    fake_client(
        monkeypatch,
        SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="bio"), parsed_output=None),
    )
    with pytest.raises(claude.ClaudeRefused, match="bio"):
        await claude.parse(summary.WeeklySummary, system="s", user="u")


async def test_truncated_answer_is_an_error(monkeypatch):
    fake_client(monkeypatch, SimpleNamespace(stop_reason="max_tokens", stop_details=None, parsed_output=None))
    with pytest.raises(RuntimeError, match="incomplete"):
        await claude.parse(summary.WeeklySummary, system="s", user="u")


# -- describe a face --------------------------------------------------------------------


def design(**kw):
    from cmfstudio.render.describe import Design, DesignLayer

    base = dict(kind="text", text="", bind="", x=233, y=233, size=30, height=0, color="#ffffff", font="",
                weight=400, align="center", goal=0)  # fmt: skip
    layers = [DesignLayer(**{**base, **layer}) for layer in kw.pop("layers")]
    return Design(**{"name": "Ice Blue!", "background": "#000000", "clock_style": 3, "clock_x": 70, "clock_y": 100,
                     "clock_color": "#AADDFF", "layers": layers, **kw})  # fmt: skip


def test_design_converts_to_valid_facespec():
    from cmfstudio.render.describe import to_spec

    spec = to_spec(
        design(
            layers=[
                {"kind": "text", "bind": "date:%a %d", "font": "Inter", "color": "#9AD0FF"},
                {"kind": "text", "text": "hello", "color": "not-a-colour"},
                {"kind": "ring", "bind": "metric:steps", "size": 215, "height": 8, "goal": 8000},
                {"kind": "circle", "size": 200, "height": 2},
                {"kind": "rect", "x": 200, "y": 300, "size": 66, "height": 3},
            ]
        )
    )
    assert spec.name == "ice-blue" and spec.clock.style == 2 and spec.clock.color == "#aaddff"
    assert [layer.type for layer in spec.layers] == ["text", "text", "ring", "circle", "rect"]
    assert spec.layers[0].bind == "date:%a %d" and spec.layers[1].text == "hello"
    assert spec.layers[1].color == "#ffffff"  # invalid colour falls back instead of failing the whole design
    assert spec.layers[2].max == 8000 and spec.layers[4].w == 66
    assert [f.family for f in spec.fonts] == ["Inter"]


async def test_describe_uses_the_small_design_schema(monkeypatch):
    from cmfstudio.render.describe import Design

    m = fake_client(monkeypatch, ok(design(layers=[{"kind": "text", "bind": "metric:steps"}])))
    out = await describe.describe_face("minimal gold", Context(metrics={"steps": 10, "hr": 70}))
    assert out.layers[0].bind == "metric:steps" and out.refresh == "on_sync"
    assert m.calls[0]["output_format"] is Design  # not FaceSpec: its grammar is too large for the API
    assert "clock_x+" in m.calls[0]["system"]  # measured clock footprints are in the prompt


# -- weekly summary: aggregates only ------------------------------------------------------


def test_summary_payload_is_aggregates_only():
    store = Store(":memory:")
    today = dt.date.today()
    noon = int(dt.datetime.combine(today, dt.time(12)).timestamp())
    raw = [Sample("hr", noon + 60 * i, 70 + i % 7) for i in range(30)]  # minute-level samples
    store.upsert_samples(raw + [Sample("steps_cum", noon, 4321)], None)
    daily.rebuild(store, noon - 86400)
    payload = summary.aggregates(store)
    text = json.dumps(payload)
    assert str(noon) not in text and str(noon + 60) not in text, "raw sample timestamps must not be sent"
    assert len(payload["days"]) == 14
    last = payload["days"][-1]
    assert last["date"] == today.isoformat() and last["steps"] == 4321 and last["avg_hr"] == 73
    assert set(last) <= {"date", "steps", "kcal", "km", "resting_hr", "avg_hr", "stress", "spo2", "sleep_h"}


async def test_generate_stores_latest(monkeypatch):
    store = Store(":memory:")
    fake_client(
        monkeypatch, ok(summary.WeeklySummary(headline="Good week", observations=["a", "b"], insight_line="Keep going"))
    )
    res = await summary.generate(store)
    assert res["headline"] == "Good week" and summary.latest(store)["insight_line"] == "Keep going"


# -- API ---------------------------------------------------------------------------------


@pytest.fixture
async def client(paired_link, tmp_path):
    store = Store(":memory:")
    q = JobQueue(store, paired_link)
    faces = FaceService(store, paired_link, q, tmp_path)
    app = create_app(paired_link, q, store, "t", tmp_path, faces=faces)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
        yield c


async def test_api_without_key_says_how_to_fix(client, monkeypatch):
    def unavailable():
        raise claude.ClaudeUnavailable("no Anthropic API key: run `uv run cmf set-key`")

    monkeypatch.setattr(claude, "client", unavailable)
    r = await client.post("/health/summary", headers=AUTH)
    assert r.status_code == 503 and "cmf set-key" in r.json()["detail"]


async def test_api_refusal_is_422(client, monkeypatch):
    fake_client(monkeypatch, SimpleNamespace(stop_reason="refusal", stop_details=None, parsed_output=None))
    r = await client.post("/faces/describe", json={"description": "a face"}, headers=AUTH)
    assert r.status_code == 422 and "declined" in r.json()["detail"]


@needs_browser
async def test_api_describe_returns_spec_and_preview(client, monkeypatch):
    fake_client(monkeypatch, ok(design(name="gold", layers=[{"kind": "text", "bind": "date:%a"}])))
    r = (await client.post("/faces/describe", json={"description": "gold minimal"}, headers=AUTH)).json()
    assert r["spec"]["name"] == "gold" and r["preview_png"] and r["checks"]["background_black"]


async def test_payload_endpoint_shows_what_is_sent(client):
    r = (await client.get("/health/summary/payload", headers=AUTH)).json()
    assert len(r["days"]) == 14 and "baselines_28d" in r


async def test_edit_sends_current_design_and_keeps_images_and_local_fonts(monkeypatch):
    from cmfstudio.render.describe import edit_face

    current = FaceSpec.model_validate(
        {
            "name": "mine",
            "fonts": [{"family": "MyFont", "source": "local", "path": "data:font/ttf;base64,AAAA"}],
            "layers": [
                {"type": "image", "src": "data:image/png;base64,AAAA", "w": 466, "h": 466},
                {"type": "text", "bind": "metric:steps", "font": "MyFont", "x": 233, "y": 300},
            ],
        }
    )
    revised = design(
        name="mine", layers=[{"kind": "text", "bind": "metric:steps", "font": "MyFont", "color": "#88ccff"}]
    )
    m = fake_client(monkeypatch, ok(revised))
    out = await edit_face("make the steps light blue", current, Context())
    sent = m.calls[0]["messages"][0]["content"]
    assert '"bind":"metric:steps"' in sent and "make the steps light blue" in sent
    assert "data:image" not in sent  # images aren't sent to Claude
    assert out.layers[0].type == "image" and out.layers[1].color == "#88ccff"
    assert out.fonts[0].source == "local" and out.fonts[0].family == "MyFont"


def test_from_spec_round_trips():
    from cmfstudio.render.describe import from_spec, to_spec

    spec = FaceSpec.model_validate(
        {
            "name": "rt",
            "clock": {"style": 3, "x": 63, "y": 171, "color": "#e8f6ff"},
            "layers": [
                {
                    "type": "text",
                    "bind": "date:%A",
                    "x": 233,
                    "y": 145,
                    "size": 28,
                    "color": "#7fd0ff",
                    "font": "Montserrat",
                },
                {"type": "ring", "bind": "metric:steps", "max": 8000, "r": 215, "width": 8, "color": "#7fd0ff"},
                {"type": "rect", "x": 203, "y": 300, "w": 60, "h": 2, "fill": "#7fd0ff"},
            ],
        }
    )
    back = to_spec(from_spec(spec), spec)
    assert back.clock == spec.clock and len(back.layers) == 3
    assert back.layers[0].bind == "date:%A" and back.layers[1].max == 8000 and back.layers[2].w == 60
