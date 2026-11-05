"""Mongo-backed web authentication and authorization helpers."""

from __future__ import annotations

import functools
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiohttp import web

from config import CONFIG
from database.database import store
from security import verify_password, verify_plain_secret

logger = logging.getLogger(__name__)
Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
MAX_LOGIN_PASSWORD_LENGTH = 1024
MAX_TENANT_SLUG_LENGTH = 32


@dataclass(frozen=True)
class AuthPrincipal:
    role: str
    tenant_id: str | None
    actor_role: str
    actor_id: str
    impersonated: bool

    @classmethod
    def from_session(cls, session: dict[str, Any]) -> AuthPrincipal:
        return cls(
            role=session["role"],
            tenant_id=session.get("tenant_id"),
            actor_role=session.get("actor_role") or session["role"],
            actor_id=session.get("actor_id") or "platform",
            impersonated=bool(session.get("impersonated", False)),
        )


PRINCIPAL_KEY = web.RequestKey("principal", AuthPrincipal)


class LoginLimiter:
    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 300,
        max_keys: int = 10_000,
    ):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self.attempts: OrderedDict[str, list[float]] = OrderedDict()

    def limited(self, key: str) -> bool:
        now = time.monotonic()
        recent = [
            value
            for value in self.attempts.get(key, [])
            if now - value < self.window_seconds
        ]
        if not recent:
            self.attempts.pop(key, None)
            return False
        self.attempts[key] = recent
        self.attempts.move_to_end(key)
        return len(recent) >= self.max_attempts

    def failure(self, key: str) -> None:
        if key not in self.attempts and len(self.attempts) >= self.max_keys:
            self.attempts.popitem(last=False)
        self.attempts.setdefault(key, []).append(time.monotonic())
        self.attempts.move_to_end(key)

    def success(self, key: str) -> None:
        self.attempts.pop(key, None)


login_limiter = LoginLimiter()


def bearer_token(request: web.Request) -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return ""
    return token.strip()


async def authenticate(request: web.Request) -> AuthPrincipal | None:
    session = await store.get_session(bearer_token(request))
    if not session:
        return None
    if session.get("role") == "tenant" and not session.get("impersonated"):
        tenant = await store.get_tenant(session.get("tenant_id", ""))
        if not tenant or not tenant.get("enabled"):
            return None
    return AuthPrincipal.from_session(session)


def require_roles(*roles: str) -> Callable[[Handler], Handler]:
    def decorator(handler: Handler) -> Handler:
        @functools.wraps(handler)
        async def wrapper(request: web.Request) -> web.StreamResponse:
            principal = await authenticate(request)
            if not principal:
                return web.json_response({"error": "Unauthorized"}, status=401)
            if principal.role not in roles:
                return web.json_response({"error": "Forbidden"}, status=403)
            request[PRINCIPAL_KEY] = principal
            return await handler(request)

        return wrapper

    return decorator


require_platform = require_roles("platform")
require_tenant = require_roles("tenant")


async def audit_request(
    request: web.Request,
    action: str,
    *,
    tenant_id: str | None = None,
    target: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    principal = request[PRINCIPAL_KEY]
    await store.record_audit(
        action=action,
        actor_role=principal.actor_role,
        actor_id=principal.actor_id,
        tenant_id=tenant_id if tenant_id is not None else principal.tenant_id,
        target=target,
        metadata=metadata,
        remote_ip=request.remote or "",
    )


def setup_auth_routes(app: web.Application) -> None:
    app.router.add_post("/api/login", api_login)
    app.router.add_post("/api/logout", api_logout)
    app.router.add_get("/api/session", api_session)


async def api_login(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    scope = str(data.get("scope") or "platform").strip().lower()
    slug = str(data.get("tenant_slug") or "").strip().lower()
    password = str(data.get("password") or "")
    limiter_scope = scope if scope in {"platform", "tenant"} else "invalid"
    limiter_key = f"{request.remote}:{limiter_scope}:{slug[:MAX_TENANT_SLUG_LENGTH]}"
    if login_limiter.limited(limiter_key):
        return web.json_response({"error": "Too many login attempts"}, status=429)

    credentials_too_large = (
        len(password) > MAX_LOGIN_PASSWORD_LENGTH
        or len(slug) > MAX_TENANT_SLUG_LENGTH
    )
    if credentials_too_large:
        valid = False
        tenant_id = None
        actor_id = slug[:MAX_TENANT_SLUG_LENGTH]
    elif scope == "platform":
        valid = verify_plain_secret(password, CONFIG.platform_admin_password)
        tenant_id = None
        actor_id = "platform"
    elif scope == "tenant" and slug:
        tenant = await store.get_tenant_by_slug(slug)
        valid = bool(
            tenant
            and tenant.get("enabled")
            and verify_password(password, tenant.get("web_password_hash", ""))
        )
        tenant_id = str(tenant["_id"]) if valid and tenant else None
        actor_id = slug
    else:
        valid = False
        tenant_id = None
        actor_id = slug[:MAX_TENANT_SLUG_LENGTH]

    if not valid:
        login_limiter.failure(limiter_key)
        return web.json_response({"success": False, "message": "Invalid credentials"}, status=401)

    login_limiter.success(limiter_key)
    token = await store.create_session(
        role=scope,
        tenant_id=tenant_id,
        actor_role=scope,
        actor_id=actor_id,
        expires_in=CONFIG.session_expire_seconds,
    )
    return web.json_response(
        {"success": True, "token": token, "role": scope, "tenant_id": tenant_id}
    )


async def api_logout(request: web.Request) -> web.Response:
    await store.delete_session(bearer_token(request))
    return web.json_response({"success": True})


async def api_session(request: web.Request) -> web.Response:
    principal = await authenticate(request)
    if not principal:
        return web.json_response({"error": "Unauthorized"}, status=401)
    response = {
        "role": principal.role,
        "tenant_id": principal.tenant_id,
        "actor_role": principal.actor_role,
        "actor_id": principal.actor_id,
        "impersonated": principal.impersonated,
    }
    if principal.tenant_id:
        tenant = await store.get_tenant(principal.tenant_id)
        response["tenant"] = {
            "slug": tenant.get("slug", "") if tenant else "",
            "display_name": tenant.get("display_name", "") if tenant else "",
        }
    return web.json_response(response)
