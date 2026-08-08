"""POST /api/bookings — create patient (upsert), reserve slot, create calendar event, send email."""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import ClientError
from pydantic import ValidationError
from ..lib import dynamo, google_calendar

from ..lib import ses
from ..lib.http import bad_request, conflict, parse_body, respond, server_error
from ..lib.ids import allocate_short_id, new_booking_id, new_patient_id
from ..lib.log_util import bind
from ..lib.time_util import (
    age_band_from_dob,
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
    Medical,
    collect_error_fields,
)

_IDEMPOTENCY_TTL_SECONDS = 24 * 60 * 60


def handler(event: dict[str, Any], _context) -> dict[str, Any]:
    log = bind(__name__, event)
    headers = event.get("headers") or {}
    idem_key = headers.get("idempotency-key") or headers.get("Idempotency-Key")
    log.info("event=request_received idempotencyKey=%s", idem_key or "<none>")

    if idem_key:
        prior = dynamo.get_idempotency(idem_key)
        if prior and prior.get("response"):
            try:
                log.info("event=idempotency_replay idempotencyKey=%s", idem_key)
                return respond(201, json.loads(prior["response"]))
            except json.JSONDecodeError:
                log.warning("event=idempotency_replay_corrupt idempotencyKey=%s", idem_key)

    try:
        body = parse_body(event)
    except Exception:
        log.warning("event=invalid_json")
        return bad_request(message="invalid json")

    try:
        req = BookingRequest.model_validate(body)
    except ValidationError as e:
        fields = collect_error_fields(e)
        log.warning("event=validation_failed fields=%s", fields)
        return bad_request(fields=fields)

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
        log.warning("event=validation_failed fields=%s requestedSlot=%s", fields, req.requestedSlot)
        return bad_request(fields=fields)

    cfg = dynamo.get_service_config(req.service)
    if not cfg:
        log.warning("event=service_not_configured service=%s", req.service)
        return bad_request(message="service_not_configured")

    medical = req.medical if req.medical is not None else Medical()

    # Resolve the selected price option server-side — the client only sends ids.
    pricing_record = None
    if req.pricing:
        catalog = dynamo.get_pricing_catalog()
        found = dynamo.find_pricing_item(catalog, req.pricing.itemId)
        if not found or found[1].get("price") is None:
            log.warning("event=validation_failed field=pricing.itemId value=%s", req.pricing.itemId)
            return bad_request(fields=["pricing.itemId"], message="unknown_pricing_item")
        category, item = found
        extras_by_id = {e.get("id"): e for e in (item.get("extras") or [])}
        chosen_extras = []
        for extra_id in req.pricing.extras:
            extra = extras_by_id.get(extra_id)
            if not extra:
                log.warning("event=validation_failed field=pricing.extras value=%s", extra_id)
                return bad_request(fields=["pricing.extras"], message="unknown_pricing_extra")
            chosen_extras.append(
                {"id": extra["id"], "name": extra.get("name") or extra["id"], "price": int(extra.get("price") or 0)}
            )
        base_price = int(item["price"])
        pricing_record = {
            "itemId": item["id"],
            "itemName": item.get("name") or item["id"],
            "categoryId": category.get("id"),
            "basePrice": base_price,
            "extras": chosen_extras,
            "total": base_price + sum(e["price"] for e in chosen_extras),
            "currency": "ZAR",
        }

    duration = int(cfg.get("durationMinutes", 30))
    capacity = int(cfg.get("concurrentCapacity") or 1)
    business_hours = (cfg.get("businessHours") or {}).get(weekday_key(slot_start_utc))
    if not business_hours:
        log.warning("event=closed_on_that_day service=%s slot=%s", req.service, req.requestedSlot)
        return bad_request(fields=["requestedSlot"], message="closed_on_that_day")

    if not is_future(slot_start_utc):
        log.warning("event=slot_in_past service=%s slot=%s", req.service, req.requestedSlot)
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
        log.info("event=patient_matched patientId=%s email=%s", patient_id, email_lc)
        patient_item = {
            **existing_patient,
            "firstName": req.personal.firstName,
            "lastName": req.personal.lastName,
            "phone": req.personal.phone,
            "dob": req.personal.dob or existing_patient.get("dob"),
            "medicalAid": req.personal.medicalAid.model_dump() if req.personal.medicalAid else None,
            "marketingOptIn": bool(medical.marketingOptIn),
            "updatedAt": now,
        }
    else:
        patient_id = new_patient_id()
        log.info("event=patient_created patientId=%s email=%s", patient_id, email_lc)
        patient_item = {
            "PK": f"PATIENT#{patient_id}",
            "SK": "PROFILE",
            "GSI1PK": f"EMAIL#{email_lc}",
            "GSI1SK": "PATIENT",
            "type": "patient",
            "patientId": patient_id,
            "firstName": req.personal.firstName,
            "lastName": req.personal.lastName,
            "dob": req.personal.dob,
            "phone": req.personal.phone,
            "email": email_lc,
            "medicalAid": req.personal.medicalAid.model_dump() if req.personal.medicalAid else None,
            "marketingOptIn": bool(medical.marketingOptIn),
            "popiaConsentAt": now,
            "createdAt": now,
            "updatedAt": now,
        }

    try:
        dynamo.put_patient(patient_item)
    except Exception:
        log.exception("event=patient_persist_failed patientId=%s email=%s", patient_id, email_lc)
        return server_error("booking_failed")

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
        "pricing": pricing_record,
        "ageBand": age_band_from_dob(req.personal.dob),
        "intake": {
            "existingConditions": medical.existingConditions or "",
            "allergies": medical.allergies or "",
            "currentMeds": medical.currentMeds or "",
            "reasonForVisit": medical.reasonForVisit or "",
            "notes": medical.notes or "",
        },
        "createdAt": now,
        "updatedAt": now,
    }

    # 3. Transactional write — slot capacity enforced via ConditionExpression
    try:
        dynamo.acquire_slot_and_write_booking(req.service, slot_start_iso, capacity, booking_item, now)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("TransactionCanceledException", "ConditionalCheckFailedException"):
            log.warning(
                "event=slot_conflict service=%s slot=%s shortId=%s code=%s",
                req.service, slot_start_iso, short_id, code,
            )
            return conflict("slot_unavailable")
        log.exception(
            "event=slot_persist_failed service=%s slot=%s bookingId=%s code=%s",
            req.service, slot_start_iso, booking_id, code,
        )
        return server_error("booking_failed")

    log.info(
        "event=booking_reserved bookingId=%s shortId=%s patientId=%s service=%s slot=%s",
        booking_id, short_id, patient_id, req.service, slot_start_iso,
    )

    # 4. Google Calendar
    service_title = SERVICE_TITLES.get(req.service, req.service)
    summary = f"WellMed · {service_title} · {req.personal.firstName} {req.personal.lastName}"
    slot_start_local_iso = to_sast(slot_start_utc).isoformat(timespec="seconds")
    slot_end_local_iso = to_sast(slot_end_utc).isoformat(timespec="seconds")

    description = google_calendar.render_event_description(booking_item["intake"])
    if pricing_record:
        extras_txt = "".join(f"\n  + {e['name']} (R{e['price']})" for e in pricing_record["extras"])
        description = (
            f"Selected: {pricing_record['itemName']} — R{pricing_record['basePrice']}{extras_txt}\n"
            f"Total: R{pricing_record['total']}\n\n{description}"
        )

    try:
        google_event_id = google_calendar.create_event(
            summary=summary,
            description=description,
            slot_start_local_iso=slot_start_local_iso,
            slot_end_local_iso=slot_end_local_iso,
            patient_email=email_lc,
            patient_name=f"{req.personal.firstName} {req.personal.lastName}",
        )
    except Exception:
        log.exception(
            "event=calendar_insert_failed bookingId=%s shortId=%s slot=%s — booking left pending",
            booking_id, short_id, slot_start_iso,
        )
        # Booking row stays at status=pending. Manual cleanup from admin dashboard.
        return server_error("calendar_unavailable")

    log.info("event=calendar_event_created bookingId=%s googleEventId=%s", booking_id, google_event_id)

    # 5. Confirm booking
    confirmed_at = now_iso()
    try:
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
    except Exception:
        log.exception(
            "event=booking_confirm_failed bookingId=%s shortId=%s googleEventId=%s",
            booking_id, short_id, google_event_id,
        )
        return server_error("booking_failed")

    # 6. Email (fail-soft)
    confirmation_sent = ses.send_booking_confirmation(
        email_lc,
        {
            "firstName": req.personal.firstName,
            "shortId": short_id,
            "serviceTitle": service_title,
            "slotLocal": format_sast(slot_start_utc),
            "calendarLink": f"https://www.google.com/calendar/event?eid={google_event_id}",
            "rescheduleUrl": "https://wellmed.org.za/pages/contact.html",
        },
    )
    if not confirmation_sent:
        log.warning(
            "event=confirmation_email_failed bookingId=%s shortId=%s email=%s",
            booking_id, short_id, email_lc,
        )

    response_body = {
        "id": short_id,
        "status": "confirmed",
        "calendarEventId": google_event_id,
        "patientCalendarInviteSent": True,
        "confirmationEmailSent": confirmation_sent,
        "pricing": pricing_record,
    }

    if idem_key:
        try:
            dynamo.put_idempotency(idem_key, json.dumps(response_body), int(time.time()) + _IDEMPOTENCY_TTL_SECONDS)
        except Exception:
            log.exception("event=idempotency_persist_failed idempotencyKey=%s", idem_key)

    log.info(
        "event=booking_confirmed bookingId=%s shortId=%s service=%s slot=%s emailSent=%s",
        booking_id, short_id, req.service, slot_start_iso, confirmation_sent,
    )
    return respond(201, response_body)
