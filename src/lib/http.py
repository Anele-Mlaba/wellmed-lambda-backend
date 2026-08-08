"""Lambda Proxy v2 response helpers for the HTTP API."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any

_ALLOWED_ORIGIN = os.environ.get("FRONT_END_ORIGIN", "*")


class _JsonEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        return super().default(o)


def _cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": _ALLOWED_ORIGIN,
        "Access-Control-Allow-Headers": "content-type,authorization,idempotency-key",
        "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,OPTIONS",
        "Vary": "Origin",
    }


def respond(status: int, body: Any, extra_headers: dict[str, str] | None = None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", **_cors_headers()}
    if extra_headers:
        headers.update(extra_headers)
    return {
        "statusCode": status,
        "headers": headers,
        "body": json.dumps(body, cls=_JsonEncoder),
    }


def ok(body: Any) -> dict[str, Any]:
    return respond(200, body)


def created(body: Any) -> dict[str, Any]:
    return respond(201, body)


def bad_request(fields: list[str] | None = None, message: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": "validation"}
    if fields is not None:
        body["fields"] = fields
    if message:
        body["message"] = message
    return respond(400, body)


def unauthorized(message: str = "unauthorized") -> dict[str, Any]:
    return respond(401, {"error": message})


def forbidden(message: str = "forbidden") -> dict[str, Any]:
    return respond(403, {"error": message})


def not_found(message: str = "not_found") -> dict[str, Any]:
    return respond(404, {"error": message})


def conflict(error: str) -> dict[str, Any]:
    return respond(409, {"error": error})


def server_error(error: str = "internal_error") -> dict[str, Any]:
    return respond(500, {"error": error})


def parse_body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode("utf-8")
    return json.loads(raw) if raw else {}
