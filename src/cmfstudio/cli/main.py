"""`cmf`: thin client over watchd's localhost API. Never touches Bluetooth."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

from ..api.auth import get_token

DEFAULT_URL = "http://127.0.0.1:8765"


class Client:
    def __init__(self, url: str, token: str | None, transport: httpx.BaseTransport | None = None) -> None:
        if not token:
            sys.exit("no API token in the Keychain yet; start watchd once (it creates one)")
        self.http = httpx.Client(
            base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=90, transport=transport
        )

    def call(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = self.http.request(method, path, **kw)
        except httpx.ConnectError:
            sys.exit(f"watchd isn't reachable at {self.http.base_url} (start it: `watchd`, or `watchd --sim`)")
        if r.status_code >= 400:
            detail = (
                r.json().get("detail", r.text)
                if r.headers.get("content-type", "").startswith("application/json")
                else r.text
            )
            sys.exit(f"error {r.status_code}: {detail}")
        return r.json()


def confirm(prompt: str, assume_yes: bool) -> None:
    if assume_yes:
        return
    if input(f"{prompt} [y/N] ").strip().lower() != "y":
        sys.exit("cancelled")


def ts(t: float | None) -> str:
    return dt.datetime.fromtimestamp(t).strftime("%H:%M:%S") if t else "-"


def show_job(job: dict[str, Any]) -> None:
    line = f"job {job['id']} {job['kind']}: {job['state']}"
    if job.get("result") is not None:
        line += f"  {json.dumps(job['result'])}"
    if job.get("error") and job["state"] != "done":
        line += f"  ({job['error']})"
    print(line)
    if job["state"] == "queued":
        print("  the watch isn't connected; it will run when the watch reconnects")


def cmd_status(c: Client, _a: argparse.Namespace) -> None:
    st = c.call("GET", "/status")
    link = st["link"]
    print(f"watch     {link['address']}  [{link['state']}]")
    if link["state"] == "needs_pair":
        print("          -> run `cmf pair` (first run, or Nothing X has paired with the watch since)")
    print(f"firmware  {link['firmware'] or '-'}   serial {link['serial'] or '-'}")
    print(f"battery   {link['battery'] if link['battery'] is not None else '-'}%")
    print(f"connected since {ts(link['connected_since'])}   last keepalive {ts(link.get('last_keepalive_at'))}")
    print(f"pending jobs {st['pending_jobs']}")
    if link["last_error"] and link["state"] != "connected":
        print(f"last error  {link['last_error']}")


def cmd_notify(c: Client, a: argparse.Namespace) -> None:
    body = {"title": a.title, "body": a.body, "icon": a.icon, "wait": not a.no_wait}
    show_job(c.call("POST", "/notify", json=body))


def cmd_face_install(c: Client, a: argparse.Namespace) -> None:
    path = Path(a.image)
    if not path.is_file():
        sys.exit(f"no such file: {path}")
    confirm("This REPLACES the watch's only custom photo face. Back up the current one first. Continue?", a.yes)
    params = {"confirm": True, "name": path.stem, "style": a.style, "x": a.x, "y": a.y, "color565": int(a.color, 16)}
    show_job(c.call("POST", "/faces/install", params=params, content=path.read_bytes()))


def cmd_pair(c: Client, a: argparse.Namespace) -> None:
    confirm(
        "Pair this Mac with the watch? Nothing X will need to re-pair afterwards. "
        "Keep the iPhone's Bluetooth off and accept the prompt on the watch.",
        a.yes,
    )
    print("pairing... accept the request on the watch screen (60 s)")
    res = c.call("POST", "/pair", params={"confirm": True})
    print(f"paired; watch is {res['link']['state']}")


def cmd_sync(c: Client, _a: argparse.Namespace) -> None:
    print("syncing health data from the watch (can take a few minutes)...")
    show_job(c.call("POST", "/sync"))


def cmd_samples(c: Client, a: argparse.Namespace) -> None:
    if not a.metric:
        summ = c.call("GET", "/samples/summary")
        for metric, m in sorted(summ["metrics"].items()):
            print(f"{metric:<18} {m['count']:>7}  {dts(m['first'])} .. {dts(m['last'])}")
        sl = summ["sleep_sessions"]
        print(f"{'sleep sessions':<18} {sl['count']:>7}  {dts(sl['first'])} .. {dts(sl['last'])}")
        print(f"{'raw frames':<18} {summ['raw_frames']:>7}")
        return
    start = int(dt.datetime.now().timestamp()) - int(a.hours * 3600)
    for t, v in c.call("GET", "/samples", params={"metric": a.metric, "start": start})["samples"]:
        print(f"{dts(t)}  {v:g}")


def cmd_days(c: Client, a: argparse.Namespace) -> None:
    days = c.call("GET", "/daily", params={"days": a.days})["days"]
    if not days:
        print("no daily data yet; run `cmf sync`")
        return
    cols = [
        ("steps", "steps", "{:.0f}"),
        ("calories", "cal*", "{:.0f}"),
        ("distance_m", "km", "{:.2f}"),
        ("hr_resting", "rest", "{:.0f}"),
        ("hr_avg", "hr avg", "{:.0f}"),
        ("hr_max", "hr max", "{:.0f}"),
        ("spo2_avg", "spo2", "{:.0f}"),
        ("stress_avg", "stress", "{:.0f}"),
        ("sleep_s", "sleep", "{}"),
    ]
    print("date        " + "".join(f"{h:>8}" for _, h, _ in cols))
    for day, m in days.items():
        cells = []
        for key, _, fmt in cols:
            v = m.get(key)
            if v is None:
                cells.append("-")
            elif key == "distance_m":
                cells.append(fmt.format(v / 1000))
            elif key == "sleep_s":
                cells.append(f"{int(v // 3600)}h{int(v % 3600 // 60):02d}")
            else:
                cells.append(fmt.format(v))
        print(f"{day}  " + "".join(f"{x:>8}" for x in cells))
    print("* calories in the watch's unit (looks like kcal; unconfirmed)")


def cmd_reparse(c: Client, _a: argparse.Namespace) -> None:
    print(json.dumps(c.call("POST", "/reparse"), indent=2))


def dts(t: float | None) -> str:
    return dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if t else "-"


def cmd_jobs(c: Client, a: argparse.Namespace) -> None:
    for job in reversed(c.call("GET", "/jobs", params={"limit": a.limit})):
        print(f"{ts(job['created_at'])}  ", end="")
        show_job(job)


def cmd_events(c: Client, a: argparse.Namespace) -> None:
    params: dict[str, Any] = {"limit": a.limit}
    if a.kind:
        params["kind"] = a.kind
    for ev in reversed(c.call("GET", "/events", params=params)):
        print(f"{ts(ev['ts'])}  {ev['kind']:<7} {json.dumps(ev['data'])}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cmf", description="Control the CMF Watch Pro 2 through watchd.")
    p.add_argument("--url", default=os.environ.get("CMF_API", DEFAULT_URL))
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="link state, firmware, battery").set_defaults(func=cmd_status)

    s = sub.add_parser("notify", help="show a notification on the watch")
    s.add_argument("title")
    s.add_argument("body", nargs="?", default="")
    s.add_argument(
        "--icon", type=int, default=0xFF, help="8 WhatsApp, 12 Telegram, 18 Instagram, 27 Gmail, 255 generic"
    )
    s.add_argument("--no-wait", action="store_true", help="queue and return immediately")
    s.set_defaults(func=cmd_notify)

    face = sub.add_parser("face", help="watch faces").add_subparsers(dest="face", required=True)
    s = face.add_parser("install", help="install an image as the custom photo face (replaces the current one)")
    s.add_argument("image")
    s.add_argument("--style", type=int, default=0, help="built-in clock style 0-4")
    s.add_argument("--x", type=int, default=56)
    s.add_argument("--y", type=int, default=77)
    s.add_argument("--color", default="FFFF", help="clock color, RGB565 hex")
    s.add_argument("-y", "--yes", action="store_true")
    s.set_defaults(func=cmd_face_install)

    s = sub.add_parser("pair", help="pair this Mac with the watch (after Nothing X has used it)")
    s.add_argument("-y", "--yes", action="store_true")
    s.set_defaults(func=cmd_pair)

    sub.add_parser("sync", help="fetch health data from the watch").set_defaults(func=cmd_sync)

    s = sub.add_parser("samples", help="stored health data: summary, or one metric's recent values")
    s.add_argument("metric", nargs="?", help="e.g. hr, steps_cum, spo2, stress, hr_resting")
    s.add_argument("--hours", type=float, default=24)
    s.set_defaults(func=cmd_samples)

    s = sub.add_parser("days", help="daily totals (steps, calories, HR, sleep)")
    s.add_argument("days", nargs="?", type=int, default=7)
    s.set_defaults(func=cmd_days)
    sub.add_parser("today", help="today's totals").set_defaults(func=cmd_days, days=1)
    sub.add_parser("reparse", help="re-run parsers over stored raw frames").set_defaults(func=cmd_reparse)

    s = sub.add_parser("jobs", help="recent jobs")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(func=cmd_jobs)

    s = sub.add_parser("events", help="recent events (buttons, link state, jobs)")
    s.add_argument("--kind", help="e.g. button, state, job")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(func=cmd_events)
    return p


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(Client(args.url, get_token(), transport), args)
