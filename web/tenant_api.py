"""Tenant-scoped business API."""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

from aiohttp import web

from config import parse_bool, tenant_defaults
from database.database import store, utcnow
from tenant import TenantContext, normalize_channel_list
from web.auth import PRINCIPAL_KEY, AuthPrincipal, audit_request, require_tenant

logger = logging.getLogger(__name__)


def _tenant_id(request: web.Request) -> str:
    principal = request[PRINCIPAL_KEY]
    if not principal.tenant_id:
        raise RuntimeError("Tenant session is missing tenant_id")
    return principal.tenant_id


def _parse_paging(request: web.Request, default: int = 20) -> tuple[int, int]:
    try:
        page = max(1, int(request.query.get("page", 1)))
        per_page = min(100, max(1, int(request.query.get("per_page", default))))
    except ValueError:
        raise web.HTTPBadRequest(
            text='{"error":"Invalid pagination"}', content_type="application/json"
        ) from None
    return page, per_page


def _parse_keywords(raw: Any) -> list[str]:
    values = raw.split(",") if isinstance(raw, str) else raw if isinstance(raw, list) else []
    result: list[str] = []
    for value in values:
        item = str(value).strip()[:32]
        if item and item not in result:
            result.append(item)
    return result


def _json_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


async def _tenant_context(tenant_id: str) -> tuple[dict[str, Any], TenantContext]:
    tenant = await store.get_tenant(tenant_id)
    if not tenant:
        raise web.HTTPNotFound(text='{"error":"Tenant not found"}', content_type="application/json")
    return tenant, TenantContext.from_document(tenant)


async def _bot_is_running(tenant_id: str) -> bool:
    runtime = await store.get_tenant_runtime(tenant_id)
    return bool(
        runtime
        and runtime.get("status") == "running"
        and runtime.get("expires_at")
        and runtime["expires_at"] > utcnow()
    )


def _share_json(share: dict[str, Any], username: str) -> dict[str, Any]:
    code = share["code"]
    return {
        "code": code,
        "owner_id": share.get("owner_id", 0),
        "title": share.get("title", "Untitled"),
        "message_ids": share.get("message_ids", []),
        "files_count": len(share.get("message_ids", [])),
        "access_count": share.get("access_count", 0),
        "protect_content": share.get("protect_content", False),
        "link": f"https://t.me/{username}?start={code}" if username else code,
        "group_text": share.get("group_text", ""),
        "keywords": share.get("keywords", []),
        "created_at": share.get("created_at", 0),
        "updated_at": share.get("updated_at", 0),
    }


def setup_tenant_routes(app: web.Application) -> None:
    app.router.add_get("/api/dashboard", api_dashboard)
    app.router.add_get("/api/users", api_users)
    app.router.add_post("/api/ban", api_ban_user)
    app.router.add_post("/api/unban", api_unban_user)
    app.router.add_get("/api/banned", api_banned_users)
    app.router.add_get("/api/shares", api_shares)
    app.router.add_get("/api/shares/{code}", api_share_detail)
    app.router.add_put("/api/shares/{code}", api_share_update)
    app.router.add_delete("/api/shares/{code}", api_share_delete)
    app.router.add_post("/api/shares/{code}/forward", api_share_forward)
    app.router.add_post("/api/broadcast", api_broadcast)
    app.router.add_get("/api/settings", api_settings)
    app.router.add_put("/api/settings", api_settings_update)
    app.router.add_post("/api/settings/reset", api_settings_reset)
    app.router.add_get("/api/jobs/{job_id}", api_job)
    app.router.add_get("/api/system/health", api_system_health)


