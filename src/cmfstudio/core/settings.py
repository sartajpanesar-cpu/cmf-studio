"""Watch settings: read with ``*_GET`` (the watch answers on the SET opcode with the current value, spec §4.4),
write with ``*_SET`` (acknowledged on cmd2 = 0x0003).

What the Pro 2 (fw 1.0.0.57) actually returns was probed on 2026-10-26 (research/findings.md): GET replies are
little-endian structs, while the refs write alarms and goals big-endian. So every write here is **verified by
reading it back**; where the write encoding is uncertain we try the documented one first, then the watch's own read
format, remember whichever the watch accepted (kv ``settings_encoding``), and restore the old value if neither did.
Settings without a GET (health monitoring, HR alerts, units) are write-only and report only the watch's ack.
"""

from __future__ import annotations

import json
import re
import struct
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from cmfkit.protocol.opcodes import Op
from pydantic import BaseModel, Field, field_validator

from ..ble.link import WatchLink
from ..store.db import Store

GET, SET, ACK = 0x0002, 0x0001, 0x0003

# "group" settings: GET 0002 / SET 0001 / ACK 0003 on one cmd1
GROUPS: dict[str, int] = {
    "alarms": 0x0063,
    "goals": 0x005E,
    "time_format": 0x005F,
    "standing_reminder": 0x0060,
    "water_reminder": 0x0061,
    "wrist_raise": 0x0062,
    "user_info": 0x0095,
    "dnd": 0x0099,
    "health_monitoring": 0x009B,
    "contacts": 0x00D5,
    "sports": 0x00DC,
}
HR_ALERTS = Op(0xFFFF, 0x9059)
UNIT_LENGTH, UNIT_LENGTH_ACK = Op(0xFFFF, 0x9067), Op(0xFFFF, 0xA067)
UNIT_TEMPERATURE, UNIT_TEMPERATURE_ACK = Op(0xFFFF, 0x9068), Op(0xFFFF, 0xA068)
MONITOR_KINDS = {"heart_rate": 0x01, "spo2": 0x02, "stress": 0x04}

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # repetition bitmask, bit 0 = Monday (Gadgetbridge)
MAX_ALARMS = 10
ALARM_SIZE = 40
ENCODING_KEY = "settings_encoding"


class SettingsError(RuntimeError):
    pass


# -- alarms ---------------------------------------------------------------------------------------------------------


class Alarm(BaseModel):
    time: str = Field(description="HH:MM, 24-hour")
    enabled: bool = True
    days: list[str] = Field(default_factory=list, description="mon..sun; empty = one-time")
    label: str = Field("", max_length=32)

    @field_validator("time")
    @classmethod
    def _time(cls, v: str) -> str:
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", v)
        if not m or int(m[1]) > 23 or int(m[2]) > 59:
            raise ValueError("time must be HH:MM (24-hour)")
        return f"{int(m[1]):02d}:{m[2]}"

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[str]) -> list[str]:
        bad = [d for d in v if d not in DAYS]
        if bad:
            raise ValueError(f"days must be among {DAYS}")
        return [d for d in DAYS if d in v]

    @property
    def seconds(self) -> int:
        h, m = self.time.split(":")
        return int(h) * 3600 + int(m) * 60

    @property
    def mask(self) -> int:
        return sum(1 << DAYS.index(d) for d in self.days)


def parse_alarms(raw: bytes) -> list[Alarm]:
    out = []
    for i in range(0, len(raw) - ALARM_SIZE + 1, ALARM_SIZE):
        rec = raw[i : i + ALARM_SIZE]
        seconds = struct.unpack_from("<I", rec)[0]  # the watch's own (read) format is little-endian
        if seconds >= 86400:
            continue  # not a time the watch can show: skip rather than guess
        label = rec[8:40].split(b"\0", 1)[0].decode("utf-8", "replace")
        days = [d for b, d in enumerate(DAYS) if rec[6] & (1 << b)]
        out.append(
            Alarm(
                time=f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}", enabled=bool(rec[5]), days=days, label=label
            )
        )
    return out


