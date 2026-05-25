"""POST /api/admin/login — verify password + issue JWT."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from ..lib import dynamo, jwt_util

from ..lib import passwords
from ..lib.http import bad_request, ok, parse_body, unauthorized
from ..lib.log_util import bind
from ..lib.time_util import now_iso
from ..lib.validate import AdminLoginRequest, collect_error_fields


def handler(event: dict[str, Any], _context) -> dict[str, Any]:
    log = bind(__name__, event)
    log.info("event=request_received")

    try:
        body = parse_body(event)
    except Exception:
        log.warning("event=invalid_json")
        return bad_request(message="invalid json")

    try:
        req = AdminLoginRequest.model_validate(body)
    except ValidationError as e:
        fields = collect_error_fields(e)
        # NB: never log the password field — only that it failed validation.
        log.warning("event=validation_failed fields=%s", fields)
        return bad_request(fields=fields)

    email_lc = req.email.lower()
    user = dynamo.get_admin_user(email_lc)
    if not user:
        log.warning("event=auth_failed reason=unknown_user email=%s", email_lc)
        return unauthorized("invalid_credentials")
    if user.get("disabledAt"):
        log.warning("event=auth_failed reason=disabled email=%s userId=%s", email_lc, user.get("userId"))
        return unauthorized("invalid_credentials")

    if not passwords.verify_password(user.get("passwordHash", ""), req.password):
        log.warning("event=auth_failed reason=bad_password email=%s userId=%s", email_lc, user.get("userId"))
        return unauthorized("invalid_credentials")

    token, expires_in = jwt_util.issue(user["userId"], user.get("role", "admin"))

    try:
        dynamo.put_admin_user({**user, "lastLoginAt": now_iso()})
    except Exception:
        log.exception("event=last_login_update_failed userId=%s (non-fatal)", user.get("userId"))

    log.info(
        "event=login_ok userId=%s role=%s email=%s expiresIn=%s",
        user.get("userId"), user.get("role", "admin"), email_lc, expires_in,
    )
    return ok({"token": token, "expiresIn": expires_in})
