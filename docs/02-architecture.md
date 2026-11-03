# 02 — Architecture

## Principle
The watch accepts **one BLE connection**. So exactly one long-running process — `watchd` — owns Bluetooth. Everything else (UI, CLI, MCP, automations) is a client of watchd's localhost API. BLE operations go through a serialized job queue; jobs submitted while the watch is away run on the next connect.

```
                 ┌──────────────────────── watchd (launchd agent) ───────────────────────┐
 Watch ◀─BLE─▶   │ ble/      session mgr (pair, reconnect, keepalive) ← cmfkit protocol  │
                 │ core/     job queue (serialized BLE ops, retries, offline backlog)    │
                 │ sync/     activity sync (port of Gadgetbridge) → raw frames + parsed  │
                 │ store/    SQLite: raw_frames, samples, daily, faces, jobs, events     │
                 │ render/   HTML/CSS → Playwright → 466×466 RGB565 → cmfkit face encoder│
                 │ api/      FastAPI on 127.0.0.1:8765 (token-auth) + websocket events   │
                 │ mcp/      MCP server exposing a narrow tool set                       │
                 └──────────┬──────────────┬─────────────────┬────────────────────────────┘
                            │              │                 │
                     Studio UI (Svelte)   CLI (`cmf …`)   Claude Code / Desktop (MCP)
                     dashboard, designer   scripts, cron    natural-language control
```

## Modules
| Module | Responsibility | Depends on |
|---|---|---|
| `ble` | Wrap cmfkit `session`; connect/keepalive/reconnect; photo and structured face installs, settings GET/SET, weather, find watch, button / find-phone events; `sim.py` = the Pro 2 simulator | cmfkit |
| `core` | Job queue, scheduler, rules engine, settings (verified read-back writes), weather + glance, Mac music, find phone, Claude client | ble, store |
| `sync` | Opcodes + parsers for steps/HR/stress/SpO2/resting HR/sleep/workouts. Always store **raw frames first**, parse second, so parsers can be re-run | ble, store |
| `store` | SQLite schema + migrations; idempotent upserts keyed by (metric, timestamp) | — |
| `render` | Photo faces: spec → HTML → PNG (Playwright) → RGB565 + dithering → dial. Native faces (`native*.py`): structured-dial codec, font glyphs, scene preview | cmfkit watchface |
| `api` | REST + websocket; bearer token stored in Keychain; binds 127.0.0.1 only | core, store, render |
| `mcp` | Tools below; calls the api, never BLE | api |
| `cli` | Thin client over api (`cmf status`, `cmf sync`, `cmf face push`, `cmf notify`) | api |
| `ui` | Health, Studio (one editor: photo faces with a built-in clock, or native faces with your font), Settings, Watch | api |

## Data model (as implemented in `store/db.py`, migration 1)
```sql
raw_frames(id, received_at, opcode, payload_hex, parser_version)
samples(metric TEXT, ts INTEGER, value REAL, source_frame_id, PRIMARY KEY(metric, ts))
sleep_sessions(start_ts, end_ts, stages_json, source_frame_id)
workouts(start_ts, end_ts, type, summary_json, source_frame_id)
daily(date, metric, value, PRIMARY KEY(date, metric))      -- derived, rebuildable
faces(id, name, spec_json, last_rendered_png, last_installed_at)
jobs(id, kind, args_json, state, attempts, created_at, finished_at, error, result_json, not_after)  -- not_after: drop stale jobs (e.g. a notification) instead of running them days later
events(id, ts, kind, data_json)                             -- buttons, connects, alerts
```
`daily` is always rebuildable from `samples`; `samples` from `raw_frames`.

## MCP tool surface (least privilege)
Allowed (as built, `mcp/server.py`; rationale in `docs/07-threat-model.md` §3.5): `watch_status`, `sync_now`,
`query_health(metric, start, end, agg)` (aggregates only), `notify(title, body)`, `render_face(spec)` (preview only),
`list_faces`, `install_face(face_id, confirm)`, `get_watch_settings`, `set_alarm` / `remove_alarm` (one alarm at a
time), `set_goals`, `set_do_not_disturb`, `find_watch`. (`set_widgets` was dropped with the widget editor.)
**Not exposed, ever:** AT shell, firmware tools, pairing/reset, raw frame send, raw settings bytes, rules.

## Connection lifecycle (Mac reality)
- Mac sleeps/closes → connection drops. On wake, watchd reconnects, runs backlog, syncs.
- User opens the laptop roughly daily → sync once per day minimum is the design target.
- Unknown: on-watch buffer length. Phase 2 measures it (leave Mac closed 1, 2, 3+ days, check for gaps).

## Security posture
- API binds 127.0.0.1, bearer token required (protects against local web pages hitting it).
- Keys in Keychain (`keyring`). Logs redact AES keys and pairing secrets.
- Claude API calls send aggregates by default, not raw minute-level health data.
- Third-party code isolated in `refs/`, pinned by commit hash in `refs/PINS.md`.
