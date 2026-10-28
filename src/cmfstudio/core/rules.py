"""Rules engine: when something happens (a sync finished, a job ended, a watch button was pressed), do a small,
fixed set of things. Rules live in ``rules.json`` next to watchd's database; without one, DEFAULT_RULES apply
(which keep the long-standing "refresh the face after each sync").

    {"rules": [
      {"on": "sync", "do": [{"action": "refresh_face"}]},
      {"on": "button", "button": "play_pause",
       "do": [{"action": "notify", "title": "Today", "body": "{steps} steps · RHR {hr_resting}"}]},
      {"on": "button", "button": "next", "do": [{"action": "sync"}]},
      {"on": "job", "kind": "install_face", "state": "failed",
       "do": [{"action": "notify", "title": "Face failed", "body": "{error}"}]}
    ]}

Least privilege: actions are a closed set (notify, sync, refresh_face, and running one of the owner's own macOS
Shortcuts by name). No shell commands, nothing Tier 2 or above. Rules are edited by the owner only: no API or MCP
tool writes them.

Buttons: the Pro 2 only reports music-control presses after the Mac has sent music info (findings 2026-10-25), so
while any button rule exists watchd sends "CMF Studio" as the track on every connect. Presses arrive while the
watch's music screen is open. Held volume keys repeat ~4-8/s, hence the per-rule cooldown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ..ble.buttons import BUTTONS
from ..store.db import Job, Store
from ..sync import daily
from .jobs import JobQueue

log = logging.getLogger(__name__)

ButtonName = Literal[tuple(sorted(set(BUTTONS.values())))]  # type: ignore[valid-type]
MUSIC_TRACK = "CMF Studio"


class Notify(BaseModel):
    action: Literal["notify"]
    title: str = Field(max_length=60)
    body: str = Field(default="", max_length=300)


class Sync(BaseModel):
    action: Literal["sync"]


class RefreshFace(BaseModel):
    action: Literal["refresh_face"]


class Shortcut(BaseModel):
    action: Literal["shortcut"]
    name: str = Field(min_length=1, max_length=100)


Action = Annotated[Notify | Sync | RefreshFace | Shortcut, Field(discriminator="action")]


class Rule(BaseModel):
    on: Literal["sync", "job", "button"]
    button: ButtonName | None = None  # on: button
    kind: str | None = None  # on: job (None = any kind except notify)
    state: Literal["done", "failed", "expired"] | None = None  # on: job (None = any final state)
    cooldown_s: float = Field(default=2.0, ge=0)
    do: list[Action] = Field(min_length=1)


class RuleSet(BaseModel):
    rules: list[Rule]


DEFAULT_RULES = RuleSet(rules=[Rule(on="sync", do=[RefreshFace(action="refresh_face")])])


def load(path: Path) -> tuple[RuleSet, str | None]:
    """The rules in ``path`` (DEFAULT_RULES when it doesn't exist), and an error if the file is invalid.

    An invalid file falls back to DEFAULT_RULES rather than half-applying it."""
    if not path.exists():
        return DEFAULT_RULES, None
    try:
        return RuleSet.model_validate(json.loads(path.read_text())), None
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        return DEFAULT_RULES, f"{path.name}: {exc}"


class _Missing(dict):
    def __missing__(self, key: str) -> str:
        return "-"


class RulesEngine:
    def __init__(
        self,
        store: Store,
        queue: JobQueue,
        path: Path,
        refresh_face: Callable[[], Awaitable[Any]],
        status: Callable[[], dict[str, Any]] = dict,
        run_shortcut: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.store = store
        self.queue = queue
        self.path = path
        self.refresh_face = refresh_face
        self.status = status
        self.run_shortcut = run_shortcut or _run_shortcut
        self.rules, self.error = load(path)
        self._last_fired: dict[int, float] = defaultdict(float)
        self._tasks: set[asyncio.Task] = set()

    def reload(self) -> dict[str, Any]:
        self.rules, self.error = load(self.path)
        self._last_fired.clear()
        if self.error:
            log.warning("rules: %s (using defaults)", self.error)
        if self.status().get("state") == "connected":
            self._enable_buttons()  # new button rules work now, not only after the next reconnect
        return self.describe()

    def describe(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "from_file": self.path.exists() and not self.error,
            "error": self.error,
            "rules": [r.model_dump(exclude_none=True) for r in self.rules.rules],
        }

    @property
    def wants_buttons(self) -> bool:
        return any(r.on == "button" for r in self.rules.rules)

    # -- triggers (wired to the link's events and the queue's on_done) ------------------------------------------

    def on_link_event(self, event: dict[str, Any]) -> None:
        if event["kind"] == "state" and event.get("state") == "connected":
            self._enable_buttons()
        elif event["kind"] == "button":
            self._fire(lambda r: r.on == "button" and r.button == event["name"], {"button": event["name"]})

    def _enable_buttons(self) -> None:
        if self.wants_buttons:
            self.queue.submit("set_music", {"track": MUSIC_TRACK, "artist": "buttons ready"}, ttl_s=60)

    def on_job_done(self, job: Job) -> None:
        if job.kind == "sync" and job.state == "done":
            self._fire(lambda r: r.on == "sync", {})
        if job.kind == "notify":  # rules send notifications; never let them trigger more rules
            return
        self._fire(
            lambda r: r.on == "job" and r.kind in (None, job.kind) and r.state in (None, job.state),
            {"job": job.kind, "state": job.state, "error": job.error or ""},
        )

    # -- actions --------------------------------------------------------------------------------------------------

    def _fire(self, match: Callable[[Rule], bool], context: dict[str, Any]) -> None:
        now = time.monotonic()
        for i, rule in enumerate(self.rules.rules):
            if not match(rule):
                continue
            if self._last_fired[i] and now - self._last_fired[i] < rule.cooldown_s:
                continue
            self._last_fired[i] = now
            self.store.add_event("rule", {"rule": i, "on": rule.on, **context})
            task = asyncio.ensure_future(self._run(rule, context))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run(self, rule: Rule, context: dict[str, Any]) -> None:
        for action in rule.do:
            try:
                await self._act(action, context)
            except Exception as exc:
                log.warning("rule action %s failed: %r", action.action, exc)
                self.store.add_event("rule", {"action": action.action, "error": repr(exc)})

    async def _act(self, action: Action, context: dict[str, Any]) -> None:
        if isinstance(action, Notify):
            values = self.values(context)
            self.queue.submit(
                "notify",
                {"title": action.title.format_map(values), "body": action.body.format_map(values)},
                ttl_s=600,
            )
        elif isinstance(action, Sync):
            self.queue.submit("sync", {}, ttl_s=3600)
        elif isinstance(action, RefreshFace):
            await self.refresh_face()
        elif isinstance(action, Shortcut):
            await self.run_shortcut(action.name)

    def values(self, context: dict[str, Any]) -> _Missing:
        """Placeholders for notify text: today's daily totals, battery, and the trigger's own fields."""
        today = next(iter(daily.read(self.store, 1).values()), {})
        out = _Missing(context)
        out.update(
            steps=_num(today.get("steps")),
            kcal=_num(today.get("calories")),
            km=f"{today['distance_m'] / 1000:.1f}" if "distance_m" in today else "-",
            hr_resting=_num(today.get("hr_resting")),
            sleep=_hours(today.get("sleep_s")),
            battery=_num(self.status().get("battery")),
        )
        return out

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()


def _num(v: float | None) -> str:
    return "-" if v is None else f"{v:,.0f}"


def _hours(s: float | None) -> str:
    return "-" if not s else f"{int(s // 3600)}h{int(s % 3600 // 60):02d}"


async def _run_shortcut(name: str) -> None:
    """Run one of the owner's macOS Shortcuts by name (argument list, no shell)."""
    proc = await asyncio.create_subprocess_exec(
        "shortcuts", "run", name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, err = await asyncio.wait_for(proc.communicate(), 60)
    if proc.returncode:
        raise RuntimeError(f"shortcut {name!r} exited {proc.returncode}: {err.decode(errors='replace')[:200]}")
