# 07 — Threat model and least privilege

CMF Studio lets a Mac, a web UI, Claude Code (via MCP and hooks) and a rules engine all drive a CMF Watch Pro 2. That
is a lot of hands on one wrist-worn device that holds health data and, through the vendor protocol, could be bricked.
This document shows what we protect, from whom, and what each design choice buys. Claims about the watch are
cross-referenced to `research/findings.md` and tagged the way that log tags them.

## 1. What we protect

| Asset | Why it matters | Where it lives |
|---|---|---|
| **The watch's firmware integrity** | A bad OTA or flash bricks the watch; there is no recovery without SWD hardware | On the watch |
| **The pairing key** (per-device AES key) | Anyone holding it can log in as the Mac and send commands | macOS Keychain, `cmf-studio` / `pairing:<uuid>` |
| **Health data** (HR, sleep, steps, SpO₂, stress) | Personal and sensitive; the owner decided it stays local | SQLite in `~/Library/Application Support/cmf-studio/` |
| **watchd's API token** | Grants everything the API grants | Keychain, `cmf-studio` / `api-token` |
| **Anthropic API key** | Billing, and a channel off the machine | Keychain, `anthropic-api-key` |
| **Owner's Mac** (files, other apps) | Any automation that runs code or reads files is a foothold | — |
| **The watch's single custom face slot and the owner's attention** | Not dangerous, but writes and buzzes must be wanted | On the watch |

## 2. Architecture and trust boundaries

```
                 ┌──────────── owner's Mac (one macOS user) ─────────────────────────────┐
  Anthropic API ◄┤ watchd ── /faces/describe, /insight, /health/summary (aggregates only) │
                 │   │                                                                     │
  BLE (one link) ◄┤ watchd ── WatchLink (only BLE owner) ── JobQueue ── RulesEngine        │
       ▲          │   ▲ HTTP 127.0.0.1:8765, bearer token, TrustedHost                     │
       │          │   ├── cmf CLI          ├── Svelte UI (/ui)                             │
   the watch      │   ├── cmf-mcp ◄─ stdio ─ Claude Code / Desktop (model-driven)          │
                  │   └── cmf-hook ◄─ Claude Code hooks (event-driven)                     │
                  │ Playwright/Chromium: renders face HTML (scripts off, fonts-only egress) │
                  └────────────────────────────────────────────────────────────────────────┘
```

Boundaries, from least to most trusted input:

1. **Radio** — anything in BLE range.
2. **The model** — Claude acting through MCP. Its inputs include whatever it has read (web pages, files), so it can be
   steered by prompt injection; treat its tool calls as untrusted requests with the owner's authority.
3. **Local processes** under the same user — can reach 127.0.0.1, but don't hold the token unless they read the
   Keychain.
4. **The owner** — CLI, UI, hand-edited `rules.json`.

## 3. Threats and mitigations

### 3.1 Bricking the watch (Tier 3)
- **Threat:** firmware/OTA upload from an automation, a model, or a mistaken script.
- **Mitigation:** no code path in `src/` can send firmware: the BLE layer exposes no OTA or flash operation, the
  MCP surface has no firmware tool, and `.claude/settings.json` *denies* `cmfkit fw pack|replace`, OTA uploads and SWD
  tools for Claude Code itself. The policy (`docs/04-safety-policy.md`) makes Tier 3 owner-only, by hand, test unit
  only.
- **Residual:** cmfkit (third-party, pinned at a commit) can do it if the owner runs it directly.

### 3.2 Factory/AT shell abuse (Tier 2)
- **Threat:** the watch exposes a plaintext AT shell (spec author; `docs/01-research-summary.md`). cmfkit's `at`
  command sends anything unfiltered (`findings.md`, audit 2026-10-24).
- **Mitigation:** watchd sends exactly two AT commands, both on the allowlist: `AT GETSECRET` (pairing only) and
  `AT GETBATLV`. Shell replies are never logged (`ble/link.py` `_on_raw`). No AT access through the API, MCP, or rules.
  Claude Code must ask before running `cmfkit at|run|pair`.
