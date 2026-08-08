"""Seed ServiceConfig items for each service slug.

Run locally with:
    TABLE_NAME=WellMed-prod AWS_REGION=eu-west-1 python -m src.seed.seed_service_config
"""

from __future__ import annotations

import os
import sys

# Allow running as `python -m src.seed.seed_service_config` from backend/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.lib import dynamo  # noqa: E402
from src.lib.time_util import now_iso  # noqa: E402

_DEFAULT_HOURS = {
    "mon": {"open": "08:00", "close": "17:00"},
    "tue": {"open": "08:00", "close": "17:00"},
    "wed": {"open": "08:00", "close": "17:00"},
    "thu": {"open": "08:00", "close": "17:00"},
    "fri": {"open": "08:00", "close": "16:00"},
    "sat": {"open": "09:00", "close": "13:00"},
    "sun": None,
}

# Yoga runs as fixed classes: Tuesday 17:30–18:30 and Friday 17:00–18:00.
_YOGA_HOURS = {
    "mon": None,
    "tue": {"open": "17:30", "close": "18:30"},
    "wed": None,
    "thu": None,
    "fri": {"open": "17:00", "close": "18:00"},
    "sat": None,
    "sun": None,
}

# concurrentCapacity > 1 lets bookings share a slot (IV chairs, yoga mats).
_SERVICES = [
    {"service": "gp-practice",           "durationMinutes": 30, "bufferMinutes": 10, "businessHours": _DEFAULT_HOURS, "maxPerDay": None, "concurrentCapacity": 1},
    {"service": "iv-therapy",            "durationMinutes": 60, "bufferMinutes": 0,  "businessHours": _DEFAULT_HOURS, "maxPerDay": None, "concurrentCapacity": 3},
    {"service": "ozone-therapy",         "durationMinutes": 20, "bufferMinutes": 10, "businessHours": _DEFAULT_HOURS, "maxPerDay": None, "concurrentCapacity": 1},
    {"service": "red-light-therapy",     "durationMinutes": 20, "bufferMinutes": 10, "businessHours": _DEFAULT_HOURS, "maxPerDay": None, "concurrentCapacity": 1},
    {"service": "weight-loss",           "durationMinutes": 45, "bufferMinutes": 10, "businessHours": _DEFAULT_HOURS, "maxPerDay": None, "concurrentCapacity": 1},
    {"service": "yoga-breathwork",       "durationMinutes": 60, "bufferMinutes": 0,  "businessHours": _YOGA_HOURS,    "maxPerDay": None, "concurrentCapacity": 10},
    {"service": "gut-biome-test",        "durationMinutes": 30, "bufferMinutes": 10, "businessHours": _DEFAULT_HOURS, "maxPerDay": None, "concurrentCapacity": 1},
    {"service": "functional-blood-test", "durationMinutes": 30, "bufferMinutes": 10, "businessHours": _DEFAULT_HOURS, "maxPerDay": None, "concurrentCapacity": 1},
]


def main() -> None:
    now = now_iso()
    for cfg in _SERVICES:
        item = {
            "PK": f"SERVICE#{cfg['service']}",
            "SK": "CONFIG",
            "type": "service_config",
            "createdAt": now,
            "updatedAt": now,
            **cfg,
        }
        dynamo.put_service_config(item)
        print(f"seeded {cfg['service']}")


if __name__ == "__main__":
    main()
