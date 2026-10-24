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

## To determine in Phase 3 (fill in)
| Question | Answer | Evidence |
|---|---|---|
| Does the face format support native live time digits? | ? | |
| Can it use custom digit sprites/fonts for live time? | ? | |
| Native live fields (steps, HR, battery)? | ? | |
| Max face file size / count on watch | ? | |
| Does AOD use a separate image? | ? | |

## Claude "describe a face" flow
Prompt Claude with: the FaceSpec schema, the constraints above, available bindings, and the user's description → returns a FaceSpec → render preview → user approves → install. Never auto-install generated faces.
