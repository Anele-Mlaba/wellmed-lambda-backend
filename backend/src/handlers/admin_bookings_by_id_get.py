"""GET /api/admin/bookings/{id} — full booking record (intake included)."""

from __future__ import annotations

from typing import Any

from ..lib import dynamo, jwt_util
from ..lib.http import not_found, ok, unauthorized


def _resolve_booking(identifier: str) -> dict[str, Any] | None:
    if identifier.upper().startswith("WM-"):
        return dynamo.get_booking_by_short_id(identifier.upper())
    return dynamo.get_booking_by_id(identifier)


def handler(event: dict[str, Any], _context) -> dict[str, Any]:
    if jwt_util.require_admin(event) is None:
        return unauthorized()

    path = event.get("pathParameters") or {}
    identifier = path.get("id", "")
    if not identifier:
        return not_found()

    booking = _resolve_booking(identifier)
    if not booking:
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
    return ok(payload)
