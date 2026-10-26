"""cmfkit's simulated watch, extended with what the Pro 2 does that cmfkit's sim (a Watch 3 Pro) doesn't:

- after each TIME it pushes DIAL_COMMAND_RET with its dial list (findings 2026-10-25);
- structured dial install over 9075 replaces an existing dial (spec §11.2 / §11.4): ``old_id`` must be in the
  list, the file must pass the structured-dial validation, else the finish byte is 0x0a.
"""

from __future__ import annotations

import struct

from cmfkit.protocol import opcodes as ops
from cmfkit.transport.sim import SimWatch

from ..render import native
from .link import CUSTOM_DIAL_ID, WF_INIT2_STRUCTURED_REPLY, WF_INIT2_STRUCTURED_REQUEST

DIAL_COMMAND_RET = ops.DIAL_COMMAND_RET


class StudioSim(SimWatch):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.state.dials = [275, 352, 276, 280]  # the owner's watch (2026-10-26), minus the custom slot
        self.active_index = 0
        self.native: dict[int, bytes] = {}  # dial id -> installed structured file
        self._structured: tuple[int, int] | None = None

    def push_dial_list(self) -> None:
        ids = self.state.dials
        body = bytes([1, self.active_index, len(ids), 7]) + b"".join(struct.pack("<I", i) for i in ids)
        self._reply(DIAL_COMMAND_RET, body + b"\xff\xff\xff\xff")

    def _dispatch(self, msg) -> None:
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
