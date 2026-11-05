"""Platform-administrator tenant lifecycle API."""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime
from typing import Any

import aiohttp
from aiohttp import web
from pymongo.errors import DuplicateKeyError

from config import CONFIG, parse_bool
from database.database import store
from security import TenantCrypto, hash_password
from web.auth import (
    MAX_LOGIN_PASSWORD_LENGTH,
    PRINCIPAL_KEY,
    audit_request,
    require_platform,
)

logger = logging.getLogger(__name__)
SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,30}[a-z0-9]$")


class ValidationFailure(Exception):
    pass


def _crypto() -> TenantCrypto:
    return TenantCrypto.from_key(CONFIG.tenant_master_key)


def _json_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _tenant_public(document: dict[str, Any], runtime: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "id": str(document["_id"]),
        "slug": document["slug"],
        "display_name": document.get("display_name", document["slug"]),
        "enabled": bool(document.get("enabled", False)),
        "bot_token_hint": document.get("bot_token_hint", ""),
        "bot_username": (runtime or {}).get("bot_username") or document.get("bot_username", ""),
        "owner_id": document.get("owner_id"),
        "admin_ids": document.get("admin_ids", []),
        "db_channel_id": document.get("db_channel_id"),
        "config_version": document.get("config_version", 1),
        "created_at": document.get("created_at", 0),
        "updated_at": document.get("updated_at", 0),
        "runtime": {
            "status": (runtime or {}).get("status", "stopped"),
            "worker_id": (runtime or {}).get("worker_id", ""),
            "generation": (runtime or {}).get("generation", 0),
            "heartbeat_at": _json_value((runtime or {}).get("heartbeat_at")),
            "expires_at": _json_value((runtime or {}).get("expires_at")),
            "last_error": (runtime or {}).get("last_error", ""),
        },
    }


async def validate_bot_configuration(token: str, channel_id: int) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=15)
    base = f"https://api.telegram.org/bot{token}"
    async with aiohttp.ClientSession(timeout=timeout) as session:

        async def call(method: str, **params: Any) -> dict[str, Any]:
            try:
                async with session.post(f"{base}/{method}", json=params) as response:
                    data = await response.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as exc:
                raise ValidationFailure("Telegram API is unavailable") from exc
            if not response.ok or not data.get("ok"):
                description = str(data.get("description") or "Telegram rejected the request")
                raise ValidationFailure(description[:200])
            return data["result"]

        bot = await call("getMe")
        chat = await call("getChat", chat_id=int(channel_id))
        membership = await call("getChatMember", chat_id=int(channel_id), user_id=int(bot["id"]))
        allowed = {"creator", "administrator"}
        if chat.get("type") != "channel":
            allowed.add("member")
        if membership.get("status") not in allowed:
            raise ValidationFailure("Bot does not have sufficient database-channel access")
        return {"id": bot["id"], "username": bot.get("username", "")}


def _required_text(data: dict[str, Any], key: str) -> str:
    value = str(data.get(key) or "").strip()
    if not value:
        raise ValidationFailure(f"{key} is required")
    return value


def _new_tenant_password() -> str:
    return secrets.token_urlsafe(18)


def setup_platform_routes(app: web.Application) -> None:
    app.router.add_get("/api/platform/tenants", api_tenants)
    app.router.add_post("/api/platform/tenants", api_tenant_create)
    app.router.add_get("/api/platform/tenants/{tenant_id}", api_tenant_detail)
    app.router.add_patch("/api/platform/tenants/{tenant_id}", api_tenant_update)
    app.router.add_post("/api/platform/tenants/{tenant_id}/enable", api_tenant_enable)
    app.router.add_post("/api/platform/tenants/{tenant_id}/disable", api_tenant_disable)
    app.router.add_post("/api/platform/tenants/{tenant_id}/reset-password", api_tenant_password)
    app.router.add_post("/api/platform/tenants/{tenant_id}/restart", api_tenant_restart)
    app.router.add_post("/api/platform/tenants/{tenant_id}/impersonate", api_tenant_impersonate)
    app.router.add_get("/api/platform/audit", api_platform_audit)
    app.router.add_get("/api/platform/jobs/{job_id}", api_platform_job)


@require_platform
async def api_tenants(request: web.Request) -> web.Response:
    documents = await store.list_tenants()
    result = []
    for document in documents:
        runtime = await store.get_tenant_runtime(str(document["_id"]))
        result.append(_tenant_public(document, runtime))
    return web.json_response({"tenants": result, "total": len(result)})


