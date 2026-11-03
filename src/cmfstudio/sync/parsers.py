"""Parsers for health-sync frames. Layouts per docs/protocol-sync.md (little-endian, epoch seconds UTC).

Pure functions: payload bytes in, records out. Bump PARSER_VERSION whenever output changes, so stored
raw frames can be re-parsed (store.raw_frames.parser_version).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from cmfkit.protocol.opcodes import Op

PARSER_VERSION = 2  # 2: workouts and GPS routes

ACTIVITY_FETCH_1 = Op(0xFFFF, 0x8005)
ACTIVITY_FETCH_ACK_1 = Op(0xFFFF, 0x0005)
ACTIVITY_FETCH_2 = Op(0xFFFF, 0x9057)
ACTIVITY_FETCH_ACK_2 = Op(0xFFFF, 0xA057)

ACTIVITY_DATA = Op(0x0056, 0x0001)
HEART_RATE = Op(0x0053, 0x0001)
HEART_RATE_WORKOUT = Op(0x00E0, 0x0001)
HEART_RATE_RESTING = Op(0x00DA, 0x0001)
SPO2 = Op(0x0055, 0x0001)
STRESS = Op(0x009D, 0x0001)
SLEEP_DATA = Op(0x0058, 0x0001)
WORKOUT_SUMMARY = Op(0x0057, 0x0001)
WORKOUT_SUMMARY_V3 = Op(0x0160, 0x0001)
WORKOUT_GPS = Op(0xFFFF, 0xA05A)

# stored raw, not parsed yet
RAW_ONLY: set[Op] = set()
WORKOUT_OPS = {WORKOUT_SUMMARY, WORKOUT_SUMMARY_V3}

SLEEP_STAGES = {1: "deep", 2: "core", 3: "rem", 4: "awake"}
DAY_S = 86400


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class Sample:
    metric: str
    ts: int
    value: float


@dataclass(frozen=True)
class SleepStage:
    ts: int
    duration_s: int
    stage: str


@dataclass(frozen=True)
class SleepSession:
    start_ts: int
    end_ts: int
    totals_s: dict[str, int]
    unknown: int
    stages: list[SleepStage] = field(default_factory=list)


def _records(payload: bytes, size: int, name: str) -> list[bytes]:
    if len(payload) % size:
        raise ParseError(f"{name}: payload length {len(payload)} is not a multiple of {size}")
    return [payload[i : i + size] for i in range(0, len(payload), size)]


def activity(payload: bytes) -> list[Sample]:
    """Cumulative counters (reset at local midnight): steps, distance in m, calories in kcal on fw 1.0.0.57.

    (Spec §8.1 says cal; our watch screen agrees with kcal. Metric id kept as calories_cum_cal.)
    """
    out = []
    for rec in _records(payload, 32, "ACTIVITY_DATA"):
        ts, steps, distance_m, calories_cal = struct.unpack_from("<IIII", rec)
        out += [
            Sample("steps_cum", ts, steps),
            Sample("distance_cum_m", ts, distance_m),
            Sample("calories_cum_cal", ts, calories_cal),
        ]
    return out


def _ts_u32_value(payload: bytes, metric: str, name: str) -> list[Sample]:
    return [Sample(metric, *struct.unpack("<II", rec)) for rec in _records(payload, 8, name)]


def heart_rate(payload: bytes) -> list[Sample]:
    return _ts_u32_value(payload, "hr", "HEART_RATE")


def heart_rate_workout(payload: bytes) -> list[Sample]:
    return _ts_u32_value(payload, "hr_workout", "HEART_RATE_WORKOUT")


def spo2(payload: bytes) -> list[Sample]:
    return _ts_u32_value(payload, "spo2", "SPO2")


def stress(payload: bytes) -> list[Sample]:
    return _ts_u32_value(payload, "stress", "STRESS")


def heart_rate_resting(payload: bytes) -> list[Sample]:
    """5-byte records ts u32 ‖ bpm u8 (spec §8.2; not parsed by Gadgetbridge)."""
    return [Sample("hr_resting", *struct.unpack("<IB", rec)) for rec in _records(payload, 5, "HEART_RATE_RESTING")]


def sleep(payload: bytes) -> SleepSession:
    if len(payload) < 18 or (len(payload) - 18) % 8:
        raise ParseError(f"SLEEP_DATA: length {len(payload)} is not 18 + 8n")
    start, end, deep, core, rem, awake, unknown = struct.unpack_from("<IIHHHHH", payload)
    stages = []
    for off in range(18, len(payload), 8):
        ts, duration, code = struct.unpack_from("<IHH", payload, off)
        stages.append(SleepStage(ts, duration, SLEEP_STAGES.get(code, f"unknown_{code}")))
    totals = {"deep": deep, "core": core, "rem": rem, "awake": awake}
    return SleepSession(start, end, totals, unknown, stages)


# -- workouts (layouts from Gadgetbridge's CmfWorkoutSummaryParser; the Pro 2 spec §8.4 describes WORKOUT_SUMMARY
# v1 differently — start, end, duration first — so v1 records are sanity-checked and the other layout tried) ------


@dataclass(frozen=True)
class Workout:
    start_ts: int
    end_ts: int
    duration_s: int
    sport: int
    hr_avg: int | None
    kcal: int | None
    steps: int | None = None
    distance_m: int | None = None
    pace_avg_s_km: int | None = None
    has_gps: bool = False
    layout: str = ""
    extra: dict = field(default_factory=dict)


def _plausible(w: Workout) -> bool:
    return (
        1_500_000_000 < w.start_ts <= w.end_ts < w.start_ts + DAY_S
        and abs((w.end_ts - w.start_ts) - w.duration_s) <= 600
    )


def _v1_gadgetbridge(r: bytes) -> Workout:
    start, duration, sport, hr, kcal, steps, dist, pace = struct.unpack_from("<IHBBHIIH", r)
    end, gps = struct.unpack_from("<IB", r, 26)
    extra = {}
    if len(r) >= 54:
        load, aer, anaer, rec, *zones = struct.unpack_from("<4H5H", r, 32)
        extra = {
            "training_load": load,
            "aerobic": aer / 100,
            "anaerobic": anaer / 100,
            "recovery_min": rec,
            "hr_zone_min": list(zones),
            "score": struct.unpack_from("<I", r, 50)[0],
        }
    return Workout(start, end, duration, sport, hr, kcal, steps, dist, pace, gps == 1, "v1-gadgetbridge", extra)


def _v1_spec(r: bytes) -> Workout:
    start, end, duration, sport, hr, kcal = struct.unpack_from("<IIIBBH", r)
    return Workout(start, end, duration, sport, hr, kcal, layout="v1-spec")


def workouts_v1(payload: bytes) -> list[Workout]:
    """WORKOUT_SUMMARY (0057:0001): 32- or 54-byte records."""
    size = 54 if len(payload) % 54 == 0 and len(payload) % 32 else 32 if len(payload) % 32 == 0 else 0
    if not size:
        raise ParseError(f"workout summary of {len(payload)} bytes isn't a multiple of 32 or 54")
    out = []
    for i in range(0, len(payload), size):
        rec = payload[i : i + size]
        w = _v1_gadgetbridge(rec)
        if not _plausible(w):
            alt = _v1_spec(rec)
            if not _plausible(alt):
                raise ParseError(f"workout record {i // size} fits neither known layout: {rec.hex()}")
            w = alt
        out.append(w)
    return out


def workout_v3(payload: bytes) -> list[Workout]:
    """WORKOUT_SUMMARY_V3 (0160:0001): one record; GPS workouts carry a 40-byte block then running metrics."""
    if len(payload) < 16:
        raise ParseError(f"v3 workout summary too short ({len(payload)} B)")
    start, end, duration, sport, hr, kcal = struct.unpack_from("<IIIBBH", payload)
    w = Workout(start, end, duration, sport, hr, kcal, layout="v3")
    if len(payload) >= 16 + 40 + 32:
        steps, dist, _u, pace_max, pace_avg, _u2, cadence, stride = struct.unpack_from("<8I", payload, 56)
        w = Workout(
            start,
            end,
            duration,
            sport,
            hr,
            kcal,
            steps,
            dist,
            pace_avg,
            True,
            "v3",
            {"pace_max_s_km": pace_max, "cadence_spm": cadence, "stride_cm": stride},
        )
    if not _plausible(w):
        raise ParseError(f"v3 workout summary isn't plausible: {payload[:16].hex()}")
    return [w]


def gps_points(payload: bytes) -> list[tuple[int, float, float]]:
    """WORKOUT_GPS (FFFF:A05A): 12-byte points, ts · longitude×1e7 · latitude×1e7 (LE, spec §9) → (ts, lat, lon)."""
    if len(payload) % 12:
        raise ParseError(f"GPS payload of {len(payload)} bytes isn't a multiple of 12")
    return [
        (ts, lat / 1e7, lon / 1e7)
        for ts, lon, lat in struct.iter_unpack("<iii", payload)
        if -90 <= lat / 1e7 <= 90 and -180 <= lon / 1e7 <= 180 and (lat or lon)
    ]


WORKOUT_PARSERS = {WORKOUT_SUMMARY: workouts_v1, WORKOUT_SUMMARY_V3: workout_v3}


SAMPLE_PARSERS = {
    ACTIVITY_DATA: activity,
    HEART_RATE: heart_rate,
    HEART_RATE_WORKOUT: heart_rate_workout,
    HEART_RATE_RESTING: heart_rate_resting,
    SPO2: spo2,
    STRESS: stress,
}

DATA_OPS = set(SAMPLE_PARSERS) | {SLEEP_DATA, WORKOUT_GPS} | WORKOUT_OPS | RAW_ONLY
