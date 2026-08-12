#!/usr/bin/env python3
"""Seed the admin master license key into MongoDB.

The master license grants UNLIMITED usage (no quota, no practical seat
limit) and is intended for the app owner / administrators only.

Usage:
    cd website
    python seed_master_license.py

It reads MONGODB_URI / MONGODB_DB_NAME (and optionally MASTER_LICENSE_KEY)
from the website/.env file. Running this is optional — the master license
document is also created automatically in MongoDB the first time the master
key is activated by a device.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

from license_store import MASTER_LICENSE_KEY, MongoLicenseStore  # noqa: E402


def main() -> int:
    if not MASTER_LICENSE_KEY:
        print("[ERROR] MASTER_LICENSE_KEY is not set. Refusing to seed.")
        return 1

    store = MongoLicenseStore()
    try:
        store.ping()
    except Exception as exc:
        print(f"[ERROR] Cannot reach MongoDB: {exc}")
        print("Check MONGODB_URI / MONGODB_DB_NAME in website/.env")
        return 1

    doc = store.ensure_master_license()
    if not doc:
        print("[ERROR] Failed to persist the master license in MongoDB.")
        return 1

    print("[OK] Master license ready in MongoDB.")
    print(f"  Collection : licenses")
    print(f"  License ID : {doc['_id']}")
    print(f"  Plan       : {doc.get('planCode')}")
    print(f"  Status     : {doc.get('status')}")
    print(f"  Quota      : Unlimited")
    print(f"  Seats      : {doc.get('seatLimit')}")
    print(f"  Hint       : {doc.get('licenseHint')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
