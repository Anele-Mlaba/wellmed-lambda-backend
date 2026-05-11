"""HS256 JWT issuance + verification for admin sessions."""

from __future__ import annotations

import os
import time
from typing import Any

import jwt

_SECRET = os.environ["JWT_SECRET"]
_EXPIRY = int(os.environ.get("JWT_EXPIRY_SECONDS", "3600"))
_ALG = "HS256"


def issue(user_id: str, role: str) -> tuple[str, int]:
    """Sign a token for an admin user. Returns (token, expiresIn)."""
    now = int(time.time())
    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + _EXPIRY,
    }
    token = jwt.encode(payload, _SECRET, algorithm=_ALG)
    return token, _EXPIRY


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
    """Returns the decoded JWT payload or None when unauthorised."""
    token = extract_bearer(event)
    if not token:
        return None
    try:
        return verify(token)
    except jwt.PyJWTError:
        return None
