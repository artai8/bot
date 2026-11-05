"""MongoDB persistence with mandatory tenant scoping.

All business-data methods take ``tenant_id`` as their first argument. Platform
collections (tenants, sessions, leases, jobs, audit logs) are intentionally
separate and expose explicit platform methods.
"""

from __future__ import annotations

import logging
import re
import secrets
import string
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

import motor.motor_asyncio
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from config import CONFIG, tenant_defaults
from security import (
    TenantCrypto,
    generate_session_token,
    hash_password,
    mask_bot_token,
    token_digest,
)

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 2
TERMINAL_JOB_STATES = {"completed", "failed", "cancelled"}
EMPTY_STRING = ""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def timestamp() -> float:
    return time.time()


class MongoStore:
    def __init__(self, client: Any, database_name: str):
        self.client = client
        self.database = client[database_name]
        self.tenants = self.database["tenants"]
        self.users = self.database["users"]
        self.shares = self.database["shares"]
        self.banned_users = self.database["banned_users"]
        self.stats = self.database["bot_stats"]
        self.sessions = self.database["web_sessions"]
        self.leases = self.database["bot_leases"]
        self.jobs = self.database["bot_jobs"]
        self.audit_logs = self.database["audit_logs"]
        self.metadata = self.database["platform_metadata"]

    async def ping(self) -> bool:
        try:
            await self.client.admin.command("ping")
            return True
        except Exception as exc:
            logger.error("Database ping failed: %s", exc)
            return False

    async def ensure_schema(self) -> None:
        marker = await self.metadata.find_one({"_id": "schema"})
        if marker:
            version = int(marker.get("version", 0))
            if version != SCHEMA_VERSION:
                raise RuntimeError(
                    f"Unsupported database schema {version}; expected {SCHEMA_VERSION}"
                )
            return

        legacy_collections = []
        for name, collection in (
            ("users", self.users),
            ("shares", self.shares),
            ("banned_users", self.banned_users),
            ("bot_stats", self.stats),
        ):
            if await collection.find_one({"tenant_id": {"$exists": False}}):
                legacy_collections.append(name)
        if legacy_collections:
            names = ", ".join(legacy_collections)
            raise RuntimeError(
                "Legacy single-tenant data detected in "
                f"{names}. Run scripts/reset_schema.py explicitly before startup."
            )
        try:
            await self.metadata.update_one(
                {"_id": "schema"},
                {"$setOnInsert": {"version": SCHEMA_VERSION, "created_at": utcnow()}},
                upsert=True,
            )
        except DuplicateKeyError:
            # Web and worker roles can initialize an empty database together.
            marker = await self.metadata.find_one({"_id": "schema"})
            if not marker or int(marker.get("version", 0)) != SCHEMA_VERSION:
                raise

    async def create_indexes(self) -> None:
        await self.ensure_schema()
        await self.tenants.create_index("slug", unique=True)
        await self.users.create_index(
            [("tenant_id", ASCENDING), ("user_id", ASCENDING)], unique=True
        )
        await self.users.create_index([("tenant_id", ASCENDING), ("joined_at", DESCENDING)])
        await self.shares.create_index([("tenant_id", ASCENDING), ("code", ASCENDING)], unique=True)
        await self.shares.create_index(
            [("tenant_id", ASCENDING), ("owner_id", ASCENDING), ("created_at", DESCENDING)]
        )
        await self.shares.create_index([("tenant_id", ASCENDING), ("message_ids", ASCENDING)])
        await self.shares.create_index([("tenant_id", ASCENDING), ("keywords", ASCENDING)])
        await self.banned_users.create_index(
            [("tenant_id", ASCENDING), ("user_id", ASCENDING)], unique=True
        )
        await self.stats.create_index([("tenant_id", ASCENDING), ("key", ASCENDING)], unique=True)
        await self.sessions.create_index("expires_at", expireAfterSeconds=0)
        await self.leases.create_index("tenant_id", unique=True)
        await self.leases.create_index("expires_at", expireAfterSeconds=0)
        await self.jobs.create_index(
            [("state", ASCENDING), ("available_at", ASCENDING), ("tenant_id", ASCENDING)]
        )
        await self.jobs.create_index("purge_at", expireAfterSeconds=0)
        await self.jobs.create_index(
            [("tenant_id", ASCENDING), ("idempotency_key", ASCENDING)],
            unique=True,
            partialFilterExpression={"idempotency_key": {"$type": "string"}},
        )
        await self.audit_logs.create_index([("tenant_id", ASCENDING), ("created_at", DESCENDING)])

    # -- tenants ---------------------------------------------------------
    async def create_tenant(
        self,
        *,
        slug: str,
        display_name: str,
        bot_token: str,
        owner_id: int,
        admin_ids: list[int],
        db_channel_id: int,
        web_password: str,
        crypto: TenantCrypto,
        enabled: bool = False,
        settings: dict[str, Any] | None = None,
        bot_username: str = "",
    ) -> dict[str, Any]:
        tenant_id = str(uuid.uuid4())
        now = timestamp()
        tenant_settings = tenant_defaults()
        tenant_settings.update(settings or {})
        admins = list(dict.fromkeys([int(owner_id), *(int(x) for x in admin_ids)]))
        document = {
            "_id": tenant_id,
            "slug": slug,
            "display_name": display_name,
            "enabled": bool(enabled),
            "bot_token_ciphertext": crypto.encrypt_bot_token(bot_token),
            "bot_token_hint": mask_bot_token(bot_token),
            "bot_username": bot_username,
            "owner_id": int(owner_id),
            "admin_ids": admins,
            "db_channel_id": int(db_channel_id),
            "web_password_hash": hash_password(web_password),
            "settings": tenant_settings,
            "config_version": 1,
            "created_at": now,
            "updated_at": now,
        }
        await self.tenants.insert_one(document)
        return document

    async def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        return await self.tenants.find_one({"_id": str(tenant_id)})

    async def get_tenant_by_slug(self, slug: str) -> dict[str, Any] | None:
        return await self.tenants.find_one({"slug": slug})

    async def list_tenants(self, *, enabled: bool | None = None) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if enabled is not None:
            query["enabled"] = enabled
        cursor = self.tenants.find(query).sort("created_at", ASCENDING)
        return [document async for document in cursor]

    async def update_tenant_core(
        self, tenant_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not updates:
            return await self.get_tenant(tenant_id)
        safe = {
            key: value
            for key, value in updates.items()
            if key
            in {
                "display_name",
                "enabled",
                "owner_id",
                "admin_ids",
                "db_channel_id",
                "bot_token_ciphertext",
                "bot_token_hint",
                "bot_username",
                "web_password_hash",
            }
        }
        if not safe:
            return await self.get_tenant(tenant_id)
        safe["updated_at"] = timestamp()
        return await self.tenants.find_one_and_update(
            {"_id": str(tenant_id)},
            {"$set": safe, "$inc": {"config_version": 1}},
            return_document=ReturnDocument.AFTER,
        )

    async def update_tenant_settings(
        self, tenant_id: str, settings: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not settings:
            return await self.get_tenant(tenant_id)
        updates = {f"settings.{key}": value for key, value in settings.items()}
        updates["updated_at"] = timestamp()
        return await self.tenants.find_one_and_update(
            {"_id": str(tenant_id)},
            {"$set": updates, "$inc": {"config_version": 1}},
            return_document=ReturnDocument.AFTER,
        )

    async def reset_tenant_setting(self, tenant_id: str, key: str) -> bool:
        result = await self.tenants.update_one(
            {"_id": str(tenant_id)},
            {
                "$unset": {f"settings.{key}": ""},
                "$set": {"updated_at": timestamp()},
                "$inc": {"config_version": 1},
            },
        )
        return result.matched_count == 1

    # -- users -----------------------------------------------------------
    async def present_user(self, tenant_id: str, user_id: int) -> bool:
        return bool(
            await self.users.find_one(
                {"tenant_id": str(tenant_id), "user_id": int(user_id)}, {"_id": 1}
            )
        )

    async def add_user(self, tenant_id: str, user_id: int) -> None:
        await self.users.update_one(
            {"tenant_id": str(tenant_id), "user_id": int(user_id)},
            {
                "$setOnInsert": {
                    "tenant_id": str(tenant_id),
                    "user_id": int(user_id),
                    "verify_status": {
                        "is_verified": False,
                        "verified_time": 0,
                        "verify_token": EMPTY_STRING,
                        "link": "",
                    },
                    "force_sub_status": {
                        "verified": False,
                        "verified_at": 0,
                        "channels": [],
                    },
                    "joined_at": timestamp(),
                }
            },
            upsert=True,
        )

    async def get_verify_status(self, tenant_id: str, user_id: int) -> dict[str, Any]:
        document = await self.users.find_one(
            {"tenant_id": str(tenant_id), "user_id": int(user_id)},
            {"verify_status": 1},
        )
        status = (document or {}).get("verify_status") or {}
        return {
            "is_verified": bool(status.get("is_verified", False)),
            "verified_time": status.get("verified_time", 0),
            "verify_token": status.get("verify_token", ""),
            "link": status.get("link", ""),
        }

    async def update_verify_status(
        self, tenant_id: str, user_id: int, status: dict[str, Any]
    ) -> None:
        await self.add_user(tenant_id, user_id)
        await self.users.update_one(
            {"tenant_id": str(tenant_id), "user_id": int(user_id)},
            {"$set": {"verify_status": dict(status)}},
        )

    async def update_force_sub_status(
        self, tenant_id: str, user_id: int, status: dict[str, Any]
    ) -> None:
        await self.add_user(tenant_id, user_id)
        await self.users.update_one(
            {"tenant_id": str(tenant_id), "user_id": int(user_id)},
            {"$set": {"force_sub_status": dict(status)}},
        )

    async def list_users(
        self, tenant_id: str, page: int = 1, per_page: int = 20
    ) -> tuple[list[int], int]:
        query = {"tenant_id": str(tenant_id)}
        total = await self.users.count_documents(query)
        cursor = (
            self.users.find(query, {"user_id": 1, "_id": 0})
            .sort("joined_at", DESCENDING)
            .skip(max(0, page - 1) * per_page)
            .limit(per_page)
        )
        return [document["user_id"] async for document in cursor], total

    async def iter_user_ids(self, tenant_id: str) -> AsyncIterator[int]:
        cursor = self.users.find({"tenant_id": str(tenant_id)}, {"user_id": 1})
        async for document in cursor:
            yield int(document["user_id"])

    async def full_userbase(self, tenant_id: str) -> list[int]:
        return [user_id async for user_id in self.iter_user_ids(tenant_id)]

    async def delete_user(self, tenant_id: str, user_id: int) -> bool:
        result = await self.users.delete_one({"tenant_id": str(tenant_id), "user_id": int(user_id)})
        return result.deleted_count == 1

    async def get_user_count(self, tenant_id: str) -> int:
        return await self.users.count_documents({"tenant_id": str(tenant_id)})

    async def get_recent_users(self, tenant_id: str, days: int = 7) -> int:
        cutoff = timestamp() - days * 86400
        return await self.users.count_documents(
            {"tenant_id": str(tenant_id), "joined_at": {"$gte": cutoff}}
        )

    # -- shares ----------------------------------------------------------
    async def create_share(
        self,
        tenant_id: str,
        share_code: str,
        owner_id: int,
        message_ids: list[int],
        title: str = "",
        protect_content: bool = False,
        group_text: str = "",
        keywords: list[str] | None = None,
        code_length: int = 8,
    ) -> dict[str, Any]:
        chars = string.ascii_letters + string.digits
        candidate = share_code
        for _ in range(10):
            now = timestamp()
            document = {
                "tenant_id": str(tenant_id),
                "code": candidate,
                "owner_id": int(owner_id),
                "message_ids": [int(value) for value in message_ids],
                "title": title,
                "group_text": group_text,
                "keywords": list(keywords or []),
                "protect_content": bool(protect_content),
                "access_count": 0,
                "created_at": now,
                "updated_at": now,
            }
            try:
                result = await self.shares.insert_one(document)
                document["_id"] = result.inserted_id
                return document
            except DuplicateKeyError:
                candidate = "".join(secrets.choice(chars) for _ in range(code_length))
        raise RuntimeError("Unable to allocate a unique share code")

    async def get_share(self, tenant_id: str, share_code: str) -> dict[str, Any] | None:
        return await self.shares.find_one({"tenant_id": str(tenant_id), "code": share_code})

    async def find_share_by_message_id(
        self, tenant_id: str, message_id: int
    ) -> dict[str, Any] | None:
        return await self.shares.find_one(
            {"tenant_id": str(tenant_id), "message_ids": int(message_id)}
        )

    async def find_share_by_group_text(
        self, tenant_id: str, group_text: str, owner_id: int | None = None
    ) -> dict[str, Any] | None:
        query: dict[str, Any] = {"tenant_id": str(tenant_id), "group_text": group_text}
        if owner_id is not None:
            query["owner_id"] = int(owner_id)
        return await self.shares.find_one(query)

    async def find_share_by_keyword(self, tenant_id: str, keyword: str) -> dict[str, Any] | None:
        return await self.shares.find_one(
            {"tenant_id": str(tenant_id), "keywords": keyword},
            sort=[("created_at", DESCENDING)],
        )

    async def find_shares_by_keyword(
        self, tenant_id: str, keyword: str, limit: int = 6
    ) -> list[dict[str, Any]]:
        cursor = (
            self.shares.find({"tenant_id": str(tenant_id), "keywords": keyword})
            .sort("created_at", DESCENDING)
            .limit(limit)
        )
        return [document async for document in cursor]

    async def increment_share_access(self, tenant_id: str, share_code: str) -> None:
        await self.shares.update_one(
            {"tenant_id": str(tenant_id), "code": share_code},
            {"$inc": {"access_count": 1}},
        )

    async def get_user_shares(
        self, tenant_id: str, owner_id: int, page: int = 1, per_page: int = 10
    ) -> tuple[list[dict[str, Any]], int]:
        query = {"tenant_id": str(tenant_id), "owner_id": int(owner_id)}
        total = await self.shares.count_documents(query)
        cursor = (
            self.shares.find(query)
            .sort("created_at", DESCENDING)
            .skip(max(0, page - 1) * per_page)
            .limit(per_page)
        )
        return [document async for document in cursor], total

    async def list_shares(
        self, tenant_id: str, page: int = 1, per_page: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        query = {"tenant_id": str(tenant_id)}
        total = await self.shares.count_documents(query)
        cursor = (
            self.shares.find(query)
            .sort("created_at", DESCENDING)
            .skip(max(0, page - 1) * per_page)
            .limit(per_page)
        )
        return [document async for document in cursor], total

    async def iter_shares(self, tenant_id: str) -> AsyncIterator[dict[str, Any]]:
        cursor = self.shares.find({"tenant_id": str(tenant_id)}).sort(
            "created_at", ASCENDING
        )
        async for document in cursor:
            yield document

    async def update_share(self, tenant_id: str, share_code: str, updates: dict[str, Any]) -> bool:
        safe = {
            key: value
            for key, value in updates.items()
            if key in {"title", "protect_content", "group_text", "keywords"}
        }
        if not safe:
            return bool(await self.get_share(tenant_id, share_code))
        safe["updated_at"] = timestamp()
        result = await self.shares.update_one(
            {"tenant_id": str(tenant_id), "code": share_code}, {"$set": safe}
        )
        return result.matched_count == 1

    async def merge_share_messages(
        self,
        tenant_id: str,
        share_code: str,
        message_ids: list[int],
        keywords: list[str] | None = None,
    ) -> bool:
        update: dict[str, Any] = {
            "$addToSet": {"message_ids": {"$each": [int(value) for value in message_ids]}},
            "$set": {"updated_at": timestamp()},
        }
        if keywords:
            update["$set"]["keywords"] = list(keywords)
        result = await self.shares.update_one(
            {"tenant_id": str(tenant_id), "code": share_code}, update
        )
        return result.matched_count == 1

    async def delete_share(self, tenant_id: str, share_code: str) -> bool:
        result = await self.shares.delete_one({"tenant_id": str(tenant_id), "code": share_code})
        return result.deleted_count == 1

    async def get_total_shares(self, tenant_id: str) -> int:
        return await self.shares.count_documents({"tenant_id": str(tenant_id)})

    async def search_shares(
        self, tenant_id: str, query: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        shares, _ = await self.search_shares_page(tenant_id, query, 1, limit)
        return shares

    async def search_shares_page(
        self, tenant_id: str, query: str, page: int, per_page: int
    ) -> tuple[list[dict[str, Any]], int]:
        escaped = re.escape(query)
        search_filter = {
            "tenant_id": str(tenant_id),
            "$or": [
                {"title": {"$regex": escaped, "$options": "i"}},
                {"keywords": {"$regex": escaped, "$options": "i"}},
                {"code": query},
            ],
        }
        total = await self.shares.count_documents(search_filter)
        cursor = (
            self.shares.find(search_filter)
            .sort("created_at", DESCENDING)
            .skip(max(0, page - 1) * per_page)
            .limit(per_page)
        )
        return [document async for document in cursor], total

    async def get_user_share_count(self, tenant_id: str, owner_id: int) -> int:
        return await self.shares.count_documents(
            {"tenant_id": str(tenant_id), "owner_id": int(owner_id)}
        )

    # -- bans and stats --------------------------------------------------
    async def ban_user(self, tenant_id: str, user_id: int, reason: str = "") -> None:
        await self.banned_users.update_one(
            {"tenant_id": str(tenant_id), "user_id": int(user_id)},
            {
                "$set": {"reason": reason, "banned_at": timestamp()},
                "$setOnInsert": {"tenant_id": str(tenant_id), "user_id": int(user_id)},
            },
            upsert=True,
        )

    async def unban_user(self, tenant_id: str, user_id: int) -> bool:
        result = await self.banned_users.delete_one(
            {"tenant_id": str(tenant_id), "user_id": int(user_id)}
        )
        return result.deleted_count == 1

    async def is_banned(self, tenant_id: str, user_id: int) -> dict[str, Any] | None:
        return await self.banned_users.find_one(
            {"tenant_id": str(tenant_id), "user_id": int(user_id)}
        )

    async def get_banned_users(self, tenant_id: str) -> list[dict[str, Any]]:
        cursor = self.banned_users.find({"tenant_id": str(tenant_id)}).sort("banned_at", DESCENDING)
        return [document async for document in cursor]

    async def get_banned_count(self, tenant_id: str) -> int:
        return await self.banned_users.count_documents({"tenant_id": str(tenant_id)})

    async def increment_stat(self, tenant_id: str, key: str, value: int = 1) -> None:
        await self.stats.update_one(
            {"tenant_id": str(tenant_id), "key": key},
            {
                "$inc": {"count": int(value)},
                "$setOnInsert": {"tenant_id": str(tenant_id), "key": key},
            },
            upsert=True,
        )

    async def get_stat(self, tenant_id: str, key: str) -> int:
        document = await self.stats.find_one({"tenant_id": str(tenant_id), "key": key})
        return int((document or {}).get("count", 0))

    async def get_all_stats(self, tenant_id: str) -> dict[str, int]:
        cursor = self.stats.find({"tenant_id": str(tenant_id)})
        return {document["key"]: int(document.get("count", 0)) async for document in cursor}

    # -- web sessions and audit -----------------------------------------
    async def create_session(
        self,
        *,
        role: str,
        expires_in: int,
        tenant_id: str | None = None,
        actor_role: str | None = None,
        actor_id: str | None = None,
        impersonated: bool = False,
    ) -> str:
        token = generate_session_token()
        now = utcnow()
        await self.sessions.insert_one(
            {
                "_id": token_digest(token),
                "role": role,
                "tenant_id": tenant_id,
                "actor_role": actor_role or role,
                "actor_id": actor_id or tenant_id or "platform",
                "impersonated": bool(impersonated),
                "created_at": now,
                "expires_at": now + timedelta(seconds=expires_in),
            }
        )
        return token

    async def get_session(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        return await self.sessions.find_one(
            {"_id": token_digest(token), "expires_at": {"$gt": utcnow()}}
        )

    async def delete_session(self, token: str) -> None:
        if token:
            await self.sessions.delete_one({"_id": token_digest(token)})

    async def delete_tenant_sessions(self, tenant_id: str) -> int:
        result = await self.sessions.delete_many(
            {"tenant_id": str(tenant_id), "impersonated": {"$ne": True}}
        )
        return result.deleted_count

    async def record_audit(
        self,
        *,
        action: str,
        actor_role: str,
        actor_id: str,
        tenant_id: str | None,
        target: str = "",
        metadata: dict[str, Any] | None = None,
        remote_ip: str = "",
    ) -> None:
        await self.audit_logs.insert_one(
            {
                "action": action,
                "actor_role": actor_role,
                "actor_id": actor_id,
                "tenant_id": tenant_id,
                "target": target,
                "metadata": metadata or {},
                "remote_ip": remote_ip,
                "created_at": timestamp(),
            }
        )

    # -- worker leases ---------------------------------------------------
    async def claim_lease(
        self, tenant_id: str, worker_id: str, lease_seconds: int
    ) -> dict[str, Any] | None:
        now = utcnow()
        expires_at = now + timedelta(seconds=lease_seconds)
        document = await self.leases.find_one_and_update(
            {
                "tenant_id": str(tenant_id),
                "$or": [
                    {"expires_at": {"$lte": now}},
                    {"worker_id": worker_id},
                ],
            },
            {
                "$set": {
                    "worker_id": worker_id,
                    "expires_at": expires_at,
                    "heartbeat_at": now,
                    "status": "starting",
                    "last_error": "",
                },
                "$inc": {"generation": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document:
            return document
        try:
            document = {
                "tenant_id": str(tenant_id),
                "worker_id": worker_id,
                "generation": 1,
                "expires_at": expires_at,
                "heartbeat_at": now,
                "status": "starting",
                "last_error": "",
            }
            result = await self.leases.insert_one(document)
            document["_id"] = result.inserted_id
            return document
        except DuplicateKeyError:
            return None

    async def renew_lease(
        self,
        tenant_id: str,
        worker_id: str,
        generation: int,
        lease_seconds: int,
        *,
        status: str = "running",
        last_error: str = "",
        bot_username: str = "",
    ) -> bool:
        now = utcnow()
        result = await self.leases.update_one(
            {
                "tenant_id": str(tenant_id),
                "worker_id": worker_id,
                "generation": int(generation),
                "expires_at": {"$gt": now},
            },
            {
                "$set": {
                    "heartbeat_at": now,
                    "expires_at": now + timedelta(seconds=lease_seconds),
                    "status": status,
                    "last_error": last_error[:500],
                    "bot_username": bot_username,
                }
            },
        )
        return result.matched_count == 1

    async def release_lease(self, tenant_id: str, worker_id: str, generation: int) -> None:
        await self.leases.delete_one(
            {
                "tenant_id": str(tenant_id),
                "worker_id": worker_id,
                "generation": int(generation),
            }
        )

    async def get_tenant_runtime(self, tenant_id: str) -> dict[str, Any] | None:
        return await self.leases.find_one({"tenant_id": str(tenant_id)})

    # -- worker jobs -----------------------------------------------------
    async def create_job(
        self,
        *,
        tenant_id: str,
        job_type: str,
        payload: dict[str, Any],
        actor: dict[str, Any],
        idempotency_key: str | None = None,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        now = utcnow()
        document = {
            "_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "type": job_type,
            "state": "queued",
            "payload": payload,
            "actor": actor,
            "idempotency_key": idempotency_key,
            "progress": {"current": 0, "total": 0, "successful": 0, "failed": 0},
            "attempts": 0,
            "max_attempts": int(max_attempts),
            "available_at": now,
            "created_at": now,
            "updated_at": now,
            "last_error": "",
        }
        if idempotency_key is None:
            document.pop("idempotency_key")
        try:
            await self.jobs.insert_one(document)
            return document
        except DuplicateKeyError:
            existing = await self.jobs.find_one(
                {"tenant_id": str(tenant_id), "idempotency_key": idempotency_key}
            )
            if existing:
                return existing
            raise

    async def claim_job(
        self,
        worker_id: str,
        tenant_ids: list[str],
        lease_seconds: int,
        job_types: list[str] | None = None,
    ) -> dict[str, Any] | None:
        if not tenant_ids:
            return None
        now = utcnow()
        query: dict[str, Any] = {
            "tenant_id": {"$in": [str(value) for value in tenant_ids]},
            "state": "queued",
            "available_at": {"$lte": now},
            "$expr": {"$lt": ["$attempts", "$max_attempts"]},
        }
        if job_types:
            query["type"] = {"$in": job_types}
        return await self.jobs.find_one_and_update(
            query,
            {
                "$set": {
                    "state": "processing",
                    "worker_id": worker_id,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "updated_at": now,
                },
                "$inc": {"attempts": 1},
            },
            sort=[("created_at", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )

    async def update_job_progress(
        self,
        job_id: str,
        worker_id: str,
        progress: dict[str, int],
        lease_seconds: int,
    ) -> bool:
        now = utcnow()
        result = await self.jobs.update_one(
            {
                "_id": job_id,
                "worker_id": worker_id,
                "state": "processing",
                "lease_expires_at": {"$gt": now},
            },
            {
                "$set": {
                    "progress": progress,
                    "updated_at": now,
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                }
            },
        )
        return result.matched_count == 1

    async def renew_job_lease(self, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        now = utcnow()
        result = await self.jobs.update_one(
            {
                "_id": job_id,
                "worker_id": worker_id,
                "state": "processing",
                "lease_expires_at": {"$gt": now},
            },
            {
                "$set": {
                    "lease_expires_at": now + timedelta(seconds=lease_seconds),
                    "updated_at": now,
                }
            },
        )
        return result.matched_count == 1

    async def finish_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        success: bool,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        now = utcnow()
        document = await self.jobs.find_one(
            {
                "_id": job_id,
                "worker_id": worker_id,
                "state": "processing",
                "lease_expires_at": {"$gt": now},
            }
        )
        if not document:
            return
        if success:
            update = {
                "state": "completed",
                "result": result or {},
                "last_error": "",
                "updated_at": now,
                "purge_at": now + timedelta(days=7),
            }
        elif int(document.get("attempts", 0)) < int(document.get("max_attempts", 3)):
            update = {
                "state": "queued",
                "available_at": now + timedelta(seconds=min(60, 2 ** document["attempts"])),
                "last_error": error[:500],
                "updated_at": now,
            }
        else:
            update = {
                "state": "failed",
                "last_error": error[:500],
                "updated_at": now,
                "purge_at": now + timedelta(days=7),
            }
        await self.jobs.update_one(
            {
                "_id": job_id,
                "worker_id": worker_id,
                "state": "processing",
                "lease_expires_at": {"$gt": now},
            },
            {"$set": update, "$unset": {"worker_id": "", "lease_expires_at": ""}},
        )

    async def requeue_expired_jobs(self) -> int:
        now = utcnow()
        exhausted = await self.jobs.update_many(
            {
                "state": "processing",
                "lease_expires_at": {"$lte": now},
                "$expr": {"$gte": ["$attempts", "$max_attempts"]},
            },
            {
                "$set": {
                    "state": "failed",
                    "last_error": "Worker lease expired after final attempt",
                    "updated_at": now,
                    "purge_at": now + timedelta(days=7),
                },
                "$unset": {"worker_id": "", "lease_expires_at": ""},
            },
        )
        retryable = await self.jobs.update_many(
            {
                "state": "processing",
                "lease_expires_at": {"$lte": now},
                "$expr": {"$lt": ["$attempts", "$max_attempts"]},
            },
            {
                "$set": {"state": "queued", "available_at": now, "updated_at": now},
                "$unset": {"worker_id": "", "lease_expires_at": ""},
            },
        )
        return exhausted.modified_count + retryable.modified_count

    async def cancel_tenant_jobs(self, tenant_id: str, reason: str) -> int:
        now = utcnow()
        result = await self.jobs.update_many(
            {
                "tenant_id": str(tenant_id),
                "state": {"$in": ["queued", "processing"]},
            },
            {
                "$set": {
                    "state": "cancelled",
                    "last_error": reason[:500],
                    "updated_at": now,
                    "purge_at": now + timedelta(days=7),
                },
                "$unset": {"worker_id": "", "lease_expires_at": ""},
            },
        )
        return result.modified_count

    async def get_job(self, tenant_id: str, job_id: str) -> dict[str, Any] | None:
        return await self.jobs.find_one(
            {"_id": job_id, "tenant_id": str(tenant_id)}, {"payload": 0}
        )

    async def get_platform_job(self, job_id: str) -> dict[str, Any] | None:
        return await self.jobs.find_one({"_id": job_id}, {"payload": 0, "actor": 0})


def _create_client() -> Any:
    uri = CONFIG.database_url or "mongodb://localhost:27017"
    safety_window_ms = (CONFIG.worker_lease_seconds - CONFIG.worker_heartbeat_seconds) * 1000
    operation_timeout_ms = max(1_000, min(10_000, safety_window_ms // 2))
    return motor.motor_asyncio.AsyncIOMotorClient(
        uri,
        tz_aware=True,
        maxPoolSize=100,
        minPoolSize=0,
        connectTimeoutMS=operation_timeout_ms,
        serverSelectionTimeoutMS=operation_timeout_ms,
        socketTimeoutMS=operation_timeout_ms,
        waitQueueTimeoutMS=operation_timeout_ms,
        retryWrites=True,
        retryReads=True,
    )


dbclient = _create_client()
store = MongoStore(dbclient, CONFIG.database_name)
