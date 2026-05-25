"""PATCH /api/admin/bookings/{id} — change status or reschedule."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError
from pydantic import ValidationError
from ..lib import dynamo, google_calendar

from ..lib import jwt_util
from ..lib.http import bad_request, conflict, not_found, ok, parse_body, server_error, unauthorized
from ..lib.log_util import bind
from ..lib.time_util import iso, is_future, now_iso, parse_iso, slot_end, to_sast, weekday_key
from ..lib.validate import AdminBookingPatch, collect_error_fields


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

    booking_id = booking["bookingId"]

    try:
        body = parse_body(event)
    except Exception:
        log.warning("event=invalid_json bookingId=%s", booking_id)
        return bad_request(message="invalid json")

    try:
        patch = AdminBookingPatch.model_validate(body)
    except ValidationError as e:
        fields = collect_error_fields(e)
        log.warning("event=validation_failed bookingId=%s fields=%s", booking_id, fields)
        return bad_request(fields=fields)

    updates: dict[str, Any] = {"updatedAt": now_iso()}

    if patch.newSlot:
        try:
            new_start = parse_iso(patch.newSlot)
        except Exception:
            log.warning("event=validation_failed bookingId=%s field=newSlot value=%s", booking_id, patch.newSlot)
            return bad_request(fields=["newSlot"])
        if not is_future(new_start):
            log.warning("event=slot_in_past bookingId=%s newSlot=%s", booking_id, patch.newSlot)
            return bad_request(fields=["newSlot"], message="slot_in_past")

        cfg = dynamo.get_service_config(booking["service"])
        if not cfg:
            log.error("event=service_not_configured bookingId=%s service=%s", booking_id, booking["service"])
            return server_error("service_not_configured")
        duration = int(cfg.get("durationMinutes", 30))
        business_hours = (cfg.get("businessHours") or {}).get(weekday_key(new_start))
        if not business_hours:
            log.warning("event=closed_on_that_day bookingId=%s newSlot=%s", booking_id, patch.newSlot)
            return bad_request(fields=["newSlot"], message="closed_on_that_day")

        new_start_iso = iso(new_start)
        new_end = slot_end(new_start, duration)
        new_end_iso = iso(new_end)
        old_slot_iso = booking["slotStart"]

        if new_start_iso != old_slot_iso:
            log.info(
                "event=reschedule_move_lock bookingId=%s service=%s from=%s to=%s",
                booking_id, booking["service"], old_slot_iso, new_start_iso,
            )
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
                    log.warning(
                        "event=slot_conflict bookingId=%s newSlot=%s code=%s",
                        booking_id, new_start_iso, code,
                    )
                    return conflict("slot_unavailable")
                log.exception(
                    "event=reschedule_persist_failed bookingId=%s newSlot=%s code=%s",
                    booking_id, new_start_iso, code,
                )
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
                log.exception(
                    "event=calendar_patch_failed bookingId=%s googleEventId=%s (booking already moved)",
                    booking_id, booking.get("googleEventId"),
                )

        updates.update(
            {
                "slotStart": new_start_iso,
                "slotEnd": new_end_iso,
                "GSI2SK": f"SLOT#{new_start_iso}",
                "GSI3SK": f"SLOT#{new_start_iso}",
            }
        )

    if patch.status:
        log.info(
            "event=status_change bookingId=%s from=%s to=%s",
            booking_id, booking.get("status"), patch.status,
        )
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
                log.exception(
                    "event=cancel_slot_lock_delete_failed bookingId=%s slot=%s",
                    booking_id, booking["slotStart"],
                )
            if booking.get("googleEventId"):
                try:
                    google_calendar.delete_event(booking["googleEventId"], notify=bool(patch.notifyPatient))
                except Exception:
                    log.exception(
                        "event=cancel_calendar_delete_failed bookingId=%s googleEventId=%s",
                        booking_id, booking.get("googleEventId"),
                    )

    if len(updates) == 1:  # only updatedAt — nothing to do
        log.warning("event=no_changes bookingId=%s", booking_id)
        return bad_request(message="no_changes")

    try:
        updated = dynamo.update_booking(booking_id, updates)
    except Exception:
        log.exception("event=update_booking_failed bookingId=%s updates=%s", booking_id, list(updates.keys()))
        return server_error("update_failed")

    log.info(
        "event=request_ok bookingId=%s status=%s slot=%s changedKeys=%s",
        booking_id, updated.get("status"), updated.get("slotStart"), list(updates.keys()),
    )
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