@require_tenant
async def api_dashboard(request: web.Request) -> web.Response:
    tenant_id = _tenant_id(request)
    tenant, context = await _tenant_context(tenant_id)
    user_count = await store.get_user_count(tenant_id)
    total_shares = await store.get_total_shares(tenant_id)
    banned_count = await store.get_banned_count(tenant_id)
    all_stats = await store.get_all_stats(tenant_id)
    runtime = await store.get_tenant_runtime(tenant_id)
    return web.json_response(
        {
            "tenant": {
                "id": tenant_id,
                "slug": context.slug,
                "display_name": context.display_name,
            },
            "users": {
                "total": user_count,
                "today": await store.get_recent_users(tenant_id, 1),
                "week": await store.get_recent_users(tenant_id, 7),
                "banned": banned_count,
            },
            "shares": {
                "total": total_shares,
                "files_shared": all_stats.get("files_shared", 0),
                "links_generated": all_stats.get("links_generated", 0),
                "share_accessed": all_stats.get("share_accessed", 0),
            },
            "activity": {
                "tokens_verified": all_stats.get("tokens_verified", 0),
                "broadcasts": all_stats.get("broadcasts", 0),
            },
            "system": {
                "database": "connected" if await store.ping() else "disconnected",
                "bot_username": (runtime or {}).get("bot_username")
                or tenant.get("bot_username", ""),
                "status": (runtime or {}).get("status", "stopped"),
                "last_error": (runtime or {}).get("last_error", ""),
            },
        }
    )


@require_tenant
async def api_users(request: web.Request) -> web.Response:
    page, per_page = _parse_paging(request)
    users, total = await store.list_users(_tenant_id(request), page, per_page)
    return web.json_response(
        {
            "users": users,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": math.ceil(total / per_page),
        }
    )


@require_tenant
async def api_ban_user(request: web.Request) -> web.Response:
    tenant_id = _tenant_id(request)
    _, context = await _tenant_context(tenant_id)
    try:
        data = await request.json()
        user_id = int(data["user_id"])
        reason = str(data.get("reason") or "Banned from WebUI")[:500]
    except (KeyError, TypeError, ValueError):
        return web.json_response({"error": "Invalid user_id"}, status=400)
    if context.is_admin(user_id):
        return web.json_response({"error": "Cannot ban a tenant administrator"}, status=409)
    await store.ban_user(tenant_id, user_id, reason)
    await audit_request(request, "user.ban", target=str(user_id), metadata={"reason": reason})
    return web.json_response({"success": True})


