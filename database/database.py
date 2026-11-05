"""MongoDB storage layer using Motor (asyncio)."""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from config import CONFIG, tenant_defaults
from security import (
    TenantCrypto,
    generate_session_token,
    hash_password,
    mask_bot_token,
    token_digest,
    verify_password,
    verify_plain_secret,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
TERMINAL_JOB_STATES = {"completed", "failed", "cancelled"}
EMPTY_STRING = ""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def timestamp() -> float:
    return time.time()


def _create_client() -> AsyncIOMotorClient:
    uri = CONFIG.database_url or "mongodb://localhost:27017"
    return AsyncIOMotorClient(uri)


class MongoStore:
    def __init__(self, client: Any, database_name: str):
        self.client = client
        self.database = client[database_name]
        self.db = self.database
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
            await self.database.command("ping")
            return True
        except Exception:
            return False

    async def ensure_schema(self) -> None:
        doc = await self.metadata.find_one({"_id": "schema"})
        if doc is not None:
            version = doc.get("version")
            if version != SCHEMA_VERSION:
                raise RuntimeError(f"Unsupported database schema {version}; expected {SCHEMA_VERSION}")
            return
        for coll in (self.users, self.shares, self.banned_users, self.stats):
            legacy = await coll.find_one({"tenant_id": {"$exists": False}})
            if legacy is not None:
                raise RuntimeError(
                    "Legacy single-tenant data detected; run scripts/reset_schema.py first."
                )
        try:
            await self.metadata.update_one(
                {"_id": "schema"},
                {"$setOnInsert": {"version": SCHEMA_VERSION, "created_at": utcnow()}},
                upsert=True,
            )
        except DuplicateKeyError:
            pass

    async def create_indexes(self) -> None:
        await self.ensure_schema()
        await self.tenants.create_index([("slug", 1)], unique=True)
        await self.users.create_index([("tenant_id", 1), ("user_id", 1)], unique=True)
        await self.users.create_index([("tenant_id", 1), ("joined_at", -1)])
        await self.shares.create_index([("tenant_id", 1), ("code", 1)], unique=True)
        await self.shares.create_index([("tenant_id", 1), ("owner_id", 1), ("created_at", -1)])
        await self.shares.create_index([("tenant_id", 1), ("message_ids", 1)])
        await self.shares.create_index([("tenant_id", 1), ("keywords", 1)])
        await self.banned_users.create_index([("tenant_id", 1), ("user_id", 1)], unique=True)
        await self.stats.create_index([("tenant_id", 1), ("key", 1)], unique=True)
        await self.sessions.create_index([("expires_at", 1)], expireAfterSeconds=0)
        await self.leases.create_index([("tenant_id", 1)], unique=True)
        await self.leases.create_index([("expires_at", 1)], expireAfterSeconds=0)
        await self.jobs.create_index([("state", 1), ("available_at", 1), ("tenant_id", 1)])
        await self.jobs.create_index([("purge_at", 1)], expireAfterSeconds=0)
        await self.jobs.create_index(
            [("tenant_id", 1), ("idempotency_key", 1)],
            unique=True,
            sparse=True,
        )
        await self.audit_logs.create_index([("tenant_id", 1), ("created_at", -1)])
        await self.database["cdkeys"].create_index([("tenant_id", 1), ("code", 1)], unique=True)
        await self.ensure_admin_config()

    async def ensure_admin_config(self) -> dict[str, Any]:
        doc = await self.metadata.find_one({"_id": "admin_config"})
        if doc is not None and isinstance(doc.get("value"), dict):
            val = doc["value"]
            changed = False
            if not val.get("admin_id"):
                val["admin_id"] = CONFIG.platform_admin_id or "admin"
                changed = True
            if not val.get("admin_path"):
                val["admin_path"] = (CONFIG.platform_admin_path or "admin").strip("/") or "admin"
                changed = True
            if not val.get("password_hash"):
                val["password_hash"] = hash_password(CONFIG.platform_admin_password or "admin")
                changed = True
            if changed:
                await self.set_platform_metadata("admin_config", val)
            return val

        initial_config = {
            "admin_id": CONFIG.platform_admin_id or "admin",
            "password_hash": hash_password(CONFIG.platform_admin_password or "admin"),
            "admin_path": (CONFIG.platform_admin_path or "admin").strip("/") or "admin",
        }
        await self.set_platform_metadata("admin_config", initial_config)
        return initial_config

    async def get_admin_config(self) -> dict[str, Any]:
        return await self.ensure_admin_config()

    async def get_admin_path(self) -> str:
        cfg = await self.get_admin_config()
        return str(cfg.get("admin_path") or "admin").strip("/") or "admin"

    async def verify_admin_credentials(self, admin_id: str, password: str) -> bool:
        cfg = await self.get_admin_config()
        expected_id = str(cfg.get("admin_id") or "admin")
        if admin_id.strip() != expected_id:
            return False
        pw_hash = str(cfg.get("password_hash") or "")
        if pw_hash and verify_password(password, pw_hash):
            return True
        return False

    async def update_admin_config(
        self,
        *,
        admin_id: str | None = None,
        new_password: str | None = None,
        admin_path: str | None = None,
    ) -> dict[str, Any]:
        cfg = dict(await self.get_admin_config())
        if admin_id is not None and admin_id.strip():
            cfg["admin_id"] = admin_id.strip()
        if new_password is not None and new_password != "":
            cfg["password_hash"] = hash_password(new_password)
        if admin_path is not None and admin_path.strip().strip("/"):
            cleaned_path = re.sub(r"[^a-zA-Z0-9_-]", "", admin_path.strip().strip("/"))
            if cleaned_path and cleaned_path.lower() not in {"api", "static", "health", "kaithhealthcheck", "kaithheathcheck"}:
                cfg["admin_path"] = cleaned_path
        await self.set_platform_metadata("admin_config", cfg)
        return cfg

    async def create_tenant(
        self,
        *,
        slug: str,
        display_name: str,
        bot_token: str = "",
        owner_id: int = 0,
        admin_ids: list[int] | None = None,
        db_channel_id: int = 0,
        web_password: str,
        crypto: TenantCrypto,
        enabled: bool = False,
        settings: dict[str, Any] | None = None,
        bot_username: str = "",
    ) -> dict[str, Any]:
        tenant_id = str(uuid.uuid4())
        merged_settings = tenant_defaults()
        if settings:
            merged_settings.update(settings)
        raw_admins = admin_ids or []
        admins_list: list[int] = []
        if int(owner_id or 0) != 0:
            admins_list.append(int(owner_id))
        for x in raw_admins:
            try:
                val = int(x)
                if val != 0 and val not in admins_list:
                    admins_list.append(val)
            except (ValueError, TypeError):
                continue

        cleaned_token = (bot_token or "").strip()
        token_cipher = crypto.encrypt_bot_token(cleaned_token) if cleaned_token else ""
        token_hint = mask_bot_token(cleaned_token) if cleaned_token else ""
        can_enable = bool(enabled and cleaned_token and int(db_channel_id or 0) != 0 and int(owner_id or 0) != 0)

        now = utcnow()
        doc: dict[str, Any] = {
            "_id": tenant_id,
            "slug": slug.strip().lower(),
            "display_name": (display_name or slug).strip(),
            "enabled": can_enable,
            "bot_token_ciphertext": token_cipher,
            "bot_token_hint": token_hint,
            "bot_username": (bot_username or "").strip().lstrip("@"),
            "owner_id": int(owner_id or 0),
            "admin_ids": admins_list,
            "db_channel_id": int(db_channel_id or 0),
            "web_password_hash": hash_password(web_password),
            "settings": merged_settings,
            "config_version": 1,
            "created_at": now,
            "updated_at": now,
        }
        await self.tenants.insert_one(doc)
        return doc

    async def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        return await self.tenants.find_one({"_id": str(tenant_id)})

    async def get_tenant_by_slug(self, slug: str) -> dict[str, Any] | None:
        if not slug:
            return None
        return await self.tenants.find_one({"slug": slug.strip().lower()})

    async def list_tenants(self, *, enabled: bool | None = None) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if enabled is not None:
            query["enabled"] = bool(enabled)
        cursor = self.tenants.find(query).sort("created_at", -1)
        return await cursor.to_list(length=5000)

    async def update_tenant_core(self, tenant_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        allowed_keys = {
            "slug",
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
        filtered = {k: v for k, v in updates.items() if k in allowed_keys}
        if not filtered:
            return await self.get_tenant(tenant_id)
        filtered["updated_at"] = utcnow()
        return await self.tenants.find_one_and_update(
            {"_id": str(tenant_id)},
            {"$set": filtered, "$inc": {"config_version": 1}},
            return_document=ReturnDocument.AFTER,
        )

    async def update_tenant_settings(self, tenant_id: str, settings: dict[str, Any]) -> dict[str, Any] | None:
        if not settings:
            return await self.get_tenant(tenant_id)
        set_Ops = {f"settings.{k}": v for k, v in settings.items()}
        set_Ops["updated_at"] = utcnow()
        return await self.tenants.find_one_and_update(
            {"_id": str(tenant_id)},
            {"$set": set_Ops, "$inc": {"config_version": 1}},
            return_document=ReturnDocument.AFTER,
        )

    async def reset_tenant_setting(self, tenant_id: str, key: str) -> bool:
        defaults = tenant_defaults()
        if key not in defaults:
            return False
        res = await self.tenants.update_one(
            {"_id": str(tenant_id)},
            {"$set": {f"settings.{key}": defaults[key], "updated_at": utcnow()}, "$inc": {"config_version": 1}},
        )
        return res.matched_count > 0

    async def delete_tenant(self, tenant_id: str) -> bool:
        tid = str(tenant_id)
        res = await self.tenants.delete_one({"_id": tid})
        await self.users.delete_many({"tenant_id": tid})
        await self.shares.delete_many({"tenant_id": tid})
        await self.banned_users.delete_many({"tenant_id": tid})
        await self.stats.delete_many({"tenant_id": tid})
        await self.sessions.delete_many({"tenant_id": tid})
        await self.leases.delete_many({"tenant_id": tid})
        await self.jobs.delete_many({"tenant_id": tid})
        await self.database["cdkeys"].delete_many({"tenant_id": tid})
        return res.deleted_count > 0

    async def present_user(self, tenant_id: str, user_id: int) -> bool:
        doc = await self.users.find_one({"tenant_id": str(tenant_id), "user_id": int(user_id)}, {"_id": 1})
        return doc is not None

    async def add_user(self, tenant_id: str, user_id: int) -> None:
        now = timestamp()
        await self.users.update_one(
            {"tenant_id": str(tenant_id), "user_id": int(user_id)},
            {
                "$setOnInsert": {
                    "tenant_id": str(tenant_id),
                    "user_id": int(user_id),
                    "joined_at": now,
                    "verify_status": {
                        "is_verified": False,
                        "verified_time": 0,
                        "verify_token": "",
                        "link": "",
                    },
                    "force_sub_status": {
                        "verified": False,
                        "verified_at": 0,
                        "channels": [],
                    },
                }
            },
            upsert=True,
        )

    async def get_verify_status(self, tenant_id: str, user_id: int) -> dict[str, Any]:
        doc = await self.users.find_one({"tenant_id": str(tenant_id), "user_id": int(user_id)})
        default = {
            "is_verified": False,
            "verified_time": 0,
            "verify_token": "",
            "link": "",
        }
        if not doc or not isinstance(doc.get("verify_status"), dict):
            return default
        merged = dict(default)
        merged.update(doc["verify_status"])
        return merged

    async def update_verify_status(self, tenant_id: str, user_id: int, status: dict[str, Any]) -> None:
        await self.add_user(tenant_id, user_id)
        await self.users.update_one(
            {"tenant_id": str(tenant_id), "user_id": int(user_id)},
            {"$set": {"verify_status": status}},
        )

    async def update_force_sub_status(self, tenant_id: str, user_id: int, status: dict[str, Any]) -> None:
        await self.add_user(tenant_id, user_id)
        await self.users.update_one(
            {"tenant_id": str(tenant_id), "user_id": int(user_id)},
            {"$set": {"force_sub_status": status}},
        )

    async def list_users(self, tenant_id: str, page: int = 1, per_page: int = 20) -> tuple[list[int], int]:
        tid = str(tenant_id)
        total = await self.users.count_documents({"tenant_id": tid})
        skip = max(0, (page - 1) * per_page)
        cursor = self.users.find({"tenant_id": tid}).sort("joined_at", -1).skip(skip).limit(per_page)
        docs = await cursor.to_list(length=per_page)
        return [int(d["user_id"]) for d in docs if "user_id" in d], total

    async def iter_user_ids(self, tenant_id: str) -> AsyncIterator[int]:
        cursor = self.users.find({"tenant_id": str(tenant_id)}, {"user_id": 1})
        async for doc in cursor:
            if "user_id" in doc:
                yield int(doc["user_id"])

    async def full_userbase(self, tenant_id: str) -> list[int]:
        return [uid async for uid in self.iter_user_ids(tenant_id)]

    async def delete_user(self, tenant_id: str, user_id: int) -> bool:
        res = await self.users.delete_one({"tenant_id": str(tenant_id), "user_id": int(user_id)})
        return res.deleted_count > 0

    async def get_user_count(self, tenant_id: str) -> int:
        return await self.users.count_documents({"tenant_id": str(tenant_id)})

    async def get_recent_users(self, tenant_id: str, days: int = 7) -> int:
        cutoff = timestamp() - (days * 86400)
        return await self.users.count_documents(
            {"tenant_id": str(tenant_id), "joined_at": {"$gte": cutoff}}
        )

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
        now = timestamp()
        doc: dict[str, Any] = {
            "tenant_id": str(tenant_id),
            "code": str(share_code),
            "owner_id": int(owner_id),
            "message_ids": [int(m) for m in message_ids],
            "title": str(title or ""),
            "group_text": str(group_text or ""),
            "keywords": [str(k).strip() for k in (keywords or []) if str(k).strip()],
            "protect_content": bool(protect_content),
            "access_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        await self.shares.update_one(
            {"tenant_id": str(tenant_id), "code": str(share_code)},
            {"$set": doc},
            upsert=True,
        )
        return doc

    async def get_share(self, tenant_id: str, share_code: str) -> dict[str, Any] | None:
        return await self.shares.find_one({"tenant_id": str(tenant_id), "code": str(share_code)})

    async def find_share_by_message_id(self, tenant_id: str, message_id: int) -> dict[str, Any] | None:
        return await self.shares.find_one({"tenant_id": str(tenant_id), "message_ids": int(message_id)})

    async def find_share_by_group_text(
        self, tenant_id: str, group_text: str, owner_id: int | None = None
    ) -> dict[str, Any] | None:
        if not group_text:
            return None
        query: dict[str, Any] = {"tenant_id": str(tenant_id), "group_text": group_text}
        if owner_id is not None:
            query["owner_id"] = int(owner_id)
        return await self.shares.find_one(query)

    async def find_share_by_keyword(self, tenant_id: str, keyword: str) -> dict[str, Any] | None:
        if not keyword:
            return None
        return await self.shares.find_one({"tenant_id": str(tenant_id), "keywords": keyword})

    async def find_shares_by_keyword(self, tenant_id: str, keyword: str, limit: int = 6) -> list[dict[str, Any]]:
        if not keyword:
            return []
        escaped = re.escape(keyword.strip())
        cursor = self.shares.find(
            {
                "tenant_id": str(tenant_id),
                "$or": [
                    {"keywords": {"$regex": escaped, "$options": "i"}},
                    {"title": {"$regex": escaped, "$options": "i"}},
                    {"group_text": {"$regex": escaped, "$options": "i"}},
                ],
            }
        ).sort("created_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def increment_share_access(self, tenant_id: str, share_code: str) -> None:
        await self.shares.update_one(
            {"tenant_id": str(tenant_id), "code": str(share_code)},
            {"$inc": {"access_count": 1}},
        )

    async def get_user_shares(
        self, tenant_id: str, owner_id: int, page: int = 1, per_page: int = 10
    ) -> tuple[list[dict[str, Any]], int]:
        query = {"tenant_id": str(tenant_id), "owner_id": int(owner_id)}
        total = await self.shares.count_documents(query)
        skip = max(0, (page - 1) * per_page)
        cursor = self.shares.find(query).sort("created_at", -1).skip(skip).limit(per_page)
        return await cursor.to_list(length=per_page), total

    async def list_shares(
        self, tenant_id: str, page: int = 1, per_page: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        query = {"tenant_id": str(tenant_id)}
        total = await self.shares.count_documents(query)
        skip = max(0, (page - 1) * per_page)
        cursor = self.shares.find(query).sort("created_at", -1).skip(skip).limit(per_page)
        return await cursor.to_list(length=per_page), total

    async def iter_shares(self, tenant_id: str) -> AsyncIterator[dict[str, Any]]:
        cursor = self.shares.find({"tenant_id": str(tenant_id)})
        async for doc in cursor:
            yield doc

    async def update_share(self, tenant_id: str, share_code: str, updates: dict[str, Any]) -> bool:
        updates = dict(updates)
        updates["updated_at"] = timestamp()
        res = await self.shares.update_one(
            {"tenant_id": str(tenant_id), "code": str(share_code)},
            {"$set": updates},
        )
        return res.matched_count > 0

    async def merge_share_messages(
        self,
        tenant_id: str,
        share_code: str,
        message_ids: list[int],
        keywords: list[str] | None = None,
    ) -> bool:
        update_doc: dict[str, Any] = {
            "$addToSet": {"message_ids": {"$each": [int(m) for m in message_ids]}},
            "$set": {"updated_at": timestamp()},
        }
        if keywords:
            update_doc["$addToSet"]["keywords"] = {"$each": [str(k).strip() for k in keywords if str(k).strip()]}
        res = await self.shares.update_one(
            {"tenant_id": str(tenant_id), "code": str(share_code)},
            update_doc,
        )
        return res.matched_count > 0

    async def delete_share(self, tenant_id: str, share_code: str) -> bool:
        res = await self.shares.delete_one({"tenant_id": str(tenant_id), "code": str(share_code)})
        return res.deleted_count > 0

    async def get_total_shares(self, tenant_id: str) -> int:
        return await self.shares.count_documents({"tenant_id": str(tenant_id)})

    async def search_shares(self, tenant_id: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
        escaped = re.escape(query.strip())
        q = {
            "tenant_id": str(tenant_id),
            "$or": [
                {"code": {"$regex": escaped, "$options": "i"}},
                {"title": {"$regex": escaped, "$options": "i"}},
                {"group_text": {"$regex": escaped, "$options": "i"}},
                {"keywords": {"$regex": escaped, "$options": "i"}},
            ],
        }
        cursor = self.shares.find(q).sort("created_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def search_shares_page(
        self, tenant_id: str, query: str, page: int, per_page: int
    ) -> tuple[list[dict[str, Any]], int]:
        escaped = re.escape(query.strip())
        q = {
            "tenant_id": str(tenant_id),
            "$or": [
                {"code": {"$regex": escaped, "$options": "i"}},
                {"title": {"$regex": escaped, "$options": "i"}},
                {"group_text": {"$regex": escaped, "$options": "i"}},
                {"keywords": {"$regex": escaped, "$options": "i"}},
            ],
        }
        total = await self.shares.count_documents(q)
        skip = max(0, (page - 1) * per_page)
        cursor = self.shares.find(q).sort("created_at", -1).skip(skip).limit(per_page)
        return await cursor.to_list(length=per_page), total

    async def get_user_share_count(self, tenant_id: str, owner_id: int) -> int:
        return await self.shares.count_documents({"tenant_id": str(tenant_id), "owner_id": int(owner_id)})

    async def ban_user(self, tenant_id: str, user_id: int, reason: str = "") -> None:
        await self.banned_users.update_one(
            {"tenant_id": str(tenant_id), "user_id": int(user_id)},
            {
                "$set": {
                    "tenant_id": str(tenant_id),
                    "user_id": int(user_id),
                    "reason": str(reason or ""),
                    "banned_at": timestamp(),
                }
            },
            upsert=True,
        )

    async def unban_user(self, tenant_id: str, user_id: int) -> bool:
        res = await self.banned_users.delete_one({"tenant_id": str(tenant_id), "user_id": int(user_id)})
        return res.deleted_count > 0

    async def is_banned(self, tenant_id: str, user_id: int) -> dict[str, Any] | None:
        return await self.banned_users.find_one({"tenant_id": str(tenant_id), "user_id": int(user_id)})

    async def get_banned_users(self, tenant_id: str) -> list[dict[str, Any]]:
        cursor = self.banned_users.find({"tenant_id": str(tenant_id)}).sort("banned_at", -1)
        return await cursor.to_list(length=2000)

    async def get_banned_count(self, tenant_id: str) -> int:
        return await self.banned_users.count_documents({"tenant_id": str(tenant_id)})

    async def increment_stat(self, tenant_id: str, key: str, value: int = 1) -> None:
        await self.stats.update_one(
            {"tenant_id": str(tenant_id), "key": str(key)},
            {"$inc": {"count": int(value)}},
            upsert=True,
        )

    async def get_stat(self, tenant_id: str, key: str) -> int:
        doc = await self.stats.find_one({"tenant_id": str(tenant_id), "key": str(key)})
        return int(doc["count"]) if doc and "count" in doc else 0

    async def get_all_stats(self, tenant_id: str) -> dict[str, int]:
        cursor = self.stats.find({"tenant_id": str(tenant_id)})
        result: dict[str, int] = {}
        async for doc in cursor:
            if "key" in doc:
                result[str(doc["key"])] = int(doc.get("count", 0))
        return result

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
        digest = token_digest(token)
        now = utcnow()
        doc = {
            "_id": digest,
            "role": str(role),
            "tenant_id": str(tenant_id) if tenant_id else None,
            "actor_role": str(actor_role or role),
            "actor_id": str(actor_id or tenant_id or role),
            "impersonated": bool(impersonated),
            "created_at": now,
            "expires_at": now + timedelta(seconds=max(60, int(expires_in))),
        }
        await self.sessions.insert_one(doc)
        return token

    async def get_session(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        digest = token_digest(token)
        doc = await self.sessions.find_one({"_id": digest})
        if not doc:
            return None
        expires_at = doc.get("expires_at")
        if isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= utcnow():
                await self.sessions.delete_one({"_id": digest})
                return None
        return doc

    async def delete_session(self, token: str) -> None:
        if not token:
            return
        await self.sessions.delete_one({"_id": token_digest(token)})

    async def delete_tenant_sessions(self, tenant_id: str) -> int:
        res = await self.sessions.delete_many({"tenant_id": str(tenant_id)})
        return res.deleted_count

    async def record_audit(
        self,
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
                "action": str(action),
                "actor_role": str(actor_role),
                "actor_id": str(actor_id),
                "tenant_id": str(tenant_id) if tenant_id else None,
                "target": str(target),
                "metadata": metadata or {},
                "remote_ip": str(remote_ip),
                "created_at": utcnow(),
            }
        )

    async def claim_lease(self, tenant_id: str, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        now = utcnow()
        expires = now + timedelta(seconds=max(10, int(lease_seconds)))
        tid = str(tenant_id)
        try:
            doc = await self.leases.find_one_and_update(
                {
                    "tenant_id": tid,
                    "$or": [
                        {"expires_at": {"$lte": now}},
                        {"worker_id": str(worker_id)},
                    ],
                },
                {
                    "$set": {
                        "worker_id": str(worker_id),
                        "expires_at": expires,
                        "heartbeat_at": now,
                        "status": "starting",
                        "last_error": "",
                    },
                    "$inc": {"generation": 1},
                    "$setOnInsert": {"tenant_id": tid},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            return doc
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
        expires = now + timedelta(seconds=max(10, int(lease_seconds)))
        update_fields: dict[str, Any] = {
            "expires_at": expires,
            "heartbeat_at": now,
            "status": str(status),
            "last_error": str(last_error or ""),
        }
        if bot_username:
            update_fields["bot_username"] = str(bot_username)
        res = await self.leases.update_one(
            {
                "tenant_id": str(tenant_id),
                "worker_id": str(worker_id),
                "generation": int(generation),
            },
            {"$set": update_fields},
        )
        return res.matched_count > 0

    async def release_lease(self, tenant_id: str, worker_id: str, generation: int) -> None:
        await self.leases.delete_one(
            {
                "tenant_id": str(tenant_id),
                "worker_id": str(worker_id),
                "generation": int(generation),
            }
        )

    async def get_tenant_runtime(self, tenant_id: str) -> dict[str, Any] | None:
        return await self.leases.find_one({"tenant_id": str(tenant_id)})

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
        tid = str(tenant_id)
        if idempotency_key:
            existing = await self.jobs.find_one({"tenant_id": tid, "idempotency_key": str(idempotency_key)})
            if existing:
                return existing
        now = utcnow()
        job_id = str(uuid.uuid4())
        doc: dict[str, Any] = {
            "_id": job_id,
            "tenant_id": tid,
            "type": str(job_type),
            "state": "queued",
            "payload": payload or {},
            "actor": actor or {},
            "progress": {"processed": 0, "total": 0, "succeeded": 0, "failed": 0},
            "attempts": 0,
            "max_attempts": int(max_attempts),
            "available_at": now,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
            "last_error": "",
            "purge_at": now + timedelta(days=7),
        }
        if idempotency_key:
            doc["idempotency_key"] = str(idempotency_key)
        await self.jobs.insert_one(doc)
        return doc

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
            "state": "queued",
            "available_at": {"$lte": now},
            "tenant_id": {"$in": [str(t) for t in tenant_ids]},
        }
        if job_types:
            query["type"] = {"$in": list(job_types)}
        return await self.jobs.find_one_and_update(
            query,
            {
                "$set": {
                    "state": "running",
                    "worker_id": str(worker_id),
                    "started_at": now,
                    "updated_at": now,
                    "lease_expires_at": now + timedelta(seconds=max(10, int(lease_seconds))),
                },
                "$inc": {"attempts": 1},
            },
            sort=[("available_at", 1)],
            return_document=ReturnDocument.AFTER,
        )

    async def update_job_progress(
        self, job_id: str, worker_id: str, progress: dict[str, int], lease_seconds: int
    ) -> bool:
        now = utcnow()
        res = await self.jobs.update_one(
            {"_id": str(job_id), "worker_id": str(worker_id), "state": "running"},
            {
                "$set": {
                    "progress": progress,
                    "updated_at": now,
                    "lease_expires_at": now + timedelta(seconds=max(10, int(lease_seconds))),
                }
            },
        )
        return res.matched_count > 0

    async def renew_job_lease(self, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        now = utcnow()
        res = await self.jobs.update_one(
            {"_id": str(job_id), "worker_id": str(worker_id), "state": "running"},
            {
                "$set": {
                    "updated_at": now,
                    "lease_expires_at": now + timedelta(seconds=max(10, int(lease_seconds))),
                }
            },
        )
        return res.matched_count > 0

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
        state = "completed" if success else "failed"
        await self.jobs.update_one(
            {"_id": str(job_id)},
            {
                "$set": {
                    "state": state,
                    "result": result or {},
                    "last_error": str(error or ""),
                    "completed_at": now,
                    "updated_at": now,
                }
            },
        )

    async def requeue_expired_jobs(self) -> int:
        now = utcnow()
        cursor = self.jobs.find({"state": "running", "lease_expires_at": {"$lte": now}})
        count = 0
        async for job in cursor:
            attempts = int(job.get("attempts", 1))
            max_attempts = int(job.get("max_attempts", 3))
            if attempts >= max_attempts:
                await self.jobs.update_one(
                    {"_id": job["_id"], "state": "running"},
                    {"$set": {"state": "failed", "last_error": "Job lease expired", "updated_at": now}},
                )
            else:
                await self.jobs.update_one(
                    {"_id": job["_id"], "state": "running"},
                    {"$set": {"state": "queued", "available_at": now, "updated_at": now}},
                )
            count += 1
        return count

    async def cancel_tenant_jobs(self, tenant_id: str, reason: str) -> int:
        now = utcnow()
        res = await self.jobs.update_many(
            {"tenant_id": str(tenant_id), "state": {"$in": ["queued", "running"]}},
            {"$set": {"state": "cancelled", "last_error": str(reason), "updated_at": now}},
        )
        return res.modified_count

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        return await self.jobs.find_one({"_id": str(job_id)})

    async def get_platform_metadata(self, key: str) -> Any:
        doc = await self.metadata.find_one({"_id": str(key)})
        return doc.get("value") if doc else None

    async def set_platform_metadata(self, key: str, value: Any) -> None:
        await self.metadata.update_one(
            {"_id": str(key)},
            {"$set": {"value": value, "updated_at": utcnow()}},
            upsert=True,
        )


dbclient = _create_client()
store = MongoStore(dbclient, CONFIG.database_name)
