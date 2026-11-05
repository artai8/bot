"""Authentication, tenant self-registration, and role-based access control."""

from __future__ import annotations

import functools
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import aiohttp
from aiohttp import web
from pymongo.errors import DuplicateKeyError

from config import CONFIG
from database.database import store
from security import TenantCrypto, verify_password, verify_plain_secret

logger = logging.getLogger(__name__)

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
MAX_LOGIN_PASSWORD_LENGTH = 1024
MAX_TENANT_SLUG_LENGTH = 32
SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,31}$")


@dataclass(frozen=True)
class AuthPrincipal:
    role: str
    tenant_id: str | None
    actor_role: str
    actor_id: str
    impersonated: bool

    @classmethod
    def from_session(cls, session: dict[str, Any]) -> AuthPrincipal:
        role = str(session.get("role") or "")
        tid = session.get("tenant_id")
        return cls(
            role=role,
            tenant_id=str(tid) if tid else None,
            actor_role=str(session.get("actor_role") or role),
            actor_id=str(session.get("actor_id") or tid or role),
            impersonated=bool(session.get("impersonated", False)),
        )


PRINCIPAL_KEY = web.AppKey("principal", AuthPrincipal) if hasattr(web, "AppKey") else "principal"


class LoginLimiter:
    def __init__(self, max_attempts: int = 8, window_seconds: int = 300, max_keys: int = 10_000):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._records: OrderedDict[str, list[float]] = OrderedDict()

    def _prune(self, key: str, now: float) -> list[float]:
        cutoff = now - self.window_seconds
        timestamps = [t for t in self._records.get(key, []) if t > cutoff]
        if timestamps:
            self._records[key] = timestamps
            self._records.move_to_end(key)
        else:
            self._records.pop(key, None)
        while len(self._records) > self.max_keys:
            self._records.popitem(last=False)
        return timestamps

    def limited(self, key: str) -> bool:
        now = time.monotonic()
        return len(self._prune(key, now)) >= self.max_attempts

    def failure(self, key: str) -> None:
        now = time.monotonic()
        timestamps = self._prune(key, now)
        timestamps.append(now)
        self._records[key] = timestamps
        self._records.move_to_end(key)

    def success(self, key: str) -> None:
        self._records.pop(key, None)


_limiter = LoginLimiter()


async def verify_telegram_bot_and_channel(bot_token: str, db_channel_id: int = 0) -> dict[str, Any]:
    token = (bot_token or "").strip()
    if not token or ":" not in token:
        return {"ok": False, "error": "Telegram Bot Token 格式无效（应类似 123456789:AAH...）"}
    base_url = f"https://api.telegram.org/bot{token}"
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{base_url}/getMe") as resp:
                data = await resp.json()
                if not data.get("ok") or not isinstance(data.get("result"), dict):
                    return {"ok": False, "error": "Telegram Bot Token 无效或已被撤销"}
                bot_info = data["result"]
                bot_id = int(bot_info.get("id", 0))
                bot_username = str(bot_info.get("username") or "").strip()

            if int(db_channel_id or 0) != 0:
                async with session.get(
                    f"{base_url}/getChatMember",
                    params={"chat_id": int(db_channel_id), "user_id": bot_id},
                ) as c_resp:
                    c_data = await c_resp.json()
                    if not c_data.get("ok") or not isinstance(c_data.get("result"), dict):
                        return {
                            "ok": False,
                            "bot_username": bot_username,
                            "error": f"机器人 (@{bot_username}) 无法访问存储频道 {db_channel_id}，请先将机器人拉入该频道并设为管理员",
                        }
                    status = str(c_data["result"].get("status") or "")
                    if status not in {"administrator", "creator"}:
                        return {
                            "ok": False,
                            "bot_username": bot_username,
                            "error": f"机器人 (@{bot_username}) 在存储频道 {db_channel_id} 中不是管理员（当前状态: {status}）",
                        }
            return {"ok": True, "bot_id": bot_id, "bot_username": bot_username}
    except aiohttp.ClientError:
        return {"ok": True, "bot_username": "", "network_skipped": True}
    except Exception as exc:
        logger.warning("Telegram verification warning: %s", exc)
        return {"ok": True, "bot_username": "", "network_skipped": True}


def bearer_token(request: web.Request) -> str:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


async def authenticate(request: web.Request) -> AuthPrincipal | None:
    token = bearer_token(request)
    if not token:
        return None
    session = await store.get_session(token)
    if not session:
        return None
    return AuthPrincipal.from_session(session)


def require_roles(*roles: str) -> Callable[[Handler], Handler]:
    allowed = set(roles)

    def decorator(handler: Handler) -> Handler:
        @functools.wraps(handler)
        async def wrapper(request: web.Request) -> web.StreamResponse:
            principal = await authenticate(request)
            if principal is None:
                return web.json_response({"error": "Unauthorized"}, status=401)
            if principal.role not in allowed:
                return web.json_response({"error": "Forbidden"}, status=403)
            request[PRINCIPAL_KEY] = principal
            return await handler(request)

        return wrapper

    return decorator


require_platform = require_roles("platform")
require_tenant = require_roles("tenant", "platform")


