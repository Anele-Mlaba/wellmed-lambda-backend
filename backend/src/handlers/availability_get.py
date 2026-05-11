"""GET /api/availability?service=...&date=YYYY-MM-DD"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ..lib import dynamo
from ..lib.http import bad_request, ok, server_error
from ..lib.time_util import (
    SAST,
    build_day_slots,
    iso,
    label_sast,
    weekday_key,
)
from ..lib.validate import SERVICES

_LOG = logging.getLogger(__name__)


def handler(event: dict[str, Any], _context) -> dict[str, Any]:
    qs = event.get("queryStringParameters") or {}
    service = (qs.get("service") or "").strip()
    date_str = (qs.get("date") or "").strip()

    fields: list[str] = []
    if service not in SERVICES:
        fields.append("service")
    if not date_str or len(date_str) != 10 or date_str[4] != "-" or date_str[7] != "-":
        fields.append("date")
    if fields:
        return bad_request(fields=fields)

    cfg = dynamo.get_service_config(service)
    if not cfg:
        _LOG.warning("no ServiceConfig for %s", service)
        return ok([])

    try:
        duration = int(cfg.get("durationMinutes", 30))
        buffer_min = int(cfg.get("bufferMinutes", 0))
        hours_map = cfg.get("businessHours") or {}
        probe = datetime(int(date_str[0:4]), int(date_str[5:7]), int(date_str[8:10]), 12, 0, tzinfo=SAST)
        window = hours_map.get(weekday_key(probe))
        slots = build_day_slots(date_str, window, duration, buffer_min)
    except Exception:
        _LOG.exception("failed to compute slot window")
        return server_error("availability_failed")

    if not slots:
        return ok([])

    slot_iso_list = [iso(s) for s in slots]
    locked = dynamo.query_existing_slotlocks(service, slot_iso_list)

    payload = [
        {
            "start": slot_iso,
            "label": label_sast(slot),
            "available": slot_iso not in locked,
        }
        for slot, slot_iso in zip(slots, slot_iso_list)
    ]
    return ok(payload)
