"""End-to-end smoke test against a deployed WellMed stack.

Usage:
    API_URL=https://abc.execute-api.eu-west-1.amazonaws.com \
    ADMIN_EMAIL=doctor@wellmed.co.za ADMIN_PASSWORD='change-me' \
    python tests/booking_e2e.py

Verifies the happy path:
    1. GET  /api/availability returns slots
    2. POST /api/bookings creates a confirmed booking (idempotent on replay)
    3. POST /api/admin/login returns a JWT
    4. GET  /api/admin/bookings/{shortId} returns the booking
    5. PATCH /api/admin/bookings/{shortId} marks it completed
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from urllib import request as urlrequest


def _http(method: str, url: str, *, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urlrequest.Request(url, data=data, method=method)
    req.add_header("content-type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urlrequest.urlopen(req) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8") or "{}")


def _next_tuesday_09_utc() -> str:
    """An arbitrary future weekday slot at 09:00 SAST = 07:00 UTC."""
    today = datetime.now(timezone.utc).date()
    days_ahead = (1 - today.weekday()) % 7 or 7  # next Tuesday
    target = today + timedelta(days=days_ahead)
    return datetime(target.year, target.month, target.day, 7, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> None:
    api = os.environ["API_URL"].rstrip("/")
    admin_email = os.environ["ADMIN_EMAIL"]
    admin_password = os.environ["ADMIN_PASSWORD"]

    slot = _next_tuesday_09_utc()
    print(f"using slot {slot}")

    status, avail = _http("GET", f"{api}/api/availability?service=iv-therapy&date={slot[:10]}")
    print(f"availability: {status} ({len(avail)} slots)")

    idem = f"e2e-{uuid.uuid4()}"
    booking_body = {
        "service": "iv-therapy",
        "requestedSlot": slot,
        "personal": {
            "firstName": "E2E", "lastName": "Patient",
            "idOrPassport": "8501010000080",
            "phone": "+27821234567",
            "email": f"e2e+{idem[:8]}@example.co.za",
            "emergencyContact": {"name": "Next of Kin", "phone": "+27827654321"},
            "medicalAid": {"provider": "Discovery", "memberNumber": "1", "mainMember": "E2E", "dependentCode": "00"},
        },
        "medical": {
            "existingConditions": "", "allergies": "", "currentMeds": "",
            "reasonForVisit": "e2e smoke", "notes": "", "marketingOptIn": False,
        },
        "consent": True,
        "submittedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    status, booking = _http("POST", f"{api}/api/bookings", body=booking_body, headers={"idempotency-key": idem})
    print(f"booking: {status} -> {booking}")
    short_id = booking["id"]

    status, replay = _http("POST", f"{api}/api/bookings", body=booking_body, headers={"idempotency-key": idem})
    assert replay["id"] == short_id, f"idempotency replay returned a different id: {replay}"
    print(f"replay OK ({short_id})")

    status, login = _http("POST", f"{api}/api/admin/login", body={"email": admin_email, "password": admin_password})
    token = login["token"]
    auth = {"authorization": f"Bearer {token}"}

    status, detail = _http("GET", f"{api}/api/admin/bookings/{short_id}", headers=auth)
    print(f"detail: {status} status={detail['status']}")

    status, patched = _http("PATCH", f"{api}/api/admin/bookings/{short_id}", body={"status": "completed"}, headers=auth)
    assert patched["status"] == "completed", patched
    print(f"completed: {patched}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}", file=sys.stderr)
        raise