async def audit_request(
    request: web.Request,
    action: str,
    *,
    tenant_id: str | None = None,
    target: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    principal: AuthPrincipal | None = request.get(PRINCIPAL_KEY)
    actor_role = principal.actor_role if principal else "anonymous"
    actor_id = principal.actor_id if principal else ""
    eff_tid = tenant_id if tenant_id is not None else (principal.tenant_id if principal else None)
    remote_ip = request.headers.get("X-Forwarded-For", request.remote or "").split(",")[0].strip()
    try:
        await store.record_audit(
            action=action,
            actor_role=actor_role,
            actor_id=actor_id,
            tenant_id=eff_tid,
            target=target,
            metadata=metadata,
            remote_ip=remote_ip,
        )
    except Exception:
        logger.exception("Failed to write audit log for action %s", action)


async def api_login(request: web.Request) -> web.Response:
    remote_ip = request.headers.get("X-Forwarded-For", request.remote or "").split(",")[0].strip()
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"error": "Invalid JSON object"}, status=400)

    scope = str(data.get("scope") or "tenant").strip().lower()
    password = str(data.get("password") or "")
    if not password or len(password) > MAX_LOGIN_PASSWORD_LENGTH:
        return web.json_response({"error": "请输入有效密码"}, status=400)

    if scope == "platform":
        admin_id = str(data.get("admin_id") or data.get("username") or data.get("id") or "admin").strip()
        rate_key = f"platform:{remote_ip}:{admin_id}"
        if _limiter.limited(rate_key):
            return web.json_response({"error": "登录尝试过于频繁，请稍后再试"}, status=429)

        ok = await store.verify_admin_credentials(admin_id, password)
        if not ok:
            _limiter.failure(rate_key)
            return web.json_response({"error": "管理员ID或密码错误"}, status=401)

        _limiter.success(rate_key)
        token = await store.create_session(
            role="platform",
            expires_in=CONFIG.session_expire_seconds,
            actor_role="platform",
            actor_id=admin_id,
        )
        admin_cfg = await store.get_admin_config()
        await store.record_audit(
            action="platform.login",
            actor_role="platform",
            actor_id=admin_id,
            tenant_id=None,
            remote_ip=remote_ip,
        )
        return web.json_response(
            {
                "token": token,
                "role": "platform",
                "tenant_id": None,
                "admin_id": admin_cfg.get("admin_id", "admin"),
                "admin_path": admin_cfg.get("admin_path", "admin"),
                "expires_in": CONFIG.session_expire_seconds,
            }
        )

    slug = str(data.get("tenant_slug") or data.get("slug") or data.get("username") or "").strip().lower()
    if not slug or len(slug) > MAX_TENANT_SLUG_LENGTH:
        return web.json_response({"error": "请输入有效的租户账号标识"}, status=400)

    rate_key = f"tenant:{remote_ip}:{slug}"
    if _limiter.limited(rate_key):
        return web.json_response({"error": "登录尝试过于频繁，请稍后再试"}, status=429)

    tenant_doc = await store.get_tenant_by_slug(slug)
    if not tenant_doc or not verify_password(password, str(tenant_doc.get("web_password_hash") or "")):
        _limiter.failure(rate_key)
        return web.json_response({"error": "租户账号或密码错误"}, status=401)

    _limiter.success(rate_key)
    tid = str(tenant_doc["_id"])
    token = await store.create_session(
        role="tenant",
        tenant_id=tid,
        expires_in=CONFIG.session_expire_seconds,
        actor_role="tenant",
        actor_id=slug,
    )
    await store.record_audit(
        action="tenant.login",
        actor_role="tenant",
        actor_id=slug,
        tenant_id=tid,
        remote_ip=remote_ip,
    )
    return web.json_response(
        {
            "token": token,
            "role": "tenant",
            "tenant_id": tid,
            "slug": tenant_doc.get("slug"),
            "display_name": tenant_doc.get("display_name"),
            "expires_in": CONFIG.session_expire_seconds,
        }
    )


