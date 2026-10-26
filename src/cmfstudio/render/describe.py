""" "Describe a face" → a FaceSpec from Claude, validated before anything renders. Never auto-installed.

Claude answers with a deliberately flat Design schema (one layer shape, every field required): the full
FaceSpec, with its discriminated union of layer types and optional fields, compiles to a structured-output
grammar the API rejects as too large (2026-10-26). The Design is then converted and validated as a FaceSpec.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from ..core import claude
from . import clock
from .bindings import Context
from .spec import FaceSpec

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class DesignLayer(BaseModel):
    kind: Literal["text", "ring", "circle", "rect"]
    text: str = Field(description="fixed text for kind=text; empty if bind is used or for other kinds")
    bind: str = Field(description="live binding for text, or the metric for kind=ring; empty if unused")
    x: int = Field(description="text: anchor x; ring/circle: centre x; rect: left")
    y: int = Field(description="text: anchor y (vertical centre); ring/circle: centre y; rect: top")
    size: int = Field(description="text: font px; ring/circle: radius px; rect: width px")
    height: int = Field(description="rect: height px; ring/circle: stroke width px; text: 0")
    color: str = Field(description="#rrggbb")
    font: str = Field(description="Google Fonts family for text, or empty for the system font")
    weight: int = Field(description="text font weight 300-800; 400 otherwise")
    align: Literal["left", "center", "right"]
    goal: int = Field(description="kind=ring: the value at which the ring is full (e.g. 8000 steps); else 0")


class Design(BaseModel):
    name: str = Field(description="short lowercase-hyphenated name")
    background: str = Field(description="#rrggbb, normally #000000")
    clock_style: int = Field(description="1-5, see the clock styles")
    clock_x: int
    clock_y: int
    clock_color: str = Field(description="#rrggbb")
    layers: list[DesignLayer] = Field(description="bottom to top, 3-9 layers")


def _clock_table() -> str:
    rows = []
    for style in range(5):
        x0, y0, x1, y1 = clock.shape(style, 0, 0).box
        kind = "hours over minutes" if clock.STYLES[style].stacked else "one row HH:MM"
        date = ", with a 'MON 26' date line" if clock.STYLES[style].date else ""
        rows.append(
            f"  style {style + 1}: {kind}{date}; digits occupy x from clock_x{x0:+.0f} to clock_x{x1:+.0f}, "
            f"y from clock_y{y0:+.0f} to clock_y{y1:+.0f} ({x1 - x0:.0f}×{y1 - y0:.0f} px)"
        )
    return "\n".join(rows)


SYSTEM = f"""You design watch faces for a CMF Watch Pro 2: a 466×466 px round AMOLED screen (only the circle
centred at 233,233 with radius 233 is visible; keep content within about 200 px of the centre).

The face is an image the watch shows under a LIVE digital clock that its firmware draws. You choose the clock's
style (1-5), anchor (clock_x, clock_y) and colour. The clock is round white dot-matrix digits tinted by its
colour. Measured footprints relative to the anchor:
{_clock_table()}
Place the clock so its digits sit fully inside the circle where the design needs the time (e.g. centred:
clock_x = 233 - (left offset + width/2)), and keep every other layer clear of the clock's footprint.
Never draw the time yourself. Everything except the clock is a snapshot refreshed about hourly.

Layers (drawn in order, first = bottom), one flat shape for all kinds:
- text: shows `text`, or a live `bind` (then text is empty). x,y is the anchor (y = vertical centre), `align`
  says how the text hangs from x. size = font px (12-90).
- ring: progress arc toward `goal` for the metric in `bind` (e.g. metric:steps, goal 8000); centre x,y,
  radius = size, thickness = height.
- circle: outline at centre x,y, radius = size, stroke width = height.
- rect: left x, top y, width = size, height = height, filled with color.
Bindings: date:<strftime> (e.g. date:%a %d %b), metric:<name> with name one of steps, kcal, km, hr, hr_resting,
hr_avg, hr_max, sleep, stress, spo2, battery; static:<text>; insight (a short motivational line).
Fonts: any Google Fonts family (or empty for the system font); choose ones that suit the brief and read small.
AMOLED: black background, mostly dark screen, no large bright fills. Colours are #rrggbb. Aim for a clear
hierarchy, generous spacing and nothing overlapping."""


def _hex(value: str, default: str) -> str:
    return value.lower() if HEX.match(value or "") else default


def to_spec(d: Design) -> FaceSpec:
    layers: list[dict] = []
    for layer in d.layers:
        color = _hex(layer.color, "#ffffff")
        if layer.kind == "text":
            entry = {
                "type": "text",
                "x": layer.x,
                "y": layer.y,
                "size": max(8, min(layer.size, 200)),
                "weight": layer.weight or 400,
                "color": color,
                "align": layer.align,
                "font": layer.font or None,
            }
            if layer.bind:
                entry["bind"] = layer.bind
            else:
                entry["text"] = layer.text
            layers.append(entry)
        elif layer.kind == "ring":
            layers.append(
                {
                    "type": "ring",
                    "bind": layer.bind or "metric:steps",
                    "max": max(layer.goal, 1),
                    "cx": layer.x,
                    "cy": layer.y,
                    "r": max(layer.size, 1),
                    "width": max(layer.height, 1),
                    "color": color,
                    "track": "#1a1a1a",
                }
            )
        elif layer.kind == "circle":
            layers.append(
                {
                    "type": "circle",
                    "cx": layer.x,
                    "cy": layer.y,
                    "r": max(layer.size, 1),
                    "stroke": color,
                    "width": max(layer.height, 1),
                    "fill": None,
                }
            )
        else:
            layers.append(
                {
                    "type": "rect",
                    "x": layer.x,
                    "y": layer.y,
                    "w": max(layer.size, 1),
                    "h": max(layer.height, 1),
                    "fill": color,
                }
            )
    fonts = sorted({entry["font"] for entry in layers if entry.get("font")})
    return FaceSpec.model_validate(
        {
            "name": re.sub(r"[^a-z0-9-]+", "-", d.name.lower()).strip("-")[:60] or "claude-face",
            "background": _hex(d.background, "#000000"),
            "fonts": [{"family": f, "source": "google", "weights": [300, 400, 500, 600, 700]} for f in fonts],
            "layers": layers,
            "clock": {
                "style": max(0, min(d.clock_style - 1, 4)),
                "x": max(0, min(d.clock_x, 465)),
                "y": max(0, min(d.clock_y, 465)),
                "color": _hex(d.clock_color, "#ffffff"),
            },
            "refresh": "on_sync",
        }
    )


async def describe_face(description: str, ctx: Context) -> FaceSpec:
    available = ", ".join(sorted(ctx.metrics)) or "none yet"
    user = f"Design a watch face: {description.strip()}\n\n(Metrics with data today: {available}.)"
    design: Design = await claude.parse(Design, system=SYSTEM, user=user, effort="medium")
    return to_spec(design)
