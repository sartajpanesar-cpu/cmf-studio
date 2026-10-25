# Findings log

Format: `YYYY-MM-DD | claim | evidence | status (verified-on-device / sim-only / unverified)`

## Device
- Advertises as `CMF Watch Pro 2-XXXX`; CoreBluetooth UUID on the MacBook Air: `XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX`
- Firmware version: **1.0.0.57** (older than the spec's 1.0.0.73; Gadgetbridge tested .46/.51)
- Serial: CI04102430001430
- Button codes (MUSIC_BUTTON FFFF:A05D, **2-byte payload** `group ‖ arg`, only after a MUSIC_INFO_SET):
  `01 00` / `01 01` play/pause (state toggle) · `02 01` / `02 00` next / prev (pressed in that order) ·
  `03 00` / `03 01` volume up / down (owner pressed up first "I think" — tentative). cmfkit's `DEFAULT_BUTTON_MAP`
  (1-byte codes 01–06, `apps/base.py:19`) is **wrong for this watch**; key on (group, arg).
- Dial list at first connect: 7 of 7 slots used; after the owner deleted faces: 5 of 7

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
- 2026-10-24 | Live run 2 (phase0-20261024-235018): script exited after scan — our parsing bug (timestamped log lines broke the `^UUID` match), not the watch | 01-scan.log | n/a (script)
- 2026-10-24 | Live run 3 (phase0-20261024-235332): watch advertises and connects every step (~0.5–2 s), but login with the run-1 auth key fails: the reply to AUTH_PHONE_NAME doesn't AES-decrypt with our key ("AES decryption failed"), then the watch drops the link ~8 s later. Between run 1 and run 3 the owner reconnected Nothing X on the iPhone to delete faces | 03-info.log:12-16, same in 04/05/07 | verified-on-device
- 2026-10-24 | Hypothesis: the watch holds one bond; a Nothing X reconnect/re-bind invalidates the Mac's auth key (secret or bond rotated), so switching phone↔Mac needs a re-pair each time. Matters for the "phone as fallback" design | run 1 vs run 3 | unverified — test by re-pairing now, then checking whether Nothing X can still connect without re-pairing
- 2026-10-25 | Live run 4 (phase0-20261025-000145): all steps OK — pair, info, listen, notify, face build+install | summary.txt | verified-on-device
- 2026-10-25 | Re-pair works: AUTH_PAIR_REQUEST → REPLY in 70 ms, watch prompt accepted ~7 s later, login + TIME; new key in Keychain; every later step logged in with it | 02-pair.log | verified-on-device
- 2026-10-25 | One-bond hypothesis strengthened: after this Mac pairing the owner's Nothing X "reconnects and re-pairs fine" (owner report). Phone↔Mac switching = re-pair on each side | owner report | verified-on-device (owner report)
- 2026-10-25 | MUSIC_BUTTON frames only arrive after the host sends MUSIC_INFO_SET (run 1 without it: none). Payload is 2 bytes; volume presses repeat ~4–8/s while held | 04-listen.log:00:02:56–00:03:31 | verified-on-device
- 2026-10-25 | Volume only moves ±1 level from the pushed value (we sent volume 8/16): the watch shows the step locally and expects the host to apply it and echo the new level via MUSIC_INFO_SET. Host behaviour, not a watch bug | owner report + MUSIC_INFO_SET `02 08 10 …` | verified-on-device (interpretation unverified)
- 2026-10-25 | DIAL_COMMAND_RET `01 04 05 07` + ids 0x113, 0x160, 0x114, 0x118, `ffffffff` = 5 dials with 4 store ids → **`ffffffff` is a counted slot (the custom photo dial), not a terminator**; active index 4 = that custom dial | 03-info.log | verified-on-device
- 2026-10-25 | Photo face install: INIT1 `01`, INIT2 `01`, watch pulls 7 chunks of 0xC00 (3072) B (last 0x7D6), FINISH `01` → "installed and activated" on the host, and the face displays. **But the watch showed an install-failed message.** cmfkit disconnects ~1 ms after WF_FINISH_ACK; hypothesis: the watch still expects something (or time) after FINISH and reports failure when the link drops. Test: keep the link up a few seconds after FINISH | 07-face-install.log; owner report | verified-on-device (cause unverified)
- 2026-10-25 | One unsolicited frame per session sometimes fails AES decryption with the session key (info and notify, right after the post-login pushes). cmfkit drops it without logging raw bytes → Phase 1 `ble` must log raw hex of undecryptable frames | 03-info.log, 05-notify.log | verified-on-device (content unknown)
- 2026-10-25 | **Only one custom photo-dial slot.** Installing the test face (9063) replaced the owner's existing custom face instead of adding a slot (owner report). Contradicts spec §11.2 "photo = APPEND, list grows" on this fw (1.0.0.57); consistent with the single `ffffffff` id. Any photo install overwrites the current custom face → back up the source image first | owner report; DIAL_COMMAND_RET | verified-on-device (owner report)
- 2026-10-25 | Notify not seen by owner in run 4. Payload matched spec §9 exactly (`ff 00 ‖ when ‖ 0a ‖ "CMF Studio" ‖ "hello"`), but no APP_NOTIFICATION_ACK (0065:0003) arrived; one undecryptable frame came ~100 ms later and cmfkit disconnected after 300 ms. Retest via scripts/phase0_notify.py (holds link, logs raw frames) | 05-notify.log | verified-on-device (cause unknown)
- 2026-10-25 | Live run 5 (phase0-20261025-001858): **notify works** — owner saw both test notifications (generic icon 0xFF and WhatsApp icon 8). Same payload as run 4; difference is the link stayed up 8 s after sending instead of 300 ms → watchd must not disconnect right after a write | 05-notify.log; owner report | verified-on-device
- 2026-10-25 | APP_NOTIFICATION_ACK arrives as a **zero-length frame** `f5 0000 0065 0001 0001 0003`. cmfkit passes the empty body to AES decrypt and drops it as "AES decryption failed" (`frame.py:122`) — so the ACK was never missing, just misparsed. Phase 1 frame decoder: skip decryption when body is empty | 05-notify.log 00:19:58.777, 00:20:06.747 | verified-on-device
- 2026-10-25 | The other recurring undecryptable frame is `FFFF:A062` with a 16-byte body (`2a8af4ab…`), pushed right after login; fails PKCS7 unpad with the session key. Unknown op (not in spec table) — try authkey / no-unpad in Phase 1 | 05-notify.log 00:19:56.755 | verified-on-device (content unknown)
- 2026-10-25 | Static IV confirmed again: FFFF:A06A `5a` and AUTH_CONFIRM_REPLY `5a` produce identical ciphertext (`6e2560ec…`) in the same session | 05-notify.log 00:19:56.702 vs .755 | verified-on-device
- 2026-10-25 | watchd on the real watch: login works with the stored key, but the **second BATTERY request in a session never gets a reply** — at 60 s (run A) and at 15 s (run B) alike, 5/5 and 3/3 sessions. The first BATTERY request right after TIME is answered in ~50 ms. Not an idle disconnect: the watch still pushes unsolicited frames (FFFF:A06B at +41 s) and CoreBluetooth reports no disconnect | research/logs/watchd-A.log, watchd-B.log | verified-on-device
- 2026-10-25 | Hypotheses for the above: (a) watch ignores byte-identical frames (static IV ⇒ a repeated GET encrypts identically), (b) each GET answered once per session, (c) needs a new TIME. Test: scripts/probe_replies.py | — | unverified
- 2026-10-25 | **Probe result: fw 1.0.0.57 never answers BATTERY requests** (005C:0001, payload A5 or 00; 0/5, incl. right after a fresh TIME). Every "battery reply" we saw before was the **unsolicited push burst that follows each TIME**: BATTERY, FFFF:A06B ("en"), FFFF:A06A `5a`, DIAL_COMMAND_RET. Contradicts spec §9 "BATTERY ✅" (spec fw 1.0.0.73) | research/logs/probe-1.log steps 1,2,6,7,8 and lines 17-20 | verified-on-device
- 2026-10-25 | Repeats are fine: FIRMWARE_VERSION_GET answered twice in a row (~60-70 ms each); byte-identical APP_NOTIFICATION frames both ACKed and both displayed (owner saw A, B, B). Identical-frame hypothesis is false | probe-1.log steps 3,4,9-11; owner report | verified-on-device
- 2026-10-25 | Consequence: watchd keepalive = FIRMWARE_VERSION_GET; battery read from TIME pushes; TIME re-sent every 15 min (keeps the clock right and refreshes battery). Test sim (tests/conftest.py FlakySim) now mirrors this behaviour | src/cmfstudio/ble/link.py | sim-only (pending re-run on device)
- 2026-10-25 | **Keepalive fix verified on device**: watchd stayed connected 2 min 14 s across several 60 s FIRMWARE_VERSION_GET keepalives with no drops (previously every session died at the 2nd keepalive) | research/logs/watchd-C.log 01:02:27–01:04:41 | verified-on-device
- 2026-10-25 | Mac Bluetooth off → CoreBluetooth reports the disconnect immediately (`ConnectionError('watch disconnected')`); reconnect attempts fail fast with BleakBluetoothNotAvailableError and back off (1,2,5,10,30 s); after Bluetooth on, reconnected within one backoff step (01:05:33). Follow-up: nudge the link when Bluetooth powers on instead of waiting out the backoff | watchd-C.log 01:04:41–01:05:33 | verified-on-device
- 2026-10-25 | **watchd under launchd can use Bluetooth** (no extra TCC prompt reported): LaunchAgent started 01:06:06, link connected 01:06:12 | ~/Library/Logs/cmf-studio/watchd.log | verified-on-device
- 2026-10-25 | The undecryptable FFFF:A062 push (16 B body `2a8af4ab…`, identical every session) still appears after some logins; harmless | watchd-C.log 01:02:27 | verified-on-device (content unknown)
- 2026-10-24 | Homebrew `uv` builds from source on macOS 14 (python@3.15 build fails); installed uv from PyPI wheel into `~/.local/share/uv-venv`, symlinked to `~/.local/bin/uv` | brew output | n/a (env)
