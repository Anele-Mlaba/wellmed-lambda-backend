"""Structured logging helper for Lambda handlers.

Handlers call ``bind(__name__, event)`` at the top of ``handler`` to get a
LoggerAdapter that auto-prefixes every line with the API Gateway v2 request
context (``requestId``, ``route``, ``sourceIp``, ``userAgent``). Pair that with
a stable ``event=<verb>`` token in each message so CloudWatch Logs Insights
filters like ``filter requestId="abc-123"`` or ``filter event="booking_created"``
return a coherent trace of a single invocation.
"""

from __future__ import annotations

import logging
import os
from typing import Any

_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.getLogger().setLevel(_LEVEL)


class _ContextAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        ctx = " ".join(f"{k}={v}" for k, v in self.extra.items() if v not in (None, ""))
        return (f"{msg} [{ctx}]" if ctx else msg), kwargs


def bind(name: str, event: dict[str, Any]) -> _ContextAdapter:
    rc = event.get("requestContext") or {}
    http = rc.get("http") or {}
    headers = event.get("headers") or {}
    ua = headers.get("user-agent") or headers.get("User-Agent") or ""
    extra = {
        "requestId": rc.get("requestId"),
        "route": event.get("routeKey") or f"{http.get('method', '?')} {http.get('path', '?')}",
        "sourceIp": http.get("sourceIp"),
        "userAgent": ua[:120],
    }
    return _ContextAdapter(logging.getLogger(name), extra)


def bind_job(name: str, job: str) -> _ContextAdapter:
    """Variant for non-HTTP triggers (EventBridge, etc.) where there is no requestContext."""
    return _ContextAdapter(logging.getLogger(name), {"job": job})
