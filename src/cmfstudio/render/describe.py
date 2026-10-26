""" "Describe a face" → a FaceSpec from Claude, validated before anything renders. Never auto-installed."""

from __future__ import annotations

from ..core import claude
from .bindings import Context
from .spec import FaceSpec, ImageLayer

SYSTEM = """You design watch faces for a CMF Watch Pro 2: a 466×466 px round AMOLED screen. You answer with a
FaceSpec (the JSON schema you are given). How the face works:

- The image you describe is the background of a "photo dial". The watch's firmware draws a LIVE digital clock on
  top of it: `clock.style` picks one of 5 built-in layouts (0-4), `clock.x`/`clock.y` place its top-left corner,
  `clock.color` tints it. The clock always shows; reserve roughly a 150×60 px area for it and keep other
  layers out of that area. Never draw the time yourself - anything else is a snapshot refreshed hourly.
- Coordinates are pixels from the top-left. Only the inscribed circle (centre 233,233, radius 233) is visible;
  keep text within about 200 px of the centre.
- AMOLED: use a pure black background (#000000) and keep most of the screen dark; bright fills waste battery.
- Layers draw in order (first = bottom). Types: text (fixed `text` or a live `bind`), ring (progress arc for a
  metric toward `max`), circle, rect. Do not use image layers.
- Text bindings: date:<strftime> (e.g. "date:%a %d %b"), metric:<name> with name one of steps, kcal, km, hr,
  hr_resting, hr_avg, hr_max, sleep, stress, spo2, battery; static:<text>; insight (a short motivational line).
  A text layer has either `text` or `bind`, not both.
- Fonts: any Google Fonts family; list each one used in `fonts` with source "google". Pick fonts that suit the
  description and read well small. Use sizes 12-90 px.
- Colours are #rrggbb. Aim for 3-9 layers, a clear hierarchy and good legibility. refresh: "on_sync".
- `name`: a short lowercase-hyphenated name for the design."""


async def describe_face(description: str, ctx: Context) -> FaceSpec:
    available = ", ".join(sorted(ctx.metrics)) or "none yet"
    user = f"Design a watch face: {description.strip()}\n\n(Metrics with data today: {available}.)"
    spec: FaceSpec = await claude.parse(FaceSpec, system=SYSTEM, user=user, effort="medium")
    # belt and braces: the prompt forbids images (there are no assets to reference)
    layers = [layer for layer in spec.layers if not isinstance(layer, ImageLayer)]
    return spec.model_copy(update={"layers": layers, "refresh": "on_sync"})
