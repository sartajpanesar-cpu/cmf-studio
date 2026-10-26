import json

import httpx
import pytest

from cmfstudio.cli import main as cli


def run(capsys, monkeypatch, argv, handler, stdin="y\n"):
    monkeypatch.setattr(cli, "get_token", lambda: "t")
    monkeypatch.setattr("builtins.input", lambda _prompt: stdin.strip())
    try:
        cli.main(argv, transport=httpx.MockTransport(handler))
    except SystemExit as exc:
        return capsys.readouterr().out, str(exc.code)
    return capsys.readouterr().out, None


STATUS = {
    "link": {
        "address": "SIM",
        "state": "needs_pair",
        "battery": None,
        "firmware": None,
        "serial": None,
        "connected_since": None,
        "last_keepalive_at": None,
        "last_error": "no pairing key",
    },
    "pending_jobs": 0,
}


def test_status_hints_pair(capsys, monkeypatch):
    out, _ = run(capsys, monkeypatch, ["status"], lambda r: httpx.Response(200, json=STATUS))
    assert "needs_pair" in out and "cmf pair" in out


def test_sends_bearer_token(capsys, monkeypatch):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json=STATUS)

    run(capsys, monkeypatch, ["status"], handler)
    assert seen["auth"] == "Bearer t"


def test_face_install_cancel_sends_nothing(capsys, monkeypatch, tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    calls = []
    _, code = run(capsys, monkeypatch, ["face", "install", str(img)], lambda r: calls.append(r), stdin="n\n")
    assert code == "cancelled" and calls == []


def test_face_install_sends_confirm(capsys, monkeypatch, tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"png-bytes")
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        seen["body"] = request.content
        job = {"id": 1, "kind": "install_face", "state": "done", "result": {"activated": True}, "error": None}
        return httpx.Response(200, json=job)

    out, _ = run(capsys, monkeypatch, ["face", "install", str(img), "-y"], handler)
    assert seen["params"]["confirm"] == "true" and seen["body"] == b"png-bytes"
    assert "activated" in out


def test_api_error_is_shown(capsys, monkeypatch):
    _, code = run(capsys, monkeypatch, ["status"], lambda r: httpx.Response(401, json={"detail": "bad token"}))
    assert "401" in code and "bad token" in code


@pytest.mark.parametrize("argv", [["notify", "hi"], ["jobs"], ["events", "--kind", "button"]])
def test_commands_parse_and_call(capsys, monkeypatch, argv):
    def handler(request):
        if request.url.path == "/notify":
            assert json.loads(request.content)["title"] == "hi"
            return httpx.Response(200, json={"id": 1, "kind": "notify", "state": "done", "result": {}, "error": None})
        return httpx.Response(200, json=[])

    _, code = run(capsys, monkeypatch, argv, handler)
    assert code is None
