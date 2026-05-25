"""ID generation: UUID v4 for patients/bookings, atomic counter-backed short IDs."""

from __future__ import annotations

import uuid

from . import dynamo


def new_patient_id() -> str:
    return str(uuid.uuid4())


def new_booking_id() -> str:
    return str(uuid.uuid4())


def new_message_id() -> str:
    return str(uuid.uuid4())


def new_admin_user_id() -> str:
    return str(uuid.uuid4())


def allocate_short_id() -> str:
    """`WM-####` short IDs start at 1001 — increment the bookingShortId counter."""
    n = dynamo.increment_counter("bookingShortId")
    return f"WM-{1000 + n}"
