"""Read-only production readiness checks.

This command never creates indexes, changes schema, or prints credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import CONFIG  # noqa: E402
from database.database import SCHEMA_VERSION, store, utcnow  # noqa: E402


def _has_index(indexes, keys, *, unique=None, ttl=None):
    expected = list(keys)
    for definition in indexes.values():
        if list(definition.get("key", [])) != expected:
            continue
        if unique is not None and bool(definition.get("unique", False)) != unique:
            continue
        if ttl is not None and definition.get("expireAfterSeconds") != ttl:
            continue
        return True
    return False


def _is_high_availability(topology: dict) -> bool:
    return bool(topology.get("setName")) or topology.get("msg") == "isdbgrid"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run read-only production readiness checks")
    parser.add_argument(
        "--require-ha",
        action="store_true",
        help="fail unless MongoDB is a replica set or sharded cluster",
    )
    parser.add_argument(
        "--require-workers",
        action="store_true",
        help="fail unless every enabled tenant has an active worker lease",
    )
    args = parser.parse_args()
    failures = []
    try:
        CONFIG.validate(require_bot_api=True, require_platform_auth=True)
    except RuntimeError as exc:
        failures.append(str(exc))

    if not await store.ping():
        failures.append("MongoDB is unreachable")
        print(json.dumps({"status": "failed", "failures": failures}))
        return 1

    if args.require_ha:
        try:
            topology = await store.client.admin.command("hello")
            if not _is_high_availability(topology):
                failures.append("MongoDB is not a replica set or sharded cluster")
        except Exception:
            failures.append("Unable to verify MongoDB high-availability topology")

    marker = await store.metadata.find_one({"_id": "schema"})
    if not marker or int(marker.get("version", 0)) != SCHEMA_VERSION:
        failures.append(f"MongoDB schema must be initialized at version {SCHEMA_VERSION}")

    checks = (
        (store.tenants, [("slug", 1)], True, None, "tenants.slug unique"),
        (store.users, [("tenant_id", 1), ("user_id", 1)], True, None, "users tenant/user"),
        (store.shares, [("tenant_id", 1), ("code", 1)], True, None, "shares tenant/code"),
        (
            store.banned_users,
            [("tenant_id", 1), ("user_id", 1)],
            True,
            None,
            "bans tenant/user",
        ),
        (store.stats, [("tenant_id", 1), ("key", 1)], True, None, "stats tenant/key"),
        (store.sessions, [("expires_at", 1)], None, 0, "sessions TTL"),
        (store.leases, [("expires_at", 1)], None, 0, "leases TTL"),
        (store.jobs, [("purge_at", 1)], None, 0, "jobs TTL"),
    )
    for collection, keys, unique, ttl, label in checks:
        indexes = await collection.index_information()
        if not _has_index(indexes, keys, unique=unique, ttl=ttl):
            failures.append(f"Missing required index: {label}")

    enabled_documents = await store.tenants.find(
        {"enabled": True}, {"_id": 1}
    ).to_list(length=None)
    enabled_tenant_ids = [str(document["_id"]) for document in enabled_documents]
    active_lease_documents = await store.leases.find(
        {
            "tenant_id": {"$in": enabled_tenant_ids},
            "expires_at": {"$gt": utcnow()},
            "status": "running",
        },
        {"tenant_id": 1, "worker_id": 1},
    ).to_list(length=None)
    enabled_tenants = len(enabled_tenant_ids)
    active_leases = len(active_lease_documents)
    active_workers = len(
        {document.get("worker_id") for document in active_lease_documents if document.get("worker_id")}
    )
    queued_jobs = await store.jobs.count_documents({"state": "queued"})
    recommended_workers = max(1, math.ceil(enabled_tenants / CONFIG.worker_bot_capacity))
    if args.require_workers and active_leases != enabled_tenants:
        failures.append(
            f"Only {active_leases} of {enabled_tenants} enabled tenants have active worker leases"
        )
    if args.require_workers and enabled_tenants and active_workers < recommended_workers:
        failures.append(
            f"Only {active_workers} active workers; at least {recommended_workers} are required"
        )
    result = {
        "status": "passed" if not failures else "failed",
        "schema_version": (marker or {}).get("version"),
        "enabled_tenants": enabled_tenants,
        "active_leases": active_leases,
        "active_workers": active_workers,
        "queued_jobs": queued_jobs,
        "minimum_worker_replicas": recommended_workers,
        "failures": failures,
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