- **Residual (device-level, not fixable here):** the shell itself. `AT GETSECRET` returns the device secret in
  plaintext to anyone who can connect while the watch is pairable — so **pairing is only as private as the radio
  neighbourhood at that moment**. We pair rarely and only on explicit request (`POST /pair?confirm=true`, never via
  MCP).

### 3.3 Eavesdropping and replay on the radio
- **Observed (verified-on-device):** frames are AES-128-CBC with a **static IV**: identical plaintexts produce identical
  ciphertexts within and across sessions (`findings.md` 2026-10-24, 2026-10-25). Pairing frames are rnd ‖
  SHA-256(rnd ‖ secret), which don't reveal the key without the secret.
- **Consequence:** a passive listener can't read payloads without the key, but can tell repeated commands apart and
  correlate traffic; replay of captured frames is plausible (unverified). This is the vendor protocol; we can't fix it.
- **Mitigation on our side:** keep the key off disk (Keychain only), never log it (`cmfkit pair` prints it, so our
  scripts redact it), and keep payloads non-sensitive where we choose them (notifications are the owner's own text).

### 3.4 Another local process driving the watch
- **Threat:** malware or a browser page talks to watchd's API.
- **Mitigation:**
  - binds 127.0.0.1 only;
  - `TrustedHostMiddleware` rejects other Host headers (blocks DNS rebinding);
  - every route needs a bearer token compared in constant time (`secrets.compare_digest`);
  - the token lives in the Keychain, not a file;
  - the UI receives it once in the URL fragment (never sent over HTTP) and keeps it in `sessionStorage`;
  - no CORS, so another origin's script can't read responses.
- **Residual:** a process running as the same user can ask the Keychain for the token (macOS may prompt). Same-user
  malware is out of scope; it already owns the Mac.

### 3.5 The model as a confused deputy (MCP)
- **Threat:** Claude, steered by injected content, uses its tools against the owner: installs faces, spams the wrist,
  pulls health data into context, or reaches pairing/firmware.
- **Mitigation — least-privilege tool surface** (`mcp/server.py`, `test_mcp.py` asserts the exact set):

  | Tool | Effect | Guard |
  |---|---|---|
  | `watch_status`, `list_faces` | read | read-only hint |
  | `query_health` | read | **aggregates only**: daily values, or hourly means for ≤ 7 days; raw samples never leave watchd |
  | `render_face` | read (preview image) | installs nothing; spec hardened (§3.6) |
  | `sync_now` | Tier 0 fetch | idempotent |
  | `notify` | Tier 1, transient | title/body truncated; 1 h TTL |
  | `install_face` | Tier 1, replaces the custom slot | refuses unless `confirm=true`; tool text says only on the owner's request; destructive hint so clients ask |

  Not exposed at all: pairing, AT, firmware, raw frames, rules, deleting data. Errors are deliberate `ToolError`s, so
  internal exceptions don't leak paths or stack traces into the model's context.
- **Residual:** a fully compromised session could still install a face or buzz the wrist. Both are reversible Tier 1
  effects; the client's approval prompt is the last check.

### 3.6 Malicious face specs (HTML rendering)
Specs come from the CLI, the UI, Claude's design assistant and MCP, and are rendered to HTML in headless Chromium.
- **Threats found while writing this document (fixed 2026-10-26):**
  1. *Markup injection*: font names went unescaped into `style` attributes and CSS, so a crafted name could break out
     and inject HTML/script into the render page.
  2. *Local file read*: image/font paths were resolved without limits, so `render_face` with `src: "/Users/…/x.png"`
     would embed any local image and return it to the model.
- **Mitigations now in place:**
  - `render/spec.py`: font names must match `[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}`; `data:` URIs must be well-formed
    base64. Colours were already `#rrggbb`-validated, numbers are typed, text is HTML-escaped.
  - `render/html.py`: local paths are only resolved when the CLI passes the spec's own folder, must stay inside it,
    and must be image/font files. API and MCP renders accept `data:` URIs only.
  - `render/raster.py`: Chromium runs with **JavaScript disabled** and a request filter that **only lets Google Fonts
    through**, so a render can neither run code nor send anything to any other host, localhost included.
  - The UI's live-preview iframes are `sandbox="allow-same-origin"` (no scripts).
  - Tests: `test_render.py` hardening section.
- **Residual:** Google Fonts sees which families a face uses and when it's rendered.

### 3.7 Health data leaving the Mac
- **Threat:** sensitive samples sent to a cloud model or written to logs/commits.
- **Mitigation:**
  - Claude API calls get aggregates, not raw samples: the weekly summary payload is inspectable at
    `/health/summary/payload`, and face design sends the layout and the *names* of metrics that have data today (no values, no images).
  - The MCP tools return aggregates only.
  - Real captured frames stay in gitignored `tests/fixtures/private/`, and the database is gitignored.
  - Watch notifications from hooks carry Claude's text, not health data.
- **Residual:** an aggregate is still personal (e.g. a week of resting HR). The owner chooses when to run AI features.

### 3.8 Automations (rules engine and Claude Code hooks)
- **Threat:** an automation that runs commands becomes a remote-code path ("press a button → run X"), or loops.
- **Mitigation:**
  - **Closed action set:** rules can only notify, sync, refresh the face, or run a *named* macOS Shortcut (argv, no
    shell). Unknown actions fail validation, and the file falls back to the defaults rather than half-applying.
  - **Owner-only authoring:** `rules.json` is edited by hand; the API offers read and reload, not write (a test
    asserts `PUT /rules` is 405), and MCP has no rules tool.
  - **Loop and flood control:** notify jobs never trigger rules; each rule has a cooldown (held volume keys repeat
    4–8/s, verified-on-device); job TTLs drop stale work.
  - **Hooks never block Claude:** async, short timeouts, errors swallowed. They send at most a 20-character title and
    a 128-character body. The session id is sanitised before it becomes a filename.
- **Residual:** a Shortcut can do anything the owner built it to do. That's the owner's code, invoked by name.

### 3.9 Untrusted third-party code
- `refs/` is read-only, and every script in it is read and summarised before its first run.
- cmfkit is pinned to a commit and wrapped. Its hazards (auto-pair on any command, unfiltered AT,
  `Screen.menu`/`Screen.buzz`) are listed in the safety policy and avoided by watchd.
- cmfkit stores keys in plaintext JSON by default; we bypass its keystore.

## 4. Least privilege, summarised

| Actor | Can | Cannot |
|---|---|---|
| Owner (CLI/UI) | everything in Tiers 0–2 via watchd, with `confirm` for installs and pairing | Tier 3 through watchd (no code path) |
| Claude via MCP | the 7 tools above; install only with `confirm=true` | pair, AT, firmware, raw frames, rules, delete data, raw health samples |
| Claude Code itself | run project commands | `cmfkit fw pack/replace`, OTA, SWD (denied); `cmfkit at/run/pair/notify/watchface install` (asks) |
| Rules engine | notify, sync, refresh face, run a named Shortcut | shell, Tier 2+, writing its own rules |
| Hooks | one short notification per event | anything else; can't delay Claude |
| Face renderer | read the spec's own folder (CLI only), fetch Google Fonts | run scripts, reach any other host, read other files |

## 5. Known gaps and next steps
- **Radio-level weaknesses** (static IV, plaintext AT shell at pairing time) are inherent to the watch. Document,
  don't pretend.
- **Replay** of captured frames is untested. A safe test is replaying a captured `notify` at the sim, then the watch.
- **Same-user malware** is out of scope (§3.4).
- **Pairing churn:** the watch keeps one bond (`findings.md` 2026-10-25), so switching to the phone forces a re-pair,
  and each re-pair re-exposes `AT GETSECRET` to the radio.
- **MCP approval UX:** the server isn't yet visible in the owner's Claude Code `/mcp` list. Approval prompts are part of
  the §3.5 defence, so verify they appear once it loads.
