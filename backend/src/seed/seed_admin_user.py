"""Seed Dr Moodley as the initial admin user.

Usage:
    TABLE_NAME=WellMed-prod AWS_REGION=eu-west-1 \
    ADMIN_EMAIL=doctor@wellmed.co.za ADMIN_PASSWORD='change-me' ADMIN_ROLE=doctor \
    python -m src.seed.seed_admin_user
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.lib import dynamo, passwords  # noqa: E402
from src.lib.ids import new_admin_user_id  # noqa: E402
from src.lib.time_util import now_iso  # noqa: E402


def main() -> None:
    email = os.environ.get("ADMIN_EMAIL", "doctor@wellmed.co.za").lower()
    password = os.environ.get("ADMIN_PASSWORD")
    role = os.environ.get("ADMIN_ROLE", "doctor")
    if not password:
        raise SystemExit("ADMIN_PASSWORD env var required")

    existing = dynamo.get_admin_user(email)
    user_id = existing.get("userId") if existing else new_admin_user_id()
    now = now_iso()

    item = {
        "PK": f"ADMIN#{email}",
        "SK": "PROFILE",
        "type": "admin_user",
        "userId": user_id,
        "email": email,
        "passwordHash": passwords.hash_password(password),
        "role": role,
        "createdAt": (existing or {}).get("createdAt", now),
        "updatedAt": now,
    }
    dynamo.put_admin_user(item)
    print(f"seeded admin user {email} (role={role})")


if __name__ == "__main__":
    main()