async def api_register(request: web.Request) -> web.Response:
    remote_ip = request.headers.get("X-Forwarded-For", request.remote or "").split(",")[0].strip()
    rate_key = f"register:{remote_ip}"
    if _limiter.limited(rate_key):
        return web.json_response({"error": "注册操作过于频繁，请稍后再试"}, status=429)

    admin_cfg = await store.get_admin_config()
    if not admin_cfg.get("allow_registration", True):
        return web.json_response({"error": "系统管理员当前已关闭新租户公开注册"}, status=403)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "请求数据格式错误"}, status=400)
    if not isinstance(data, dict):
        return web.json_response({"error": "请求数据格式错误"}, status=400)

    required_invite = str(admin_cfg.get("registration_invite_code") or "").strip()
    if required_invite:
        candidate_invite = str(data.get("invite_code") or "").strip()
        if not verify_plain_secret(candidate_invite, required_invite):
            _limiter.failure(rate_key)
            return web.json_response({"error": "注册邀请码错误或未填写"}, status=403)

    slug = str(data.get("slug") or data.get("username") or "").strip().lower()
    if not slug or not SLUG_RE.match(slug):
        return web.json_response(
            {"error": "账号标识需为 2-32 位字母、数字、下划线或连字符，且以字母或数字开头"},
            status=400,
        )

    password = str(data.get("password") or "")
    if len(password) < 4 or len(password) > MAX_LOGIN_PASSWORD_LENGTH:
        return web.json_response({"error": "登录密码长度需在 4 到 1024 位之间"}, status=400)

    display_name = str(data.get("display_name") or slug).strip()[:64] or slug
    bot_token = str(data.get("bot_token") or "").strip()

    try:
        owner_id = int(data.get("owner_id") or 0)
    except (ValueError, TypeError):
        return web.json_response({"error": "Owner Telegram ID 必须为整数"}, status=400)

    try:
        db_channel_id = int(data.get("db_channel_id") or 0)
    except (ValueError, TypeError):
        return web.json_response({"error": "存储频道 ID 必须为整数"}, status=400)

    raw_admins = data.get("admin_ids") or []
    if isinstance(raw_admins, str):
        raw_admins = [x for x in raw_admins.replace(",", " ").split() if x.strip()]
    admin_ids: list[int] = []
    if isinstance(raw_admins, list):
        for item in raw_admins:
            try:
                val = int(str(item).strip())
                if val != 0:
                    admin_ids.append(val)
            except (ValueError, TypeError):
                continue

    existing = await store.get_tenant_by_slug(slug)
    if existing is not None:
        return web.json_response({"error": "该租户账号标识已被注册，请更换一个"}, status=409)

    bot_username = ""
    if bot_token:
        verify_res = await verify_telegram_bot_and_channel(bot_token, db_channel_id)
        if not verify_res.get("ok"):
            return web.json_response({"error": verify_res.get("error") or "Bot Token 验证失败"}, status=400)
        bot_username = str(verify_res.get("bot_username") or "")

    crypto = TenantCrypto.from_key(CONFIG.tenant_master_key)
    can_enable = bool(bot_token and owner_id != 0 and db_channel_id != 0)
    try:
        tenant_doc = await store.create_tenant(
            slug=slug,
            display_name=display_name,
            bot_token=bot_token,
            owner_id=owner_id,
            admin_ids=admin_ids,
            db_channel_id=db_channel_id,
            web_password=password,
            crypto=crypto,
            enabled=can_enable,
            bot_username=bot_username,
        )
    except DuplicateKeyError:
        return web.json_response({"error": "该租户账号标识已被注册，请更换一个"}, status=409)

    tid = str(tenant_doc["_id"])
    token = await store.create_session(
        role="tenant",
        tenant_id=tid,
        expires_in=CONFIG.session_expire_seconds,
        actor_role="tenant",
        actor_id=slug,
    )
    await store.record_audit(
        action="tenant.register",
        actor_role="tenant",
        actor_id=slug,
        tenant_id=tid,
        remote_ip=remote_ip,
    )
    return web.json_response(
        {
            "success": True,
            "token": token,
            "role": "tenant",
            "tenant_id": tid,
            "slug": tenant_doc.get("slug"),
            "display_name": tenant_doc.get("display_name"),
            "expires_in": CONFIG.session_expire_seconds,
        },
        status=201,
    )


async def api_logout(request: web.Request) -> web.Response:
    token = bearer_token(request)
    if token:
        await store.delete_session(token)
    return web.json_response({"ok": True})


async def api_session(request: web.Request) -> web.Response:
    principal = await authenticate(request)
    if principal is None:
        return web.json_response({"authenticated": False})
    resp: dict[str, Any] = {
        "authenticated": True,
        "role": principal.role,
        "tenant_id": principal.tenant_id,
        "actor_role": principal.actor_role,
        "actor_id": principal.actor_id,
        "impersonated": principal.impersonated,
    }
    if principal.role == "platform":
        cfg = await store.get_admin_config()
        resp["admin_id"] = cfg.get("admin_id", "admin")
        resp["admin_path"] = cfg.get("admin_path", "admin")
    elif principal.tenant_id:
        t = await store.get_tenant(principal.tenant_id)
        if t:
            resp["slug"] = t.get("slug")
            resp["display_name"] = t.get("display_name")
    return web.json_response(resp)


async def api_route_info(request: web.Request) -> web.Response:
    path_arg = str(request.query.get("path") or "").strip().strip("/")
    cfg = await store.get_admin_config()
    admin_path = str(cfg.get("admin_path") or "admin").strip("/") or "admin"
    is_admin_entry = bool(path_arg and path_arg == admin_path)
    return web.json_response(
        {
            "is_admin_entry": is_admin_entry,
            "allow_registration": bool(cfg.get("allow_registration", True)),
            "require_invite_code": bool(str(cfg.get("registration_invite_code") or "").strip()),
        }
    )


def setup_auth_routes(app: web.Application) -> None:
    app.router.add_post("/api/login", api_login)
    app.router.add_post("/api/register", api_register)
    app.router.add_post("/api/logout", api_logout)
    app.router.add_get("/api/session", api_session)
    app.router.add_get("/api/route-info", api_route_info)
