"""Administrator control plane API for managing all tenants' settings, data, and admin configuration."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from aiohttp import web

from config import CONFIG, parse_bool
from database.database import store
from security import TenantCrypto, hash_password, mask_bot_token
from tenant import TenantContext
from web.auth import (
    MAX_LOGIN_PASSWORD_LENGTH,
    PRINCIPAL_KEY,
    AuthPrincipal,
    audit_request,
    require_platform,
    verify_telegram_bot_and_channel,
)
from web.tenant_api import EXTRA_SETTING_DEFAULTS, normalize_settings

logger = logging.getLogger(__name__)


def _crypto() -> TenantCrypto:
    return TenantCrypto.from_key(CONFIG.tenant_master_key)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (frozenset, set, tuple)):
        return list(value)
    return value


def _tenant_public(
    document: dict[str, Any],
    runtime: dict[str, Any] | None = None,
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    ctx = TenantContext.from_document(document)
    merged_settings = dict(EXTRA_SETTING_DEFAULTS)
    merged_settings.update(ctx.settings)
    merged_settings.update(document.get("settings") or {})

    rt_info: dict[str, Any] = {
        "worker_id": runtime.get("worker_id", "") if runtime else "",
        "status": runtime.get("status", "stopped") if runtime else "stopped",
        "last_error": runtime.get("last_error", "") if runtime else "",
        "bot_username": (runtime.get("bot_username") if runtime else "") or document.get("bot_username", ""),
    }
    c = counts or {}
    return {
        "id": ctx.tenant_id,
        "slug": ctx.slug,
        "display_name": ctx.display_name,
        "enabled": bool(document.get("enabled", False)),
        "bot_username": document.get("bot_username", ""),
        "bot_token_hint": document.get("bot_token_hint", ""),
        "has_bot_token": bool(document.get("bot_token_ciphertext")),
        "owner_id": ctx.owner_id,
        "admin_ids": sorted(ctx.admin_ids),
        "db_channel_id": ctx.db_channel_id,
        "settings": {k: _json_value(v) for k, v in merged_settings.items()},
        "config_version": ctx.config_version,
        "runtime": rt_info,
        "counts": {
            "users": c.get("users", 0),
            "shares": c.get("shares", 0),
            "banned": c.get("banned", 0),
            "cdkeys": c.get("cdkeys", 0),
        },
        "created_at": _json_value(document.get("created_at")),
        "updated_at": _json_value(document.get("updated_at")),
    }


@require_platform
async def api_admin_config_get(request: web.Request) -> web.Response:
    cfg = await store.get_admin_config()
    return web.json_response(
        {
            "admin_id": cfg.get("admin_id", "admin"),
            "admin_path": cfg.get("admin_path", "admin"),
            "allow_registration": bool(cfg.get("allow_registration", True)),
            "registration_invite_code": cfg.get("registration_invite_code", ""),
        }
    )


@require_platform
async def api_admin_config_update(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"error": "Invalid JSON"}, status=400)

    admin_id = str(data.get("admin_id") or "").strip() if "admin_id" in data else None
    new_password = str(data.get("password") or "") if "password" in data else None
    admin_path = str(data.get("admin_path") or "").strip().strip("/") if "admin_path" in data else None
    allow_reg = parse_bool(data.get("allow_registration"), True) if "allow_registration" in data else None
    invite_code = str(data.get("registration_invite_code") or "").strip() if "registration_invite_code" in data else None

    if admin_id is not None and (len(admin_id) < 1 or len(admin_id) > 64):
        return web.json_response({"error": "管理员登录 ID 长度需在 1 到 64 位之间"}, status=400)

    if new_password is not None and new_password != "":
        if len(new_password) < 1 or len(new_password) > MAX_LOGIN_PASSWORD_LENGTH:
            return web.json_response({"error": "管理员密码长度不合法"}, status=400)

    if admin_path is not None:
        cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", admin_path)
        if not cleaned or cleaned.lower() in {"api", "static", "health", "kaithhealthcheck", "kaithheathcheck"}:
            return web.json_response(
                {"error": "后台路径后缀仅支持字母、数字、下划线或连字符，且不能为系统保留路径"},
                status=400,
            )
        admin_path = cleaned

    updated = await store.update_admin_config(
        admin_id=admin_id,
        new_password=new_password if (new_password and new_password != "") else None,
        admin_path=admin_path,
        allow_registration=allow_reg,
        registration_invite_code=invite_code,
    )
    await audit_request(
        request,
        "admin.config_update",
        metadata={
            "admin_id": updated.get("admin_id"),
            "admin_path": updated.get("admin_path"),
            "allow_registration": updated.get("allow_registration"),
            "password_changed": bool(new_password),
        },
    )
    return web.json_response(
        {
            "success": True,
            "admin_id": updated.get("admin_id", "admin"),
            "admin_path": updated.get("admin_path", "admin"),
            "allow_registration": bool(updated.get("allow_registration", True)),
            "registration_invite_code": updated.get("registration_invite_code", ""),
        }
    )


@require_platform
async def api_tenants(request: web.Request) -> web.Response:
    enabled_param = request.query.get("enabled")
    enabled_filter = parse_bool(enabled_param) if enabled_param is not None else None
    docs = await store.list_tenants(enabled=enabled_filter)
    tids = [str(d["_id"]) for d in docs]

    counts_map = await store.aggregate_tenant_counts(tids)
    lease_cursor = store.leases.find({"tenant_id": {"$in": tids}})
    runtime_map: dict[str, dict[str, Any]] = {}
    async for ldoc in lease_cursor:
        runtime_map[str(ldoc.get("tenant_id"))] = ldoc

    items: list[dict[str, Any]] = []
    for doc in docs:
        tid = str(doc["_id"])
        items.append(
            _tenant_public(
                doc,
                runtime=runtime_map.get(tid),
                counts=counts_map.get(tid, {"users": 0, "shares": 0, "banned": 0, "cdkeys": 0}),
            )
        )
    return web.json_response({"tenants": items, "total": len(items)})


@require_platform
async def api_cleanup_empty_tenants(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        data = {}
    hours = float(data.get("older_than_hours") or 0)
    deleted_count = await store.cleanup_unconfigured_tenants(older_than_hours=hours)
    await audit_request(request, "platform.cleanup_tenants", metadata={"deleted": deleted_count})
    return web.json_response({"success": True, "deleted": deleted_count})


@require_platform
async def api_tenant_detail(request: web.Request) -> web.Response:
    tid = request.match_info["tenant_id"]
    doc = await store.get_tenant(tid)
    if not doc:
        return web.json_response({"error": "Tenant not found"}, status=404)
    runtime = await store.get_tenant_runtime(tid)
    counts_map = await store.aggregate_tenant_counts([tid])
    return web.json_response(
        _tenant_public(
            doc,
            runtime=runtime,
            counts=counts_map.get(tid),
        )
    )


@require_platform
async def api_tenant_update(request: web.Request) -> web.Response:
    tid = request.match_info["tenant_id"]
    doc = await store.get_tenant(tid)
    if not doc:
        return web.json_response({"error": "Tenant not found"}, status=404)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"error": "Invalid JSON"}, status=400)

    core_updates: dict[str, Any] = {}
    if "slug" in data and str(data["slug"]).strip():
        new_slug = str(data["slug"]).strip().lower()
        if new_slug != doc.get("slug"):
            existing = await store.get_tenant_by_slug(new_slug)
            if existing and str(existing["_id"]) != tid:
                return web.json_response({"error": "该租户账号标识已被占用"}, status=409)
            core_updates["slug"] = new_slug

    if "display_name" in data and str(data["display_name"]).strip():
        core_updates["display_name"] = str(data["display_name"]).strip()[:64]

    if "owner_id" in data:
        try:
            core_updates["owner_id"] = int(data.get("owner_id") or 0)
        except (ValueError, TypeError):
            return web.json_response({"error": "Owner Telegram ID 必须为整数"}, status=400)

    if "db_channel_id" in data:
        try:
            core_updates["db_channel_id"] = int(data.get("db_channel_id") or 0)
        except (ValueError, TypeError):
            return web.json_response({"error": "数据库存储频道 ID 必须为整数"}, status=400)

    if "admin_ids" in data:
        raw_admins = data.get("admin_ids")
        if isinstance(raw_admins, str):
            raw_admins = [x for x in raw_admins.replace(",", " ").split() if x.strip()]
        admins_list: list[int] = []
        eff_owner = int(core_updates.get("owner_id", doc.get("owner_id", 0)) or 0)
        if eff_owner != 0:
            admins_list.append(eff_owner)
        if isinstance(raw_admins, list):
            for x in raw_admins:
                try:
                    val = int(str(x).strip())
                    if val != 0 and val not in admins_list:
                        admins_list.append(val)
                except (ValueError, TypeError):
                    continue
        core_updates["admin_ids"] = admins_list

    raw_new_token = str(data.get("bot_token") or "").strip() if "bot_token" in data else ""
    if raw_new_token:
        crypto = _crypto()
        core_updates["bot_token_ciphertext"] = crypto.encrypt_bot_token(raw_new_token)
        core_updates["bot_token_hint"] = mask_bot_token(raw_new_token)

    if "bot_username" in data and str(data.get("bot_username") or "").strip():
        core_updates["bot_username"] = str(data.get("bot_username") or "").strip().lstrip("@")

    if "web_password" in data and str(data.get("web_password") or "").strip():
        core_updates["web_password_hash"] = hash_password(str(data["web_password"]))

    if "enabled" in data:
        core_updates["enabled"] = parse_bool(data.get("enabled"))

    nested_settings = data.get("settings") if isinstance(data.get("settings"), dict) else data
    try:
        setting_updates = normalize_settings(nested_settings)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    if core_updates:
        doc = await store.update_tenant_core(tid, core_updates) or doc
    if setting_updates:
        doc = await store.update_tenant_settings(tid, setting_updates) or doc

    await audit_request(
        request,
        "platform.tenant_update",
        tenant_id=tid,
        metadata={"core": sorted(core_updates.keys()), "settings": sorted(setting_updates.keys())},
    )
    runtime = await store.get_tenant_runtime(tid)
    return web.json_response(_tenant_public(doc, runtime=runtime))


@require_platform
async def api_tenant_delete(request: web.Request) -> web.Response:
    tid = request.match_info["tenant_id"]
    deleted = await store.delete_tenant(tid)
    if not deleted:
        return web.json_response({"error": "Tenant not found"}, status=404)
    await audit_request(request, "platform.tenant_delete", tenant_id=tid)
    return web.json_response({"ok": True})


@require_platform
async def api_tenant_enable(request: web.Request) -> web.Response:
    tid = request.match_info["tenant_id"]
    doc = await store.get_tenant(tid)
    if not doc:
        return web.json_response({"error": "Tenant not found"}, status=404)
    if not doc.get("bot_token_ciphertext") or not int(doc.get("db_channel_id") or 0) or not int(doc.get("owner_id") or 0):
        return web.json_response(
            {"error": "启用前需先为该租户配置 Bot Token、Owner ID 与存储频道 ID"},
            status=400,
        )
    try:
        plain_token = _crypto().decrypt_bot_token(str(doc["bot_token_ciphertext"]))
        verify_res = await verify_telegram_bot_and_channel(plain_token, int(doc.get("db_channel_id") or 0))
        if not verify_res.get("ok"):
            return web.json_response({"error": verify_res.get("error") or "Bot 校验未通过"}, status=400)
        updates: dict[str, Any] = {"enabled": True}
        if verify_res.get("bot_username"):
            updates["bot_username"] = str(verify_res["bot_username"])
        doc = await store.update_tenant_core(tid, updates) or doc
    except Exception:
        doc = await store.update_tenant_core(tid, {"enabled": True}) or doc

    runtime = await store.get_tenant_runtime(tid)
    await audit_request(request, "platform.tenant_enable", tenant_id=tid)
    return web.json_response({"ok": True, "tenant": _tenant_public(doc, runtime=runtime)})


@require_platform
async def api_tenant_disable(request: web.Request) -> web.Response:
    tid = request.match_info["tenant_id"]
    doc = await store.update_tenant_core(tid, {"enabled": False})
    if not doc:
        return web.json_response({"error": "Tenant not found"}, status=404)
    await store.cancel_tenant_jobs(tid, "Tenant disabled")
    runtime = await store.get_tenant_runtime(tid)
    await audit_request(request, "platform.tenant_disable", tenant_id=tid)
    return web.json_response({"ok": True, "tenant": _tenant_public(doc, runtime=runtime)})


@require_platform
async def api_tenant_restart(request: web.Request) -> web.Response:
    tid = request.match_info["tenant_id"]
    doc = await store.get_tenant(tid)
    if not doc:
        return web.json_response({"error": "Tenant not found"}, status=404)
    principal: AuthPrincipal = request[PRINCIPAL_KEY]
    job = await store.create_job(
        tenant_id=tid,
        job_type="bot_restart",
        payload={},
        actor={"role": principal.actor_role, "id": principal.actor_id},
    )
    await audit_request(request, "platform.tenant_restart", tenant_id=tid)
    return web.json_response({"job_id": job["_id"], "state": job["state"]})


@require_platform
async def api_platform_audit(request: web.Request) -> web.Response:
    try:
        limit = max(1, min(500, int(request.query.get("limit", "100"))))
    except ValueError:
        limit = 100
    tid = (request.query.get("tenant_id") or "").strip()
    query: dict[str, Any] = {}
    if tid:
        query["tenant_id"] = tid
    cursor = store.audit_logs.find(query).sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    logs: list[dict[str, Any]] = []
    for d in docs:
        logs.append(
            {
                "id": str(d.get("_id", "")),
                "action": d.get("action", ""),
                "actor_role": d.get("actor_role", ""),
                "actor_id": d.get("actor_id", ""),
                "tenant_id": d.get("tenant_id"),
                "target": d.get("target", ""),
                "metadata": d.get("metadata") or {},
                "remote_ip": d.get("remote_ip", ""),
                "created_at": _json_value(d.get("created_at")),
            }
        )
    return web.json_response({"logs": logs})


@require_platform
async def api_platform_job(request: web.Request) -> web.Response:
    job_id = request.match_info["job_id"]
    job = await store.get_job(job_id)
    if not job:
        return web.json_response({"error": "Job not found"}, status=404)
    return web.json_response({k: _json_value(v) for k, v in job.items() if k != "_id"} | {"id": str(job["_id"])})


def setup_platform_routes(app: web.Application) -> None:
    app.router.add_get("/api/admin/config", api_admin_config_get)
    app.router.add_put("/api/admin/config", api_admin_config_update)
    app.router.add_get("/api/platform/tenants", api_tenants)
    app.router.add_post("/api/platform/tenants/cleanup", api_cleanup_empty_tenants)
    app.router.add_get("/api/platform/tenants/{tenant_id}", api_tenant_detail)
    app.router.add_patch("/api/platform/tenants/{tenant_id}", api_tenant_update)
    app.router.add_put("/api/platform/tenants/{tenant_id}", api_tenant_update)
    app.router.add_delete("/api/platform/tenants/{tenant_id}", api_tenant_delete)
    app.router.add_post("/api/platform/tenants/{tenant_id}/enable", api_tenant_enable)
    app.router.add_post("/api/platform/tenants/{tenant_id}/disable", api_tenant_disable)
    app.router.add_post("/api/platform/tenants/{tenant_id}/restart", api_tenant_restart)
    app.router.add_get("/api/platform/audit", api_platform_audit)
    app.router.add_get("/api/platform/jobs/{job_id}", api_platform_job)
