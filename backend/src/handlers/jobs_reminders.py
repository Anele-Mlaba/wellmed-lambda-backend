"""EventBridge-triggered: find confirmed bookings ~24h ahead, send reminder emails."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from boto3.dynamodb.conditions import Attr, Key

from ..lib import dynamo, ses
from ..lib.time_util import format_sast, iso, now_iso, now_utc, parse_iso
from ..lib.validate import SERVICE_TITLES

_LOG = logging.getLogger(__name__)


def handler(_event: dict[str, Any], _context) -> dict[str, Any]:
    now = now_utc()
    window_start = iso(now + timedelta(hours=23))
    window_end = iso(now + timedelta(hours=25))

    sent = 0
    skipped = 0

    kwargs = {
        "IndexName": "GSI2",
        "KeyConditionExpression": Key("GSI2PK").eq("STATUS#confirmed")
        & Key("GSI2SK").between(f"SLOT#{window_start}", f"SLOT#{window_end}"),
        "FilterExpression": Attr("reminderSentAt").not_exists(),
    }

    while True:
        resp = dynamo.table.query(**kwargs)
        for booking in resp.get("Items", []):
            patient = dynamo.get_patient_by_id(booking.get("patientId", "")) or {}
            email = patient.get("email")
            if not email:
                skipped += 1
                continue

            slot_dt = parse_iso(booking["slotStart"])
            data = {
                "firstName": patient.get("firstName", ""),
                "shortId": booking.get("shortId", ""),
                "serviceTitle": SERVICE_TITLES.get(booking.get("service", ""), booking.get("service", "")),
                "slotLocal": format_sast(slot_dt),
                "rescheduleUrl": "https://wellmed.co.za/pages/contact.html",
            }

            if ses.send_booking_reminder(email, data):
                try:
                    dynamo.update_booking(booking["bookingId"], {"reminderSentAt": now_iso()})
                    sent += 1
                except Exception:
                    _LOG.exception("failed to mark reminderSentAt for %s", booking.get("bookingId"))
            else:
                skipped += 1

        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    _LOG.info("reminders sweep done: sent=%d skipped=%d", sent, skipped)
    return {"sent": sent, "skipped": skipped}