def encode_alarms(alarms: list[Alarm], byteorder: str) -> bytes:
    """40 B each: seconds-of-day u32 · index · enabled · repetition · in-use 1 · label[32] UTF-8 (spec §9).

    The watch takes the seconds big-endian and reads them back little-endian (verified 2026-10-26)."""
    fmt = ">I" if byteorder == "big" else "<I"
    out = b""
    for i, a in enumerate(alarms):
        label = a.label.encode("utf-8")[:32]
        out += struct.pack(fmt, a.seconds) + bytes([i, int(a.enabled), a.mask, 1]) + label.ljust(32, b"\0")
    return out


# -- goals ----------------------------------------------------------------------------------------------------------


class Goals(BaseModel):
    steps: int = Field(ge=1000, le=60000)
    distance_m: int = Field(ge=500, le=99000)
    kcal: int = Field(ge=50, le=5000)


def parse_goals(raw: bytes) -> dict[str, Any]:
    """28-byte read form: steps, distance m, kcal, sleep min, exercise min (u32 LE) · stand h · 7 flags."""
    if len(raw) >= 21:
        steps, dist, kcal, sleep, exercise = struct.unpack_from("<5I", raw)
        return {
            "steps": steps,
            "distance_m": dist,
            "kcal": kcal,
            "sleep_min": sleep,
            "exercise_min": exercise,
            "stand_h": raw[20],
        }
    if len(raw) == 10:
        steps, dist, kcal = struct.unpack(">IIH", raw)
        return {"steps": steps, "distance_m": dist, "kcal": kcal}
    raise SettingsError(f"unexpected goals payload {raw.hex()}")


def encode_goals(g: Goals, form: str, current: bytes | None = None) -> bytes:
    if form == "short_be":  # spec §9 / Gadgetbridge: the official app's 10-byte form
        return struct.pack(">IIH", g.steps, g.distance_m, g.kcal)
    if current is None or len(current) < 21:
        raise SettingsError("need the watch's current goals to write the long form")
    return struct.pack("<3I", g.steps, g.distance_m, g.kcal) + current[12:]  # keep sleep/exercise/stand/flags


# -- reminders ------------------------------------------------------------------------------------------------------


class Reminder(BaseModel):
    enabled: bool
    every_min: int = Field(60, ge=30, le=180)
    quiet_start: str | None = Field(None, description="HH:MM: no reminders from here…")
    quiet_end: str | None = Field(None, description="…until here")


