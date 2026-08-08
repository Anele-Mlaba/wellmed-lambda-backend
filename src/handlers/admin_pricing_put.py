"""PUT /api/admin/pricing — replace the price catalog (admin only)."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ..lib import dynamo, jwt_util
from ..lib.http import bad_request, ok, parse_body, server_error, unauthorized
from ..lib.log_util import bind
from ..lib.time_util import now_iso
from ..lib.validate import PricingCatalogPut, collect_error_fields


def handler(event: dict[str, Any], _context) -> dict[str, Any]:
    log = bind(__name__, event)
    claims = jwt_util.require_admin(event)
    if claims is None:
        log.warning("event=auth_failed reason=missing_or_invalid_jwt")
        return unauthorized()

    try:
        body = parse_body(event)
    except Exception:
        log.warning("event=invalid_json")
        return bad_request(message="invalid json")

    try:
        catalog = PricingCatalogPut.model_validate(body)
    except ValidationError as e:
        fields = collect_error_fields(e)
        log.warning("event=validation_failed fields=%s", fields)
        return bad_request(fields=fields)

    item_ids: set[str] = set()
    for category in catalog.categories:
        for item in category.items:
            if item.id in item_ids:
                log.warning("event=validation_failed reason=duplicate_item_id id=%s", item.id)
                return bad_request(fields=["categories"], message=f"duplicate_item_id:{item.id}")
            item_ids.add(item.id)

    updated_at = now_iso()
    try:
        dynamo.put_pricing_catalog(
            categories=[c.model_dump() for c in catalog.categories],
            updated_at=updated_at,
            updated_by=str(claims.get("sub") or ""),
        )
    except Exception:
        log.exception("event=pricing_persist_failed")
        return server_error("pricing_update_failed")

    log.info(
        "event=pricing_updated userId=%s categories=%d items=%d",
        claims.get("sub"), len(catalog.categories), len(item_ids),
    )
    return ok({"updatedAt": updated_at, "categories": [c.model_dump() for c in catalog.categories]})
