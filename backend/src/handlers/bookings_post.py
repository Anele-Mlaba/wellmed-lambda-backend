"""POST /api/bookings — create patient (upsert), reserve slot, create calendar event, send email."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from botocore.exceptions import ClientError
from pydantic import ValidationError

from ..lib import dynamo, google_calendar, ses
from ..lib.http import bad_request, conflict, parse_body, respond, server_error
from ..lib.ids import allocate_short_id, new_booking_id, new_patient_id
from ..lib.time_util import (
    format_sast,
    is_future,
    iso,
    now_iso,
    parse_iso,
    slot_end,
    to_sast,
    weekday_key,
)
from ..lib.validate import (
    SERVICE_TITLES,
    SERVICES,
    BookingRequest,
    collect_error_fields,
)

_LOG = logging.getLogger(__name__)
_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60


def handler(event: dict[str, Any], _context) -> dict[str, Any]:
    headers = event.get("headers") or {}
    idem_key = headers.get("idempotency-key") or headers.get("Idempotency-Key")

    if idem_key:
        prior = dynamo.get_idempotency(idem_key)
        if prior and prior.get("response"):
            try:
                return respond(201, json.loads(prior["response"]))
            except json.JSONDecodeError:
                pass

    try:
        body = parse_body(event)
    except Exception:
        return bad_request(message="invalid json")

    try:
        req = BookingRequest.model_validate(body)
    except ValidationError as e:
        return bad_request(fields=collect_error_fields(e))

    fields: list[str] = []
    if req.service not in SERVICES:
        fields.append("service")
    if not req.consent:
        fields.append("consent")

    try:
        slot_start_utc = parse_iso(req.requestedSlot)
    except Exception:
        fields.append("requestedSlot")
        slot_start_utc = None

    if fields:
        return bad_request(fields=fields)

    cfg = dynamo.get_service_config(req.service)
    if not cfg:
        return bad_request(message="service_not_configured")

    duration = int(cfg.get("durationMinutes", 30))
    business_hours = (cfg.get("businessHours") or {}).get(weekday_key(slot_start_utc))
    if not business_hours:
        return bad_request(fields=["requestedSlot"], message="closed_on_that_day")

    if not is_future(slot_start_utc):
        return bad_request(fields=["requestedSlot"], message="slot_in_past")

    slot_start_iso = iso(slot_start_utc)
    slot_end_utc = slot_end(slot_start_utc, duration)
    slot_end_iso = iso(slot_end_utc)

    # 1. Upsert patient by email
    email_lc = req.personal.email.lower()
    existing_patient = dynamo.get_patient_by_email(email_lc)
    now = now_iso()

    if existing_patient:
        patient_id = existing_patient["patientId"]
        patient_item = {
            **existing_patient,
            "firstName": req.personal.firstName,
            "lastName": req.personal.lastName,
            "phone": req.personal.phone,
            "emergencyName": req.personal.emergencyContact.name,
            "emergencyPhone": req.personal.emergencyContact.phone,
            "idOrPassport": req.personal.idOrPassport,
            "medicalAid": req.personal.medicalAid.model_dump() if req.personal.medicalAid else None,
            "marketingOptIn": bool(req.medical.marketingOptIn),
            "updatedAt": now,
        }
    else:
        patient_id = new_patient_id()
        patient_item = {
            "PK": f"PATIENT#{patient_id}",
            "SK": "PROFILE",
            "GSI1PK": f"EMAIL#{email_lc}",
            "GSI1SK": "PATIENT",
            "type": "patient",
            "patientId": patient_id,
            "firstName": req.personal.firstName,
            "lastName": req.personal.lastName,
            "idOrPassport": req.personal.idOrPassport,
            "phone": req.personal.phone,
            "email": email_lc,
            "emergencyName": req.personal.emergencyContact.name,
            "emergencyPhone": req.personal.emergencyContact.phone,
            "medicalAid": req.personal.medicalAid.model_dump() if req.personal.medicalAid else None,
            "marketingOptIn": bool(req.medical.marketingOptIn),
            "popiaConsentAt": now,
            "createdAt": now,
            "updatedAt": now,
        }

    dynamo.put_patient(patient_item)

    # 2. Allocate short ID
    short_id = allocate_short_id()
    booking_id = new_booking_id()

    booking_item = {
        "PK": f"BOOKING#{booking_id}",
        "SK": "META",
        "GSI1PK": f"SHORTID#{short_id}",
        "GSI1SK": "BOOKING",
        "GSI2PK": "STATUS#pending",
        "GSI2SK": f"SLOT#{slot_start_iso}",
        "GSI3PK": f"PATIENT#{patient_id}",
        "GSI3SK": f"SLOT#{slot_start_iso}",
        "type": "booking",
        "bookingId": booking_id,
        "shortId": short_id,
        "patientId": patient_id,
        "service": req.service,
        "slotStart": slot_start_iso,
        "slotEnd": slot_end_iso,
        "status": "pending",
        "source": "online",
        "googleEventId": None,
        "intake": {
            "existingConditions": req.medical.existingConditions or "",
            "allergies": req.medical.allergies or "",
            "currentMeds": req.medical.currentMeds or "",
            "reasonForVisit": req.medical.reasonForVisit or "",
            "notes": req.medical.notes or "",
        },
        "createdAt": now,
        "updatedAt": now,
    }

    slot_lock_item = {
        "PK": f"SLOTLOCK#{req.service}#{slot_start_iso}",
        "SK": "LOCK",
        "type": "slot_lock",
        "bookingId": booking_id,
        "createdAt": now,
    }

    # 3. Transactional write — slot uniqueness via ConditionExpression
    try:
        dynamo.write_slotlock_and_booking(slot_lock_item, booking_item)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("TransactionCanceledException", "ConditionalCheckFailedException"):
            return conflict("slot_unavailable")
        _LOG.exception("booking transact_write_items failed")
        return server_error("booking_failed")

    # 4. Google Calendar
    service_title = SERVICE_TITLES.get(req.service, req.service)
    summary = f"WellMed · {service_title} · {req.personal.firstName} {req.personal.lastName}"
    slot_start_local_iso = to_sast(slot_start_utc).isoformat(timespec="seconds")
    slot_end_local_iso = to_sast(slot_end_utc).isoformat(timespec="seconds")

    try:
        google_event_id = google_calendar.create_event(
            summary=summary,
            description=google_calendar.render_event_description(booking_item["intake"]),
            slot_start_local_iso=slot_start_local_iso,
            slot_end_local_iso=slot_end_local_iso,
            patient_email=email_lc,
            patient_name=f"{req.personal.firstName} {req.personal.lastName}",
        )
    except Exception:
        _LOG.exception("google calendar insert failed — booking left at pending")
        # Booking row stays at status=pending. Manual cleanup from admin dashboard.
        return server_error("calendar_unavailable")

    # 5. Confirm booking
    confirmed_at = now_iso()
    dynamo.update_booking(
        booking_id,
        {
            "status": "confirmed",
            "GSI2PK": "STATUS#confirmed",
            "googleEventId": google_event_id,
            "confirmationSentAt": confirmed_at,
            "updatedAt": confirmed_at,
        },
    )

    # 6. Email (fail-soft)
    confirmation_sent = ses.send_booking_confirmation(
        email_lc,
        {
            "firstName": req.personal.firstName,
            "shortId": short_id,
            "serviceTitle": service_title,
            "slotLocal": format_sast(slot_start_utc),
            "calendarLink": f"https://www.google.com/calendar/event?eid={google_event_id}",
            "rescheduleUrl": "https://wellmed.co.za/pages/contact.html",
        },
    )

    response_body = {
        "id": short_id,
        "status": "confirmed",
        "calendarEventId": google_event_id,
        "patientCalendarInviteSent": True,
        "confirmationEmailSent": confirmation_sent,
    }

    if idem_key:
        try:
            dynamo.put_idempotency(idem_key, json.dumps(response_body), int(time.time()) + _IDEMPOTENCY_TTL_SECONDS)
        except Exception:
            _LOG.exception("idempotency persist failed (non-fatal)")

    return respond(201, response_body)
