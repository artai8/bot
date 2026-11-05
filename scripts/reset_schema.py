"""Explicitly reset the single-tenant database into schema version 2.

The command is a dry run unless both ``--execute`` and
``--confirm <DATABASE_NAME>`` are provided.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import CONFIG  # noqa: E402
from database.database import SCHEMA_VERSION, store, utcnow  # noqa: E402

RESET_COLLECTIONS = (
    "tenants",
    "users",
    "shares",
    "banned_users",
    "bot_stats",
    "bot_config",
    "web_sessions",
    "bot_leases",
    "bot_jobs",
    "audit_logs",
    "platform_metadata",
)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Reset the multi-tenant Mongo schema")
    parser.add_argument("--dry-run", action="store_true", help="show counts only (default)")
    parser.add_argument("--execute", action="store_true", help="perform the reset")
    parser.add_argument("--confirm", default="", help="must equal DATABASE_NAME")
    args = parser.parse_args()

    CONFIG.validate()
    print(f"Database: {CONFIG.database_name}")
    for name in RESET_COLLECTIONS:
        count = await store.database[name].count_documents({})
        print(f"{name}: {count} document(s)")

    if args.dry_run or not args.execute:
        print("Dry run only. No data was deleted.")
        return 0
    if args.confirm != CONFIG.database_name:
        print("Refusing reset: --confirm must exactly match DATABASE_NAME", file=sys.stderr)
        return 2

    for name in RESET_COLLECTIONS:
        await store.database[name].drop()
    await store.metadata.insert_one(
        {"_id": "schema", "version": SCHEMA_VERSION, "created_at": utcnow()}
    )
    await store.create_indexes()
    print(f"Reset complete. Schema version: {SCHEMA_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
