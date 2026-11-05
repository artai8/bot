"""Tenant and administrator unified data/settings management API."""

from __future__ import annotations

import logging
import math
import re
import secrets
import string
import time
import types as _types
from datetime import datetime
from typing import Any

from aiohttp import web

import config as _cfg
import tenant as _tenant_mod
from config import CONFIG, parse_bool, tenant_defaults
from database.database import store, timestamp, utcnow
from security import TenantCrypto, hash_password, mask_bot_token
from tenant import TenantContext, normalize_channel_list
from web.auth import MAX_LOGIN_PASSWORD_LENGTH, PRINCIPAL_KEY, AuthPrincipal, audit_request, require_tenant

logger = logging.getLogger(__name__)

_EXTRA_DEFAULTS: dict[str, Any] = {
    "force_sub_require_msg": False,
    "force_sub_msg_prompt": "\n\n💬 <b>附加解锁条件：</b>加入后，请在该频道/关联讨论群内<b>发送一条任意消息</b>，随后点击下方",
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

_base_dict = dict(getattr(_cfg, "DEFAULT_TENANT_SETTINGS", {}))
for _k, _v in _EXTRA_DEFAULTS.items():
    _base_dict.setdefault(_k, _v)
_cfg.DEFAULT_TENANT_SETTINGS = _types.MappingProxyType(_base_dict)
if hasattr(_tenant_mod, "DEFAULT_TENANT_SETTINGS"):
    _tenant_mod.DEFAULT_TENANT_SETTINGS = _cfg.DEFAULT_TENANT_SETTINGS

if hasattr(_cfg, "tenant_defaults"):
    _orig_td = _cfg.tenant_defaults

    def _patched_td() -> dict[str, Any]:
        d = dict(_orig_td())
        for k, v in _EXTRA_DEFAULTS.items():
            d.setdefault(k, v)
        return d

    _cfg.tenant_defaults = _patched_td
    if hasattr(_tenant_mod, "tenant_defaults"):
        _tenant_mod.tenant_defaults = _patched_td

EXTRA_SETTING_DEFAULTS = dict(_EXTRA_DEFAULTS)

BOOLEAN_SETTINGS = {
    "is_verify",
    "protect_content",
    "disable_channel_button",
    "show_promo",
    "force_sub_require_msg",
    "pay_to_unlock",
    "pay_enable_stars",
    "pay_enable_usdt",
    "pay_enable_cashier",
    "pay_enable_cdkey",
}

INTEGER_RANGES: dict[str, tuple[int, int]] = {
    "verify_expire": (60, 86400 * 365),
    "auto_delete_time": (0, 86400 * 30),
    "share_code_length": (4, 32),
    "rate_limit_max": (1, 10000),
    "rate_limit_window": (1, 86400),
    "pay_unlock_expire": (0, 86400 * 3650),
    "pay_amount_stars": (1, 1000000),
}

CHANNEL_SETTINGS = {
    "force_sub_channels",
    "bound_channels",
}

ALLOWED_SETTINGS = set(_base_dict.keys()) | set(_EXTRA_DEFAULTS.keys())


def _tenant_id(request: web.Request) -> str:
    principal: AuthPrincipal = request[PRINCIPAL_KEY]
    if principal.role == "platform":
        override = (
            request.query.get("tenant_id")
            or request.headers.get("X-Tenant-Id")
            or principal.tenant_id
            or ""
        ).strip()
        if not override:
            raise web.HTTPBadRequest(
                text='{"error": "请先选择一个要管理的租户"}',
                content_type="application/json",
            )
        return override
    if not principal.tenant_id:
        raise web.HTTPForbidden(
            text='{"error": "No tenant bound to session"}',
            content_type="application/json",
        )
    return principal.tenant_id


def _parse_paging(request: web.Request, default: int = 20) -> tuple[int, int]:
    try:
        page = max(1, int(request.query.get("page", "1")))
    except ValueError:
        page = 1
    try:
        per_page = max(1, min(200, int(request.query.get("per_page", str(default)))))
    except ValueError:
        per_page = default
    return page, per_page


def _share_json(share: dict[str, Any]) -> dict[str, Any]:
    msg_ids = [int(x) for x in (share.get("message_ids") or [])]
    return {
        "code": str(share.get("code") or ""),
        "owner_id": int(share.get("owner_id") or 0),
        "message_ids": msg_ids,
        "file_count": len(msg_ids),
        "title": str(share.get("title") or ""),
        "group_text": str(share.get("group_text") or ""),
        "keywords": list(share.get("keywords") or []),
        "protect_content": bool(share.get("protect_content", False)),
        "access_count": int(share.get("access_count") or 0),
        "created_at": share.get("created_at", 0),
        "updated_at": share.get("updated_at", 0),
    }


def normalize_settings(data: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for key, value in data.items():
        if key not in ALLOWED_SETTINGS:
            continue
        if key in BOOLEAN_SETTINGS:
            updates[key] = parse_bool(value)
        elif key in INTEGER_RANGES:
            low, high = INTEGER_RANGES[key]
            try:
                ival = int(value)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"设置项 {key} 必须为整数") from exc
            if ival < low or ival > high:
                raise ValueError(f"设置项 {key} 必须在 {low} 到 {high} 之间")
            updates[key] = ival
        elif key in CHANNEL_SETTINGS:
            updates[key] = normalize_channel_list(value)
        else:
            updates[key] = str(value if value is not None else "")[:10000]
    return updates


@require_tenant
async def api_dashboard(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    doc = await store.get_tenant(tid)
    if not doc:
        return web.json_response({"error": "Tenant not found"}, status=404)
    ctx = TenantContext.from_document(doc)

    total_users = await store.get_user_count(tid)
    users_today = await store.get_recent_users(tid, days=1)
    users_week = await store.get_recent_users(tid, days=7)
    users_month = await store.get_recent_users(tid, days=30)

    total_shares = await store.get_total_shares(tid)
    now = timestamp()
    shares_today = await store.shares.count_documents({"tenant_id": tid, "created_at": {"$gte": now - 86400}})
    shares_week = await store.shares.count_documents({"tenant_id": tid, "created_at": {"$gte": now - 7 * 86400}})
    shares_month = await store.shares.count_documents({"tenant_id": tid, "created_at": {"$gte": now - 30 * 86400}})

    banned_count = await store.get_banned_count(tid)
    stats = await store.get_all_stats(tid)
    cdkeys_count = await store.database["cdkeys"].count_documents({"tenant_id": tid})
    unlocked_count = await store.users.count_documents({"tenant_id": tid, "paid_status.is_unlocked": True})

    runtime_doc = await store.get_tenant_runtime(tid)
    runtime_info: dict[str, Any] | None = None
    if runtime_doc:
        runtime_info = {
            "worker_id": runtime_doc.get("worker_id", ""),
            "status": runtime_doc.get("status", "stopped"),
            "last_error": runtime_doc.get("last_error", ""),
            "bot_username": runtime_doc.get("bot_username") or doc.get("bot_username", ""),
        }

    return web.json_response(
        {
            "tenant": {
                "id": ctx.tenant_id,
                "slug": ctx.slug,
                "display_name": ctx.display_name,
                "enabled": bool(doc.get("enabled", False)),
                "bot_username": doc.get("bot_username", ""),
                "bot_token_hint": doc.get("bot_token_hint", ""),
                "owner_id": ctx.owner_id,
                "admin_ids": list(ctx.admin_ids),
                "db_channel_id": ctx.db_channel_id,
            },
            "users": {
                "total": total_users,
                "today": users_today,
                "week": users_week,
                "month": users_month,
                "unlocked": unlocked_count,
            },
            "shares": {
                "total": total_shares,
                "today": shares_today,
                "week": shares_week,
                "month": shares_month,
            },
            "banned": banned_count,
            "cdkeys_count": cdkeys_count,
            "stats": stats,
            "runtime": runtime_info,
        }
    )


@require_tenant
async def api_users(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    page, per_page = _parse_paging(request, default=20)
    search_q = (request.query.get("search") or "").strip()

    query: dict[str, Any] = {"tenant_id": tid}
    if search_q:
        try:
            uid_int = int(search_q)
            query["user_id"] = uid_int
        except ValueError:
            pass

    total = await store.users.count_documents(query)
    skip = max(0, (page - 1) * per_page)
    cursor = store.users.find(query).sort("joined_at", -1).skip(skip).limit(per_page)
    docs = await cursor.to_list(length=per_page)

    banned_docs = await store.get_banned_users(tid)
    banned_map = {int(b["user_id"]): b.get("reason", "") for b in banned_docs if "user_id" in b}

    users_out: list[dict[str, Any]] = []
    for d in docs:
        uid = int(d.get("user_id", 0))
        paid = d.get("paid_status") if isinstance(d.get("paid_status"), dict) else {}
        fsub = d.get("force_sub_status") if isinstance(d.get("force_sub_status"), dict) else {}
        vstat = d.get("verify_status") if isinstance(d.get("verify_status"), dict) else {}
        users_out.append(
            {
                "user_id": uid,
                "joined_at": d.get("joined_at", 0),
                "is_banned": uid in banned_map,
                "ban_reason": banned_map.get(uid, ""),
                "verify_status": vstat,
                "force_sub_status": fsub,
                "paid_status": {
                    "is_unlocked": bool(paid.get("is_unlocked", False)),
                    "unlocked_at": paid.get("unlocked_at", 0),
                    "method": paid.get("method", ""),
                    "amount": paid.get("amount", "0"),
                    "tx_ref": paid.get("tx_ref", ""),
                },
            }
        )

    total_pages = max(1, math.ceil(total / per_page))
    return web.json_response(
        {
            "users": users_out,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
        }
    )


@require_tenant
async def api_user_create(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    try:
        user_id = int(data.get("user_id"))
    except (ValueError, TypeError):
        return web.json_response({"error": "请输入有效的 Telegram 用户 ID"}, status=400)

    await store.add_user(tid, user_id)
    if data.get("unlocked"):
        await store.users.update_one(
            {"tenant_id": tid, "user_id": user_id},
            {
                "$set": {
                    "paid_status": {
                        "is_unlocked": True,
                        "unlocked_at": time.time(),
                        "method": "manual_add",
                        "amount": "0",
                        "tx_ref": "admin",
                    }
                }
            },
        )
    await audit_request(request, "user.create", tenant_id=tid, target=str(user_id))
    return web.json_response({"success": True, "user_id": user_id})


@require_tenant
async def api_user_delete(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    try:
        user_id = int(request.match_info["user_id"])
    except (ValueError, KeyError):
        return web.json_response({"error": "Invalid user_id"}, status=400)
    await store.delete_user(tid, user_id)
    await store.unban_user(tid, user_id)
    await audit_request(request, "user.delete", tenant_id=tid, target=str(user_id))
    return web.json_response({"success": True})


@require_tenant
async def api_ban_user(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    doc = await store.get_tenant(tid)
    if not doc:
        return web.json_response({"error": "Tenant not found"}, status=404)
    ctx = TenantContext.from_document(doc)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    try:
        user_id = int(data.get("user_id"))
    except (ValueError, TypeError):
        return web.json_response({"error": "请输入有效的用户 ID"}, status=400)

    if ctx.is_admin(user_id):
        return web.json_response({"error": "不能封禁租户管理员"}, status=409)

    reason = str(data.get("reason") or "")[:500]
    await store.ban_user(tid, user_id, reason)
    await audit_request(request, "user.ban", tenant_id=tid, target=str(user_id), metadata={"reason": reason})
    return web.json_response({"success": True})


@require_tenant
async def api_unban_user(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    try:
        user_id = int(data.get("user_id"))
    except (ValueError, TypeError):
        return web.json_response({"error": "请输入有效的用户 ID"}, status=400)

    removed = await store.unban_user(tid, user_id)
    if not removed:
        return web.json_response({"error": "该用户未被封禁"}, status=404)
    await audit_request(request, "user.unban", tenant_id=tid, target=str(user_id))
    return web.json_response({"success": True})


@require_tenant
async def api_banned_users(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    docs = await store.get_banned_users(tid)
    items = [
        {
            "user_id": int(d.get("user_id", 0)),
            "reason": str(d.get("reason") or ""),
            "banned_at": d.get("banned_at", 0),
        }
        for d in docs
    ]
    return web.json_response({"banned_users": items, "total": len(items)})


@require_tenant
async def api_shares(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    page, per_page = _parse_paging(request, default=20)
    search_q = (request.query.get("search") or "").strip()

    if search_q:
        docs, total = await store.search_shares_page(tid, search_q, page, per_page)
    else:
        docs, total = await store.list_shares(tid, page=page, per_page=per_page)

    total_pages = max(1, math.ceil(total / per_page))
    return web.json_response(
        {
            "shares": [_share_json(s) for s in docs],
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
        }
    )


@require_tenant
async def api_share_create(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    doc = await store.get_tenant(tid)
    if not doc:
        return web.json_response({"error": "Tenant not found"}, status=404)
    ctx = TenantContext.from_document(doc)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    raw_mids = data.get("message_ids")
    message_ids: list[int] = []
    if isinstance(raw_mids, str):
        for p in raw_mids.replace(",", " ").split():
            try:
                message_ids.append(int(p.strip()))
            except ValueError:
                continue
    elif isinstance(raw_mids, list):
        for p in raw_mids:
            try:
                message_ids.append(int(p))
            except (ValueError, TypeError):
                continue

    if not message_ids:
        return web.json_response({"error": "请提供至少一个有效的频道消息 ID (message_ids)"}, status=400)

    code_len = max(4, min(32, int(ctx.setting("share_code_length", 8) or 8)))
    alphabet = string.ascii_letters + string.digits
    code = str(data.get("code") or "").strip()
    if not code:
        code = "".join(secrets.choice(alphabet) for _ in range(code_len))

    title = str(data.get("title") or "").strip()[:300]
    group_text = str(data.get("group_text") or "").strip()[:2000]
    protect_content = parse_bool(data.get("protect_content"), False)
    raw_kw = data.get("keywords") or []
    if isinstance(raw_kw, str):
        keywords = [k.strip() for k in raw_kw.replace("，", ",").split(",") if k.strip()]
    elif isinstance(raw_kw, list):
        keywords = [str(k).strip() for k in raw_kw if str(k).strip()]
    else:
        keywords = []

    share = await store.create_share(
        tenant_id=tid,
        share_code=code,
        owner_id=int(data.get("owner_id") or ctx.owner_id or 0),
        message_ids=message_ids,
        title=title,
        protect_content=protect_content,
        group_text=group_text,
        keywords=keywords,
        code_length=code_len,
    )
    await audit_request(request, "share.create", tenant_id=tid, target=code)
    return web.json_response({"success": True, "share": _share_json(share)}, status=201)


@require_tenant
async def api_share_detail(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    code = request.match_info["code"]
    share = await store.get_share(tid, code)
    if not share:
        return web.json_response({"error": "Share not found"}, status=404)
    return web.json_response(_share_json(share))


@require_tenant
async def api_share_update(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    code = request.match_info["code"]
    share = await store.get_share(tid, code)
    if not share:
        return web.json_response({"error": "Share not found"}, status=404)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    updates: dict[str, Any] = {}
    if "title" in data:
        updates["title"] = str(data.get("title") or "")[:300]
    if "group_text" in data:
        updates["group_text"] = str(data.get("group_text") or "")[:2000]
    if "protect_content" in data:
        updates["protect_content"] = parse_bool(data.get("protect_content"))
    if "keywords" in data:
        raw_kw = data.get("keywords")
        if isinstance(raw_kw, str):
            updates["keywords"] = [k.strip() for k in raw_kw.replace("，", ",").split(",") if k.strip()]
        elif isinstance(raw_kw, list):
            updates["keywords"] = [str(k).strip() for k in raw_kw if str(k).strip()]
    if "message_ids" in data:
        raw_mids = data.get("message_ids")
        mids: list[int] = []
        if isinstance(raw_mids, str):
            for p in raw_mids.replace(",", " ").split():
                try:
                    mids.append(int(p.strip()))
                except ValueError:
                    continue
        elif isinstance(raw_mids, list):
            for p in raw_mids:
                try:
                    mids.append(int(p))
                except (ValueError, TypeError):
                    continue
        if mids:
            updates["message_ids"] = mids

    if updates:
        await store.update_share(tid, code, updates)
        await audit_request(request, "share.update", tenant_id=tid, target=code, metadata={"fields": sorted(updates)})
    return web.json_response({"success": True})


@require_tenant
async def api_share_delete(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    code = request.match_info["code"]
    deleted = await store.delete_share(tid, code)
    if not deleted:
        return web.json_response({"error": "Share not found"}, status=404)
    await audit_request(request, "share.delete", tenant_id=tid, target=code)
    return web.json_response({"success": True})


@require_tenant
async def api_share_forward(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    code = request.match_info["code"]
    share = await store.get_share(tid, code)
    if not share:
        return web.json_response({"error": "Share not found"}, status=404)

    runtime = await store.get_tenant_runtime(tid)
    if not runtime or runtime.get("status") != "running":
        return web.json_response({"error": "当前租户机器人未在运行中，请先启动机器人"}, status=409)

    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    updates: dict[str, Any] = {}
    if "group_text" in data:
        updates["group_text"] = str(data.get("group_text") or "")[:2000]
    if "keywords" in data:
        raw_kw = data.get("keywords")
        if isinstance(raw_kw, str):
            updates["keywords"] = [k.strip() for k in raw_kw.replace("，", ",").split(",") if k.strip()]
        elif isinstance(raw_kw, list):
            updates["keywords"] = [str(k).strip() for k in raw_kw if str(k).strip()]
    if updates:
        await store.update_share(tid, code, updates)

    payload = {
        "code": code,
        "forward_all": parse_bool(data.get("forward_all"), True),
        "forward_indices": data.get("forward_indices") or [],
        "forward_count": int(data.get("forward_count") or 0),
    }
    principal: AuthPrincipal = request[PRINCIPAL_KEY]
    idem_key = request.headers.get("Idempotency-Key")
    job = await store.create_job(
        tenant_id=tid,
        job_type="share_forward",
        payload=payload,
        actor={"role": principal.actor_role, "id": principal.actor_id},
        idempotency_key=idem_key,
    )
    await audit_request(request, "share.forward", tenant_id=tid, target=code)
    return web.json_response({"success": True, "job_id": job["_id"]}, status=202)


@require_tenant
async def api_broadcast(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    runtime = await store.get_tenant_runtime(tid)
    if not runtime or runtime.get("status") != "running":
        return web.json_response({"error": "当前租户机器人未在运行中，请先启动机器人"}, status=409)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    message = str(data.get("message") or "").strip()[:4096]
    if not message:
        return web.json_response({"error": "广播消息内容不能为空"}, status=400)

    principal: AuthPrincipal = request[PRINCIPAL_KEY]
    idem_key = request.headers.get("Idempotency-Key")
    job = await store.create_job(
        tenant_id=tid,
        job_type="broadcast",
        payload={"message": message},
        actor={"role": principal.actor_role, "id": principal.actor_id},
        idempotency_key=idem_key,
    )
    await audit_request(request, "tenant.broadcast", tenant_id=tid)
    return web.json_response({"success": True, "job_id": job["_id"]}, status=202)


@require_tenant
async def api_settings(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    doc = await store.get_tenant(tid)
    if not doc:
        return web.json_response({"error": "Tenant not found"}, status=404)
    ctx = TenantContext.from_document(doc)
    merged: dict[str, Any] = dict(EXTRA_SETTING_DEFAULTS)
    merged.update(ctx.settings)
    merged.update(doc.get("settings") or {})
    merged["force_sub_channels"] = normalize_channel_list(merged.get("force_sub_channels"))
    merged["bound_channels"] = normalize_channel_list(merged.get("bound_channels"))

    merged["id"] = ctx.tenant_id
    merged["slug"] = ctx.slug
    merged["display_name"] = ctx.display_name
    merged["enabled"] = bool(doc.get("enabled", False))
    merged["bot_username"] = doc.get("bot_username", "")
    merged["bot_token_hint"] = doc.get("bot_token_hint", "")
    merged["has_bot_token"] = bool(doc.get("bot_token_ciphertext"))
    merged["owner_id"] = ctx.owner_id
    merged["admin_ids"] = sorted(ctx.admin_ids)
    merged["db_channel_id"] = ctx.db_channel_id
    merged["config_version"] = ctx.config_version
    return web.json_response(merged)


@require_tenant
async def api_settings_update(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
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
    if "display_name" in data and str(data["display_name"]).strip():
        core_updates["display_name"] = str(data["display_name"]).strip()[:64]
    if "slug" in data and str(data["slug"]).strip():
        new_slug = str(data["slug"]).strip().lower()
        if new_slug != doc.get("slug"):
            if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,31}$", new_slug):
                return web.json_response({"error": "租户标识格式不合法"}, status=400)
            existing = await store.get_tenant_by_slug(new_slug)
            if existing and str(existing["_id"]) != tid:
                return web.json_response({"error": "该租户标识已被占用"}, status=409)
            core_updates["slug"] = new_slug

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

    if "bot_token" in data and str(data.get("bot_token") or "").strip():
        raw_token = str(data["bot_token"]).strip()
        crypto = TenantCrypto.from_key(CONFIG.tenant_master_key)
        core_updates["bot_token_ciphertext"] = crypto.encrypt_bot_token(raw_token)
        core_updates["bot_token_hint"] = mask_bot_token(raw_token)

    if "bot_username" in data:
        core_updates["bot_username"] = str(data.get("bot_username") or "").strip().lstrip("@")

    if "web_password" in data and str(data.get("web_password") or "").strip():
        new_pw = str(data["web_password"])
        if len(new_pw) < 4 or len(new_pw) > MAX_LOGIN_PASSWORD_LENGTH:
            return web.json_response({"error": "密码长度需在 4 到 1024 位之间"}, status=400)
        core_updates["web_password_hash"] = hash_password(new_pw)

    if "enabled" in data:
        want_enable = parse_bool(data.get("enabled"))
        has_tok = bool(core_updates.get("bot_token_ciphertext") or doc.get("bot_token_ciphertext"))
        eff_db = int(core_updates.get("db_channel_id", doc.get("db_channel_id", 0)) or 0)
        eff_own = int(core_updates.get("owner_id", doc.get("owner_id", 0)) or 0)
        if want_enable and (not has_tok or eff_db == 0 or eff_own == 0):
            return web.json_response(
                {"error": "启用机器人前，请先配置 Bot Token、Owner Telegram ID 和存储频道 ID"},
                status=400,
            )
        core_updates["enabled"] = want_enable

    try:
        setting_updates = normalize_settings(data)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)

    if core_updates:
        await store.update_tenant_core(tid, core_updates)
    if setting_updates:
        await store.update_tenant_settings(tid, setting_updates)

    updated_fields = sorted(set(core_updates.keys()) | set(setting_updates.keys()))
    await audit_request(request, "settings.update", tenant_id=tid, metadata={"fields": updated_fields})
    return web.json_response({"success": True, "updated": updated_fields})


@require_tenant
async def api_tenant_core_update(request: web.Request) -> web.Response:
    return await api_settings_update(request)


@require_tenant
async def api_tenant_bot_restart(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
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
    await audit_request(request, "tenant.bot_restart", tenant_id=tid)
    return web.json_response({"success": True, "job_id": job["_id"]})


@require_tenant
async def api_settings_reset(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    key = str(data.get("key") or "").strip()
    if key not in ALLOWED_SETTINGS:
        return web.json_response({"error": "不支持的设置项"}, status=400)

    defaults = tenant_defaults()
    default_val = defaults.get(key, EXTRA_SETTING_DEFAULTS.get(key))
    await store.update_tenant_settings(tid, {key: default_val})
    await audit_request(request, "settings.reset", tenant_id=tid, target=key)
    return web.json_response({"success": True, "key": key, "value": default_val})


@require_tenant
async def api_job(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    job_id = request.match_info["job_id"]
    job = await store.get_job(job_id)
    if not job or str(job.get("tenant_id")) != tid:
        return web.json_response({"error": "Job not found"}, status=404)
    out: dict[str, Any] = {"id": str(job["_id"])}
    for k, v in job.items():
        if k in {"_id", "actor"}:
            continue
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return web.json_response(out)


@require_tenant
async def api_system_health(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    db_ok = await store.ping()
    runtime = await store.get_tenant_runtime(tid)
    bot_ok = bool(runtime and runtime.get("status") == "running")
    status = "ok" if (db_ok and bot_ok) else "degraded"
    return web.json_response(
        {
            "status": status,
            "components": {
                "mongodb": "ok" if db_ok else "down",
                "bot_runner": runtime.get("status", "stopped") if runtime else "stopped",
            },
        }
    )


@require_tenant
async def api_list_cdkeys(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    status_filter = (request.query.get("status") or "all").strip().lower()
    query: dict[str, Any] = {"tenant_id": str(tid)}
    if status_filter == "unused":
        query["used_by"] = None
    elif status_filter == "used":
        query["used_by"] = {"$ne": None}
    cursor = store.database["cdkeys"].find(query, {"_id": 0}).sort("created_at", -1).limit(500)
    items = await cursor.to_list(length=500)
    return web.json_response({"items": items, "total": len(items)})


@require_tenant
async def api_create_cdkeys(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    try:
        data = await request.json()
    except Exception:
        data = {}
    try:
        count = max(1, min(200, int(data.get("count", 10))))
    except (ValueError, TypeError):
        count = 10

    note = str(data.get("note") or "").strip()[:100]
    alphabet = string.ascii_uppercase + string.digits
    now = time.time()
    docs: list[dict[str, Any]] = []
    codes: list[str] = []
    for _ in range(count):
        code = "CDK-" + "".join(secrets.choice(alphabet) for _ in range(10))
        codes.append(code)
        docs.append(
            {
                "tenant_id": str(tid),
                "code": code,
                "note": note,
                "created_at": now,
                "used_by": None,
                "used_at": None,
            }
        )
    if docs:
        await store.database["cdkeys"].insert_many(docs)
    await audit_request(request, "cdkeys.create", tenant_id=tid, metadata={"count": count})
    return web.json_response({"success": True, "codes": codes})


@require_tenant
async def api_delete_cdkey(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    code = request.match_info["code"].strip()
    res = await store.database["cdkeys"].delete_one({"tenant_id": str(tid), "code": code})
    if res.deleted_count == 0:
        return web.json_response({"error": "CDKey not found"}, status=404)
    await audit_request(request, "cdkeys.delete", tenant_id=tid, target=code)
    return web.json_response({"success": True})


@require_tenant
async def api_toggle_user_unlock(request: web.Request) -> web.Response:
    tid = _tenant_id(request)
    try:
        user_id = int(request.match_info["user_id"])
    except (ValueError, KeyError):
        return web.json_response({"error": "Invalid user_id"}, status=400)

    try:
        body = await request.json()
    except Exception:
        body = {}
    unlocked = parse_bool(body.get("unlocked"), True)
    method = str(body.get("method") or "admin_manual")

    await store.add_user(tid, user_id)
    if unlocked:
        await store.users.update_one(
            {"tenant_id": str(tid), "user_id": user_id},
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
            {"tenant_id": str(tid), "user_id": user_id},
            {"$set": {"paid_status.is_unlocked": False}},
        )
    await audit_request(
        request,
        "user.unlock_toggle",
        tenant_id=tid,
        target=str(user_id),
        metadata={"unlocked": unlocked},
    )
    return web.json_response({"success": True, "user_id": user_id, "unlocked": unlocked})


async def api_payment_webhook(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    tenant_id = str(body.get("tenant_id") or "").strip()
    try:
        user_id = int(body.get("user_id"))
    except (ValueError, TypeError):
        return web.json_response({"error": "Invalid user_id"}, status=400)
    if not tenant_id:
        return web.json_response({"error": "Missing tenant_id"}, status=400)

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
    app.router.add_post("/api/users", api_user_create)
    app.router.add_delete("/api/users/{user_id}", api_user_delete)
    app.router.add_post("/api/ban", api_ban_user)
    app.router.add_post("/api/unban", api_unban_user)
    app.router.add_get("/api/banned", api_banned_users)
    app.router.add_get("/api/shares", api_shares)
    app.router.add_post("/api/shares", api_share_create)
    app.router.add_get("/api/shares/{code}", api_share_detail)
    app.router.add_put("/api/shares/{code}", api_share_update)
    app.router.add_delete("/api/shares/{code}", api_share_delete)
    app.router.add_post("/api/shares/{code}/forward", api_share_forward)
    app.router.add_post("/api/broadcast", api_broadcast)
    app.router.add_get("/api/settings", api_settings)
    app.router.add_put("/api/settings", api_settings_update)
    app.router.add_put("/api/tenant/core", api_tenant_core_update)
    app.router.add_post("/api/tenant/restart", api_tenant_bot_restart)
    app.router.add_post("/api/settings/reset", api_settings_reset)
    app.router.add_get("/api/jobs/{job_id}", api_job)
    app.router.add_get("/api/system/health", api_system_health)
    app.router.add_get("/api/cdkeys", api_list_cdkeys)
    app.router.add_post("/api/cdkeys", api_create_cdkeys)
    app.router.add_delete("/api/cdkeys/{code}", api_delete_cdkey)
    app.router.add_post("/api/users/{user_id}/unlock", api_toggle_user_unlock)
    app.router.add_post("/api/pay/webhook", api_payment_webhook)
