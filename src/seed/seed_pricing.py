"""Seed the price catalog (CONFIG#PRICING / CATALOG).

Prices come from Dr Moodley's printed flyers (Aug 2026) and the practice's
session/package rates. Admins maintain these afterwards from the dashboard
Pricing tab (PUT /api/admin/pricing) — re-running this seed OVERWRITES any
dashboard edits.

Run locally with:
    TABLE_NAME=WellMed-prod AWS_REGION=eu-west-1 python -m src.seed.seed_pricing
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.lib import dynamo  # noqa: E402
from src.lib.time_util import now_iso  # noqa: E402

CATALOG = [
    {
        "id": "iv-therapy",
        "title": "IV Therapy",
        "schedule": [],
        "items": [
            {
                "id": "energy-boost",
                "name": "Energy Boost",
                "price": 1150,
                "priceNote": None,
                "description": "Boosts energy and immunity. Includes Vit C, B vitamins, Calcium and Magnesium.",
                "extras": [
                    {"id": "extra-magnesium-taurine", "name": "Extra Magnesium & Taurine", "price": 150},
                ],
            },
            {
                "id": "immunity-boost",
                "name": "Immunity Boost",
                "price": 1150,
                "priceNote": None,
                "description": "Boosts immunity and helps prevent illnesses. Includes Vit C, B vitamins, Calcium and Magnesium.",
                "extras": [
                    {"id": "extra-taurine", "name": "Extra Taurine", "price": 150},
                ],
            },
            {
                "id": "skin-glow",
                "name": "Skin Glow",
                "price": 1350,
                "priceNote": None,
                "description": "Promotes the health and radiance of your skin, hair and nails. Includes Glutathione, Vit C and Biotin.",
                "extras": [],
            },
            {
                "id": "hangover-hydration",
                "name": "Hangover / Hydration",
                "price": 1350,
                "priceNote": None,
                "description": "Fluids, fluids, fluids (1L). Includes Taurine, Glutathione and Vit C — nature's natural detox.",
                "extras": [],
            },
            {
                "id": "iron-drip",
                "name": "Iron Drip",
                "price": 650,
                "priceNote": None,
                "description": "For iron-deficiency anaemia — 100% absorption and immediately bioavailable.",
                "extras": [],
            },
            {
                "id": "nad-plus",
                "name": "NAD+",
                "price": 1600,
                "priceNote": None,
                "description": "Works at an intracellular level to improve longevity, reduce stress and anxiety, and is anti-ageing.",
                "extras": [],
            },
            {
                "id": "weight-loss-drip",
                "name": "Weight Loss",
                "price": 850,
                "priceNote": None,
                "description": "Aids weight loss by enhancing metabolism and breaking down fat cells into energy. Includes Glutamine, Arginine and Carnitine.",
                "extras": [],
            },
            {
                "id": "libido",
                "name": "Libido",
                "price": 1050,
                "priceNote": None,
                "description": "For men and women — assists improved blood flow and a naturally stimulating change in libido. Includes Taurine, Vit B12 and Glutathione.",
                "extras": [],
            },
        ],
    },
    {
        "id": "ozone-therapy",
        "title": "Ozone Therapy",
        "schedule": [],
        "items": [
            {"id": "ozone-single", "name": "Single session (20 min)", "price": 300, "priceNote": None, "description": None, "extras": []},
            {"id": "ozone-package-10", "name": "Package — 10 sessions", "price": 2500, "priceNote": None, "description": None, "extras": []},
        ],
    },
    {
        "id": "red-light-therapy",
        "title": "Red Light Therapy",
        "schedule": [],
        "items": [
            {"id": "redlight-single", "name": "Single session (20 min)", "price": 250, "priceNote": None, "description": None, "extras": []},
            {"id": "redlight-package-10", "name": "Package — 10 sessions", "price": 2000, "priceNote": None, "description": None, "extras": []},
        ],
    },
    {
        "id": "yoga-breathwork",
        "title": "Yoga",
        "schedule": [
            {"day": "Tuesday", "time": "17:30 – 18:30"},
            {"day": "Friday", "time": "17:00 – 18:00"},
        ],
        "items": [
            {"id": "yoga-walk-in", "name": "Walk-in class", "price": 150, "priceNote": None, "description": None, "extras": []},
            {"id": "yoga-4-month", "name": "4 sessions per month", "price": 450, "priceNote": None, "description": None, "extras": []},
            {"id": "yoga-8-month", "name": "8 sessions per month", "price": 750, "priceNote": None, "description": None, "extras": []},
        ],
    },
    {
        "id": "tests",
        "title": "Tests & Gut Health",
        "schedule": [],
        "items": [
            {
                "id": "gut-biome-test",
                "name": "Gut Biome Test",
                "price": 4100,
                "priceNote": None,
                "description": "Comprehensive analysis of your gut microbiome.",
                "extras": [],
            },
            {
                "id": "functional-blood-test",
                "name": "Functional Blood Tests",
                "price": 2500,
                "priceNote": None,
                "description": "Functional blood tests conducted in-practice.",
                "extras": [],
            },
            {
                "id": "probiotics-custom",
                "name": "Tailor-made Probiotics",
                "price": None,
                "priceNote": "Tailored to your gut — quoted after your gut biome test",
                "description": "Probiotics formulated specifically for your gut profile.",
                "extras": [],
            },
        ],
    },
]


def main() -> None:
    dynamo.put_pricing_catalog(categories=CATALOG, updated_at=now_iso(), updated_by="seed")
    items = sum(len(c["items"]) for c in CATALOG)
    print(f"seeded pricing catalog: {len(CATALOG)} categories, {items} items")


if __name__ == "__main__":
    main()
