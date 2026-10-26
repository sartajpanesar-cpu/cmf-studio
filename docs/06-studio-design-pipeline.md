# 06 — Studio design pipeline

## Why this works
A watchface is a pixel canvas. Anything rendered into it — any font, gradient, illustration, layout — reaches the watch. Only **system** menus are locked to firmware fonts (`fonts.bin`, Phase 7). So "custom fonts and graphics" = a good renderer + re-rendering on each sync.

## Pipeline
```
FaceSpec (JSON)  →  HTML/CSS template  →  Playwright (466×466, DPR 1)  →  PNG
   →  round mask + black-level check  →  RGB565 quantize (ordered dither)  →  cmfkit encoder  →  install job
```

## FaceSpec v0
```json
{
  "name": "deco-steps",
  "background": "#000000",
  "fonts": [{"family": "Poiret One", "source": "google"}],
  "layers": [
    {"type": "text", "bind": "date:%a %d", "x": 233, "y": 120, "font": "Poiret One", "size": 34, "color": "#E8C66A", "align": "center"},
    {"type": "text", "bind": "metric:steps", "x": 233, "y": 300, "size": 56},
    {"type": "text", "bind": "insight", "x": 233, "y": 370, "size": 18, "maxWidth": 300},
    {"type": "image", "src": "assets/ring.png", "x": 0, "y": 0, "w": 466, "h": 466}
  ],
  "refresh": "on_sync"
}
```
Bindings resolve at render time from the store: `date:<strftime>`, `metric:<name>[:agg]`, `insight` (Claude-written line), `static:<text>`.

## Constraints to design for
- **Round display:** usable area is the 466 px circle; keep text inside ~420 px diameter.
- **AMOLED:** pure black background saves power and looks best; avoid large bright fills.
- **RGB565:** 65k colors → gradients band. Use ordered (Bayer) dithering; preview the quantized result, not the source PNG.
- **Staleness:** baked-in data is only as fresh as the last sync. Time itself must come from the format's native time fields (if supported) — otherwise the face shows a stale clock. **Phase 3 must determine this first.**
- File size limits: unknown → measure.

## Determined in Phase 3 (2026-10-26)
Two face kinds exist (spec §11). We ship **photo dials** now; **structured dials** are the path to more slots and
native live fields (see "Next" below).

| Question | Answer | Evidence |
|---|---|---|
| Does the face format support native live time digits? | **Yes, both kinds.** Photo dial: the firmware always draws a live digital clock over the image — one of 5 built-in styles, positioned and tinted by INIT2 (`styleId`, `posX/posY`, `color565`); there is no "off". Structured dial: live analog hands and digit-sprite time | Ours: deco-steps install job 40 with the spec's clock settings; spec §11.3, §11.6 ("hands live"), §11.10 |
| Can it use custom digit sprites/fonts for live time? | Photo dial: **no** (only the 5 built-in styles). Structured dial: **yes** — digits are sprite atlases (`img_number`, cf=5) that can be re-skinned; re-authored dial 373 rendered live | spec §11.6, §11.10 |
| Native live fields (steps, HR, battery)? | Photo dial: **none** — everything except the clock is baked into the image and refreshed after each sync (hourly). Structured dial: **yes** — configurable complication slots bound to data-source ids (steps, HR, battery, kcal, …) and progress rings | Ours: refresh loop jobs 41–43; spec §11.13, §11.15, §16 |
| Max face file size / count on watch | **7 dials total** (DIAL_COMMAND max = 7), **1 custom photo slot** (an install replaces it). Our photo dials: 17–20 KB (LZ4); raw max ≈ 434 KB + 146 KB thumb. Structured re-skins must not grow the file. No hard byte limit observed | Ours: dial lists `…07`, Phase 0 run 4; spec §11.3, §11.6 |
| Does AOD use a separate image? | Structured dial: **yes**, a separate `0x22` AOD container with its own layers. Photo dial: **unknown** (spec silent; not yet observed with AOD on) | spec §11.9 |

**Consequences for design:** keep the time where the firmware clock goes (the preview marks its anchor); anything else
is a snapshot that's at most ~1 h old (refresh after each sync, only while the custom face is on screen).

**Next (structured dials):** re-skin a store dial (swap its background and sprites, recompute both header CRC32s, never
grow the file, fresh-install into its slot) → up to 7 custom faces with live hands/complications. Needs the store dial
binaries and a careful container editor (spec §11.4–11.15).

## Claude "describe a face" flow
Prompt Claude with: the FaceSpec schema, the constraints above, available bindings, and the user's description → returns a FaceSpec → render preview → user approves → install. Never auto-install generated faces.
