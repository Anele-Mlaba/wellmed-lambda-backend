"""POST /api/contact — persist a contact message."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from ..lib import dynamo
from ..lib.http import bad_request, ok, parse_body, server_error
from ..lib.ids import new_message_id
from ..lib.time_util import now_iso, now_utc
from ..lib.validate import ContactRequest, collect_error_fields

_LOG = logging.getLogger(__name__)


def handler(event: dict[str, Any], _context) -> dict[str, Any]:
    try:
        body = parse_body(event)
    except Exception:
        return bad_request(message="invalid json")

    try:
        req = ContactRequest.model_validate(body)
    except ValidationError as e:
        return bad_request(fields=collect_error_fields(e))

    now = now_utc()
    message_id = new_message_id()
    item = {
        "PK": f"MESSAGE#{message_id}",
        "SK": "RECORD",
        "GSI1PK": f"MONTH#{now.strftime('%Y-%m')}",
        "GSI1SK": f"MSG#{now_iso()}",
        "type": "contact_message",
        "messageId": message_id,
        "name": req.name,
        "email": req.email.lower(),
        "phone": req.phone or "",
        "topic": req.topic or "",
        "message": req.message,
        "ipAddress": (event.get("requestContext", {}) or {}).get("http", {}).get("sourceIp", ""),
        "createdAt": now_iso(),
    }
    try:
        dynamo.put_contact_message(item)
    except Exception:
        _LOG.exception("contact persist failed")
        return server_error("contact_failed")

    return ok({"ok": True})
