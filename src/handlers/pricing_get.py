"""GET /api/pricing — public price catalog for the website."""

from __future__ import annotations

from typing import Any

from ..lib import dynamo
from ..lib.http import ok
from ..lib.log_util import bind


def handler(event: dict[str, Any], _context) -> dict[str, Any]:
    log = bind(__name__, event)
    item = dynamo.get_pricing_catalog()
    if not item:
        log.warning("event=pricing_catalog_missing outcome=empty")
        return ok({"categories": [], "updatedAt": None})
    log.info("event=request_ok categories=%d", len(item.get("categories") or []))
    return ok({"categories": item.get("categories") or [], "updatedAt": item.get("updatedAt")})
