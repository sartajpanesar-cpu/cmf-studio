"""FaceSpec + bound values → a self-contained 466×466 HTML page.

Local fonts and images are inlined as data: URIs (an in-memory page can't load file:// resources);
Google fonts load from fonts.googleapis.com. Every layer is absolutely positioned in spec order.
"""

from __future__ import annotations

import base64
import html
import math
import mimetypes
from pathlib import Path

from .bindings import Context, metric_value, resolve
from .spec import SIZE, CircleLayer, FaceSpec, ImageLayer, RectLayer, RingLayer, TextLayer

FONT_FORMATS = {".ttf": "truetype", ".otf": "opentype", ".woff": "woff", ".woff2": "woff2"}
DEFAULT_FONTS = "-apple-system,'Helvetica Neue',sans-serif"  # page default; also every layer's fallback
MIME_FORMATS = {"font/ttf": "truetype", "font/otf": "opentype", "font/woff": "woff", "font/woff2": "woff2"}


def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _src(src: str, base: Path) -> str:
    return src if src.startswith("data:") else data_uri((base / src).resolve())


def inline_assets(spec: FaceSpec, base_dir: Path | str) -> FaceSpec:
    """Replace local image and font paths with data: URIs so the spec is self-contained (stored in the DB)."""
    base = Path(base_dir)
    data = spec.model_dump(by_alias=True)
    for layer in data["layers"]:
        if layer["type"] == "image" and not layer["src"].startswith("data:"):
            layer["src"] = data_uri((base / layer["src"]).resolve())
    for font in data["fonts"]:
        if font["source"] == "local" and font["path"] and not font["path"].startswith("data:"):
            font["path"] = data_uri((base / font["path"]).resolve())
    return FaceSpec.model_validate(data)


def _font_css(spec: FaceSpec, base: Path) -> tuple[str, str]:
    links, faces = [], []
    for f in spec.fonts:
        if f.source == "google":
            weights = ";".join(str(w) for w in sorted(set(f.weights)))
            family = f.family.replace(" ", "+")
            links.append(
                f'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family={family}:wght@{weights}'
                '&display=block">'
            )
        elif f.source == "local":
            if not f.path:
                raise ValueError(f"local font {f.family!r} needs a path")
            if f.path.startswith("data:"):
                mime = f.path[5:].split(";", 1)[0]
                uri, fmt = f.path, MIME_FORMATS.get(mime, "truetype")
            else:
                path = (base / f.path).resolve()
                uri, fmt = data_uri(path), FONT_FORMATS.get(path.suffix.lower(), "truetype")
            faces.append(f"@font-face{{font-family:'{f.family}';src:url({uri}) format('{fmt}');}}")
    return "\n".join(links), "\n".join(faces)


def _text(layer: TextLayer, ctx: Context) -> str:
    content = layer.text if layer.text is not None else resolve(layer.bind or "static:", ctx)
    shift = {"left": "0", "center": "-50%", "right": "-100%"}[layer.align]
    style = [
        "position:absolute",
        f"left:{layer.x}px",
        f"top:{layer.y}px",
        f"transform:translate({shift},-50%)",
        f"font-size:{layer.size}px",
        f"font-weight:{layer.weight}",
        f"color:{layer.color}",
        f"text-align:{layer.align}",
        f"letter-spacing:{layer.letter_spacing}px",
        f"opacity:{layer.opacity}",
        "line-height:1.1",
    ]
    if layer.font:
        style.append(f"font-family:'{layer.font}',{DEFAULT_FONTS}")
    if layer.max_width:
        style.append(f"width:{layer.max_width}px")
    else:
        style.append("white-space:nowrap")
    return f'<div style="{";".join(style)}">{html.escape(content)}</div>'


def _svg(inner: str, opacity: float = 1) -> str:
    return (
        f'<svg width="{SIZE}" height="{SIZE}" style="position:absolute;left:0;top:0;opacity:{opacity}" '
        f'xmlns="http://www.w3.org/2000/svg">{inner}</svg>'
    )


def _circle(layer: CircleLayer) -> str:
    fill = layer.fill or "none"
    stroke = layer.stroke or "none"
    return _svg(
        f'<circle cx="{layer.cx}" cy="{layer.cy}" r="{layer.r}" fill="{fill}" stroke="{stroke}" '
        f'stroke-width="{layer.width}"/>',
        layer.opacity,
    )


def _rect(layer: RectLayer) -> str:
    return _svg(
        f'<rect x="{layer.x}" y="{layer.y}" width="{layer.w}" height="{layer.h}" rx="{layer.radius}" '
        f'fill="{layer.fill}"/>',
        layer.opacity,
    )


def _ring(layer: RingLayer, ctx: Context) -> str:
    value = metric_value(layer.bind, ctx) or 0
    frac = max(0.0, min(1.0, value / layer.max))
    circ = 2 * math.pi * layer.r
    parts = []
    if layer.track:
        parts.append(
            f'<circle cx="{layer.cx}" cy="{layer.cy}" r="{layer.r}" fill="none" stroke="{layer.track}" '
            f'stroke-width="{layer.width}"/>'
        )
    if frac > 0:
        parts.append(
            f'<circle cx="{layer.cx}" cy="{layer.cy}" r="{layer.r}" fill="none" stroke="{layer.color}" '
            f'stroke-width="{layer.width}" stroke-linecap="round" stroke-dasharray="{circ * frac:.2f} {circ:.2f}" '
            f'transform="rotate({layer.start_deg} {layer.cx} {layer.cy})"/>'
        )
    return _svg("".join(parts))


def build(spec: FaceSpec, ctx: Context, base_dir: Path | str = ".") -> str:
    base = Path(base_dir)
    links, faces = _font_css(spec, base)
    body = []
    for layer in spec.layers:
        if isinstance(layer, TextLayer):
            body.append(_text(layer, ctx))
        elif isinstance(layer, ImageLayer):
            body.append(
                f'<img src="{_src(layer.src, base)}" style="position:absolute;left:{layer.x}px;top:{layer.y}px;'
                f'width:{layer.w}px;height:{layer.h}px;opacity:{layer.opacity}">'
            )
        elif isinstance(layer, CircleLayer):
            body.append(_circle(layer))
        elif isinstance(layer, RectLayer):
            body.append(_rect(layer))
        elif isinstance(layer, RingLayer):
            body.append(_ring(layer, ctx))
    return f"""<!doctype html>
<html><head><meta charset="utf-8">{links}
<style>{faces}
html,body{{margin:0;padding:0;width:{SIZE}px;height:{SIZE}px;overflow:hidden;background:{spec.background};
font-family:{DEFAULT_FONTS};-webkit-font-smoothing:antialiased}}</style>
</head><body>{"".join(body)}</body></html>"""
