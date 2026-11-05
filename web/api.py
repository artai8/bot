"""Aiohttp web application factory, middlewares, health checks, and dynamic admin path routing."""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from database.database import store
from web.auth import setup_auth_routes
from web.platform_api import setup_platform_routes
from web.tenant_api import setup_tenant_routes

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@web.middleware
async def security_headers(request: web.Request, handler):
    response = await handler(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


@web.middleware
async def sanitized_errors(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("Unhandled error for %s %s", request.method, request.path)
        return web.json_response({"error": "Internal server error"}, status=500)


async def serve_index(_request: web.Request) -> web.StreamResponse:
    return web.FileResponse(STATIC_DIR / "index.html")


async def serve_dynamic_admin(request: web.Request) -> web.StreamResponse:
    candidate = request.match_info.get("admin_path", "").strip().strip("/")
    admin_path = await store.get_admin_path()
    if candidate and candidate == admin_path:
        return web.FileResponse(STATIC_DIR / "index.html")
    raise web.HTTPNotFound(text="404 Not Found")


async def health_live(_request: web.Request) -> web.Response:
    return web.json_response({"status": "alive"})


async def health_ready(_request: web.Request) -> web.Response:
    db_ok = await store.ping()
    status = "ready" if db_ok else "degraded"
    code = 200 if db_ok else 503
    return web.json_response(
        {"status": status, "database": "ready" if db_ok else "unavailable"},
        status=code,
    )


def setup_api_routes(app: web.Application) -> None:
    setup_auth_routes(app)
    setup_platform_routes(app)
    setup_tenant_routes(app)
    app.router.add_get("/", serve_index)
    app.router.add_get("/health/live", health_live)
    app.router.add_get("/health/ready", health_ready)
    app.router.add_get("/health", health_ready)
    app.router.add_get("/kaithhealthcheck", health_live)
    app.router.add_get("/kaithheathcheck", health_live)
    app.router.add_static("/static/", STATIC_DIR, name="static")
    app.router.add_get("/{admin_path}", serve_dynamic_admin)
    app.router.add_get("/{admin_path}/", serve_dynamic_admin)


def create_web_app() -> web.Application:
    app = web.Application(middlewares=[sanitized_errors, security_headers])
    setup_api_routes(app)
    return app
