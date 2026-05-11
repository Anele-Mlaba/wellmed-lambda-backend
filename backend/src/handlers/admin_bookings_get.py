"""GET /api/admin/bookings — list bookings with filters."""

from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from typing import Any

from boto3.dynamodb.conditions import Attr, Key

from ..lib import dynamo, jwt_util
from ..lib.http import ok, unauthorized
from ..lib.time_util import age_band_from_id, gender_from_id

_LOG = logging.getLogger(__name__)


_STATUSES = ("pending", "confirmed", "completed", "noshow", "cancelled")


def _date_to_utc_iso(date_str: str, end_of_day: bool = False) -> str:
    """YYYY-MM-DD -> ISO-8601 UTC at start/end of that calendar day."""
    y, m, d = (int(x) for x in date_str.split("-"))
    base = datetime(y, m, d, tzinfo=timezone.utc)
    if end_of_day:
        base = datetime.combine(base.date(), time(23, 59, 59), tzinfo=timezone.utc)
    return base.isoformat().replace("+00:00", "Z")


def handler(event: dict[str, Any], _context) -> dict[str, Any]:
    if jwt_util.require_admin(event) is None:
        return unauthorized()

    qs = event.get("queryStringParameters") or {}
    statuses = [qs["status"]] if qs.get("status") in _STATUSES else list(_STATUSES)
    service = qs.get("service")
    date_from = qs.get("from")
    date_to = qs.get("to")
    q = (qs.get("q") or "").strip().lower()

    slot_from = _date_to_utc_iso(date_from) if date_from else "0000-01-01T00:00:00Z"
    slot_to = _date_to_utc_iso(date_to, end_of_day=True) if date_to else "9999-12-31T23:59:59Z"

    bookings: list[dict[str, Any]] = []
    for status in statuses:
        kwargs = {
            "IndexName": "GSI2",
            "KeyConditionExpression": Key("GSI2PK").eq(f"STATUS#{status}")
            & Key("GSI2SK").between(f"SLOT#{slot_from}", f"SLOT#{slot_to}"),
        }
        if service:
            kwargs["FilterExpression"] = Attr("service").eq(service)
        while True:
            resp = dynamo.table.query(**kwargs)
            bookings.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    # Build summary rows; we need each booking's patient for the display name.
    patient_cache: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for b in bookings:
        pid = b.get("patientId")
        patient = patient_cache.get(pid)
        if patient is None and pid:
            patient = dynamo.get_patient_by_id(pid) or {}
            patient_cache[pid] = patient
        patient = patient or {}
        first = patient.get("firstName", "")
        last = patient.get("lastName", "")
        full_name = f"{first} {last}".strip()
        if q and q not in full_name.lower() and q not in str(b.get("shortId", "")).lower():
            continue

        id_or_passport = patient.get("idOrPassport", "")
        medical_aid = (patient.get("medicalAid") or {}).get("provider") or ""

        rows.append(
            {
                "id": b.get("shortId"),
                "bookingId": b.get("bookingId"),
                "patient": full_name,
                "service": b.get("service"),
                "slot": b.get("slotStart"),
                "status": b.get("status"),
                "source": b.get("source", "online"),
                "ageBand": age_band_from_id(id_or_passport) or "",
                "gender": gender_from_id(id_or_passport) or "",
                "medicalAid": medical_aid,
            }
        )

    rows.sort(key=lambda r: r.get("slot") or "")
    return ok(rows)
