# CMF Studio

Mac-side companion, design studio, and health-analytics stack for the **CMF Watch Pro 2** (model D398, Actions ATS3089C, Zephyr RTOS + LVGL).
Goal: the MacBook replaces the phone app — sync health data, design faces/fonts/graphics, push widgets and alerts, and drive the watch from Claude via MCP.

> Status: Phase 0 done (GO) and Phase 1 done (watchd running as a launchd agent), 2026-10-25. Next: Phase 2, health sync. See `docs/03-roadmap.md`.

## Quick start (after setup below)
```bash
uv sync
uv run watchd            # owns Bluetooth; or `uv run watchd --sim` for the simulated watch
uv run cmf status        # in another terminal
uv run cmf pair          # first run, or after Nothing X has used the watch (accept on the watch)
uv run cmf notify "Hello" "from the Mac"
bash scripts/launchd.sh install   # run watchd at login, restart on exit
```

## Folder map

| Path | What |
|---|---|
| `CLAUDE.md` | Standing instructions for Claude Code (read automatically) |
| `.claude/settings.json` | Permission rules — hard-blocks firmware/flash commands |
| `docs/01-research-summary.md` | What's known about the watch, the protocol, and prior work, with sources |
| `docs/02-architecture.md` | watchd daemon + API + Studio UI + MCP design |
| `docs/03-roadmap.md` | Phases with acceptance criteria |
| `docs/04-safety-policy.md` | Risk tiers, what may never be automated |
| `docs/05-claude-code-prompts.md` | Copy-paste prompts, one per phase |
| `docs/06-studio-design-pipeline.md` | Face / font / graphics rendering spec |
| `research/findings.md` | Running evidence log (Claude Code appends here) |
| `scripts/bootstrap.sh` | Clones reference repos, sets up Python env |
| `scripts/check_env.sh` | Verifies toolchain + Bluetooth permissions |

## Setup — do these in order (≈45 min)

### 1. Back up before anything touches the watch
- [ ] Open Nothing X on the iPhone → sync fully. If it offers Apple Health sync, turn it on and let it backfill (this is your only export of history).
- [ ] Screenshot your current watch face / widget layout.
- [ ] Note the firmware version (watch: Settings → About). Then **turn off automatic firmware updates** in Nothing X — a known, frozen version is what you want.

### 2. Mac toolchain
```bash
# Homebrew (skip if installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install uv git node gh
uv python install 3.12
```

### 3. Bluetooth permission (most common failure)
System Settings → Privacy & Security → **Bluetooth** → enable your terminal app (Terminal / iTerm / VS Code). Without this, every BLE scan silently returns nothing.

### 4. Put this folder in place and bootstrap
```bash
mv ~/Downloads/cmf-studio ~/cmf-studio   # sits next to ~/GoveeLights
cd ~/cmf-studio
git init && git add -A && git commit -m "Project docs"
bash scripts/bootstrap.sh
bash scripts/check_env.sh
```

### 5. Optional now, needed by Phase 2 research
- [ ] REA (reverse-engineering MCP for Claude Code): `npx rea-agents setup` — **only** from github.com/morluto/rea / rea.tools / npm `rea-agents` (trojaned copies exist).
- [ ] Ghidra: `brew install --cask ghidra` (needs a JDK: `brew install --cask temurin@21`).
- [ ] Xcode **Additional Tools** (PacketLogger) from developer.apple.com/download/all + the **iOS Bluetooth logging profile** — for capturing Nothing X's BLE traffic from the iPhone.

### 6. Start Claude Code
```bash
cd ~/cmf-studio && claude
```
Paste the **Phase 0** prompt from `docs/05-claude-code-prompts.md`. Before running Phase 0's live steps: turn off the iPhone's Bluetooth (the watch accepts one connection at a time).

## Rules of the road
- Simulator before hardware, read before write, never flash. Details in `docs/04-safety-policy.md`.
- The iPhone pairing is the fallback. If the Mac pairing breaks, re-pair with Nothing X.
- Don't use Nothing/CMF logos or names in anything you publish beyond describing compatibility.
