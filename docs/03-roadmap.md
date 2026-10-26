# 03 — Roadmap

Each phase has acceptance criteria (AC). Don't start a phase until the previous one's ACs are checked. Mark them `[x]` in this file as they're met, with a findings.md reference.

---

## Phase 0 — Validate the base (go/no-go) · ~1 evening
**Status: DONE 2026-10-25 — GO.** cmfkit talks to this Pro 2 (fw 1.0.0.57) from the MacBook Air.
**Question:** does cmfkit actually talk to this Pro 2 from this Mac?
- [x] Reference repos cloned into `refs/`, commit hashes pinned in `refs/PINS.md` (findings 2026-10-24)
- [x] Audit summary of cmfkit written to `research/findings.md` (network calls, file writes, which commands write to the device) (findings: cmfkit audit)
- [x] `cmfkit --sim` commands pass; `uv run pytest` in refs/CMF-Open passes (findings 2026-10-24: 28 passed)
- [x] Real watch (iPhone BT off): `scan` finds it, `pair` succeeds, `info` returns firmware/serial/battery (findings: live run 1 — fw 1.0.0.57)
- [x] `listen` prints button codes; codes recorded in findings (run 4; volume direction to confirm)
- [x] `notify` delivers a test message (run 5; link must stay up after sending)
- [x] **One** image face installs and displays; watch still re-pairs with Nothing X afterward (fallback confirmed) (run 4; watch showed a spurious 'failed' message — see findings)

**No-go handling:** if pairing/info fails, capture `-vv` logs, compare frames against the joshuapassos spec, fix in a fork. If unfixable, fall back to porting Gadgetbridge's protocol layer directly.

## Phase 1 — watchd skeleton · ~1 weekend
**Status: DONE 2026-10-25.** watchd runs as a launchd agent, holds the link (FIRMWARE_VERSION_GET keepalive, TIME every 15 min),
reconnects after Bluetooth off/on and after sleep, and serves the CLI. 50 sim tests. Follow-ups carried into Phase 2:
~~nudge the link on Bluetooth power-on~~ (done: 2 s polling while Bluetooth is off); push to GitHub so CI runs. Notes: the watch keeps one pairing — if Nothing X connects,
run `uv run cmf pair`. Agent: `bash scripts/launchd.sh status|logs|uninstall`.

- [x] `src/cmfstudio` package, uv project, ruff + pytest + CI (GitHub Actions, sim-only tests) (workflow written; no GitHub remote yet, so only run locally)
- [x] `ble` wrapper over cmfkit with reconnect + keepalive (verified on device 2026-10-25: keepalive holds, reconnect after Mac BT off/on)
- [x] `core` job queue with offline backlog
- [x] `store` schema + migrations
- [x] FastAPI on 127.0.0.1 with Keychain token; `/status`, `/notify`, `/faces/install` (+ `/pair`, `/jobs`, `/events`)
- [x] `cmf` CLI over the API
- [x] launchd plist; survives Mac sleep/wake and reconnects automatically (verified 2026-10-25: reconnect 1.8 s after an 11-min sleep; needed wake detection + connecting to the peripheral macOS already holds)
- [x] All device-writing endpoints covered by sim tests (tests/test_api.py, test_jobs.py, test_ble.py)

## Phase 2 — Health sync (critical path) · 1–2 weekends
**Status: DONE 2026-10-26** (owner decision to move on with two items deferred). watchd auto-syncs after each connect and
hourly; daily totals; `cmf sync | samples | today | days | reparse`; real-frame fixtures stay local. One day cross-checked against
the watch screens by the owner: identical. **Deferred:** buffer length (leave the Mac closed 1–3 days and look for gaps); it can be checked any time without code.

