"""Parse every frame captured from the real watch (local only; skipped when absent, e.g. in CI).

Refresh with `uv run python scripts/export_fixtures.py`.
"""

import json
from pathlib import Path

import pytest
from cmfkit.protocol.opcodes import Op

from cmfstudio.sync import parsers as p

FIXTURE = Path(__file__).parent / "fixtures" / "private" / "raw_frames.json"

pytestmark = pytest.mark.skipif(not FIXTURE.exists(), reason="no local real-frame fixtures")


def frames():
    if not FIXTURE.exists():
        return []
    return json.loads(FIXTURE.read_text())["frames"]


def op_of(name: str) -> Op:
    cmd1, cmd2 = name.split(":")
    return Op(int(cmd1, 16), int(cmd2, 16))


@pytest.mark.parametrize("frame", frames(), ids=lambda f: f"{f['id']}-{f['opcode']}")
def test_real_frame_parses(frame):
    op, payload = op_of(frame["opcode"]), bytes.fromhex(frame["payload_hex"])
    if op in p.RAW_ONLY:
        pytest.skip("kept raw by design")
    if op == p.SLEEP_DATA:
        s = p.sleep(payload)
        assert s.start_ts < s.end_ts
        return
    samples = p.SAMPLE_PARSERS[op](payload)
    assert samples
    for s in samples:
        assert 1_700_000_000 < s.ts < 2_000_000_000, "timestamp outside 2023-2033: layout wrong?"
        if s.metric in ("hr", "hr_workout", "hr_resting"):
            assert 25 <= s.value <= 230
        if s.metric == "spo2":
            assert 70 <= s.value <= 100
