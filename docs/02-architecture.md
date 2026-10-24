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
| `ble` | Wrap cmfkit `session`; connect/pair/keepalive; expose async methods (`info`, `notify`, `install_face`, `set_widgets`, `fetch_activity`, `listen_buttons`) | cmfkit |
| `core` | Job queue (asyncio), job states (`queued/running/done/failed/deferred`), scheduler (sync on connect + every N min while connected) | ble, store |
| `sync` | Opcodes + parsers for steps/HR/stress/SpO2/resting HR/sleep/workouts. Always store **raw frames first**, parse second, so parsers can be re-run | ble, store |
| `store` | SQLite schema + migrations; idempotent upserts keyed by (metric, timestamp) | — |
| `render` | Face specs → HTML → PNG (Playwright) → RGB565 + dithering → face binary | cmfkit watchface |
| `api` | REST + websocket; bearer token stored in Keychain; binds 127.0.0.1 only | core, store, render |
| `mcp` | Tools below; calls the api, never BLE | api |
| `cli` | Thin client over api (`cmf status`, `cmf sync`, `cmf face push`, `cmf notify`) | api |
| `ui` (Phase 4) | Studio + Health dashboard | api |

## Data model (initial)
```sql
raw_frames(id, received_at, opcode, payload_hex, parser_version)
samples(metric TEXT, ts INTEGER, value REAL, source_frame_id, PRIMARY KEY(metric, ts))
sleep_sessions(start_ts, end_ts, stages_json, source_frame_id)
workouts(start_ts, end_ts, type, summary_json, source_frame_id)
daily(date, metric, value, PRIMARY KEY(date, metric))      -- derived, rebuildable
faces(id, name, spec_json, last_rendered_png, last_installed_at)
jobs(id, kind, args_json, state, attempts, created_at, finished_at, error)
events(id, ts, kind, data_json)                             -- buttons, connects, alerts
```
`daily` is always rebuildable from `samples`; `samples` from `raw_frames`.

## MCP tool surface (least privilege)
Allowed: `watch_status`, `sync_now`, `query_health(metric, start, end, agg)`, `notify(title, body)`, `render_face(spec)` (preview only), `install_face(face_id)` (requires confirm flag), `set_widgets(layout)` (requires confirm flag), `list_faces`.
**Not exposed, ever:** AT shell, firmware tools, pairing/reset, raw frame send.

## Connection lifecycle (Mac reality)
- Mac sleeps/closes → connection drops. On wake, watchd reconnects, runs backlog, syncs.
- User opens the laptop roughly daily → sync once per day minimum is the design target.
- Unknown: on-watch buffer length. Phase 2 measures it (leave Mac closed 1, 2, 3+ days, check for gaps).

## Security posture
- API binds 127.0.0.1, bearer token required (protects against local web pages hitting it).
- Keys in Keychain (`keyring`). Logs redact AES keys and pairing secrets.
- Claude API calls send aggregates by default, not raw minute-level health data.
- Third-party code isolated in `refs/`, pinned by commit hash in `refs/PINS.md`.
