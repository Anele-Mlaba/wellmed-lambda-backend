"""DynamoDB client + access-pattern helpers for the single WellMed table."""

from __future__ import annotations

import os
from typing import Any, Iterable

import boto3
from boto3.dynamodb.conditions import Key

_TABLE_NAME = os.environ["TABLE_NAME"]

_resource = boto3.resource("dynamodb")
_client = boto3.client("dynamodb")

table = _resource.Table(_TABLE_NAME)


def get_table_name() -> str:
    return _TABLE_NAME


def client():
    return _client


# ---------------------------------------------------------------------------
# Patient access patterns
# ---------------------------------------------------------------------------

def get_patient_by_email(email: str) -> dict[str, Any] | None:
    resp = table.query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq(f"EMAIL#{email.lower()}") & Key("GSI1SK").eq("PATIENT"),
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def get_patient_by_id(patient_id: str) -> dict[str, Any] | None:
    resp = table.get_item(Key={"PK": f"PATIENT#{patient_id}", "SK": "PROFILE"})
    return resp.get("Item")


def put_patient(patient: dict[str, Any]) -> None:
    table.put_item(Item=patient)


# ---------------------------------------------------------------------------
# Booking access patterns
# ---------------------------------------------------------------------------

def get_booking_by_id(booking_id: str) -> dict[str, Any] | None:
    resp = table.get_item(Key={"PK": f"BOOKING#{booking_id}", "SK": "META"})
    return resp.get("Item")


