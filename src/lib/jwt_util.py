"""HS256 JWT issuance + verification for admin sessions."""

from __future__ import annotations

import os
import time
from typing import Any

import jwt

_SECRET = os.environ["JWT_SECRET"]
_EXPIRY = int(os.environ.get("JWT_EXPIRY_SECONDS", "3600"))
_ALG = "HS256"


def issue(user_id: str, role: str, expiry_seconds: int | None = None) -> tuple[str, int]:
    """Sign a token. Returns (token, expiresIn)."""
    expiry = _EXPIRY if expiry_seconds is None else expiry_seconds
    now = int(time.time())
    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + expiry,
    }
    token = jwt.encode(payload, _SECRET, algorithm=_ALG)
    return token, expiry


def verify(token: str) -> dict[str, Any]:
    return jwt.decode(token, _SECRET, algorithms=[_ALG])


def extract_bearer(event: dict[str, Any]) -> str | None:
    headers = event.get("headers") or {}
    # API Gateway HTTP API lower-cases header names.
    auth = headers.get("authorization") or headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip()


def require_admin(event: dict[str, Any]) -> dict[str, Any] | None:
    """Returns the decoded JWT payload or None when unauthorised.

    Patient tokens share the signing secret, so the role claim is what keeps
    them out of the admin API.
    """
    claims = _decode_bearer(event)
    if claims is None or claims.get("role") == "patient":
        return None
    return claims


def require_patient(event: dict[str, Any]) -> dict[str, Any] | None:
    """Returns the decoded JWT payload for a patient token, else None."""
    claims = _decode_bearer(event)
    if claims is None or claims.get("role") != "patient":
        return None
    return claims


def _decode_bearer(event: dict[str, Any]) -> dict[str, Any] | None:
    token = extract_bearer(event)
    if not token:
        return None
    try:
        return verify(token)
    except jwt.PyJWTError:
        return None
