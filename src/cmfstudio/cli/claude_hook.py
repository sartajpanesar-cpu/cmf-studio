"""`cmf-hook`: Claude Code hooks -> a short notification on the wrist, via watchd.

Wired in .claude/settings.json:
  UserPromptSubmit -> `cmf-hook start`   (remembers when the turn began)
  Stop             -> `cmf-hook stop`    (alerts only if the turn ran at least CMF_HOOK_MIN_S, default 60 s)
  Notification     -> `cmf-hook notify`  (Claude is waiting: permission prompt or idle)

Reads the hook's JSON on stdin. Never blocks or fails Claude: any problem (watchd down, no token) is a silent exit 0.
Alerts are queued with a short TTL, so a watch that is away doesn't get a pile of stale ones when it reconnects.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from ..api.auth import get_token

DEFAULT_URL = os.environ.get("CMF_API", "http://127.0.0.1:8765")
MIN_TURN_S = float(os.environ.get("CMF_HOOK_MIN_S", "60"))
TTL_S = 300
TITLE_MAX = 20
BODY_MAX = 128
STATE_DIR = Path(tempfile.gettempdir()) / "cmf-hook"


def _state(session: str) -> Path:
    return STATE_DIR / f"{''.join(ch for ch in session if ch.isalnum() or ch == '-')[:64] or 'default'}"


def _short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _project(event: dict[str, Any]) -> str:
    return Path(event.get("cwd") or os.getcwd()).name


def alert_for(cmd: str, event: dict[str, Any], now: float) -> tuple[str, str] | None:
    """(title, body) to send for this hook event, or None. Records turn start for `start`."""
    session = str(event.get("session_id") or "default")
    if cmd == "start":
        STATE_DIR.mkdir(exist_ok=True)
        _state(session).write_text(str(now))
        return None
    if cmd == "stop":
        if event.get("stop_hook_active"):
            return None
        path = _state(session)
        try:
            began = float(path.read_text())
            path.unlink()
        except (OSError, ValueError):
            return None
        took = now - began
        if took < MIN_TURN_S:
            return None
        mins, secs = divmod(int(took), 60)
        body = event.get("last_assistant_message") or f"Finished in {mins}m {secs:02d}s"
        return _short(f"Claude done · {_project(event)}", TITLE_MAX), _short(str(body), BODY_MAX)
    if cmd == "notify":
        kind = event.get("notification_type", "")
        title = "Claude needs you" if kind != "idle_prompt" else "Claude is waiting"
        return _short(title, TITLE_MAX), _short(str(event.get("message") or _project(event)), BODY_MAX)
    return None


def send(title: str, body: str, transport: httpx.BaseTransport | None = None) -> None:
    token = get_token()
    if not token:
        return
    with httpx.Client(
        base_url=DEFAULT_URL, headers={"Authorization": f"Bearer {token}"}, timeout=3, transport=transport
    ) as c:
        c.post("/notify", json={"title": title, "body": body, "ttl_s": TTL_S, "wait": False})


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        event = json.loads(sys.stdin.read() or "{}")
        msg = alert_for(cmd, event, time.time())
        if msg:
            send(*msg)
    except Exception:  # a hook must never get in Claude's way
        pass


if __name__ == "__main__":
    main()
