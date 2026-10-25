# Findings log

Format: `YYYY-MM-DD | claim | evidence | status (verified-on-device / sim-only / unverified)`

## Device
- Advertises as `CMF Watch Pro 2-XXXX`; CoreBluetooth UUID on the MacBook Air: `XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX`
- Firmware version: **1.0.0.57** (older than the spec's 1.0.0.73; Gadgetbridge tested .46/.51)
- Serial: CI04102430001430
- Button codes: _not captured yet — first listen run saw no MUSIC_BUTTON frames_
- Dial list at first connect: **7 of 7 slots used** (full)

## cmfkit audit (refs/CMF-Open @ 37781b1, 2026-10-24)
Scope: `src/cmfkit/**` (≈3.5k lines Python), `tests/`, `pyproject.toml`, `uv.lock`. The `web/` app store
(TypeScript, Web Bluetooth, Vercel/Capacitor) was not audited; we don't run it.

**Dependencies:** bleak, cryptography, lz4, pillow (+ pytest, pytest-asyncio, ruff dev). `uv.lock`: 29
packages, all from `https://pypi.org/simple`, plus the project itself (editable).

**Network calls:** none. No `requests`/`urllib`/`httpx`/`socket`/http URLs in `src/` or `tests/`.

**Subprocess / code loading:**
- `apps/examples/ticker.py:30` — `create_subprocess_shell(<user-supplied command>)` (the `ticker` app).
- `apps/registry.py:27` — `cmfkit run path/to/file.py` imports and executes an arbitrary Python file.

**File writes:**
- `keystore.py:30` — auth key, **plaintext hex**, at `$XDG_CONFIG_HOME/cmfkit/devices.json`
  (default `~/.config/...`, chmod 0600), keyed by BLE address. Written by `pair` and by any connected
  command that auto-pairs (below). Conflicts with our Keychain rule → `scripts/phase0_live.sh`
  redirects `XDG_CONFIG_HOME` to a temp dir and moves the key into Keychain
  (service `cmf-studio`, account `pairing:<CoreBluetooth UUID>`). Phase 1 `ble` must read from there and
  call `Session.login(key)` directly instead of using cmfkit's keystore.
- `cli.py:116,206,220` — `-o` outputs of `watchface build`, `fw pack`, `fw replace` (local only).
- `firmware/scan.py:106`, `firmware/aota.py:186` — `fw scan --extract` / `fw extract` into a user dir.

**Secrets printed/logged:** `cmfkit pair` prints the auth key to stdout (`cli.py:74`). At `-vv`
the session logs every frame payload pre-encryption (`session.py:79,63`), including the pairing
frames (rnd ‖ SHA-256(rnd‖secret)); those are also plaintext on air and don't reveal the key without the
device secret. The live script redacts `GETSECRET:<hex>`, its hex form, and `Auth key: <hex>`.

**Commands that write to the watch** (all others are read-only or offline):
| Command | What it sends | Tier |
|---|---|---|
| any connected cmd (`info`, `listen`, `notify`, `find`, `run`, `watchface install`) | **auto-pairs if no stored key** (`cli.py:49-56`), and every login sends `TIME` (FFFF:8004) — sets the watch clock to the Mac's time (`session.py:151`) | 1 (pairing = 2 on first run) |
| `pair` | `AT GETSECRET` on shell, then pair handshake; watch shows an accept prompt | 2 (allowlisted) |
| `notify` | APP_NOTIFICATION | 1 |
| `find` | FIND_WATCH (rings) | 1 |
| `watchface install` | photo dial over data channel (9063 = **APPEND** to dial list, auto-activates) | 1 |
| `run <app>` | music info, weather, notifications; `Screen.menu()` → **CONTACTS_SET overwrites the watch's contact list** (used by `checklist`); `Screen.buzz()` → **`AT SETMOTOR=1`** (`apps/surfaces.py:48`) — prohibited by our policy; no built-in app calls it | 1–2 |
| `at <anything>` | **unrestricted** raw AT command | 2–3 — use only allowlisted getters |

No CLI path sends FACTORY_RESET (009A:0001), DEVICE_REBOOT (FFFF:9080), FW_* OTA or AGPS opcodes;
they're defined in `protocol/opcodes.py` but unreferenced. `fw pack|replace` only write local files
(denied in `.claude/settings.json` regardless). No CLI command deletes a face; recovery = Nothing X,
or our own DIAL_COMMAND (9055) implementation later.

**Pro 2 compatibility vs joshuapassos spec (on-device verified, fw 1.0.0.73):**
- Advertised name `CMF Watch Pro 2-XXXX` matches cmfkit's `CMF Watch` scan prefix (`transport/ble.py:10`).
- All 6 characteristic UUIDs (fff1/fff2, 77d4ff01/02, 02f0…ffe1/ffe2) match (spec §1).
- Pairing/login sequence matches (spec §4). cmfkit sends phone model `"CMF Watch 3 Pro"` in
  AUTH_PHONE_NAME; spec example uses `"CMF Watch Pro 2"` — effect unknown.
- Photo dial INIT2 header and container (magic 6c8dc4a5, 466² FULL + 270² THUMB, LZ4 RGB565-LE) match
  spec §11.3 byte-for-byte (`payloads.py:131`, `watchface/photo.py:83`).
- Spec §11.2: photo upload appends; example list max is 7 dials — a full list may make install return `0a`.

**Candidate AT getters** (spec §14 "tested live"; NOT allowlisted — need `fw scan` evidence + owner
approval per policy): `GETVERSION`, `GETSN`, `GETNAME`, `GETPID`, `GETGSENSOR`, `GETNTCTEMP`.

## Log
- 2026-10-24 | Ref pins match HEADs; CMF-Open working tree unmodified | `git rev-parse HEAD` vs refs/PINS.md | verified (local)
- 2026-10-24 | cmfkit has no network calls; deps all from PyPI | grep of src/tests; uv.lock sources | verified (static)
- 2026-10-24 | cmfkit test suite passes: 28 passed in 3.4 s; ruff clean | `uv run pytest -q` in refs/CMF-Open (Python 3.12, uv 0.13.5) | sim-only
- 2026-10-24 | `--sim` scan-free commands work: info (fw 1.0.0.77, serial CI0SIMULATOR0001, 87%), listen (MUSIC_BUTTON 01–06), find, notify, at GETBATLV (`3921mv,OK`), pair, watchface build+install ("installed and activated"), run pomodoro | CLI output this session | sim-only
- 2026-10-24 | cmfkit `run` bug: flags after the app name are swallowed by `argparse.REMAINDER` (`run pomodoro --duration 2` → ValueError); put flags first | `cli.py:280` | sim-only
- 2026-10-24 | `scripts/phase0_live.sh --sim` runs all steps OK; secrets redacted in logs | research/logs/phase0-20261024-162311-sim/ | sim-only
- 2026-10-24 | Live run 1 (research/logs/phase0-20261024-164137): scan ok, pair ok, info ok, listen ran but no button frames, notify FAILED, face-build ok, face-install FAILED | summary.txt | verified-on-device
- 2026-10-24 | scan finds the watch from macOS with iPhone BT off; name prefix `CMF Watch` matches | 01-scan.log | verified-on-device
- 2026-10-24 | GATT services present: 180F, 180A, FFD0, E49A3001-…, 02F00000-…FE00 (FF01/FF02), F48A23C0-…, FFF0 (FFF1/FFF2), **77D4E67C-…** (77D4FF01/02), 02F00000-…FFE0 (FFE1/FFE2) — matches spec §1 incl. the 77d4e67c shell service | 03-info.log:40-123 | verified-on-device
- 2026-10-24 | `cmfkit pair` works on Pro 2 fw 1.0.0.57: AT GETSECRET → pair → accept on watch → login; auth key redacted in log and stored in Keychain (`cmf-studio` / `pairing:XXXXXXXX-…`) | 02-pair.log:134-141 | verified-on-device
- 2026-10-24 | Login with stored key works on reconnect (AUTH_PHONE_NAME → AUTH_WATCH_MAC → NONCE → CONFIRM `5a` → TIME). AUTH_WATCH_MAC payload = `5a` ‖ 6-byte MAC. Phone model string "CMF Watch 3 Pro" is accepted | 03-info.log:131-147 | verified-on-device
- 2026-10-24 | AUTH_NONCE_REQUEST ciphertext is byte-identical across sessions (same authkey, fixed IV) → AES-CBC IV is static/deterministic | 03-info.log:137 vs 04-listen.log:125 | verified-on-device
- 2026-10-24 | `info`: FIRMWARE_VERSION_RET `01000039` → 1.0.0.57; BATTERY `5800` → 88 %, not charging; SERIAL `10 4349…30` → CI04102430001430 | 03-info.log:152-173 | verified-on-device
- 2026-10-24 | After login the watch pushes unsolicited: BATTERY, `FFFF:A06B` (`656e00…` = "en", likely language), `FFFF:A06A 5a`, DIAL_COMMAND_RET `01 06 07 07` + ids 0x113, 0x160, 0x120, 0x114, 0x115, 0x118, `ffffffff` → result ok, active index 6, **7 dials, max 7**. 6 store ids + `ffffffff` for 7 slots suggests the active 7th slot is a custom photo dial (id sentinel FFFFFFFF), not a list terminator — unverified | 03-info.log:158-160 | verified-on-device (interpretation unverified)
- 2026-10-24 | `listen` 30 s: no MUSIC_BUTTON / FIND_PHONE frames received; only the post-login pushes above | 04-listen.log:139-151 | verified-on-device (cause unknown)
- 2026-10-24 | `notify` and `watchface install` never connected: CoreBluetooth scanned for `XXXXXXXX-…` for the whole run without seeing it (watch absent from advertisements after the listen session). Failure is at connect, not protocol | 05-notify.log, 07-face-install.log (scan-only) | verified-on-device (cause unknown)
- 2026-10-24 | Homebrew `uv` builds from source on macOS 14 (python@3.15 build fails); installed uv from PyPI wheel into `~/.local/share/uv-venv`, symlinked to `~/.local/bin/uv` | brew output | n/a (env)
