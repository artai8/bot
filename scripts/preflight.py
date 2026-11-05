#!/usr/bin/env python3
"""Preflight environment and schema validation check."""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from config import CONFIG
from database.database import SCHEMA_VERSION, store


def _has_index(indexes: dict[str, Any], keys: list[tuple[str, int]], *, unique: bool | None = None, ttl: int | None = None) -> bool:
    for info in indexes.values():
        key_list = [tuple(pair) for pair in info.get("key", [])]
        if key_list != keys:
            continue
        if unique is not None and bool(info.get("unique", False)) != unique:
            continue
        if ttl is not None and info.get("expireAfterSeconds") != ttl:
            continue
        return True
    return False


async def run_preflight(require_ha: bool = False, require_workers: bool = False) -> int:
    CONFIG.validate(require_bot_api=True, require_platform_auth=False)
    if not await store.ping():
        print("Database ping failed", file=sys.stderr)
        return 1

    await store.create_indexes()
    schema_doc = await store.metadata.find_one({"_id": "schema"})
    if not schema_doc or schema_doc.get("version") != SCHEMA_VERSION:
        print("Schema version mismatch", file=sys.stderr)
        return 1

    print("Preflight check passed.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-ha", action="store_true")
    parser.add_argument("--require-workers", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_preflight(args.require_ha, args.require_workers)))


if __name__ == "__main__":
    main()
