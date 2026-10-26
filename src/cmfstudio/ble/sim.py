"""cmfkit's simulated watch, extended with what the Pro 2 does that cmfkit's sim (a Watch 3 Pro) doesn't:

- after each TIME it pushes DIAL_COMMAND_RET with its dial list (findings 2026-10-25);
- structured dial install over 9075 replaces an existing dial (spec §11.2 / §11.4): ``old_id`` must be in the
  list, the file must pass the structured-dial validation, else the finish byte is 0x0a;
- settings: GET (cmd2 0002) answers on the SET opcode with the stored value, SET (0001) stores and acks (0003).
  Initial values are what the owner's watch returned (probe 2026-10-26), and writes behave as measured that day:
  alarms go in big-endian and come back little-endian, sorted by time, byte 7 set; goal writes are acked but the
  goals GET doesn't change.
"""

from __future__ import annotations

import struct

from cmfkit.protocol import opcodes as ops
from cmfkit.protocol.opcodes import Op
from cmfkit.transport.sim import SimWatch

from ..render import native
from .link import CUSTOM_DIAL_ID, WF_INIT2_STRUCTURED_REPLY, WF_INIT2_STRUCTURED_REQUEST

DIAL_COMMAND_RET = ops.DIAL_COMMAND_RET

# QUICK_CARD GET reply captured by the spec author (README §16): 4 screens, 11 cards
SPEC_TILES = bytes.fromhex("01000401025d0061000103190 02e002300".replace(" ", "") + "01035c0004005a020103480051005300")

PROBED: dict[int, bytes] = {  # cmd1 -> GET reply from the owner's watch, fw 1.0.0.57 (findings 2026-10-26)
    0x0063: bytes.fromhex("30750000000000010000000000000000000000000000000000000000000000000000000000000000"),
    0x005E: bytes.fromhex("88130000a00f000090010000d00200001e0000000c01010101010101"),
    0x005F: b"\x01",
    0x0060: bytes.fromhex("00003c0000000000000000"),
    0x0061: bytes.fromhex("00003c0000000000000000"),
    0x0062: b"\x01",
    0x0099: b"\x00",
    0x009B: bytes.fromhex("a505a13270bba50d"),
    0x00D5: b"",
    0x00DC: bytes.fromhex("10020301190572041a1810130f21070822"),
}


class StudioSim(SimWatch):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.state.dials = [275, 352, 276, 280]  # the owner's watch (2026-10-26), minus the custom slot
        self.active_index = 0
        self.native: dict[int, bytes] = {}  # dial id -> installed structured file
        self._structured: tuple[int, int] | None = None
        self.settings: dict[int, bytes] = dict(PROBED)
        self.written: list[tuple[int, int, bytes]] = []  # (cmd1, cmd2, payload) of every settings write

    def push_dial_list(self) -> None:
        ids = self.state.dials
        body = bytes([1, self.active_index, len(ids), 7]) + b"".join(struct.pack("<I", i) for i in ids)
        self._reply(DIAL_COMMAND_RET, body + b"\xff\xff\xff\xff")

    def _setting(self, msg) -> bool:
        cmd1, cmd2, p = msg.op.cmd1, msg.op.cmd2, msg.payload
        if cmd1 in self.settings and cmd2 == 0x0002:
            self._reply(Op(cmd1, 0x0001), self.settings[cmd1])
            return True
        if cmd1 in self.settings and cmd2 == 0x0001:
            self.written.append((cmd1, cmd2, p))
            self.settings[cmd1] = _as_read(cmd1, p, self.settings[cmd1])
            self._reply(Op(cmd1, 0x0003))
            return True
        if cmd1 == 0xFFFF and cmd2 == 0x906D and p[:1] == b"\x00":
            self._reply(Op(0xFFFF, 0xA06D), SPEC_TILES)
            return True
        if cmd1 == 0xFFFF and cmd2 in (0x9059, 0x9067, 0x9068):
            self.written.append((cmd1, cmd2, p))
            if cmd2 != 0x9059:
                self._reply(Op(0xFFFF, (cmd2 & 0x0FFF) | 0xA000))  # 9067 -> A067
            return True
        return False

    def _dispatch(self, msg) -> None:
        if self._setting(msg):
            return
        if msg.op == WF_INIT2_STRUCTURED_REQUEST:
            _kind, old_id, new_id, size = struct.unpack("<BIII", msg.payload[:13])
            self._structured = (old_id, new_id)
            self._upload_size, self._upload, self._next_offset = size, bytearray(), 0
            self._reply(WF_INIT2_STRUCTURED_REPLY, b"\x01")
            self._request_chunk()
            return
        super()._dispatch(msg)
        if msg.op == ops.TIME:
            self.push_dial_list()
        if msg.op == ops.WF_INIT2_PHOTO_REQUEST:
            self._structured = None

    def _finish_upload(self) -> None:
        if self._structured is None:
            super()._finish_upload()
            if self.state.dials.count(CUSTOM_DIAL_ID) > 1:  # the real watch keeps one custom slot
                self.state.dials.remove(CUSTOM_DIAL_ID)
            self.active_index = self.state.dials.index(CUSTOM_DIAL_ID)
            return
        old_id, new_id = self._structured
        blob = bytes(self._upload or b"")
        self._upload, self._structured = None, None
        problems = native.validate(blob)
        if old_id not in self.state.dials or new_id in self.state.dials or problems:
            self.on_event(f"structured dial rejected: old={old_id} new={new_id} {problems}")
            self._reply(ops.WF_FINISH_REQUEST, b"\x0a")
            return
        index = self.state.dials.index(old_id)
        self.state.dials[index] = new_id
        self.active_index = index
        self.native[new_id] = blob
        self.on_event(f"structured dial {new_id} installed over {old_id} ({len(blob)} bytes)")
        self._reply(ops.WF_FINISH_REQUEST, b"\x01")


def _as_read(cmd1: int, written: bytes, current: bytes) -> bytes:
    """What a GET returns after a SET, under the sim's hypotheses."""
    if cmd1 == 0x0063:  # alarms: big-endian seconds in, little-endian out, sorted by time, byte 7 = 1
        recs = []
        for i in range(0, len(written) - 39, 40):
            rec = written[i : i + 40]
            seconds = struct.unpack_from(">I", rec)[0] % (1 << 32)
            recs.append((seconds, struct.pack("<I", seconds) + rec[4:7] + b"\x01" + rec[8:]))
        return b"".join(r for _s, r in sorted(recs))
    if cmd1 == 0x005E:
        return current  # goal writes are acked but the GET doesn't change (fw 1.0.0.57)
    return written
