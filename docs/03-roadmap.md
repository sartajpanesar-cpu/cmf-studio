# 03 — Roadmap

Each phase has acceptance criteria (AC). Don't start a phase until the previous one's ACs are checked. Mark them `[x]` in this file as they're met, with a findings.md reference.

---

## Phase 0 — Validate the base (go/no-go) · ~1 evening
**Question:** does cmfkit actually talk to this Pro 2 from this Mac?
- [x] Reference repos cloned into `refs/`, commit hashes pinned in `refs/PINS.md` (findings 2026-10-24)
- [x] Audit summary of cmfkit written to `research/findings.md` (network calls, file writes, which commands write to the device) (findings: cmfkit audit)
- [x] `cmfkit --sim` commands pass; `uv run pytest` in refs/CMF-Open passes (findings 2026-10-24: 28 passed)
- [x] Real watch (iPhone BT off): `scan` finds it, `pair` succeeds, `info` returns firmware/serial/battery (findings: live run 1 — fw 1.0.0.57)
- [ ] `listen` prints button codes; codes recorded in findings
- [ ] `notify` delivers a test message
- [ ] **One** image face installs and displays; watch still re-pairs with Nothing X afterward (fallback confirmed)

**No-go handling:** if pairing/info fails, capture `-vv` logs, compare frames against the joshuapassos spec, fix in a fork. If unfixable, fall back to porting Gadgetbridge's protocol layer directly.

## Phase 1 — watchd skeleton · ~1 weekend
- [ ] `src/cmfstudio` package, uv project, ruff + pytest + CI (GitHub Actions, sim-only tests)
- [ ] `ble` wrapper over cmfkit with reconnect + keepalive
- [ ] `core` job queue with offline backlog
- [ ] `store` schema + migrations
- [ ] FastAPI on 127.0.0.1 with Keychain token; `/status`, `/notify`, `/faces/install`
- [ ] `cmf` CLI over the API
- [ ] launchd plist; survives Mac sleep/wake and reconnects automatically
- [ ] All device-writing endpoints covered by sim tests

## Phase 2 — Health sync (critical path) · 1–2 weekends
- [ ] Map Gadgetbridge's CMF sync code (opcodes, request sequence, payload layouts) into `docs/protocol-sync.md` with file/line refs
- [ ] Port parsers to `sync/`; raw frames stored before parsing
- [ ] Fixtures: real captured frames in `tests/fixtures/`, parser tests pass
- [ ] Steps, HR, stress, SpO2, resting HR land in `samples` from the real watch
- [ ] Values cross-checked against the watch's own screens for one day (document discrepancies)
- [ ] Sleep: implement, then document how it differs from the watch (known Gadgetbridge bug)
- [ ] Measure on-watch buffer length (findings)
- [ ] Re-sync is idempotent (no duplicate samples)

## Phase 3 — Face pipeline · ~1 weekend
See `06-studio-design-pipeline.md`.
- [ ] Face spec format (JSON) + HTML template renderer
- [ ] Playwright render → 466×466 PNG → RGB565 with dithering → cmfkit encoder
- [ ] Round-mask preview + AMOLED black check
- [ ] Data bindings refresh on each sync (steps, date, resting HR, "insight" text)
- [ ] Any Google Font / local .ttf works
- [ ] Document what the face format supports natively (live time digits? which fields?) vs baked-in

## Phase 4 — Studio + Health UI · 2–3 weekends
- [ ] Svelte app served by watchd
- [ ] Studio: layers (text/image/shape/data), font picker, color, live round preview, save/install
- [ ] "Describe a face" → Claude generates HTML/CSS → preview → install
- [ ] Health: trends, baselines, anomaly flags, weekly Claude summary (aggregates only)
- [ ] Widgets editor (port Gadgetbridge widget setup)

## Phase 5 — MCP + automations · ~1 weekend
- [ ] MCP server with the tool surface in `02-architecture.md`; registered in Claude Code
- [ ] Rules engine: on-sync → re-render face; job-done → notify; button → action
- [ ] Claude Code hooks → watch alerts (oh-my-wrist pattern)
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
