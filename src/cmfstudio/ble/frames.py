"""Frame decoding fixes on top of cmfkit (pinned commit 37781b1).

cmfkit's ``FrameDecoder`` sends every encrypted-opcode body to AES, including the zero-length
frames the watch uses for ACKs (e.g. APP_NOTIFICATION_ACK ``f5 0000 0065 0001 0001 0003``),
and drops them as "AES decryption failed". It also discards the raw bytes of frames it can't
open. See research/findings.md, live run 5.
"""

from __future__ import annotations

from cmfkit.protocol.frame import FrameDecoder, FrameError


class SafeFrameDecoder(FrameDecoder):
    def _open(self, body: bytes, encrypted: bool) -> bytes:
        if not body:
            return b""
        try:
            return super()._open(body, encrypted)
        except FrameError as exc:
            raise FrameError(f"{exc}; body={body.hex()}") from exc
