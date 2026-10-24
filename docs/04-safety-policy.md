# 04 — Safety policy (binding)

## Risk tiers
| Tier | Operations | Rule |
|---|---|---|
| 0 Read-only | scan, info, battery, listen, activity fetch, AT allowlist reads | Free once Phase 0 passes |
| 1 Reversible writes | notify, install/delete face, widget layout, time/timezone, alarms | Ask before each until Phase 1 done; then allowed via watchd |
| 2 State-changing | pairing, unbind, factory settings, any AT command off the allowlist | Ask every time, explain the effect first |
| 3 Brick risk | OTA, firmware upload, AGPS upload, `fw pack/replace` output to device, SWD | **Never automated.** Owner runs manually, Phase 7 only, test unit only |

## AT shell allowlist (read-only)
`AT GETSECRET` (pairing only, via cmfkit `pair`), `AT GETBATLV`.
Add commands only after: found via `cmfkit fw scan`, name clearly a getter, owner approved, recorded in findings.md. Anything matching `SET|RESET|FACTORY|CAL|ERASE|WRITE|FORMAT|OTA|REBOOT|TEST` is prohibited.

## Enforcement
- `.claude/settings.json` denies firmware/flash commands and asks before device-writing cmfkit commands.
- watchd's MCP surface excludes AT, firmware, pairing, raw frames.
- Any new device-writing code path needs a sim test before hardware.
- cmfkit hazards (audit 2026-10-24, see findings.md): `cmfkit at` sends any AT command unfiltered; any
  connected command auto-pairs when no key is stored and sets the watch clock on login; `cmfkit run` apps
  can overwrite the watch's contacts (`Screen.menu`) or send prohibited `AT SETMOTOR=1` (`Screen.buzz`).
  Don't use `run`/`at` on the real watch; watchd must not call `Screen.menu`/`Screen.buzz`.

## Recovery plan
1. Watch misbehaves after a Tier 1 write → delete the face/reset widgets via the CLI, or re-pair with Nothing X and reapply defaults.
2. Pairing lost on the Mac → re-run `cmfkit pair`; if that fails, re-pair with Nothing X (may reset on-watch data — that's why the backup step exists).
3. Watch won't boot → Tier 3 territory; stop and document. No recovery exists without SWD hardware.

## Data & secrets
- Pairing keys, API tokens, Anthropic key → macOS Keychain only.
- Health data stays local. Claude API calls send aggregates (daily/weekly), not raw samples, unless the owner explicitly asks.
- Don't commit `refs/`, firmware images, captures, or the database (see .gitignore).
