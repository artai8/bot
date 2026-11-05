from __future__ import annotations

import logging
import math
import secrets
import string
import time
from datetime import datetime
from typing import Any

from aiohttp import web
from bson import ObjectId

from config import parse_bool, tenant_defaults
from database.database import store, utcnow
from tenant import TenantContext, normalize_channel_list
from web.auth import PRINCIPAL_KEY, AuthPrincipal, audit_request, require_tenant

logger = logging.getLogger(__name__)

# 自动将新增配置项注入 config 与 tenant 模块（无需手动修改 config.py / tenant.py）
import types as _types
import config as _cfg
import tenant as _tenant_mod

_EXTRA_DEFAULTS = {
    "force_sub_require_msg": False,
    "force_sub_msg_prompt": "\n\n💬 <b>附加解锁条件：</b>加入后，请在该频道/关联讨论群内<b>发送一条任意消息</b>，随后点击下方「🔄 重试」即可解锁。",
    "pay_to_unlock": False,
    "pay_unlock_expire": 0,
    "pay_enable_stars": False,
    "pay_amount_stars": 50,
    "pay_enable_usdt": False,
    "pay_amount_usdt": "5.00",
    "pay_usdt_network": "TRC20",
    "pay_usdt_address": "",
    "pay_enable_cashier": False,
    "pay_custom_url": "",
    "pay_enable_cdkey": True,
    "pay_unlock_message": "🔒 <b>当前资源需付费解锁后访问</b>\n\n请选择以下任意一种已启用的方式完成解锁：",
}

_merged_cfg = dict(getattr(_cfg, "DEFAULT_TENANT_SETTINGS", {}))
for _k, _v in _EXTRA_DEFAULTS.items():
    _merged_cfg.setdefault(_k, _v)
_cfg.DEFAULT_TENANT_SETTINGS = _types.MappingProxyType(_merged_cfg)
if hasattr(_tenant_mod, "DEFAULT_TENANT_SETTINGS"):
    _tenant_mod.DEFAULT_TENANT_SETTINGS = _cfg.DEFAULT_TENANT_SETTINGS

if hasattr(_cfg, "tenant_defaults"):
    _orig_td = _cfg.tenant_defaults
    def _patched_td():
        d = dict(_orig_td())
        for _k, _v in _EXTRA_DEFAULTS.items():
            d.setdefault(_k, _v)
        return d
    _cfg.tenant_defaults = _patched_td
    if hasattr(_tenant_mod, "tenant_defaults"):
        _tenant_mod.tenant_defaults = _patched_td


# ==============================================================================
# 1. 允许保存的租户设置字段白名单（已加入「发送消息解锁」与「多种可选付费方式」全部开关）
# ==============================================================================

BOOLEAN_SETTINGS = {
    "is_verify",
    "protect_content",
    "show_promo",
    "disable_channel_button",
    # 可选项 1：绑定强制订阅频道时，是否要求在频道内发送一条任意消息才能解锁
    "force_sub_require_msg",
    # 可选项 2：不绑定强制订阅频道时，是否启用付费解锁总开关及 4 种可选付费渠道开关
    "pay_to_unlock",
    "pay_enable_stars",
    "pay_enable_usdt",
    "pay_enable_cashier",
    "pay_enable_cdkey",
}

INTEGER_RANGES = {
    "verify_expire": (60, 31_536_000),
    "auto_delete_time": (0, 31_536_000),
    "share_code_length": (6, 12),
    "rate_limit_max": (1, 1000),
    "rate_limit_window": (1, 3600),
    "pay_unlock_expire": (0, 31_536_000),
    "pay_amount_stars": (0, 100_000),
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
    "force_sub_msg_prompt",
    "auto_delete_message",
    "user_reply_text",
    "about_text",
    "help_text",
    "admin_help_text",
    "keyword_button_text",
    "pay_amount_usdt",
    "pay_usdt_network",
    "pay_usdt_address",
    "pay_custom_url",
    "pay_unlock_message",
}

CHANNEL_SETTINGS = {"bound_channels", "force_sub_channels"}

ALLOWED_SETTINGS = BOOLEAN_SETTINGS | set(INTEGER_RANGES) | TEXT_SETTINGS | CHANNEL_SETTINGS

EXTRA_SETTING_DEFAULTS: dict[str, Any] = {
    "force_sub_require_msg": False,
    "force_sub_msg_prompt": "\n\n💬 <b>附加解锁条件：</b>加入后，请在该频道/关联讨论群内<b>发送一条任意消息</b>，随后点击下方「🔄 重试」即可解锁。",
    "pay_to_unlock": False,
    "pay_unlock_expire": 0,
    "pay_enable_stars": False,
    "pay_amount_stars": 50,
    "pay_enable_usdt": False,
    "pay_amount_usdt": "5.00",
    "pay_usdt_network": "TRC20",
    "pay_usdt_address": "",
    "pay_enable_cashier": False,
    "pay_custom_url": "",
    "pay_enable_cdkey": True,
    "pay_unlock_message": "🔒 <b>当前资源需付费解锁后访问</b>\n\n请选择以下任意一种已启用的方式完成解锁：",
}


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
    if isinstance(raw, str):
        items = [part.strip().lower() for part in raw.split(",")]
    elif isinstance(raw, list):
        items = [str(part).strip().lower() for part in raw]
    else:
        return []
    seen: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.append(item[:64])
    return seen[:20]


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, ObjectId):
        return str(value)
    return value


async def _tenant_context(tenant_id: str) -> tuple[dict[str, Any], TenantContext]:
    tenant = await store.get_tenant(tenant_id)
    if not tenant:
        raise web.HTTPNotFound(
            text='{"error":"Tenant not found"}', content_type="application/json"
        )
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


