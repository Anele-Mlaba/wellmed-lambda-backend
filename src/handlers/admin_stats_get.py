"""GET /api/admin/stats?from=YYYY-MM-DD&to=YYYY-MM-DD"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timedelta, timezone
from typing import Any

from boto3.dynamodb.conditions import Key
from ..lib import dynamo

from ..lib import jwt_util
from ..lib.http import ok, unauthorized
from ..lib.log_util import bind
from ..lib.time_util import age_band_from_dob, age_band_from_id, gender_from_id, now_utc

_STATUSES = ("pending", "confirmed", "completed", "noshow", "cancelled")
_AGE_BANDS = ("0-17", "18-24", "25-34", "35-44", "45-54", "55+")


def _parse_date(value: str | None, default: datetime) -> datetime:
    if not value:
        return default
    try:
        y, m, d = (int(x) for x in value.split("-"))
        return datetime(y, m, d, tzinfo=timezone.utc)
    except Exception:
        return default


def handler(event: dict[str, Any], _context) -> dict[str, Any]:
    log = bind(__name__, event)
    claims = jwt_util.require_admin(event)
    if claims is None:
        log.warning("event=auth_failed reason=missing_or_invalid_jwt")
        return unauthorized()

    qs = event.get("queryStringParameters") or {}
    now = now_utc()
    default_from = now - timedelta(days=30)
    date_from = _parse_date(qs.get("from"), default_from)
    date_to = _parse_date(qs.get("to"), now)
    log.info(
        "event=request_received userId=%s from=%s to=%s",
        claims.get("sub"), date_from.date().isoformat(), date_to.date().isoformat(),
    )

    slot_from_iso = date_from.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
    slot_to_iso = datetime.combine(date_to.date(), time(23, 59, 59), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

    bookings: list[dict[str, Any]] = []
    for status in _STATUSES:
        kwargs = {
            "IndexName": "GSI2",
            "KeyConditionExpression": Key("GSI2PK").eq(f"STATUS#{status}")
            & Key("GSI2SK").between(f"SLOT#{slot_from_iso}", f"SLOT#{slot_to_iso}"),
        }
        while True:
            resp = dynamo.table.query(**kwargs)
            bookings.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    totals = Counter()
    by_service: Counter = Counter()
    age_bands: dict[str, int] = {b: 0 for b in _AGE_BANDS}
    gender: dict[str, int] = {"F": 0, "M": 0, "Other": 0}

    patient_cache: dict[str, dict[str, Any]] = {}
    now_iso_str = now.isoformat().replace("+00:00", "Z")

    for b in bookings:
        status = b.get("status", "pending")
        totals[status] += 1
        # Upcoming = confirmed and slotStart >= now.
        if status == "confirmed" and (b.get("slotStart") or "") >= now_iso_str:
            totals["upcoming"] += 1
        by_service[b.get("service", "unknown")] += 1

        pid = b.get("patientId")
        if pid and pid not in patient_cache:
            patient_cache[pid] = dynamo.get_patient_by_id(pid) or {}
        patient = patient_cache.get(pid, {})
        id_or_passport = patient.get("idOrPassport", "")
        band = b.get("ageBand") or age_band_from_dob(patient.get("dob")) or age_band_from_id(id_or_passport)
        if band:
            age_bands[band] = age_bands.get(band, 0) + 1
        g = gender_from_id(id_or_passport) or "Other"
        if g not in gender:
            g = "Other"
        gender[g] += 1

    log.info(
        "event=request_ok scanned=%d uniquePatients=%d upcoming=%d confirmed=%d cancelled=%d",
        len(bookings), len(patient_cache),
        totals.get("upcoming", 0), totals.get("confirmed", 0), totals.get("cancelled", 0),
    )
    return ok(
        {
            "totals": {
                "bookings": sum(totals[s] for s in _STATUSES),
                "completed": totals.get("completed", 0),
                "noshow": totals.get("noshow", 0),
                "pending": totals.get("pending", 0),
                "upcoming": totals.get("upcoming", 0),
                "cancelled": totals.get("cancelled", 0),
                "confirmed": totals.get("confirmed", 0),
            },
            "byService": [
                {"service": s, "count": c} for s, c in sorted(by_service.items(), key=lambda kv: -kv[1])
            ],
            "demographics": {
                "ageBands": age_bands,
                "gender": gender,
            },
            "range": {"from": slot_from_iso, "to": slot_to_iso},
        }
    )