@require_tenant
async def api_unban_user(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        user_id = int(data["user_id"])
    except (KeyError, TypeError, ValueError):
        return web.json_response({"error": "Invalid user_id"}, status=400)
    found = await store.unban_user(_tenant_id(request), user_id)
    if not found:
        return web.json_response({"error": "Ban not found"}, status=404)
    await audit_request(request, "user.unban", target=str(user_id))
    return web.json_response({"success": True})


@require_tenant
async def api_banned_users(request: web.Request) -> web.Response:
    users = await store.get_banned_users(_tenant_id(request))
    result = [
        {
            "user_id": document["user_id"],
            "reason": document.get("reason", ""),
            "banned_at": document.get("banned_at", 0),
        }
        for document in users
    ]
    return web.json_response({"banned_users": result, "total": len(result)})


@require_tenant
async def api_shares(request: web.Request) -> web.Response:
    tenant_id = _tenant_id(request)
    page, per_page = _parse_paging(request)
    tenant, _ = await _tenant_context(tenant_id)
    runtime = await store.get_tenant_runtime(tenant_id)
    username = (runtime or {}).get("bot_username") or tenant.get("bot_username", "")
    search = request.query.get("search", "").strip()
    if search:
        shares, total = await store.search_shares_page(
            tenant_id, search, page, per_page
        )
    else:
        shares, total = await store.list_shares(tenant_id, page, per_page)
    return web.json_response(
        {
            "shares": [_share_json(share, username) for share in shares],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": math.ceil(total / per_page),
        }
    )


@require_tenant
async def api_share_detail(request: web.Request) -> web.Response:
    tenant_id = _tenant_id(request)
    share = await store.get_share(tenant_id, request.match_info["code"])
    if not share:
        return web.json_response({"error": "Share not found"}, status=404)
    tenant, _ = await _tenant_context(tenant_id)
    runtime = await store.get_tenant_runtime(tenant_id)
    username = (runtime or {}).get("bot_username") or tenant.get("bot_username", "")
    return web.json_response(_share_json(share, username))


@require_tenant
async def api_share_update(request: web.Request) -> web.Response:
    tenant_id = _tenant_id(request)
    code = request.match_info["code"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    updates: dict[str, Any] = {}
    if "title" in data:
        updates["title"] = str(data["title"])[:500]
    if "protect_content" in data:
        try:
            updates["protect_content"] = parse_bool(data["protect_content"])
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
    if "keywords" in data:
        updates["keywords"] = _parse_keywords(data["keywords"])
    if "group_text" in data:
        updates["group_text"] = str(data.get("group_text") or "")[:4000]
    if not await store.update_share(tenant_id, code, updates):
        return web.json_response({"error": "Share not found"}, status=404)
    await audit_request(request, "share.update", target=code, metadata={"fields": sorted(updates)})
    return web.json_response({"success": True})


@require_tenant
async def api_share_delete(request: web.Request) -> web.Response:
    code = request.match_info["code"]
    if not await store.delete_share(_tenant_id(request), code):
        return web.json_response({"error": "Share not found"}, status=404)
    await audit_request(request, "share.delete", target=code)
    return web.json_response({"success": True})


def _actor(principal: AuthPrincipal) -> dict[str, str]:
    return {"role": principal.actor_role, "id": principal.actor_id}


@require_tenant
async def api_share_forward(request: web.Request) -> web.Response:
    tenant_id = _tenant_id(request)
    code = request.match_info["code"]
    share = await store.get_share(tenant_id, code)
    if not share:
        return web.json_response({"error": "Share not found"}, status=404)
    if not await _bot_is_running(tenant_id):
        return web.json_response({"error": "Tenant bot is not running"}, status=409)
    try:
        data = await request.json()
        forward_all = parse_bool(data.get("forward_all", False))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    updates: dict[str, Any] = {}
    if "keywords" in data:
        updates["keywords"] = _parse_keywords(data["keywords"])
    if "group_text" in data:
        updates["group_text"] = str(data.get("group_text") or "")[:4000]
    if updates:
        await store.update_share(tenant_id, code, updates)
    payload = {
        "code": code,
        "forward_all": forward_all,
        "forward_indices": data.get("forward_indices") or [],
        "forward_count": data.get("forward_count", 2),
    }
    principal = request[PRINCIPAL_KEY]
    job = await store.create_job(
        tenant_id=tenant_id,
        job_type="share_forward",
        payload=payload,
        actor=_actor(principal),
        idempotency_key=request.headers.get("Idempotency-Key"),
    )
    await audit_request(request, "share.forward", target=code, metadata={"job_id": job["_id"]})
    return web.json_response({"success": True, "job_id": job["_id"]}, status=202)


@require_tenant
async def api_broadcast(request: web.Request) -> web.Response:
    tenant_id = _tenant_id(request)
    if not await _bot_is_running(tenant_id):
        return web.json_response({"error": "Tenant bot is not running"}, status=409)
    try:
        data = await request.json()
        message = str(data.get("message") or "").strip()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not message:
        return web.json_response({"error": "Message is required"}, status=400)
    principal = request[PRINCIPAL_KEY]
    job = await store.create_job(
        tenant_id=tenant_id,
        job_type="broadcast",
        payload={"message": message[:4096]},
        actor=_actor(principal),
        idempotency_key=request.headers.get("Idempotency-Key"),
    )
    await audit_request(request, "broadcast.create", metadata={"job_id": job["_id"]})
    return web.json_response({"success": True, "job_id": job["_id"]}, status=202)


@require_tenant
async def api_settings(request: web.Request) -> web.Response:
    tenant, context = await _tenant_context(_tenant_id(request))
    result = dict(context.settings)
    result.update(
        {
            "owner_id": context.owner_id,
            "admin_ids": sorted(context.admin_ids),
            "db_channel_id": context.db_channel_id,
            "slug": context.slug,
            "display_name": context.display_name,
            "config_version": tenant.get("config_version", 1),
        }
    )
    return web.json_response(result)


BOOLEAN_SETTINGS = {
    "is_verify",
    "protect_content",
    "show_promo",
    "disable_channel_button",
}
INTEGER_RANGES = {
    "verify_expire": (60, 31_536_000),
    "auto_delete_time": (0, 31_536_000),
    "share_code_length": (6, 12),
    "rate_limit_max": (1, 1000),
    "rate_limit_window": (1, 3600),
}
TEXT_SETTINGS = {
    "shortlink_url",
    "shortlink_api",
    "tutorial_video",
    "promo_text",
    "custom_buttons",
    "custom_caption",
    "start_message",
    "force_sub_message",
    "auto_delete_message",
    "user_reply_text",
    "about_text",
    "help_text",
    "admin_help_text",
    "keyword_button_text",
}
CHANNEL_SETTINGS = {"bound_channels", "force_sub_channels"}
ALLOWED_SETTINGS = BOOLEAN_SETTINGS | set(INTEGER_RANGES) | TEXT_SETTINGS | CHANNEL_SETTINGS


def normalize_settings(data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in data.items():
        if key not in ALLOWED_SETTINGS:
            continue
        if key in BOOLEAN_SETTINGS:
            result[key] = parse_bool(value)
        elif key in INTEGER_RANGES:
            minimum, maximum = INTEGER_RANGES[key]
            number = int(value)
            if not minimum <= number <= maximum:
                raise ValueError(f"{key} must be between {minimum} and {maximum}")
            result[key] = number
        elif key in CHANNEL_SETTINGS:
            result[key] = normalize_channel_list(value)
        else:
            result[key] = str(value)[:10_000]
    return result


@require_tenant
async def api_settings_update(request: web.Request) -> web.Response:
    try:
        updates = normalize_settings(await request.json())
    except (TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    await store.update_tenant_settings(_tenant_id(request), updates)
    await audit_request(request, "settings.update", metadata={"fields": sorted(updates)})
    return web.json_response({"success": True, "updated": sorted(updates)})


@require_tenant
async def api_settings_reset(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        key = str(data.get("key") or "")
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if key not in ALLOWED_SETTINGS:
        return web.json_response({"error": "Invalid setting key"}, status=400)
    if not await store.reset_tenant_setting(_tenant_id(request), key):
        return web.json_response({"error": "Tenant not found"}, status=404)
    await audit_request(request, "settings.reset", target=key)
    return web.json_response({"success": True, "key": key, "value": tenant_defaults().get(key)})


@require_tenant
async def api_job(request: web.Request) -> web.Response:
    job = await store.get_job(_tenant_id(request), request.match_info["job_id"])
    if not job:
        return web.json_response({"error": "Job not found"}, status=404)
    result = {key: _json_value(value) for key, value in job.items() if key not in {"_id", "actor"}}
    result["id"] = str(job["_id"])
    return web.json_response(result)


@require_tenant
async def api_system_health(request: web.Request) -> web.Response:
    import psutil

    tenant_id = _tenant_id(request)
    runtime = await store.get_tenant_runtime(tenant_id)
    process = psutil.Process()
    return web.json_response(
        {
            "database": "connected" if await store.ping() else "disconnected",
            "bot": (runtime or {}).get("status", "stopped"),
            "worker_id": (runtime or {}).get("worker_id", ""),
            "heartbeat_at": _json_value((runtime or {}).get("heartbeat_at")),
            "last_error": (runtime or {}).get("last_error", ""),
            "web_memory_mb": round(process.memory_info().rss / 1024 / 1024, 2),
        }
    )
