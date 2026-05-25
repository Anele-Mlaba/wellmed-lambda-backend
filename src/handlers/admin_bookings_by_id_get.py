"""GET /api/admin/bookings/{id} — full booking record (intake included)."""

from __future__ import annotations

from typing import Any

from ..lib import dynamo

from ..lib import jwt_util
from ..lib.http import not_found, ok, unauthorized
from ..lib.log_util import bind


def _resolve_booking(identifier: str) -> dict[str, Any] | None:
    if identifier.upper().startswith("WM-"):
        return dynamo.get_booking_by_short_id(identifier.upper())
    return dynamo.get_booking_by_id(identifier)


def handler(event: dict[str, Any], _context) -> dict[str, Any]:
    log = bind(__name__, event)
    claims = jwt_util.require_admin(event)
    if claims is None:
        log.warning("event=auth_failed reason=missing_or_invalid_jwt")
        return unauthorized()

    path = event.get("pathParameters") or {}
    identifier = path.get("id", "")
    log.info("event=request_received userId=%s identifier=%s", claims.get("sub"), identifier or "<missing>")
    if not identifier:
        log.warning("event=not_found reason=missing_identifier")
        return not_found()

    booking = _resolve_booking(identifier)
    if not booking:
        log.warning("event=not_found identifier=%s", identifier)
        return not_found()

    patient = dynamo.get_patient_by_id(booking.get("patientId", "")) if booking.get("patientId") else None
    payload = {
        "id": booking.get("shortId"),
        "bookingId": booking.get("bookingId"),
        "status": booking.get("status"),
        "service": booking.get("service"),
        "slotStart": booking.get("slotStart"),
        "slotEnd": booking.get("slotEnd"),
        "source": booking.get("source"),
        "googleEventId": booking.get("googleEventId"),
        "intake": booking.get("intake") or {},
        "createdAt": booking.get("createdAt"),
        "updatedAt": booking.get("updatedAt"),
        "confirmationSentAt": booking.get("confirmationSentAt"),
        "reminderSentAt": booking.get("reminderSentAt"),
        "patient": patient and {
            "patientId": patient.get("patientId"),
            "firstName": patient.get("firstName"),
            "lastName": patient.get("lastName"),
            "email": patient.get("email"),
            "phone": patient.get("phone"),
            "idOrPassport": patient.get("idOrPassport"),
            "emergencyName": patient.get("emergencyName"),
            "emergencyPhone": patient.get("emergencyPhone"),
            "medicalAid": patient.get("medicalAid"),
            "marketingOptIn": patient.get("marketingOptIn", False),
        },
    }
    log.info(
        "event=request_ok bookingId=%s shortId=%s status=%s patientId=%s",
        booking.get("bookingId"), booking.get("shortId"), booking.get("status"), booking.get("patientId"),
    )
    return ok(payload)
