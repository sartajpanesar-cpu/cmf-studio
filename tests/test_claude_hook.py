"""Claude Code hook -> watch alert decisions, and the request it sends to watchd."""

import json

import httpx
import pytest

from cmfstudio.cli import claude_hook as h


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(h, "STATE_DIR", tmp_path / "state")


def test_short_turns_stay_quiet_long_turns_alert():
    ev = {"session_id": "s1", "cwd": "/x/cmf-studio"}
    assert h.alert_for("start", ev, 1000) is None
    assert h.alert_for("stop", ev, 1030) is None, "30 s turn: no buzz"
    h.alert_for("start", ev, 2000)
    title, body = h.alert_for("stop", ev, 2125)
    assert title == "Claude done · cmf-s…" and len(title) <= h.TITLE_MAX
    assert body == "Finished in 2m 05s"
    assert h.alert_for("stop", ev, 2200) is None, "start marker is consumed"


def test_stop_uses_last_message_and_respects_stop_hook_active():
    h.alert_for("start", {"session_id": "s2"}, 0)
    assert h.alert_for("stop", {"session_id": "s2", "stop_hook_active": True}, 500) is None
    _, body = h.alert_for("stop", {"session_id": "s2", "last_assistant_message": "All tests pass.\n" + "x" * 300}, 500)
    assert body.startswith("All tests pass. x") and len(body) == h.BODY_MAX and body.endswith("…")


def test_stop_without_start_is_quiet():
    assert h.alert_for("stop", {"session_id": "never"}, 999) is None


def test_notification_kinds():
    assert h.alert_for("notify", {"message": "Claude needs your permission to use Bash"}, 0) == (
        "Claude needs you",
        "Claude needs your permission to use Bash",
    )
    assert h.alert_for("notify", {"notification_type": "idle_prompt", "message": "waiting"}, 0)[0] == (
        "Claude is waiting"
    )


def test_session_id_cannot_escape_state_dir():
    assert h._state("../../etc/passwd").parent == h.STATE_DIR


def test_send_posts_a_short_lived_unwaited_notify(monkeypatch):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, req.headers["authorization"], json.loads(req.content)))
        return httpx.Response(200, json={})

    monkeypatch.setattr(h, "get_token", lambda: "tok")
    h.send("t", "b", transport=httpx.MockTransport(handler))
    assert seen == [("/notify", "Bearer tok", {"title": "t", "body": "b", "ttl_s": h.TTL_S, "wait": False})]


def test_main_never_raises(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["cmf-hook", "notify"])
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("not json"))
    h.main()
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO('{"message": "m"}'))
    monkeypatch.setattr(h, "get_token", lambda: "tok")
    monkeypatch.setattr(h, "DEFAULT_URL", "http://127.0.0.1:9")  # nothing listens
    h.main()
