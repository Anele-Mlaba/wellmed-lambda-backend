"""POST /api/contact — persist a contact message."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ..lib import dynamo
from ..lib.http import bad_request, ok, parse_body, server_error
from ..lib.ids import new_message_id
from ..lib.log_util import bind
from ..lib.time_util import now_iso, now_utc
from ..lib.validate import ContactRequest, collect_error_fields


def handler(event: dict[str, Any], _context) -> dict[str, Any]:
    log = bind(__name__, event)
    log.info("event=request_received")

    try:
        body = parse_body(event)
    except Exception:
        log.warning("event=invalid_json")
        return bad_request(message="invalid json")

    try:
        req = ContactRequest.model_validate(body)
    except ValidationError as e:
        fields = collect_error_fields(e)
        log.warning("event=validation_failed fields=%s", fields)
        return bad_request(fields=fields)

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
        log.exception("event=contact_persist_failed messageId=%s email=%s", message_id, item["email"])
        return server_error("contact_failed")

    log.info("event=contact_created messageId=%s email=%s topic=%s", message_id, item["email"], item["topic"] or "<none>")
    return ok({"ok": True})
