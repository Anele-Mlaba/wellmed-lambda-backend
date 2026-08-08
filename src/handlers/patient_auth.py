"""Patient self-service accounts — register / login / profile.

One Lambda serves all four routes (mirrors the lifestyle-club auth service):
    POST /api/auth/register
    POST /api/auth/login
    GET  /api/me
    PUT  /api/me

The stored profile carries exactly the booking-flow fields (name, surname,
DOB, phone, email, medical aid) so a logged-in patient never re-types them.
"""

from __future__ import annotations

import os
from typing import Any

from botocore.exceptions import ClientError
from pydantic import ValidationError

from ..lib import dynamo, jwt_util, passwords
from ..lib.http import bad_request, conflict, ok, parse_body, respond, server_error, unauthorized
from ..lib.log_util import bind
from ..lib.time_util import now_iso
from ..lib.validate import (
    PatientLoginRequest,
    PatientProfileUpdate,
    PatientRegisterRequest,
    collect_error_fields,
    is_valid_phone,
)

_PATIENT_JWT_EXPIRY = int(os.environ.get("PATIENT_JWT_EXPIRY_SECONDS", "86400"))


def _member_view(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "firstName": user.get("firstName"),
        "lastName": user.get("lastName"),
        "email": user.get("email"),
        "phone": user.get("phone"),
        "dob": user.get("dob"),
        "medicalAid": user.get("medicalAid"),
    }


def handler(event: dict[str, Any], _context) -> dict[str, Any]:
    log = bind(__name__, event)
    route = event.get("routeKey") or ""
    if route == "POST /api/auth/register":
        return _register(event, log)
    if route == "POST /api/auth/login":
        return _login(event, log)
    if route == "GET /api/me":
        return _me_get(event, log)
    if route == "PUT /api/me":
        return _me_put(event, log)
    log.warning("event=unknown_route route=%s", route)
    return respond(405, {"error": "method_not_allowed"})


def _register(event: dict[str, Any], log) -> dict[str, Any]:
    try:
        body = parse_body(event)
    except Exception:
        log.warning("event=invalid_json")
        return bad_request(message="invalid json")

    try:
        req = PatientRegisterRequest.model_validate(body)
    except ValidationError as e:
        fields = collect_error_fields(e)
        # NB: never log the password field — only that validation failed.
        log.warning("event=validation_failed fields=%s", fields)
        return bad_request(fields=fields)

    if not is_valid_phone(req.phone):
        log.warning("event=validation_failed fields=['phone']")
        return bad_request(fields=["phone"])

    email_lc = req.email.lower()
    now = now_iso()
    user_item = {
        "PK": f"USER#{email_lc}",
        "SK": "PROFILE",
        "type": "patient_user",
        "email": email_lc,
        "firstName": req.firstName,
        "lastName": req.lastName,
        "dob": req.dob,
        "phone": req.phone,
        "medicalAid": req.medicalAid.model_dump() if req.medicalAid else None,
        "passwordHash": passwords.hash_password(req.password),
        "createdAt": now,
        "updatedAt": now,
    }

    try:
        dynamo.create_patient_user(user_item)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            log.warning("event=register_conflict email=%s", email_lc)
            return conflict("email_already_registered")
        log.exception("event=register_persist_failed email=%s", email_lc)
        return server_error("registration_failed")
    except Exception:
        log.exception("event=register_persist_failed email=%s", email_lc)
        return server_error("registration_failed")

    token, expires_in = jwt_util.issue(email_lc, "patient", _PATIENT_JWT_EXPIRY)
    log.info("event=register_ok email=%s", email_lc)
    return respond(201, {"member": _member_view(user_item), "token": token, "expiresIn": expires_in})


def _login(event: dict[str, Any], log) -> dict[str, Any]:
    try:
        body = parse_body(event)
    except Exception:
        log.warning("event=invalid_json")
        return bad_request(message="invalid json")

    try:
        req = PatientLoginRequest.model_validate(body)
    except ValidationError as e:
        log.warning("event=validation_failed fields=%s", collect_error_fields(e))
        return bad_request(fields=collect_error_fields(e))

    email_lc = req.email.lower()
    user = dynamo.get_patient_user(email_lc)
    if not user or not passwords.verify_password(user.get("passwordHash", ""), req.password):
        log.warning("event=auth_failed email=%s", email_lc)
        return unauthorized("invalid_credentials")

    token, expires_in = jwt_util.issue(email_lc, "patient", _PATIENT_JWT_EXPIRY)

    try:
        dynamo.put_patient_user({**user, "lastLoginAt": now_iso()})
    except Exception:
        log.exception("event=last_login_update_failed email=%s (non-fatal)", email_lc)

    log.info("event=login_ok email=%s expiresIn=%s", email_lc, expires_in)
    return ok({"member": _member_view(user), "token": token, "expiresIn": expires_in})


def _require_user(event: dict[str, Any], log) -> dict[str, Any] | None:
    claims = jwt_util.require_patient(event)
    if claims is None:
        log.warning("event=auth_failed reason=missing_or_invalid_jwt")
        return None
    email = str(claims.get("sub") or "").lower()
    if not email:
        return None
    return dynamo.get_patient_user(email)


def _me_get(event: dict[str, Any], log) -> dict[str, Any]:
    user = _require_user(event, log)
    if user is None:
        return unauthorized()
    log.info("event=request_ok email=%s", user.get("email"))
    return ok({"member": _member_view(user)})


def _me_put(event: dict[str, Any], log) -> dict[str, Any]:
    user = _require_user(event, log)
    if user is None:
        return unauthorized()

    try:
        body = parse_body(event)
    except Exception:
        log.warning("event=invalid_json")
        return bad_request(message="invalid json")

    try:
        req = PatientProfileUpdate.model_validate(body)
    except ValidationError as e:
        log.warning("event=validation_failed fields=%s", collect_error_fields(e))
        return bad_request(fields=collect_error_fields(e))

    if req.phone is not None and not is_valid_phone(req.phone):
        log.warning("event=validation_failed fields=['phone']")
        return bad_request(fields=["phone"])

    changes = req.model_dump(exclude_none=True)
    if req.medicalAid is not None:
        changes["medicalAid"] = req.medicalAid.model_dump()
    if not changes:
        return bad_request(message="no_changes")

    updated = {**user, **changes, "updatedAt": now_iso()}
    try:
        dynamo.put_patient_user(updated)
    except Exception:
        log.exception("event=profile_update_failed email=%s", user.get("email"))
        return server_error("profile_update_failed")

    log.info("event=profile_updated email=%s fields=%s", user.get("email"), sorted(changes.keys()))
    return ok({"member": _member_view(updated)})