def get_booking_by_short_id(short_id: str) -> dict[str, Any] | None:
    resp = table.query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq(f"SHORTID#{short_id}") & Key("GSI1SK").eq("BOOKING"),
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def query_bookings_by_status_in_range(status: str, slot_from_iso: str, slot_to_iso: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    kwargs = {
        "IndexName": "GSI2",
        "KeyConditionExpression": Key("GSI2PK").eq(f"STATUS#{status}") & Key("GSI2SK").between(
            f"SLOT#{slot_from_iso}", f"SLOT#{slot_to_iso}"
        ),
    }
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


def query_bookings_for_patient(patient_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    kwargs = {
        "IndexName": "GSI3",
        "KeyConditionExpression": Key("GSI3PK").eq(f"PATIENT#{patient_id}"),
    }
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items


# ---------------------------------------------------------------------------
# Slot lock + booking transactional write
#
# Slot locks are capacity counters: `count` bookings held against the slot,
# admitted while count < the service's concurrentCapacity. Legacy lock items
# written before `count` existed have no count attribute; the condition below
# treats them as full, which is the safe reading for capacity-1 services.
# ---------------------------------------------------------------------------

def _slot_acquire_update(service: str, slot_start_iso: str, capacity: int, now_iso: str) -> dict[str, Any]:
    return {
        "Update": {
            "TableName": _TABLE_NAME,
            "Key": {"PK": {"S": f"SLOTLOCK#{service}#{slot_start_iso}"}, "SK": {"S": "LOCK"}},
            "UpdateExpression": "SET #t = :type, #u = :now ADD #c :one",
            "ConditionExpression": "attribute_not_exists(PK) OR #c < :cap",
            "ExpressionAttributeNames": {"#c": "count", "#t": "type", "#u": "updatedAt"},
            "ExpressionAttributeValues": {
                ":one": {"N": "1"},
                ":cap": {"N": str(capacity)},
                ":type": {"S": "slot_lock"},
                ":now": {"S": now_iso},
            },
        }
    }


def acquire_slot_and_write_booking(
    service: str,
    slot_start_iso: str,
    capacity: int,
    booking_item: dict[str, Any],
    now_iso: str,
) -> None:
    """Increment the slot counter (while below capacity) + write Booking atomically."""
    _client.transact_write_items(
        TransactItems=[
            _slot_acquire_update(service, slot_start_iso, capacity, now_iso),
            {
                "Put": {
                    "TableName": _TABLE_NAME,
                    "Item": _to_dynamo(booking_item),
                }
            },
        ]
    )


def release_slot(service: str, slot_start_iso: str) -> None:
    """Decrement the slot counter (a cancel/reschedule freeing one seat)."""
    table.update_item(
        Key={"PK": f"SLOTLOCK#{service}#{slot_start_iso}", "SK": "LOCK"},
        UpdateExpression="ADD #c :minus",
        ExpressionAttributeNames={"#c": "count"},
        ExpressionAttributeValues={":minus": -1},
    )


def move_slot(service: str, old_slot_iso: str, new_slot_iso: str, capacity: int, now_iso: str) -> None:
    """Atomically free one seat on the old slot and take one on the new slot."""
    _client.transact_write_items(
        TransactItems=[
            {
                "Update": {
                    "TableName": _TABLE_NAME,
                    "Key": {"PK": {"S": f"SLOTLOCK#{service}#{old_slot_iso}"}, "SK": {"S": "LOCK"}},
                    "UpdateExpression": "ADD #c :minus",
                    "ExpressionAttributeNames": {"#c": "count"},
                    "ExpressionAttributeValues": {":minus": {"N": "-1"}},
                }
            },
            _slot_acquire_update(service, new_slot_iso, capacity, now_iso),
        ]
    )


def query_slot_counts(service: str, slot_starts: Iterable[str]) -> dict[str, int | None]:
    """Slot ISO → booked count for slots that have a lock item.

    Legacy lock items without a count attribute map to None (treat as full).
    Slots with no lock item are absent from the result (count 0).
    """
    counts: dict[str, int | None] = {}
    keys = [{"PK": f"SLOTLOCK#{service}#{s}", "SK": "LOCK"} for s in slot_starts]
    for chunk_start in range(0, len(keys), 100):
        chunk = keys[chunk_start:chunk_start + 100]
        if not chunk:
            continue
        resp = _resource.batch_get_item(
            RequestItems={
                _TABLE_NAME: {
                    "Keys": chunk,
                    "ProjectionExpression": "PK, #c",
                    "ExpressionAttributeNames": {"#c": "count"},
                }
            }
        )
        for item in resp.get("Responses", {}).get(_TABLE_NAME, []):
            slot = item["PK"].split("#", 2)[-1]
            raw = item.get("count")
            counts[slot] = int(raw) if raw is not None else None
    return counts


# ---------------------------------------------------------------------------
# Pricing catalog
# ---------------------------------------------------------------------------

def get_pricing_catalog() -> dict[str, Any] | None:
    resp = table.get_item(Key={"PK": "CONFIG#PRICING", "SK": "CATALOG"})
    return resp.get("Item")


def put_pricing_catalog(categories: list[dict[str, Any]], updated_at: str, updated_by: str | None = None) -> None:
    table.put_item(
        Item={
            "PK": "CONFIG#PRICING",
            "SK": "CATALOG",
            "type": "pricing_catalog",
            "categories": categories,
            "updatedAt": updated_at,
            "updatedBy": updated_by,
        }
    )


def find_pricing_item(catalog: dict[str, Any] | None, item_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Locate a catalog item by id. Returns (category, item) or None."""
    for category in (catalog or {}).get("categories", []) or []:
        for item in category.get("items", []) or []:
            if item.get("id") == item_id:
                return category, item
    return None


# ---------------------------------------------------------------------------
# Patient user accounts (self-service login)
# ---------------------------------------------------------------------------

def get_patient_user(email: str) -> dict[str, Any] | None:
    resp = table.get_item(Key={"PK": f"USER#{email.lower()}", "SK": "PROFILE"})
    return resp.get("Item")


def create_patient_user(item: dict[str, Any]) -> None:
    """Create a patient login; raises ConditionalCheckFailedException if the email is taken."""
    table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")


def put_patient_user(item: dict[str, Any]) -> None:
    table.put_item(Item=item)


# ---------------------------------------------------------------------------
# ServiceConfig
# ---------------------------------------------------------------------------

def get_service_config(slug: str) -> dict[str, Any] | None:
    resp = table.get_item(Key={"PK": f"SERVICE#{slug}", "SK": "CONFIG"})
    return resp.get("Item")


def put_service_config(item: dict[str, Any]) -> None:
    table.put_item(Item=item)


# ---------------------------------------------------------------------------
# Admin users
# ---------------------------------------------------------------------------

def get_admin_user(email: str) -> dict[str, Any] | None:
    resp = table.get_item(Key={"PK": f"ADMIN#{email.lower()}", "SK": "PROFILE"})
    return resp.get("Item")


def put_admin_user(item: dict[str, Any]) -> None:
    table.put_item(Item=item)


# ---------------------------------------------------------------------------
# Contact messages
# ---------------------------------------------------------------------------

def put_contact_message(item: dict[str, Any]) -> None:
    table.put_item(Item=item)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def get_idempotency(key: str) -> dict[str, Any] | None:
    resp = table.get_item(Key={"PK": f"IDEMPOTENCY#{key}", "SK": "RECORD"})
    return resp.get("Item")


def put_idempotency(key: str, response_body: str, ttl_epoch: int) -> None:
    table.put_item(
        Item={
            "PK": f"IDEMPOTENCY#{key}",
            "SK": "RECORD",
            "type": "idempotency",
            "response": response_body,
            "createdAt": ttl_epoch,
            "ttl": ttl_epoch,
        }
    )


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

def increment_counter(name: str) -> int:
    resp = table.update_item(
        Key={"PK": f"COUNTER#{name}", "SK": "VALUE"},
        UpdateExpression="ADD #v :one",
        ExpressionAttributeNames={"#v": "value"},
        ExpressionAttributeValues={":one": 1},
        ReturnValues="UPDATED_NEW",
    )
    return int(resp["Attributes"]["value"])


# ---------------------------------------------------------------------------
# Booking updates
# ---------------------------------------------------------------------------

def update_booking(booking_id: str, attrs: dict[str, Any]) -> dict[str, Any]:
    """Patch an existing booking item; returns the updated attributes."""
    expr_names: dict[str, str] = {}
    expr_values: dict[str, Any] = {}
    sets: list[str] = []
    for i, (k, v) in enumerate(attrs.items()):
        name_ph = f"#a{i}"
        value_ph = f":v{i}"
        expr_names[name_ph] = k
        expr_values[value_ph] = v
        sets.append(f"{name_ph} = {value_ph}")
    resp = table.update_item(
        Key={"PK": f"BOOKING#{booking_id}", "SK": "META"},
        UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
        ReturnValues="ALL_NEW",
    )
    return resp.get("Attributes", {})


# ---------------------------------------------------------------------------
# Low-level encode helper
# ---------------------------------------------------------------------------

def _to_dynamo(item: dict[str, Any]) -> dict[str, Any]:
    from boto3.dynamodb.types import TypeSerializer
    ser = TypeSerializer()
    return {k: ser.serialize(v) for k, v in item.items()}
