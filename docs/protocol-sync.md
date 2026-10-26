# Health sync protocol (CMF Watch Pro 2)

Sources, read 2026-10-26. Facts are restated here in our own words; no code is copied.
- **GB** = Gadgetbridge @ `ed37d5f` (`refs/Gadgetbridge`, AGPL-3.0). Paths below are relative to
  `app/src/main/java/nodomain/freeyourgadget/gadgetbridge/`.
- **Spec** = joshuapassos/CMF-Watch-Pro-2-BLE-Protocol @ `c1e5993` §8 (author verified on a Pro 2, fw 1.0.0.73).
- **Ours** = observed on our watch (fw 1.0.0.57), see `research/findings.md`.

Status: **GB+Spec** = both agree · **Spec only** / **GB only** · **Ours** = verified on our watch · **?** = open.
Nothing in this file is verified on our watch yet unless marked **Ours**.

## Opcodes

All on the command characteristic (fff1 notify / fff2 write), encrypted with the session key, multi-chunk
frames reassembled per opcode (cmfkit `FrameDecoder` already does this).

| Name | (cmd1, cmd2) | Dir | Source |
|---|---|---|---|
| ACTIVITY_FETCH_1 | `FFFF 8005` | phone → watch, payload `A5` | GB `CmfCommand.java:23`, Spec §8 |
| ACTIVITY_FETCH_ACK_1 | `FFFF 0005` | watch → phone, `[0]` = `01` ready / `02` finished | GB `CmfCommand.java:25`, `CmfActivitySync.java:113-132` |
| ACTIVITY_FETCH_2 | `FFFF 9057` | phone → watch, payload `A5` | GB `CmfCommand.java:24`, `CmfActivitySync.java:119` |
| ACTIVITY_FETCH_ACK_2 | `FFFF A057` | watch → phone, `since_ts(u32 LE) ‖ flags(4)` | GB `CmfCommand.java:26`, `CmfActivitySync.java:134-141` |
| ACTIVITY_DATA | `0056 0001` | watch → phone | GB `CmfCommand.java:22` |
| HEART_RATE_MANUAL_AUTO | `0053 0001` | watch → phone | GB `CmfCommand.java:78` |
| HEART_RATE_WORKOUT | `00E0 0001` | watch → phone | GB `CmfCommand.java:79` |
| HEART_RATE_RESTING | `00DA 0001` | watch → phone | GB `CmfCommand.java:77` |
| SPO2 | `0055 0001` | watch → phone | GB `CmfCommand.java:88` |
| STRESS | `009D 0001` | watch → phone | GB `CmfCommand.java:92` |
| SLEEP_DATA | `0058 0001` | watch → phone | GB `CmfCommand.java:87` |
| WORKOUT_SUMMARY / _V3 | `0057 0001` / `0160 0001` | watch → phone | GB `CmfCommand.java:105-106` |
| WORKOUT_GPS | `FFFF A05A` | watch → phone | GB `CmfCommand.java:104` |
| TRIGGER_SYNC | `005C 0002` | ? (defined, unused by GB) | GB `CmfCommand.java:95` |

## Sequence (GB+Spec)

