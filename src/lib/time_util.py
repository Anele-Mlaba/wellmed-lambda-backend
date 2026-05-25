"""Time + slot math. Server stores UTC; SAST is for display only."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

SAST = ZoneInfo(os.environ.get("PRACTICE_TIMEZONE", "Africa/Johannesburg"))

_WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return iso(now_utc())


def iso(dt: datetime) -> str:
    """ISO-8601 UTC, second precision, Z suffix — what we store everywhere."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc).replace(microsecond=0)
    return dt.isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    """Parse ISO-8601 (handles trailing Z) → aware UTC datetime."""
    cleaned = value.replace("Z", "+00:00") if value.endswith("Z") else value
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_sast(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SAST)


def format_sast(dt: datetime, fmt: str = "%a %d %b %Y, %H:%M") -> str:
    return to_sast(dt).strftime(fmt)


def weekday_key(dt: datetime) -> str:
    return _WEEKDAY_KEYS[to_sast(dt).weekday()]


def build_day_slots(
    date_str: str,
    business_hours: dict | list | None,
    duration_minutes: int,
    buffer_minutes: int,
) -> list[datetime]:
    """Given a YYYY-MM-DD (in SAST) and a {open, close} window, emit UTC slot-start datetimes."""
    if not business_hours:
        return []
    if isinstance(business_hours, list):
        if len(business_hours) != 2:
            return []
        open_str, close_str = business_hours
    elif isinstance(business_hours, dict):
        open_str = business_hours.get("open")
        close_str = business_hours.get("close")
        if not open_str or not close_str:
            return []
    else:
        return []

    year, month, day = (int(x) for x in date_str.split("-"))
    open_h, open_m = (int(x) for x in open_str.split(":"))
    close_h, close_m = (int(x) for x in close_str.split(":"))

    start_local = datetime(year, month, day, open_h, open_m, tzinfo=SAST)
    close_local = datetime(year, month, day, close_h, close_m, tzinfo=SAST)

    step = timedelta(minutes=duration_minutes + buffer_minutes)
    visit_len = timedelta(minutes=duration_minutes)

    slots: list[datetime] = []
    cur = start_local
    while cur + visit_len <= close_local:
        slots.append(cur.astimezone(timezone.utc))
        cur += step
    return slots


def slot_end(slot_start_utc: datetime, duration_minutes: int) -> datetime:
    return slot_start_utc + timedelta(minutes=duration_minutes)


def is_future(dt: datetime, *, grace_seconds: int = 60) -> bool:
    return dt > (now_utc() - timedelta(seconds=grace_seconds))


def label_sast(dt: datetime) -> str:
    return to_sast(dt).strftime("%H:%M")


def age_band_from_id(id_or_passport: str) -> str | None:
    """South-African ID first 6 digits encode YYMMDD. Returns coarse age band, or None."""
    digits = "".join(ch for ch in id_or_passport if ch.isdigit())
    if len(digits) < 6:
        return None
    yy = int(digits[0:2])
    mm = int(digits[2:4])
    dd = int(digits[4:6])
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return None
    today = datetime.now(SAST).date()
    century = 2000 if yy <= (today.year % 100) else 1900
    try:
        dob = datetime(century + yy, mm, dd).date()
    except ValueError:
        return None
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    if age < 0 or age > 120:
        return None
    if age < 18:
        return "0-17"
    if age < 25:
        return "18-24"
    if age < 35:
        return "25-34"
    if age < 45:
        return "35-44"
    if age < 55:
        return "45-54"
    return "55+"


def gender_from_id(id_or_passport: str) -> str | None:
    """SA-ID digits 7-10 encode gender (>=5000 → male). Returns 'M', 'F', or None."""
    digits = "".join(ch for ch in id_or_passport if ch.isdigit())
    if len(digits) < 10:
        return None
    try:
        seq = int(digits[6:10])
    except ValueError:
        return None
    return "M" if seq >= 5000 else "F"
