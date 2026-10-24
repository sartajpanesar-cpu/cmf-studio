# 05 — Claude Code prompts

Paste one phase at a time. Each assumes Claude Code has read `CLAUDE.md`.

---

## Phase 0 — Validate
```
Read CLAUDE.md, docs/04-safety-policy.md, and Phase 0 in docs/03-roadmap.md.

1. Check refs/ (created by scripts/bootstrap.sh). Record each repo's commit hash in refs/PINS.md.
2. Audit refs/CMF-Open (cmfkit): list every network call, file write, and every CLI command that writes to the watch. Write the summary to research/findings.md.
3. Set up cmfkit in its own uv environment and run its test suite and the --sim examples from its README. Report results.
4. Write scripts/phase0_live.sh that runs, one step at a time with a pause between: scan, pair, info, listen (30s), notify "CMF Studio" "hello", then installs ONE image face built from scripts/assets/test_face.png (generate a simple 466x466 test image with a clock-free design if it doesn't exist). Log every step with -vv to research/logs/.
5. Do NOT run the live script. Tell me when it's ready; I'll turn off my iPhone's Bluetooth and run it myself.
```

After you run it, paste the output back:
```
Here is the phase0_live.sh output and logs in research/logs/. Analyze them, update research/findings.md with verified facts (button codes, firmware version, what worked/failed), and tick the Phase 0 ACs that passed. If anything failed, compare the frames to refs/CMF-Watch-Pro-2-BLE-Protocol and propose a fix.
```

## Phase 1 — watchd skeleton
```
Phase 0 passed. Start Phase 1 per docs/03-roadmap.md and docs/02-architecture.md.
Create the src/cmfstudio package (uv project, ruff, pytest, GitHub Actions running sim-only tests). Build ble (wrapping cmfkit with reconnect + keepalive), core (asyncio job queue with offline backlog), store (SQLite schema + migrations from the architecture doc), api (FastAPI on 127.0.0.1:8765, bearer token in macOS Keychain via keyring), and a thin `cmf` CLI. Add a launchd plist + install script. Every device-writing endpoint needs a simulator test. Work in small commits prefixed "P1:". Stop and ask before anything touches the real watch.
```

## Phase 2 — Health sync
```
Start Phase 2. First, clone Gadgetbridge into refs/ (read-only) and locate the Nothing/CMF device support code. Produce docs/protocol-sync.md: every sync-related opcode, request sequence, and payload layout (steps, HR, stress, SpO2, resting HR, sleep, workouts), each with file:line references. Then port the parsers to src/cmfstudio/sync/, storing raw frames before parsing. Write parser tests from the Java logic first; once I approve, run a real sync and save captured frames as fixtures. Note Gadgetbridge's known sleep mismatch and don't assume its sleep parsing is right.
```

## Phase 3 — Face pipeline
```
Start Phase 3 using docs/06-studio-design-pipeline.md. First read cmfkit's watchface encoder and docs/08-watch-faces.md and write down exactly what the face format supports natively (static image only? dynamic time digits? data fields? fonts?) into the design doc. Then build render/: JSON face spec → HTML template → Playwright PNG at 466x466 → RGB565 with ordered dithering → cmfkit encoder. Add a round-mask preview PNG. Show me 3 sample faces as previews before installing any.
```

## Phase 4 — Studio UI
```
Start Phase 4. Use the frontend-design skill if available. Build a Svelte + Vite app served by watchd: (1) Studio — layer editor (text/image/shape/data binding), font picker (Google Fonts + local .ttf), live round 466x466 preview using the real render pipeline, save/install; (2) Health — trends with baselines and anomaly flags from the daily table. Dark, minimal, AMOLED-aware design. Add a "describe a face" box that calls the Claude API (key from Keychain) to produce a face spec, then previews it.
```

## Phase 5 — MCP + automations
```
Start Phase 5. Implement the MCP server with exactly the tool surface in docs/02-architecture.md — nothing more. Write install-face and set-widgets with a required confirm flag. Register it with Claude Code. Then build the rules engine (on-sync re-render, job-done notify, button→action) and a Claude Code hook that pushes session-complete and permission-waiting alerts to the watch. Finish with docs/threat-model.md covering the agent→watch trust boundary.
```

## Phase 6 — Firmware research (read-only)
```
Start Phase 6. Read-only research — never send anything to the watch. Using the stock OTA in fw/ (gitignored), run cmfkit fw info and report prominently whether boot images are signed. Then fw extract and fw scan; document fonts.bin and res.bin formats in docs/firmware-notes.md. If REA is configured, use it with Ghidra on app.bin to map the screen/app table and font loading. Separately write a converter from Apple PacketLogger .pklg to btsnoop so cmfkit snoop can decode my iPhone captures.
```
