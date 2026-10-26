"""The 5 firmware clock styles of a photo dial, for previews only (the watch draws the real clock).

Measured from owner photos of calibration faces, rectified with the grid (research/captures/clock-styles/,
findings 2026-10-26): all styles are round-dot matrix digits; positions are offsets from the INIT2 anchor
(clock.x, clock.y), measured with the anchor at (80, 120). Glyph shapes for digits not photographed
(4-9) follow the same 4x6 dot grid and are approximations.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

# 4 columns x 6 rows; "#" = dot
GLYPHS: dict[str, list[str]] = {
    "0": [".##.", "#..#", "#..#", "#..#", "#..#", ".##."],
    "1": ["..#.", ".##.", "..#.", "..#.", "..#.", "..#."],  # stem right of centre, no base (photos)
    "2": [".##.", "#..#", "...#", "..#.", ".#..", "####"],
    "3": ["###.", "...#", ".##.", "...#", "...#", "###."],
    "4": ["#..#", "#..#", "#..#", "####", "...#", "...#"],
    "5": ["####", "#...", "###.", "...#", "...#", "###."],
    "6": [".##.", "#...", "###.", "#..#", "#..#", ".##."],
    "7": ["####", "...#", "..#.", ".#..", ".#..", ".#.."],
    "8": [".##.", "#..#", ".##.", "#..#", "#..#", ".##."],
    "9": [".##.", "#..#", "#..#", ".###", "...#", ".##."],
    ":": ["....", ".#..", "....", "....", ".#..", "...."],
}


@dataclass(frozen=True)
class Style:
    pitch: float  # distance between dot centres
    dot: float  # dot diameter (dots touch at pitch == dot)
    stacked: bool  # hours over minutes, else one row "HH:MM"
    dx: float  # top-left of the digits relative to the anchor
    dy: float
    line_gap: float = 0  # extra space between stacked lines
    date: bool = False  # "MON 26" under the time
    date_size: float = 0
    date_dy: float = 0  # date top relative to the bottom of the digits


STYLES: dict[int, Style] = {
    0: Style(pitch=20, dot=19, stacked=True, dx=-2, dy=30, line_gap=35),
    1: Style(pitch=16, dot=15, stacked=True, dx=2, dy=20, line_gap=19, date=True, date_size=24, date_dy=59),
    2: Style(pitch=14, dot=13, stacked=False, dx=23, dy=20),
    3: Style(pitch=14, dot=13, stacked=False, dx=20, dy=19, date=True, date_size=24, date_dy=70),
    4: Style(pitch=22, dot=21, stacked=True, dx=9, dy=24, line_gap=30),
}


@dataclass
class ClockShape:
    dots: list[tuple[float, float, float]]  # (cx, cy, r) in watch pixels
    date: tuple[float, float, float, str] | None  # (centre x, top y, size, text)
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 footprint


def _place(text: str, x: float, y: float, st: Style, dots: list) -> float:
    """Draw ``text`` with its top-left at (x, y); returns the x after the last glyph."""
    for ch in text:
        glyph = GLYPHS[ch]
        width = 1 if ch == ":" else 4
        for row, line in enumerate(glyph):
            for col, cell in enumerate(line):
                if cell == "#":
                    dots.append((x + col * st.pitch + st.pitch / 2, y + row * st.pitch + st.pitch / 2, st.dot / 2))
        x += (width + 1) * st.pitch
    return x


def shape(style: int, x: int, y: int, when: dt.datetime | None = None) -> ClockShape:
    st = STYLES[style]
    when = when or dt.datetime(2026, 10, 26, 10, 9)
    hh, mm = when.strftime("%H"), when.strftime("%M")
    dots: list[tuple[float, float, float]] = []
    ox, oy = x + st.dx, y + st.dy
    line_h = 6 * st.pitch
    if st.stacked:
        right = _place(hh, ox, oy, st, dots)
        _place(mm, ox, oy + line_h + st.line_gap, st, dots)
        x1, y1 = right - st.pitch, oy + 2 * line_h + st.line_gap
    else:
        right = _place(f"{hh}:{mm}", ox, oy, st, dots)
        x1, y1 = right - st.pitch, oy + line_h
    date = None
    if st.date:
        text = when.strftime("%a %d").upper()
        date = ((ox + x1) / 2, y1 + st.date_dy - st.date_size, st.date_size, text)
        y1 = y1 + st.date_dy
    return ClockShape(dots, date, (ox, oy, x1, y1))


def svg(style: int, x: int, y: int, color: str, when: dt.datetime | None = None, size: int = 466) -> str:
    """An SVG overlay (full face size) of the clock, for the live preview."""
    s = shape(style, x, y, when)
    circles = "".join(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}"/>' for cx, cy, r in s.dots)
    date = ""
    if s.date:
        cx, top, fs, text = s.date
        date = (
            f'<text x="{cx:.1f}" y="{top + fs * 0.85:.1f}" font-size="{fs:.0f}" text-anchor="middle" '
            f'font-family="ui-monospace,Menlo,monospace" font-weight="700" letter-spacing="1">{text}</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 466 466">'
        f'<g fill="{color}">{circles}{date}</g></svg>'
    )
