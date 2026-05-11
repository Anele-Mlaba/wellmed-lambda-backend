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
# ---------------------------------------------------------------------------

def write_slotlock_and_booking(slot_lock_item: dict[str, Any], booking_item: dict[str, Any]) -> None:
    """Write SlotLock + Booking atomically. Raises on conflict."""
    _client.transact_write_items(
        TransactItems=[
            {
                "Put": {
                    "TableName": _TABLE_NAME,
                    "Item": _to_dynamo(slot_lock_item),
                    "ConditionExpression": "attribute_not_exists(PK)",
                }
            },
            {
                "Put": {
                    "TableName": _TABLE_NAME,
                    "Item": _to_dynamo(booking_item),
                }
            },
        ]
    )


def delete_slotlock(service: str, slot_start_iso: str) -> None:
    table.delete_item(
        Key={"PK": f"SLOTLOCK#{service}#{slot_start_iso}", "SK": "LOCK"}
    )


def query_existing_slotlocks(service: str, slot_starts: Iterable[str]) -> set[str]:
    """Returns the subset of slot_starts that already have a SlotLock."""
    locked: set[str] = set()
    keys = [{"PK": f"SLOTLOCK#{service}#{s}", "SK": "LOCK"} for s in slot_starts]
    for chunk_start in range(0, len(keys), 100):
        chunk = keys[chunk_start:chunk_start + 100]
        if not chunk:
            continue
        resp = _resource.batch_get_item(
            RequestItems={_TABLE_NAME: {"Keys": chunk, "ProjectionExpression": "PK"}}
        )
        for item in resp.get("Responses", {}).get(_TABLE_NAME, []):
            pk = item["PK"]
            slot = pk.split("#", 2)[-1]
            locked.add(slot)
    return locked


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
