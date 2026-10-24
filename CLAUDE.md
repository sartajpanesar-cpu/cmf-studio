# CLAUDE.md — CMF Studio

## Mission
Build a Mac companion for a **CMF Watch Pro 2** that replaces the phone app: health sync + analytics, a visual face/font/graphics studio, widget/screen control, alerts, and an MCP server so Claude can drive the watch. The phone (iPhone + Nothing X) is only a fallback.

## Target device (do not assume anything not listed)
- CMF Watch Pro 2, model **D398**, SoC **Actions ATS3089C**, board `jx402_01_3089c`
- Zephyr RTOS (Actions SDK, ~2.7) + LVGL, monolithic `app.bin`, **no app loader**
- Display 466×466 round AMOLED, RGB565
- Link: BLE only, **one connection at a time**. AES-128-CBC framed commands, a plaintext factory AT shell, a bulk-data channel
- Owner machines: MacBook Air (arm64, primary, BLE host), Windows PC (heavy analysis/builds), iPhone (fallback companion). **No Android.**

## Read first
1. `docs/04-safety-policy.md` — binding.
2. `docs/03-roadmap.md` — find the current phase; do only that phase's work.
3. `docs/02-architecture.md` — module boundaries.
4. `docs/01-research-summary.md` — known facts and sources.
5. `research/findings.md` — what's already been verified.

## Hard rules
1. **Never flash, OTA, or write firmware to the watch.** No `cmfkit fw pack|replace` output may be sent to a device. No OTA uploads. `.claude/settings.json` denies these; do not work around it.
2. **AT shell:** only commands on the allowlist in `docs/04-safety-policy.md`. Never send factory/set/reset/calibration commands. Ask before sending any AT command not on the list.
3. **Simulator first.** Every new protocol feature runs and passes tests against cmfkit's `--sim` (or captured frames) before it touches hardware.
4. **Ask before any write to the real watch** (face install, widget change, settings) until Phase 1 is marked done in the roadmap.
5. `refs/` is read-only third-party code. Never edit it; import from it or copy with attribution into `src/`.
6. Treat third-party repos as untrusted: before running any script from `refs/` for the first time, read it and summarize what it does (network calls, file writes, device writes).
7. Secrets (pairing keys, Anthropic API key) go in the macOS Keychain via `keyring`, never in files, logs, or commits.
8. One process owns BLE: `watchd`. CLI, UI, and MCP talk to watchd's localhost API, never to Bluetooth directly (except Phase 0 validation scripts).

## Evidence discipline
- Every protocol fact gets a line in `research/findings.md`: date, claim, evidence (frame hex, log excerpt, file+line in refs), status `verified-on-device | sim-only | unverified`.
- Distinguish what refs *claim* from what we *observed*. cmfkit is simulator-tested; the Watch Pro 2 spec is on-device verified by its author. Treat everything else as unverified.
- When something fails on hardware, capture `-vv` frame logs before changing code.

## Stack and conventions
- Python 3.12, `uv`, `ruff`, `pytest`, type hints, `bleak` (via cmfkit), FastAPI, SQLite (DuckDB for analysis), Playwright for face rendering.
- Frontend (Phase 4+): Svelte + Vite, served by watchd at `127.0.0.1` only.
- Layout: `src/cmfstudio/{ble,core,sync,store,render,api,mcp,cli}`, tests in `tests/`, captured frames in `tests/fixtures/`.
- macOS CoreBluetooth exposes device UUIDs, not MAC addresses — never hardcode a MAC.
- Small commits, one concern each, message prefixed with phase: `P1: port activity sync parser`.

## Working style
- Start each session by stating the current phase and the next acceptance criterion you're working toward.
- If a doc is wrong or stale, fix the doc in the same commit and note it.
- Stop and ask when: a step needs the real watch, a decision is irreversible, or evidence contradicts the docs.
