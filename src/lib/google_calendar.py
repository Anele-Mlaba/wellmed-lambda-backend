"""Google Calendar wrapper using a bundled service-account JSON file.

Service + credentials are built once per warm Lambda container.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

_LOG = logging.getLogger(__name__)
_SCOPES = ["https://www.googleapis.com/auth/calendar"]

_CALENDAR_ID = os.environ["PRACTICE_CALENDAR_ID"]
_SA_PATH = os.environ["GOOGLE_SA_PATH"]
_TIMEZONE = os.environ.get("PRACTICE_TIMEZONE", "Africa/Johannesburg")

_service = None


def _get_service():
    global _service
    if _service is None:
        creds = service_account.Credentials.from_service_account_file(
            _SA_PATH, scopes=_SCOPES
        )
        _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return _service


def create_event(
    *,
    summary: str,
    description: str,
    slot_start_local_iso: str,
    slot_end_local_iso: str,
    patient_email: str,
    patient_name: str,
) -> str:
    """Create the calendar event and return its `id`."""
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": slot_start_local_iso, "timeZone": _TIMEZONE},
        "end": {"dateTime": slot_end_local_iso, "timeZone": _TIMEZONE},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "email", "minutes": 24 * 60},
                {"method": "popup", "minutes": 60},
            ],
        },
    }
    event = (
        _get_service()
        .events()
        .insert(calendarId=_CALENDAR_ID, body=body, sendUpdates="all")
        .execute()
    )
    return event["id"]


def patch_event(
    event_id: str,
    *,
    slot_start_local_iso: str,
    slot_end_local_iso: str,
    notify: bool = True,
) -> None:
    body: dict[str, Any] = {
        "start": {"dateTime": slot_start_local_iso, "timeZone": _TIMEZONE},
        "end": {"dateTime": slot_end_local_iso, "timeZone": _TIMEZONE},
    }
    (
        _get_service()
        .events()
        .patch(
            calendarId=_CALENDAR_ID,
            eventId=event_id,
            body=body,
            sendUpdates="all" if notify else "none",
        )
        .execute()
    )


def delete_event(event_id: str, notify: bool = True) -> None:
    try:
        (
            _get_service()
            .events()
            .delete(
                calendarId=_CALENDAR_ID,
                eventId=event_id,
                sendUpdates="all" if notify else "none",
            )
            .execute()
        )
    except HttpError as exc:
        if exc.resp.status in (404, 410):
            _LOG.warning("calendar event %s already gone", event_id)
            return
        raise


def render_event_description(intake: dict[str, Any]) -> str:
    """Patient-facing event body — no medical-aid numbers."""
    lines = []
    reason = intake.get("reasonForVisit")
    if reason:
        lines.append(f"Reason for visit: {reason}")
    conditions = intake.get("existingConditions")
    if conditions:
        lines.append(f"Existing conditions: {conditions}")
    allergies = intake.get("allergies")
    if allergies:
        lines.append(f"Allergies: {allergies}")
    meds = intake.get("currentMeds")
    if meds:
        lines.append(f"Current medications: {meds}")
    notes = intake.get("notes")
    if notes:
        lines.append(f"Notes: {notes}")
    lines.append("")
    lines.append("WellMed, Umhlanga — Dr Moodley")
    return "\n".join(lines)
