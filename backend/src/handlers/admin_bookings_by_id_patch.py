"""PATCH /api/admin/bookings/{id} — change status or reschedule."""

from __future__ import annotations

import logging
from typing import Any

from botocore.exceptions import ClientError
from pydantic import ValidationError

from ..lib import dynamo, google_calendar, jwt_util
from ..lib.http import bad_request, conflict, not_found, ok, parse_body, server_error, unauthorized
from ..lib.time_util import iso, is_future, now_iso, parse_iso, slot_end, to_sast, weekday_key
from ..lib.validate import AdminBookingPatch, collect_error_fields

_LOG = logging.getLogger(__name__)


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

    try:
        body = parse_body(event)
    except Exception:
        return bad_request(message="invalid json")

    try:
        patch = AdminBookingPatch.model_validate(body)
    except ValidationError as e:
        return bad_request(fields=collect_error_fields(e))

    booking_id = booking["bookingId"]
    updates: dict[str, Any] = {"updatedAt": now_iso()}

    if patch.newSlot:
        try:
            new_start = parse_iso(patch.newSlot)
        except Exception:
            return bad_request(fields=["newSlot"])
        if not is_future(new_start):
            return bad_request(fields=["newSlot"], message="slot_in_past")

        cfg = dynamo.get_service_config(booking["service"])
        if not cfg:
            return server_error("service_not_configured")
        duration = int(cfg.get("durationMinutes", 30))
        business_hours = (cfg.get("businessHours") or {}).get(weekday_key(new_start))
        if not business_hours:
            return bad_request(fields=["newSlot"], message="closed_on_that_day")

        new_start_iso = iso(new_start)
        new_end = slot_end(new_start, duration)
        new_end_iso = iso(new_end)
        old_slot_iso = booking["slotStart"]

        if new_start_iso != old_slot_iso:
            # Move the slot lock atomically: delete old, write new with condition.
            try:
                dynamo.client().transact_write_items(
                    TransactItems=[
                        {
                            "Delete": {
                                "TableName": dynamo.get_table_name(),
                                "Key": {
                                    "PK": {"S": f"SLOTLOCK#{booking['service']}#{old_slot_iso}"},
                                    "SK": {"S": "LOCK"},
                                },
                            }
                        },
                        {
                            "Put": {
                                "TableName": dynamo.get_table_name(),
                                "Item": {
                                    "PK": {"S": f"SLOTLOCK#{booking['service']}#{new_start_iso}"},
                                    "SK": {"S": "LOCK"},
                                    "type": {"S": "slot_lock"},
                                    "bookingId": {"S": booking_id},
                                    "createdAt": {"S": now_iso()},
                                },
                                "ConditionExpression": "attribute_not_exists(PK)",
                            }
                        },
                    ]
                )
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in ("TransactionCanceledException", "ConditionalCheckFailedException"):
                    return conflict("slot_unavailable")
                _LOG.exception("reschedule transact_write_items failed")
                return server_error("reschedule_failed")

        if booking.get("googleEventId"):
            try:
                google_calendar.patch_event(
                    booking["googleEventId"],
                    slot_start_local_iso=to_sast(new_start).isoformat(timespec="seconds"),
                    slot_end_local_iso=to_sast(new_end).isoformat(timespec="seconds"),
                    notify=bool(patch.notifyPatient),
                )
            except Exception:
                _LOG.exception("google calendar patch failed (booking already moved)")

        updates.update(
            {
                "slotStart": new_start_iso,
                "slotEnd": new_end_iso,
                "GSI2SK": f"SLOT#{new_start_iso}",
                "GSI3SK": f"SLOT#{new_start_iso}",
            }
        )

    if patch.status:
        updates["status"] = patch.status
        updates["GSI2PK"] = f"STATUS#{patch.status}"
        if patch.status == "cancelled":
            updates["cancelledAt"] = now_iso()
            if patch.cancelReason:
                updates["cancelReason"] = patch.cancelReason
            # Free the slot lock.
            try:
                dynamo.delete_slotlock(booking["service"], booking["slotStart"])
            except Exception:
                _LOG.exception("failed to delete slot lock on cancel")
            if booking.get("googleEventId"):
                try:
                    google_calendar.delete_event(booking["googleEventId"], notify=bool(patch.notifyPatient))
                except Exception:
                    _LOG.exception("failed to delete google event on cancel")

    if len(updates) == 1:  # only updatedAt — nothing to do
        return bad_request(message="no_changes")

    try:
        updated = dynamo.update_booking(booking_id, updates)
    except Exception:
        _LOG.exception("update_booking failed")
        return server_error("update_failed")

    return ok(
        {
            "id": updated.get("shortId"),
            "bookingId": updated.get("bookingId"),
            "status": updated.get("status"),
            "slotStart": updated.get("slotStart"),
            "slotEnd": updated.get("slotEnd"),
            "updatedAt": updated.get("updatedAt"),
        }
    )
