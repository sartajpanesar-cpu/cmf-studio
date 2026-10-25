"""MUSIC_BUTTON (FFFF:A05D) decoding for the Pro 2.

Verified on device (findings, live run 4): the payload is 2 bytes, ``group || arg``, and only
arrives after the host has sent MUSIC_INFO_SET. cmfkit's 1-byte DEFAULT_BUTTON_MAP doesn't apply.
"""

from __future__ import annotations

from dataclasses import dataclass

# (group, arg) -> name. Volume direction is tentative (owner pressed up first "I think").
BUTTONS: dict[tuple[int, int], str] = {
    (0x01, 0x00): "play_pause",
    (0x01, 0x01): "play_pause",
    (0x02, 0x01): "next",
    (0x02, 0x00): "prev",
    (0x03, 0x00): "vol_up",
    (0x03, 0x01): "vol_down",
}


@dataclass(frozen=True)
class ButtonEvent:
    name: str
    raw: bytes


def decode(payload: bytes) -> ButtonEvent:
    key = (payload[0], payload[1]) if len(payload) >= 2 else None
    name = BUTTONS.get(key, "unknown") if key else "unknown"
    return ButtonEvent(name, payload)
