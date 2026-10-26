"""Parsers for health-sync frames. Layouts per docs/protocol-sync.md (little-endian, epoch seconds UTC).

Pure functions: payload bytes in, records out. Bump PARSER_VERSION whenever output changes, so stored
raw frames can be re-parsed (store.raw_frames.parser_version).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from cmfkit.protocol.opcodes import Op

PARSER_VERSION = 1

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

# stored raw, not parsed yet (workout layouts are only partly known)
RAW_ONLY = {WORKOUT_SUMMARY, WORKOUT_SUMMARY_V3, WORKOUT_GPS}

SLEEP_STAGES = {1: "deep", 2: "core", 3: "rem", 4: "awake"}


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
    """Cumulative counters (reset daily): steps, distance in m, calories in cal (not kcal)."""
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


SAMPLE_PARSERS = {
    ACTIVITY_DATA: activity,
    HEART_RATE: heart_rate,
    HEART_RATE_WORKOUT: heart_rate_workout,
    HEART_RATE_RESTING: heart_rate_resting,
    SPO2: spo2,
    STRESS: stress,
}

DATA_OPS = set(SAMPLE_PARSERS) | {SLEEP_DATA} | RAW_ONLY
