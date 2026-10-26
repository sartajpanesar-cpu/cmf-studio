# 01 — Research summary (as of 2026-10-24)

Status legend: **V** = verified on a real device by someone · **S** = simulator/static only · **U** = claimed, unverified · **?** = unknown

## Hardware & OS
| Fact | Status | Source |
|---|---|---|
| SoC Actions ATS3089C (≈202 MHz Arm MCU + DSP, 2.5D GPU, 8 MB PSRAM) | S (firmware strings) | whatotter, CMF-Open |
| Zephyr RTOS (Actions SDK ~2.7), LVGL UI, FatFS | S | whatotter, CMF-Open |
| One monolithic `app.bin`, no app loader → "native apps" = firmware rebuild | S | CMF-Open docs/01 |
| 466×466 AMOLED, RGB565, 353 PPI | V (spec sheet) | nothing.tech |
| UART0–2 + a UART command line exist in firmware; pad locations unknown | S | whatotter |
| SWD (SWDIO/SWCLK) is the only brick recovery path; no USB, no recovery mode | U | krtkygpta README |
| Vendor SDK is public: `lvgl/lv_port_actions_technology`, board `ats3089c_dev_watch` | V (repo exists) | CMF-Open |

## BLE protocol
| Fact | Status | Source |
|---|---|---|
| Framed GATT commands, AES-128-CBC; plaintext factory AT shell; bulk-data channel | V (Pro 2 spec author) | joshuapassos/CMF-Watch-Pro-2-BLE-Protocol |
| Pairing: `AT GETSECRET` → SHA-256 challenge → per-session AES key; no Nothing account needed | V for Pro 2 via Gadgetbridge "no-vendor pair" | Gadgetbridge, CMF-Open |
| One phone/host session at a time | V | CMF-Open, Gadgetbridge |
| Watch 3 Pro uses the same protocol as Pro 2 | V (Gadgetbridge) | Gadgetbridge |

## Feature support in Gadgetbridge (reference implementation, Java)
Pro 2 marked "mostly supported", tested on fw 1.0.0.46 and 1.0.0.51.
- Works: auth, battery, fw version/serial, time/timezone, notifications with icons, alarms, find watch/phone, music info, weather, **activity sync: steps, HR, stress, SpO2, resting HR**. (The website also lists widget setup for Pro 2 & 3, but Gadgetbridge @ ed37d5f has no CMF widget code — corrected 2026-10-26, see findings.)
- Broken/missing: sleep times don't match watch; goals don't sync; contacts; measurement units; hourly weather; set watchface from phone; DND.
- Experimental/untested: watchface upload, AGPS upload, heart alerts, calories/distance UI.
- **Use as the source of truth for sync opcodes and payload parsing** (port to Python).

## cmfkit (TechJeeper/CMF-Open) — our base library
- Python 3.12 + uv, bleak transport, in-process **simulated watch**.
- Working (per README): host-side `WatchApp`s that draw onto music / notification / weather / contacts screens and read button presses; image → watchface encoder + BLE install; generative/live faces; web "app store" over Web Bluetooth.
- Firmware tools: `fw info | extract | scan | pack | replace`, btsnoop decoder, Zephyr+LVGL `cmf_hello` demo.
- Firmware updates: Actions **AOTA** container, **CRC32 only**, must match `board_name` and increase `version_code`. SoC *can* enforce RSA-2048 signed boot images — `cmfkit fw info` reports whether a stock OTA does. **This single check decides whether firmware mods are possible.**
- Caveat: written for Watch 3 Pro, tested end-to-end against its simulator only, **not on hardware**. Pro 2 is the spec's reference device → likely works, must verify (Phase 0).
- Activity sync: **not implemented** (our main porting job).
- Very low star count, single author → audit before running.

## Firmware structure (Pro 2 OTA, per krtkygpta — treat as U)
| Offset | Part |
|---|---|
| 0x00000400 | `ota.xml` manifest + CRC32 |
| 0x00000C00 | `TEMP.bin` → nested AOTA with `app.bin` (Zephyr) |
| 0x0013FE00 | `res.bin` — UI icons, themes |
| 0x0071D400 | `fonts.bin` — system fonts, preloaded dials |
| 0x00E8BE00 | `res_e.bin` — face animations `1.ajs`–`19.ajs` |
| 0x033F7600 | `sdfs_k.bin` — ringtones, certs |
| 0x03448458 | AGPS |

whatotter warns recompiled images have binary mismatches and can brick. krtkygpta claims "verified" flash methods with zero evidence (0 stars, 4 commits) → **do not use**.

## What nobody has publicly demonstrated
- Custom firmware running custom code on a Pro 2.
- System font/icon replacement.
- Voice/mic access over BLE (mic likely Classic BT for calls).
- How long the watch buffers activity data before overwriting.

## Claude-on-a-watch prior art (designs to borrow, not code to run on this watch)
- **oh-my-wrist** — desktop daemon + Garmin app, Claude Code activity over BLE, haptic alerts. Closest pattern to ours.
- **claude-watch** — Claude Code hooks → bridge → Apple Watch, approve/deny from wrist.
- **Claude Desktop Hardware Buddy** — Anthropic dev-mode BLE API, Nordic UART + JSON. Incompatible transport; not worth shimming.
- **OpenClaw / PicoClaw / NullClaw / Anima** — agent runtimes. Need Linux/Wear OS; can only act as a Mac-side brain. OpenClaw has a poor security record → if used, least-privilege only.

## Sources
- Gadgetbridge Nothing/CMF page: https://gadgetbridge.org/gadgets/wearables/nothing/
- cmfkit / CMF-Open: https://github.com/TechJeeper/CMF-Open
- Pro 2 BLE protocol spec: https://github.com/joshuapassos/CMF-Watch-Pro-2-BLE-Protocol
- Firmware dump: https://github.com/whatotter/cmf-watch-firmware
- Nothing X / OTA research: https://ambraglow.org/blog/nothing-x/a-look-into-the-nothing-x-smartwatch-app/
- Actions SDK (LVGL port): https://github.com/lvgl/lv_port_actions_technology
- Unverified flash repo (do not use): https://github.com/krtkygpta/cmf-watch-2-pro-custom-firmware
- REA: https://github.com/morluto/rea
- oh-my-wrist: https://github.com/yazon/oh-my-wrist · claude-watch: https://github.com/shobhit99/claude-watch