@require_platform
async def api_tenant_detail(request: web.Request) -> web.Response:
    document = await store.get_tenant(request.match_info["tenant_id"])
    if not document:
        return web.json_response({"error": "Tenant not found"}, status=404)
    runtime = await store.get_tenant_runtime(str(document["_id"]))
    return web.json_response(_tenant_public(document, runtime))


@require_platform
async def api_tenant_create(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        slug = _required_text(data, "slug").lower()
        if not SLUG_PATTERN.fullmatch(slug):
            raise ValidationFailure("slug must contain 3-32 lowercase letters, digits or hyphens")
        display_name = _required_text(data, "display_name")
        token = _required_text(data, "bot_token")
        password_supplied = bool(str(data.get("web_password") or "").strip())
        password = str(data.get("web_password") or "").strip() or _new_tenant_password()
        if not 12 <= len(password) <= MAX_LOGIN_PASSWORD_LENGTH:
            raise ValidationFailure("web_password must contain 12-1024 characters")
        owner_id = int(data["owner_id"])
        db_channel_id = int(data["db_channel_id"])
        admin_ids = [int(value) for value in data.get("admin_ids", [])]
        identity = await validate_bot_configuration(token, db_channel_id)
        document = await store.create_tenant(
            slug=slug,
            display_name=display_name,
            bot_token=token,
            owner_id=owner_id,
            admin_ids=admin_ids,
            db_channel_id=db_channel_id,
            web_password=password,
            crypto=_crypto(),
            enabled=parse_bool(data.get("enabled", False)),
            bot_username=identity["username"],
        )
    except ValidationFailure as exc:
        return web.json_response({"error": str(exc)}, status=422)
    except (KeyError, TypeError, ValueError):
        return web.json_response({"error": "Invalid tenant fields"}, status=400)
    except DuplicateKeyError:
        return web.json_response({"error": "Tenant slug already exists"}, status=409)
    except Exception:
        logger.exception("Tenant creation failed")
        return web.json_response({"error": "Tenant creation failed"}, status=500)

    await audit_request(request, "tenant.create", tenant_id=str(document["_id"]), target=slug)
    response = _tenant_public(document, None)
    if not password_supplied:
        response["temporary_password"] = password
    return web.json_response(response, status=201)


@require_platform
async def api_tenant_update(request: web.Request) -> web.Response:
    tenant_id = request.match_info["tenant_id"]
    current = await store.get_tenant(tenant_id)
    if not current:
        return web.json_response({"error": "Tenant not found"}, status=404)
    try:
        data = await request.json()
        updates: dict[str, Any] = {}
        if "display_name" in data:
            updates["display_name"] = _required_text(data, "display_name")
        if "owner_id" in data:
            updates["owner_id"] = int(data["owner_id"])
        if "admin_ids" in data:
            updates["admin_ids"] = list(
                dict.fromkeys(
                    [
                        int(data.get("owner_id", current["owner_id"])),
                        *(int(x) for x in data["admin_ids"]),
                    ]
                )
            )
        elif "owner_id" in updates:
            updates["admin_ids"] = list(
                dict.fromkeys([updates["owner_id"], *current.get("admin_ids", [])])
            )
        if "db_channel_id" in data:
            updates["db_channel_id"] = int(data["db_channel_id"])

        token = str(data.get("bot_token") or "").strip()
        channel_changed = "db_channel_id" in updates
        if token or channel_changed:
            effective_token = token or _crypto().decrypt_bot_token(current["bot_token_ciphertext"])
            effective_channel = int(updates.get("db_channel_id", current["db_channel_id"]))
            identity = await validate_bot_configuration(effective_token, effective_channel)
            updates["bot_username"] = identity["username"]
            if token:
                updates["bot_token_ciphertext"] = _crypto().encrypt_bot_token(token)
                from security import mask_bot_token

                updates["bot_token_hint"] = mask_bot_token(token)
        document = await store.update_tenant_core(tenant_id, updates)
    except ValidationFailure as exc:
        return web.json_response({"error": str(exc)}, status=422)
    except (TypeError, ValueError):
        return web.json_response({"error": "Invalid tenant fields"}, status=400)
    except Exception:
        logger.exception("Tenant update failed")
        return web.json_response({"error": "Tenant update failed"}, status=500)

    await audit_request(
        request,
        "tenant.update",
        tenant_id=tenant_id,
        target=current["slug"],
        metadata={"fields": sorted(updates)},
    )
    runtime = await store.get_tenant_runtime(tenant_id)
    return web.json_response(_tenant_public(document, runtime))


async def _set_enabled(request: web.Request, enabled: bool) -> web.Response:
    tenant_id = request.match_info["tenant_id"]
    current = await store.get_tenant(tenant_id)
    if not current:
        return web.json_response({"error": "Tenant not found"}, status=404)
    if enabled:
        try:
            token = _crypto().decrypt_bot_token(current["bot_token_ciphertext"])
            await validate_bot_configuration(token, int(current["db_channel_id"]))
        except (ValidationFailure, ValueError) as exc:
            return web.json_response({"error": str(exc)}, status=422)
    document = await store.update_tenant_core(tenant_id, {"enabled": enabled})
    if not enabled:
        await store.delete_tenant_sessions(tenant_id)
        await store.cancel_tenant_jobs(tenant_id, "Tenant was disabled")
    action = "tenant.enable" if enabled else "tenant.disable"
    await audit_request(request, action, tenant_id=tenant_id, target=current["slug"])
    runtime = await store.get_tenant_runtime(tenant_id)
    return web.json_response(_tenant_public(document, runtime))


@require_platform
async def api_tenant_enable(request: web.Request) -> web.Response:
    return await _set_enabled(request, True)


@require_platform
async def api_tenant_disable(request: web.Request) -> web.Response:
    return await _set_enabled(request, False)


@require_platform
async def api_tenant_password(request: web.Request) -> web.Response:
    tenant_id = request.match_info["tenant_id"]
    tenant = await store.get_tenant(tenant_id)
    if not tenant:
        return web.json_response({"error": "Tenant not found"}, status=404)
    try:
        data = await request.json()
        password = _required_text(data, "web_password")
        if not 12 <= len(password) <= MAX_LOGIN_PASSWORD_LENGTH:
            raise ValidationFailure("web_password must contain 12-1024 characters")
    except ValidationFailure as exc:
        return web.json_response({"error": str(exc)}, status=422)
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    await store.update_tenant_core(tenant_id, {"web_password_hash": hash_password(password)})
    await store.delete_tenant_sessions(tenant_id)
    await audit_request(
        request, "tenant.password_reset", tenant_id=tenant_id, target=tenant["slug"]
    )
    return web.json_response({"success": True})


@require_platform
async def api_tenant_restart(request: web.Request) -> web.Response:
    tenant_id = request.match_info["tenant_id"]
    tenant = await store.get_tenant(tenant_id)
    if not tenant:
        return web.json_response({"error": "Tenant not found"}, status=404)
    if not tenant.get("enabled"):
        return web.json_response({"error": "Disabled tenant cannot be restarted"}, status=409)
    principal = request[PRINCIPAL_KEY]
    job = await store.create_job(
        tenant_id=tenant_id,
        job_type="bot_restart",
        payload={},
        actor={"role": principal.actor_role, "id": principal.actor_id},
        idempotency_key=request.headers.get("Idempotency-Key"),
    )
    await audit_request(
        request,
        "tenant.restart",
        tenant_id=tenant_id,
        target=tenant["slug"],
        metadata={"job_id": job["_id"]},
    )
    return web.json_response({"success": True, "job_id": job["_id"]}, status=202)


@require_platform
async def api_tenant_impersonate(request: web.Request) -> web.Response:
    tenant_id = request.match_info["tenant_id"]
    tenant = await store.get_tenant(tenant_id)
    if not tenant:
        return web.json_response({"error": "Tenant not found"}, status=404)
    principal = request[PRINCIPAL_KEY]
    token = await store.create_session(
        role="tenant",
        tenant_id=tenant_id,
        actor_role="platform",
        actor_id=principal.actor_id,
        impersonated=True,
        expires_in=min(CONFIG.session_expire_seconds, 3600),
    )
    await audit_request(request, "tenant.impersonate", tenant_id=tenant_id, target=tenant["slug"])
    return web.json_response({"success": True, "token": token, "tenant_id": tenant_id})


@require_platform
async def api_platform_audit(request: web.Request) -> web.Response:
    page = max(1, int(request.query.get("page", 1)))
    per_page = min(100, max(1, int(request.query.get("per_page", 50))))
    total = await store.audit_logs.count_documents({})
    cursor = (
        store.audit_logs.find({}, {"_id": 0})
        .sort("created_at", -1)
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    entries = [document async for document in cursor]
    return web.json_response({"entries": entries, "total": total, "page": page})


@require_platform
async def api_platform_job(request: web.Request) -> web.Response:
    job = await store.get_platform_job(request.match_info["job_id"])
    if not job:
        return web.json_response({"error": "Job not found"}, status=404)
    result = {key: _json_value(value) for key, value in job.items() if key != "_id"}
    result["id"] = str(job["_id"])
    return web.json_response(result)