1. Precondition: logged in and **TIME sent in this session** — the watch ignores data queries before TIME
   (Spec §4.3; watchd's login already ends with TIME).
2. Phone → `ACTIVITY_FETCH_1 A5` (GB `CmfWatchProSupport.java:784-786`).
3. Watch → `ACTIVITY_FETCH_ACK_1 01`; phone → `ACTIVITY_FETCH_2 A5` (GB `CmfActivitySync.java:115-119`).
4. Watch → `ACTIVITY_FETCH_ACK_2` with the timestamp it is sending data *since* plus 4 flag bytes of unknown
   meaning (GB `CmfActivitySync.java:134-141`, "TODO what do they mean?").
5. Watch streams data frames in any order. **The phone sends no per-frame ACKs** (no sends in any GB
   handler). Spec: a heavy session is ~170–210 frames over ~160 s.
6. Watch → `ACTIVITY_FETCH_ACK_1 02` = finished (GB `CmfActivitySync.java:121-125`).

GB triggers this only on user request/schedule (`onFetchRecordedData`); nothing is fetched automatically on
connect.

## Payloads (all little-endian, timestamps epoch seconds UTC)

| Record | Layout | Status / source |
|---|---|---|
| ACTIVITY_DATA | N × 32 B: `ts u32 ‖ steps u32 ‖ distance u32 (m) ‖ calories u32 ‖ 16 B reserved` | GB+Spec — GB `CmfActivitySync.java:143-166`, Spec §8.1 |
| HR (manual/auto, workout) | N × 8 B: `ts u32 ‖ bpm u32` | GB+Spec — GB `:189-207`, Spec §8.2 |
| SpO2 | N × 8 B: `ts u32 ‖ percent u32` | GB+Spec — GB `:308-326` |
| Stress | N × 8 B: `ts u32 ‖ index u32` (bands 1–29 / 30–59 / 60–79 / 80–99) | GB+Spec — GB `:277-295`, Spec §8.2 |
| Resting HR | N × **5 B**: `ts u32 ‖ bpm u8` | **Spec only** (live example `5e dc 29 6a 4e` → 78 bpm). GB doesn't parse it (`:219-222`) |
| SLEEP_DATA | one session per frame: header 18 B + N × 8 B records | GB+Spec (header detail Spec only) |
| — header | `start u32 ‖ wakeup u32 ‖ deep_s u16 ‖ core_s u16 ‖ rem_s u16 ‖ awake_s u16 ‖ ? u16` | Spec §8.3; GB keeps bytes 8–17 as opaque metadata (`:229-237`) |
| — record | `ts u32 ‖ duration_s u16 ‖ stage u16`; stage 1 deep · 2 core/light · 3 REM · 4 awake | GB `:241-247`, `devices/cmfwatchpro/samples/CmfSleepSessionProvider.kt:65-72` (GB maps 4 → unknown); Spec §8.3 |
| WORKOUT_SUMMARY v1 | 54 B (GB also accepts 32 B) records | GB `:339-401`; Spec §8.4 says v1 confirmed, V3 extended block offsets uncertain |
| WORKOUT_GPS | N × 12 B: `ts u32 ‖ lon i32 ‖ lat i32` (scale ?) | GB only `:403-422` |

## Semantics that matter for storage

- **Steps (and distance, calories) are cumulative**, resetting at the turn of the day: GB converts them to
  per-sample deltas on read (`devices/AbstractSampleProvider.java:290-320`, "fix over-counting at the turn of
  day"). Store the raw cumulative value; derive deltas/daily totals in `daily`. **?** on our watch: reset at
  local midnight or UTC?
- **Activity calories are in cal, not kcal** (divide daily sum by 1000); workout-summary calories are kcal.
  Spec only (§8.1).
- Sample interval: Spec calls them "per-minute samples"; GB doesn't assume one. **?**
- One night can produce several SLEEP_DATA sessions (micro-wakes). Spec §8.3. GB's sleep times are known not
  to match the watch (docs/01).
- Battery is not part of sync; it arrives in the push burst after TIME (**Ours**, probe-1). GB likewise only
  handles BATTERY pushes (`CmfWatchProSupport.java:417-424`).

## Observed on our watch (fw 1.0.0.57, 2026-10-25)

- The sequence above works as written; a light day is 4 frames in ~1 s (**Ours**).
- **The watch keeps a cursor**: a repeat fetch doesn't resend already-fetched HR; it re-sends only the
  current-hour activity record (stamped at fetch time) and a re-stamped resting-HR record (**Ours**).
- ACTIVITY_DATA is **hourly** cumulative records; HR every 5 min while worn (**Ours**).
- Calories appear to be **kcal**, contradicting Spec §8.1 (to confirm on the watch screen).

## Open questions for our watch (Phase 2 measures these)

1. ~~Cursor or full buffer?~~ **Cursor** (see above).
2. ~~ACK_2 meaning~~ **Answered (Ours, 2026-10-26):** ACK_2 = the watch's latest heart-rate reading,
   `ts u32 LE ‖ bpm u8 ‖ 00 00 00` (9/12 syncs match a stored HR sample exactly). GB's "since"/"flags" naming is wrong.
3. How long does the watch buffer data (roadmap AC: measure by leaving the Mac closed 1, 2, 3+ days)?
4. Resting HR 5-byte layout and calorie units on fw 1.0.0.57.
5. ~~Sleep header totals reconcile?~~ **Yes (Ours, 2026-10-26):** records are contiguous over the session, per-stage
   sums equal the header totals, totals sum to the session length. Header bytes 16-17 still unknown.
6. Does sync conflict with Nothing X's cursor (if the watch tracks one cursor, whoever syncs first gets the
   data)?
