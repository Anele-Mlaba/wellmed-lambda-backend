"""POST /api/admin/login — verify password + issue JWT."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from ..lib import dynamo, jwt_util, passwords
from ..lib.http import bad_request, ok, parse_body, unauthorized
from ..lib.time_util import now_iso
from ..lib.validate import AdminLoginRequest, collect_error_fields

_LOG = logging.getLogger(__name__)


def handler(event: dict[str, Any], _context) -> dict[str, Any]:
    try:
        body = parse_body(event)
    except Exception:
        return bad_request(message="invalid json")

    try:
        req = AdminLoginRequest.model_validate(body)
    except ValidationError as e:
        return bad_request(fields=collect_error_fields(e))

    user = dynamo.get_admin_user(req.email.lower())
    if not user or user.get("disabledAt"):
        return unauthorized("invalid_credentials")

    if not passwords.verify_password(user.get("passwordHash", ""), req.password):
        return unauthorized("invalid_credentials")

    token, expires_in = jwt_util.issue(user["userId"], user.get("role", "admin"))

    try:
        dynamo.put_admin_user({**user, "lastLoginAt": now_iso()})
    except Exception:
        _LOG.exception("failed to update lastLoginAt (non-fatal)")

    return ok({"token": token, "expiresIn": expires_in})
