"""EventBridge-triggered: find confirmed bookings ~24h ahead, send reminder emails."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from boto3.dynamodb.conditions import Attr, Key
from ..lib import dynamo

from ..lib import ses
from ..lib.log_util import bind_job
from ..lib.time_util import format_sast, iso, now_iso, now_utc, parse_iso
from ..lib.validate import SERVICE_TITLES


def handler(_event: dict[str, Any], _context) -> dict[str, Any]:
    log = bind_job(__name__, "reminders_sweep")
    now = now_utc()
    window_start = iso(now + timedelta(hours=23))
    window_end = iso(now + timedelta(hours=25))
    log.info("event=sweep_start window_from=%s window_to=%s", window_start, window_end)

    sent = 0
    skipped_no_email = 0
    skipped_ses_failed = 0

    kwargs = {
        "IndexName": "GSI2",
        "KeyConditionExpression": Key("GSI2PK").eq("STATUS#confirmed")
        & Key("GSI2SK").between(f"SLOT#{window_start}", f"SLOT#{window_end}"),
        "FilterExpression": Attr("reminderSentAt").not_exists(),
    }

    while True:
        resp = dynamo.table.query(**kwargs)
        for booking in resp.get("Items", []):
            booking_id = booking.get("bookingId")
            patient = dynamo.get_patient_by_id(booking.get("patientId", "")) or {}
            email = patient.get("email")
            if not email:
                log.warning(
                    "event=reminder_skipped reason=no_email bookingId=%s patientId=%s",
                    booking_id, booking.get("patientId"),
                )
                skipped_no_email += 1
                continue

            slot_dt = parse_iso(booking["slotStart"])
            data = {
                "firstName": patient.get("firstName", ""),
                "shortId": booking.get("shortId", ""),
                "serviceTitle": SERVICE_TITLES.get(booking.get("service", ""), booking.get("service", "")),
                "slotLocal": format_sast(slot_dt),
                "rescheduleUrl": "https://wellmed.org.za/pages/contact.html",
            }

            if ses.send_booking_reminder(email, data):
                try:
                    dynamo.update_booking(booking_id, {"reminderSentAt": now_iso()})
                    sent += 1
                    log.info(
                        "event=reminder_sent bookingId=%s shortId=%s email=%s slot=%s",
                        booking_id, booking.get("shortId"), email, booking.get("slotStart"),
                    )
                except Exception:
                    log.exception(
                        "event=reminder_mark_failed bookingId=%s email=%s",
                        booking_id, email,
                    )
            else:
                log.warning(
                    "event=reminder_skipped reason=ses_failed bookingId=%s email=%s",
                    booking_id, email,
                )
                skipped_ses_failed += 1

        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    log.info(
        "event=sweep_done sent=%d skippedNoEmail=%d skippedSesFailed=%d",
        sent, skipped_no_email, skipped_ses_failed,
    )
    return {"sent": sent, "skipped": skipped_no_email + skipped_ses_failed}
