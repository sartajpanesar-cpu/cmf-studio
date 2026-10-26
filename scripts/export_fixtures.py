"""Export captured raw frames from watchd's database into tests/fixtures/private/ (gitignored).

Health data stays local (docs/04-safety-policy.md), so these fixtures are never committed; tests that
use them skip when the folder is absent (CI). Re-run after syncs that bring new frame types.

    uv run python scripts/export_fixtures.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = Path.home() / "Library" / "Application Support" / "cmf-studio" / "cmfstudio.db"
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "private" / "raw_frames.json"


def main() -> None:
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = db.execute("SELECT id, received_at, opcode, payload_hex FROM raw_frames ORDER BY id").fetchall()
    frames = [{"id": i, "received_at": t, "opcode": op, "payload_hex": hx} for i, t, op, hx in rows]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"source": str(DB), "frames": frames}, indent=1))
    kinds = sorted({f["opcode"] for f in frames})
    print(f"wrote {len(frames)} frames ({', '.join(kinds)}) to {OUT}")


if __name__ == "__main__":
    main()