- [x] Map Gadgetbridge's CMF sync code (opcodes, request sequence, payload layouts) into `docs/protocol-sync.md` with file/line refs (2026-10-26, cross-checked with spec §8)
- [x] Port parsers to `sync/`; raw frames stored before parsing (written from docs/protocol-sync.md, not translated from Gadgetbridge's AGPL code)
- [x] Fixtures: real captured frames in `tests/fixtures/`, parser tests pass (owner decision 2026-10-26: real frames live in gitignored `tests/fixtures/private/`, exported by `scripts/export_fixtures.py`; tests skip without them, so CI uses synthetic frames)
- [x] Steps, HR, stress, SpO2, resting HR land in `samples` from the real watch (steps, HR, resting HR ✓ 2026-10-25; stress ✓ and SpO2 ✓ 2026-10-26)
- [x] Values cross-checked against the watch's own screens for one day (document discrepancies) (2026-10-26, owner: identical; calories are kcal)
- [x] Sleep: implement, then document how it differs from the watch (known Gadgetbridge bug) (2026-10-26: no difference for the first night; see findings)
- [ ] Measure on-watch buffer length (findings) (**deferred** by owner; needs 1–3 days with the Mac closed)
- [x] Re-sync is idempotent (no duplicate samples) (upsert on (metric, ts) + watch-side cursor; verified on device 2026-10-25)

## Phase 3 — Face pipeline · ~1 weekend
**Status: DONE 2026-10-26.** Photo-dial faces from JSON specs render, install and refresh hourly on the real watch
(`cmf face preview|use|list`, `cmf insight`; example `faces/deco-steps.json`). Optional extension, not yet started:
structured-dial re-skins for up to 7 custom faces with native live fields (docs/06 "Next").
See `06-studio-design-pipeline.md`.
- [x] Face spec format (JSON) + HTML template renderer (`render/spec.py`, `render/html.py`)
- [x] Playwright render → 466×466 PNG → RGB565 with dithering → cmfkit encoder (installed on the watch 2026-10-26)
- [x] Round-mask preview + AMOLED black check (`cmf face preview`)
- [x] Data bindings refresh on each sync (steps, date, resting HR, "insight" text) (verified on device: jobs 41–43)
- [x] Any Google Font / local .ttf works (Google: Poiret One on device; local .ttf/.otf: tests with a not-loaded control)
- [x] Document what the face format supports natively (live time digits? which fields?) vs baked-in (docs/06 table)

## Phase 4 — Studio + Health UI · 2–3 weekends
**Status: DONE 2026-10-26** (owner: "we can move on"). App served by watchd (`uv run cmf ui`): Health (trends, baselines,
anomaly flags), Studio (live preview ~80 ms, drag/snap/centre, measured firmware clock overlay, Claude design + edit),
Watch. Notes: weekly Claude recap built and tested with a fake client but not run live yet; widgets editor deferred.

- [x] Svelte app served by watchd (`cmf ui`; token via URL fragment)
- [x] Studio: layers (text/image/shape/data), font picker, color, live round preview, save/install
- [x] "Describe a face" → Claude generates HTML/CSS → preview → install (Claude returns a design converted to a validated FaceSpec, not raw HTML; edits the current design; verified live 2026-10-26)
- [x] Health: trends, baselines, anomaly flags, weekly Claude summary (aggregates only) (weekly recap: tested with a fake client; first live run still to do)
- [ ] Widgets editor (port Gadgetbridge widget setup) (**deferred** by owner 2026-10-26: Gadgetbridge has no CMF widget code to port; only the QUICK_CARD opcode FFFF 906D/A06D is known. Revisit with new evidence)

## Backlog (owner priorities)
1. **Custom time font** (owner: "I really want to change the time font"). Photo dials can't: the clock is one of 5
   firmware styles (findings 2026-10-26). Path: structured dials — re-skin a store dial's digit sprite atlas
   (`img_number`, spec §11.6/§11.10) with digits rendered in any font; live time, up to 7 slots. Needs a container
   editor (two header CRC32s, never grow the file, fresh install) and store dial binaries (spec repo
   `wfweb/public/templates` has 5). Firmware font replacement is Tier 3 — not this.
2. Widgets / quick-card editor (deferred: no Gadgetbridge code; only opcode FFFF 906D known).
3. Measure on-watch buffer length (Phase 2 deferred).

## Phase 5 — MCP + automations · ~1 weekend
- [x] MCP server with the tool surface in `02-architecture.md`; registered in Claude Code (`.mcp.json` → `cmf-mcp`;
  verified over stdio against the real watchd 2026-10-26)
- [ ] Rules engine: on-sync → re-render face; job-done → notify; button → action
- [x] Claude Code hooks → watch alerts (oh-my-wrist pattern): `cmf-hook` in `.claude/settings.json`. Turns of
  ≥ 60 s (`CMF_HOOK_MIN_S`) and permission/idle prompts alert the wrist; 5 min TTL, never blocks Claude.
  Pipe-tested against the real watch 2026-10-26 (job acked)
- [ ] Threat model + least-privilege write-up (portfolio artifact)

## Phase 6 — Firmware research (read-only, optional) · open-ended
- [ ] Obtain stock OTA for the current firmware version
- [ ] `cmfkit fw info` → **are boot images signed?** Record result prominently
- [ ] `fw extract`, `fw scan`; inventory fonts.bin / res.bin formats
- [ ] iPhone PacketLogger capture of a Nothing X sync → convert to btsnoop → decode
- [ ] REA + Ghidra on app.bin: screen/app table, font loader
- [ ] Build `cmf_hello` against the Actions SDK (compile only)
- [ ] Write-up: "Firmware update integrity assessment of a consumer wearable"

## Phase 7 — Firmware mods (brick risk, gated)
Only if Phase 6 shows images are **unsigned**, and only with: second test unit, SWD pads located, ST-Link/J-Link on hand. First mod = one asset swap in res.bin on the test unit. Requires explicit owner approval per flash. Never on the daily watch until proven on the test unit.