def _hhmm(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def _secs(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 3600 + int(m) * 60


def parse_reminder(raw: bytes) -> Reminder:
    """11 B, big-endian on fw 1.0.0.57 (probe 2026-10-26): enabled · minutes u16 · quiet start u32 · quiet end u32."""
    enabled, every, start, end = struct.unpack(">BHII", raw[:11])
    quiet = (start or end) and start != end
    return Reminder(
        enabled=bool(enabled),
        every_min=max(30, min(180, every)),
        quiet_start=_hhmm(start) if quiet else None,
        quiet_end=_hhmm(end) if quiet else None,
    )


def encode_reminder(r: Reminder) -> bytes:
    start = _secs(r.quiet_start) if r.enabled and r.quiet_start and r.quiet_end else 0
    end = _secs(r.quiet_end) if r.enabled and r.quiet_start and r.quiet_end else 0
    return struct.pack(">BHII", int(r.enabled), r.every_min, start, end)


# -- home tiles (QUICK_CARD, FFFF 906D/A06D; spec §16) — READ ONLY ------------------------------------------------
# A SET with an unknown card id wipes the watch's screens (recoverable only via Nothing X), and the SET framing isn't
# documented, so tiles are only read here.

QUICK_CARD, QUICK_CARD_RET = Op(0xFFFF, 0x906D), Op(0xFFFF, 0xA06D)
CARD_TYPES = [
    (1, 6, "Steps"),
    (7, 12, "Calories"),
    (13, 18, "Stand"),
    (19, 24, "Moderate activity"),
    (25, 30, "Heart rate"),
    (31, 36, "SpO₂"),
    (37, 42, "Stress"),
    (43, 48, "Sleep"),
    (49, 53, "Weather"),
    (97, 98, "Weather"),
    (54, 58, "Timer"),
    (59, 62, "Breathing"),
    (63, 63, "Stopwatch"),
    (65, 67, "Stopwatch"),
    (68, 71, "Battery"),
    (72, 76, "Recents"),
    (77, 81, "Contacts"),
    (82, 86, "Dial / phone"),
    (87, 91, "Sport"),
    (92, 92, "Music"),
    (93, 93, "Activity record"),
    (94, 94, "PAI"),
    (96, 96, "Cycle"),
]


def card_name(card_id: int) -> str:
    if card_id == 0:
        return "empty"
    return next((n for lo, hi, n in CARD_TYPES if lo <= card_id <= hi), f"card {card_id}")


def parse_tiles(raw: bytes) -> list[list[dict[str, Any]]]:
    """``status · 00 · N · N × (01 · K · K × (card id, sport id))`` → screens of cards."""
    if len(raw) < 3:
        raise SettingsError(f"unexpected tiles reply {raw.hex()}")
    screens, i = [], 3
    for _ in range(raw[2]):
        if i + 2 > len(raw) or raw[i] != 0x01:
            raise SettingsError(f"unexpected tiles reply {raw.hex()}")
        k = raw[i + 1]
        cards = raw[i + 2 : i + 2 + 2 * k]
        screens.append(
            [{"id": cards[j], "sport": cards[j + 1], "name": card_name(cards[j])} for j in range(0, 2 * k, 2)]
        )
        i += 2 + 2 * k
    return screens


# -- contacts as an info panel (CONTACTS_SET 00D5 0001: up to 20 × name[32] + number[25], spec §9) ------------------

CONTACT_NAME, CONTACT_NUMBER, MAX_CONTACTS = 32, 25, 20


def parse_contacts(raw: bytes) -> list[dict[str, str]]:
    out = []
    for i in range(0, len(raw) - 56, 57):
        name = raw[i : i + 32].split(b"\0", 1)[0].decode("utf-8", "replace")
        number = raw[i + 32 : i + 57].split(b"\0", 1)[0].decode("utf-8", "replace")
        if name or number:  # an all-zero record is how the list is cleared (an empty write isn't taken)
            out.append({"name": name, "number": number})
    return out


def _fit(text: str, n: int) -> bytes:
    raw = text.encode("utf-8")[:n]
    while raw:
        try:
            raw.decode("utf-8")
            return raw
        except UnicodeDecodeError:
            raw = raw[:-1]
    return raw


def encode_contacts(entries: list[dict[str, str]]) -> bytes:
    """``[]`` encodes as one all-zero record: the watch ignores an empty write (findings 2026-10-26)."""
    if len(entries) > MAX_CONTACTS:
        raise SettingsError(f"the watch shows up to {MAX_CONTACTS} contacts")
    if not entries:
        return bytes(CONTACT_NAME + CONTACT_NUMBER)
    return b"".join(
        _fit(e["name"], CONTACT_NAME).ljust(CONTACT_NAME, b"\0")
        + _fit(e.get("number", ""), CONTACT_NUMBER).ljust(CONTACT_NUMBER, b"\0")
        for e in entries
    )


class ContactLine(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    number: str = Field("", max_length=25)


# -- request bodies -------------------------------------------------------------------------------------------------


class Switch(BaseModel):
    on: bool


class HrAlerts(BaseModel):
    low: int | None = Field(None, ge=30, le=120)
    high: int | None = Field(None, ge=80, le=220)
    sport_high: int | None = Field(None, ge=80, le=220)
    spo2_low: int | None = Field(None, ge=70, le=99)


class RawSetting(BaseModel):
    name: str
    hex: str = Field(max_length=800, pattern=r"^[0-9a-fA-F]*$")


class Units(BaseModel):
    metric: bool = True
    celsius: bool = True


# -- sports menu ------------------------------------------------------------------------------------------------------

# Activity type codes -> names, from Gadgetbridge's CmfActivityType (refs/Gadgetbridge); the watch's menu holds
# up to 36. All 16 codes in the owner's menu resolve (probe 2026-10-26).
SPORTS: dict[int, str] = {
    0x01: "Outdoor walking",
    0x02: "Outdoor running",
    0x03: "Indoor running",
    0x04: "Mountain hike",
    0x05: "Outdoor cycling",
    0x06: "Jump roping",
    0x07: "Soccer",
    0x08: "Basketball",
    0x09: "Badminton",
    0x0A: "Table tennis",
    0x0B: "Cricket",
    0x0C: "Tennis",
    0x0D: "Dynamic cycle",
    0x0E: "Rowing machine",
    0x0F: "Yoga",
    0x10: "Free training",
    0x11: "Sit ups",
    0x13: "Strength training",
    0x15: "Stairs",
    0x18: "Cross trainer",
    0x19: "Indoor walking",
    0x1A: "Hiking",
    0x1B: "Cross country running",
    0x1D: "Horse riding",
    0x1E: "Hockey",
    0x1F: "Tai chi",
    0x20: "Shuttlecock",
    0x21: "Boxing",
    0x22: "Skiing",
    0x23: "Gymnastics",
    0x24: "Ice hockey",
    0x25: "Taekwondo",
    0x26: "Treadmill",
    0x27: "Track and field",
    0x29: "Karate",
    0x2B: "Cross training",
    0x2C: "Pilates",
    0x2E: "Functional training",
    0x2F: "Physical training",
    0x30: "Archery",
    0x31: "Flexibility",
    0x33: "Latin dance",
    0x34: "Street dance",
    0x35: "Kickboxing",
    0x36: "Ballet",
    0x37: "Australian football",
    0x38: "Martial arts",
    0x39: "Handball",
    0x3A: "Baseball",
    0x3B: "Bowling",
    0x3C: "Squash",
    0x3D: "Curling",
    0x3E: "Snowboarding",
    0x42: "Fishing",
    0x43: "Disc sports",
    0x44: "Rugby",
    0x45: "Golf",
    0x48: "Snow sports",
    0x49: "Volleyball",
    0x4B: "Core training",
    0x4C: "Skating",
    0x4D: "Fitness gaming",
    0x4E: "Fitness exercises",
    0x50: "Tae bo",
    0x53: "Wrestling",
    0x54: "Fencing",
    0x55: "Softball",
    0x56: "Horizontal bar",
    0x57: "Parallel bar",
    0x58: "Roller skating",
    0x59: "Hula hooping",
    0x5A: "Darts",
    0x5B: "Pickleball",
    0x5C: "HIIT",
    0x5F: "Trampoline",
    0x60: "Skateboarding",
    0x62: "Parkour",
    0x64: "Surfing",
    0x66: "Pull ups",
    0x67: "Push ups",
    0x68: "Plank",
    0x69: "Rock climbing",
    0x6A: "High jump",
    0x6C: "Long jump",
    0x6E: "Cross country skiing",
    0x70: "Kite flying",
    0x71: "Swing",
    0x72: "Indoor cycling",
    0x73: "Stair stepper",
    0x74: "Crossfit",
    0x75: "Kendo",
    0x76: "Belly dance",
    0x77: "Jazz dance",
    0x78: "Dance",
    0x7C: "Billiards",
    0x7D: "Beach soccer",
    0x7E: "Beach volleyball",
    0x7F: "Gateball",
    0x80: "Sepak takraw",
    0x81: "Dodgeball",
    0x82: "Sailing",
    0x87: "Jet skiing",
    0x8A: "Luge",
    0x8E: "Parachuting",
    0x8F: "Auto racing",
    0x92: "Cooldown",
    0x93: "Hunting",
    0x94: "Aerobic exercise",
    0x95: "Rolling",
    0x96: "Hand cycling",
    0x97: "Mind and body",
    0x98: "Lacross",
    0x99: "Battle rope",
    0x9A: "Smith machine",
    0x9B: "Zumba",
    0x9C: "Kabaddi",
    0x9D: "Shot",
    0x9E: "Javelin",
    0x9F: "Dumbbell",
    0xA0: "Karting",
}


def parse_sports(raw: bytes) -> list[int]:
    return list(raw[1 : 1 + raw[0]]) if raw else []


def encode_sports(codes: list[int]) -> bytes:
    if not 1 <= len(codes) <= 36:
        raise SettingsError("the sports menu holds 1–36 sports")
    return bytes([len(codes), *codes])


# -- service --------------------------------------------------------------------------------------------------------


@dataclass
class WriteResult:
    ok: bool
    acked: bool
    encoding: str | None = None
    tried: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return asdict(self)


class SettingsService:
    def __init__(self, link: WatchLink, store: Store) -> None:
        self.link = link
        self.store = store

    # -- raw ------------------------------------------------------------------------------------------------------

    async def read(self, name: str) -> bytes | None:
        return await self.link.read_setting(GROUPS[name])

    async def write(self, name: str, payload: bytes) -> bool:
        return await self.link.write_setting(GROUPS[name], SET, payload, ACK)

    def _encodings(self) -> dict[str, str]:
        raw = self.store.get_kv(ENCODING_KEY)
        return json.loads(raw) if raw else {}

    def _learn(self, name: str, encoding: str) -> None:
        enc = self._encodings()
        if enc.get(name) != encoding:
            enc[name] = encoding
            self.store.set_kv(ENCODING_KEY, json.dumps(enc))
            self.store.add_event("settings", {"learned": name, "encoding": encoding})

    async def _write_verified(
        self, name: str, candidates: dict[str, bytes], check, restore: bytes | None = None
    ) -> WriteResult:  # type: ignore[no-untyped-def]
        """Try each encoding (the learned one first) until reading back satisfies ``check``. If none does, write
        ``restore`` — the previous value in the WRITE format (read and write formats differ, so echoing the raw
        read bytes would corrupt it: findings 2026-10-26). Without one, nothing is restored."""
        learned = self._encodings().get(name)
        order = sorted(candidates, key=lambda k: k != learned)
        result = WriteResult(ok=False, acked=False)
        for enc in order:
            result.tried.append(enc)
            result.acked = await self.write(name, candidates[enc]) or result.acked
            back = await self.read(name)
            if back is not None and check(back):
                self._learn(name, enc)
                result.ok, result.encoding = True, enc
                return result
        if restore is not None:  # nothing stuck: put the watch back how it was
            await self.write(name, restore)
        return result

    # -- typed ----------------------------------------------------------------------------------------------------

    async def snapshot(self) -> dict[str, Any]:
        """Everything readable, decoded (None where the watch didn't answer)."""
        out: dict[str, Any] = {}
        raw = {
            n: await self.read(n)
            for n in (
                "alarms",
                "goals",
                "time_format",
                "standing_reminder",
                "water_reminder",
                "wrist_raise",
                "dnd",
                "sports",
            )
        }
        out["alarms"] = [a.model_dump() for a in parse_alarms(raw["alarms"])] if raw["alarms"] is not None else None
        out["goals"] = parse_goals(raw["goals"]) if raw["goals"] else None
        out["time_format"] = ("12h" if raw["time_format"][0] else "24h") if raw["time_format"] else None
        for r in ("standing_reminder", "water_reminder"):
            out[r] = parse_reminder(raw[r]).model_dump() if raw[r] and len(raw[r]) >= 11 else None
        out["wrist_raise"] = bool(raw["wrist_raise"][0]) if raw["wrist_raise"] else None
        out["dnd"] = bool(raw["dnd"][0]) if raw["dnd"] else None
        codes = parse_sports(raw["sports"]) if raw["sports"] else []
        out["sports"] = (
            [{"code": c, "name": SPORTS.get(c, f"Sport {c:#04x}")} for c in codes] if raw["sports"] else None
        )
        out["sports_available"] = [{"code": c, "name": n} for c, n in SPORTS.items()]
        return out

    async def set_alarms(self, alarms: list[Alarm]) -> WriteResult:
        if len(alarms) > MAX_ALARMS:
            raise SettingsError(f"the watch holds up to {MAX_ALARMS} alarms")
        current = await self.read("alarms")
        restore = encode_alarms(parse_alarms(current), "big") if current is not None else None
        # the watch keeps its alarms sorted by time and sets byte 7 itself (findings 2026-10-26)
        want = sorted((a.time, a.enabled, a.days, a.label[:32]) for a in alarms)
        return await self._write_verified(
            "alarms",
            {"be": encode_alarms(alarms, "big")},  # verified on-device: BE in, LE out (findings 2026-10-26)
            lambda back: sorted((a.time, a.enabled, a.days, a.label) for a in parse_alarms(back)) == want,
            restore,
        )

    async def set_goals(self, goals: Goals) -> WriteResult:
        """The official app's 10-byte BE form (spec §9). The watch acks it, but its goals GET doesn't change on
        fw 1.0.0.57 (findings 2026-10-26), so this can't be verified by read-back: ``ok`` = acked."""
        acked = await self.write("goals", encode_goals(goals, "short_be"))
        self.store.set_kv("goals_sent", json.dumps(goals.model_dump()))
        return WriteResult(ok=acked, acked=acked, encoding="short_be (unverified)")

    async def set_simple(self, name: str, value: bool) -> WriteResult:
        """One-byte switches: dnd, wrist_raise; time_format takes True for 12h."""
        if name not in ("dnd", "wrist_raise", "time_format"):
            raise SettingsError(f"{name} isn't a switch")
        before = await self.read(name)
        return await self._write_verified(
            name, {"byte": bytes([int(value)])}, lambda back: back[:1] == bytes([value]), before[:1] if before else None
        )

    async def set_reminder(self, name: str, r: Reminder) -> WriteResult:
        if name not in ("standing_reminder", "water_reminder"):
            raise SettingsError(f"{name} isn't a reminder")
        payload = encode_reminder(r)
        before = await self.read(name)  # same format both ways (findings 2026-10-26)
        return await self._write_verified(name, {"be": payload}, lambda back: back[:11] == payload, before)

    async def set_sports(self, codes: list[int]) -> WriteResult:
        payload = encode_sports(codes)
        before = await self.read("sports")  # count + codes, same format both ways
        return await self._write_verified(
            "sports", {"list": payload}, lambda back: back[: len(payload)] == payload, before
        )

    async def tiles(self) -> list[list[dict[str, Any]]]:
        """The watch's home screens and their cards (read-only)."""
        async with self.link._op() as session:
            reply = await session.request(QUICK_CARD, b"\x00", QUICK_CARD_RET, timeout=3)
        return parse_tiles(reply.payload)

    async def contacts(self) -> list[dict[str, str]]:
        raw = await self.read("contacts")
        return parse_contacts(raw or b"")

    async def set_contacts(self, entries: list[ContactLine]) -> WriteResult:
        """Replace the watch's contacts (e.g. as an info panel). The old list is backed up in kv first."""
        before = await self.read("contacts")
        backups = json.loads(self.store.get_kv("contacts_backups") or "[]")
        if before:
            backups = ([{"saved_at": time.time(), "contacts": parse_contacts(before)}] + backups)[:5]
            self.store.set_kv("contacts_backups", json.dumps(backups))
        payload = encode_contacts([e.model_dump() for e in entries])
        want = [(_fit(e.name, CONTACT_NAME).decode(), _fit(e.number, CONTACT_NUMBER).decode()) for e in entries]
        return await self._write_verified(
            "contacts",
            {"list": payload},
            lambda back: [(c["name"], c["number"]) for c in parse_contacts(back)] == want,
            before,
        )

    async def set_monitoring(self, kind: str, enabled: bool) -> WriteResult:
        """All-day heart rate / SpO2 / stress measurement (write-only: the watch's read-back isn't understood)."""
        payload = bytes([MONITOR_KINDS[kind], int(enabled)])
        acked = await self.link.write_setting(GROUPS["health_monitoring"], SET, payload, ACK)
        if acked:
            sent = json.loads(self.store.get_kv("monitoring") or "{}")
            self.store.set_kv("monitoring", json.dumps({**sent, kind: enabled}))
        return WriteResult(ok=acked, acked=acked, encoding="kind+switch")

    async def set_hr_alerts(
        self, low: int | None, high: int | None, sport_high: int | None, spo2_low: int | None
    ) -> WriteResult:
        """HR / SpO2 alerts (spec §9, Gadgetbridge): 00 = off, else 01 · low · high · sport high · SpO2 low · 4×00."""
        if not any((low, high, sport_high, spo2_low)):
            payload = b"\x00"
        else:
            payload = bytes([1, low or 0, high or 255, sport_high or 255, spo2_low or 0, 0, 0, 0, 0])
        await self.link.write_setting(HR_ALERTS.cmd1, HR_ALERTS.cmd2, payload, None)
        self.store.set_kv(
            "hr_alerts", json.dumps({"low": low, "high": high, "sport_high": sport_high, "spo2_low": spo2_low})
        )
        return WriteResult(ok=True, acked=False, encoding="spec")

    async def set_units(self, metric: bool, celsius: bool) -> WriteResult:
        a = await self.link.write_setting(
            UNIT_LENGTH.cmd1, UNIT_LENGTH.cmd2, bytes([1, 0 if metric else 1]), UNIT_LENGTH_ACK.cmd2
        )
        b = await self.link.write_setting(
            UNIT_TEMPERATURE.cmd1, UNIT_TEMPERATURE.cmd2, bytes([1, 0 if celsius else 1]), UNIT_TEMPERATURE_ACK.cmd2
        )
        self.store.set_kv("units", json.dumps({"metric": metric, "celsius": celsius}))
        return WriteResult(ok=a and b, acked=a and b, encoding="gadgetbridge")
