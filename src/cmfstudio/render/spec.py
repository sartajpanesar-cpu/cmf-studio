"""FaceSpec v0 (docs/06-studio-design-pipeline.md): a JSON description of a photo-dial face.

The rendered image is the dial's background; the watch firmware draws the live clock on top
(``clock`` picks one of its 5 built-in styles, position and colour — findings, Phase 0 run 4).
Coordinates are pixels on the 466×466 canvas; the visible area is the inscribed circle.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

SIZE = 466
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _color(v: str) -> str:
    if not COLOR.match(v):
        raise ValueError(f"colour must be #rrggbb, got {v!r}")
    return v.lower()


Color = Annotated[str, AfterValidator(_color)]

# Font names and data: URIs end up inside HTML attributes and CSS; constrain them instead of trusting escaping.
FONT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
DATA_URI = re.compile(r"^data:[\w.+-]+/[\w.+-]+;base64,[A-Za-z0-9+/]*={0,2}$")


def _font_name(v: str) -> str:
    if not FONT_NAME.match(v):
        raise ValueError(f"font name may use letters, digits, space, '_', '.', '-' only, got {v!r}")
    return v


def _asset(v: str) -> str:
    if v.startswith("data:") and not DATA_URI.match(v):
        raise ValueError("data: URIs must be base64 (data:<type>/<subtype>;base64,...)")
    return v


FontName = Annotated[str, AfterValidator(_font_name)]
Asset = Annotated[str, AfterValidator(_asset)]


class FontRef(BaseModel):
    family: FontName
    source: Literal["google", "local", "system"] = "google"
    path: Asset | None = Field(default=None, description="local .ttf/.otf, relative to the spec file")
    weights: list[int] = [400, 700]


class Clock(BaseModel):
    """The firmware-drawn live clock (photo dials always have one; there is no 'off')."""

    style: int = Field(0, ge=0, le=4)
    x: int = Field(56, ge=0, lt=SIZE)
    y: int = Field(77, ge=0, lt=SIZE)
    color: Color = "#ffffff"

    @property
    def color565(self) -> int:
        r, g, b = (int(self.color[i : i + 2], 16) for i in (1, 3, 5))
        return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


class TextLayer(BaseModel):
    type: Literal["text"] = "text"
    text: str | None = None
    bind: str | None = Field(default=None, description="date:<strftime> | metric:<name> | static:<text> | insight")
    x: int = SIZE // 2
    y: int = SIZE // 2
    font: FontName | None = None
    size: int = Field(32, gt=0, le=400)
    weight: int = 400
    color: Color = "#ffffff"
    align: Literal["left", "center", "right"] = "center"
    max_width: int | None = Field(default=None, alias="maxWidth")
    letter_spacing: float = Field(0, alias="letterSpacing")
    opacity: float = Field(1, ge=0, le=1)

    model_config = {"populate_by_name": True}


class ImageLayer(BaseModel):
    type: Literal["image"] = "image"
    src: Asset = Field(description="path relative to the spec file, or a data: URI")
    x: int = 0
    y: int = 0
    w: int = SIZE
    h: int = SIZE
    opacity: float = Field(1, ge=0, le=1)


class CircleLayer(BaseModel):
    type: Literal["circle"] = "circle"
    cx: int = SIZE // 2
    cy: int = SIZE // 2
    r: int = Field(200, gt=0)
    fill: Color | None = None
    stroke: Color | None = "#ffffff"
    width: float = 2
    opacity: float = Field(1, ge=0, le=1)


class RectLayer(BaseModel):
    type: Literal["rect"] = "rect"
    x: int = 0
    y: int = 0
    w: int = 100
    h: int = 100
    fill: Color = "#ffffff"
    radius: int = 0
    opacity: float = Field(1, ge=0, le=1)


class RingLayer(BaseModel):
    """A progress arc, e.g. steps toward a goal: value from ``bind`` (a metric), full at ``max``."""

    type: Literal["ring"] = "ring"
    bind: str = "metric:steps"
    max: float = Field(8000, gt=0)
    cx: int = SIZE // 2
    cy: int = SIZE // 2
    r: int = Field(215, gt=0)
    width: float = 10
    color: Color = "#ffffff"
    track: Color | None = "#202020"
    start_deg: float = Field(-90, alias="startDeg")

    model_config = {"populate_by_name": True}


Layer = Annotated[TextLayer | ImageLayer | CircleLayer | RectLayer | RingLayer, Field(discriminator="type")]


class FaceSpec(BaseModel):
    version: Literal[0] = 0
    name: str = Field(min_length=1, max_length=60)
    background: Color = "#000000"
    fonts: list[FontRef] = []
    layers: list[Layer] = []
    clock: Clock = Clock()
    refresh: Literal["on_sync", "manual"] = "on_sync"