def _actor(principal: AuthPrincipal) -> dict[str, str]:
    return {
        "user_id": principal.user_id,
        "username": principal.username or "",
        "role": principal.role,
    }


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
    tenant_id = _tenant_id(request)
    page, per_page = _parse_paging(request)
    users, total = await store.list_users(tenant_id, page, per_page)
    # 补充每个用户的发言解锁与付费解锁状态字段（供前端列表展示）
    user_ids = [int(u["user_id"]) for u in users if "user_id" in u]
    extra_map: dict[int, dict[str, Any]] = {}
    if user_ids:
        cursor = store.users.find(
            {"tenant_id": str(tenant_id), "user_id": {"$in": user_ids}},
            {"_id": 0, "user_id": 1, "force_sub_status": 1, "paid_status": 1},
        )
        async for doc in cursor:
            extra_map[int(doc["user_id"])] = doc
    for u in users:
        uid = int(u.get("user_id", 0))
        if uid in extra_map:
            u["force_sub_status"] = extra_map[uid].get("force_sub_status") or {}
            u["paid_status"] = extra_map[uid].get("paid_status") or {}
    return web.json_response(
        {
            "users": users,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": math.ceil(total / per_page) if per_page else 1,
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
        shares, total = await store.search_shares_page(tenant_id, search, page, per_page)
    else:
        shares, total = await store.list_shares(tenant_id, page, per_page)
    return web.json_response(
        {
            "shares": [_share_json(share, username) for share in shares],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": math.ceil(total / per_page) if per_page else 1,
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
    result = dict(EXTRA_SETTING_DEFAULTS)
    result.update(dict(context.settings))
    raw_settings = (tenant or {}).get("settings") or {}
    result.update(raw_settings)
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
    default_val = EXTRA_SETTING_DEFAULTS.get(key, tenant_defaults().get(key))
    await store.update_tenant_settings(_tenant_id(request), {key: default_val})
    await audit_request(request, "settings.reset", target=key)
    return web.json_response({"success": True, "key": key, "value": default_val})


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


# ==============================================================================
# 新增：卡密管理、管理员手动解锁与第三方支付 Webhook 接口
# ==============================================================================

@require_tenant
async def api_list_cdkeys(request: web.Request) -> web.Response:
    tenant_id = _tenant_id(request)
    cursor = (
        store.database["cdkeys"]
        .find({"tenant_id": str(tenant_id)}, {"_id": 0})
        .sort("created_at", -1)
        .limit(200)
    )
    items = await cursor.to_list(length=200)
    return web.json_response({"items": items})


@require_tenant
async def api_create_cdkeys(request: web.Request) -> web.Response:
    tenant_id = _tenant_id(request)
    body = await request.json()
    count = max(1, min(200, int(body.get("count", 10) or 10)))
    alphabet = string.ascii_uppercase + string.digits
    codes: list[str] = []
    docs: list[dict[str, Any]] = []
    now = time.time()
    for _ in range(count):
        code = "CDK-" + "".join(secrets.choice(alphabet) for _ in range(10))
        codes.append(code)
        docs.append(
            {
                "tenant_id": str(tenant_id),
                "code": code,
                "created_at": now,
                "used_by": None,
                "used_at": None,
            }
        )
    await store.database["cdkeys"].insert_many(docs)
    await audit_request(request, "cdkeys.create", metadata={"count": len(codes)})
    return web.json_response({"success": True, "codes": codes})


@require_tenant
async def api_toggle_user_unlock(request: web.Request) -> web.Response:
    tenant_id = _tenant_id(request)
    user_id = int(request.match_info["user_id"])
    body = await request.json()
    unlocked = bool(body.get("unlocked", True))
    method = str(body.get("method", "admin_manual"))
    await store.add_user(tenant_id, user_id)
    if unlocked:
        await store.users.update_one(
            {"tenant_id": str(tenant_id), "user_id": user_id},
            {
                "$set": {
                    "paid_status": {
                        "is_unlocked": True,
                        "unlocked_at": time.time(),
                        "method": method,
                        "amount": "0",
                        "tx_ref": "admin",
                    }
                }
            },
        )
    else:
        await store.users.update_one(
            {"tenant_id": str(tenant_id), "user_id": user_id},
            {"$set": {"paid_status.is_unlocked": False}},
        )
    return web.json_response({"success": True, "user_id": user_id, "unlocked": unlocked})


async def api_payment_webhook(request: web.Request) -> web.Response:
    body = await request.json()
    tenant_id = str(body.get("tenant_id", "")).strip()
    user_id = int(body.get("user_id", 0) or 0)
    if not tenant_id or not user_id:
        return web.json_response({"error": "Missing tenant_id or user_id"}, status=400)
    await store.add_user(tenant_id, user_id)
    await store.users.update_one(
        {"tenant_id": tenant_id, "user_id": user_id},
        {
            "$set": {
                "paid_status": {
                    "is_unlocked": True,
                    "unlocked_at": time.time(),
                    "method": str(body.get("method", "webhook")),
                    "amount": str(body.get("amount", "0")),
                    "tx_ref": str(body.get("tx_ref", "")),
                }
            }
        },
    )
    return web.json_response({"success": True})


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
    app.router.add_get("/api/cdkeys", api_list_cdkeys)
    app.router.add_post("/api/cdkeys", api_create_cdkeys)
    app.router.add_post("/api/users/{user_id}/unlock", api_toggle_user_unlock)
    app.router.add_post("/api/pay/webhook", api_payment_webhook)
