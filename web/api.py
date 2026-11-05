"""aiohttp application factory and static control-plane routes."""

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
INDEX_FILE = STATIC_DIR / "index.html"


@web.middleware
async def security_headers(request: web.Request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = exc
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = (
        "no-store" if request.path.startswith("/api/") else "public, max-age=300"
    )
    return response


@web.middleware
async def sanitized_errors(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("Unhandled request error for %s %s", request.method, request.path)
        return web.json_response({"error": "Internal server error"}, status=500)


async def serve_index(_request: web.Request) -> web.StreamResponse:
    if not INDEX_FILE.is_file():
        return web.json_response({"error": "Admin UI is unavailable"}, status=503)
    return web.FileResponse(INDEX_FILE)


async def health_live(_request: web.Request) -> web.Response:
    return web.json_response({"status": "alive"})


async def health_ready(_request: web.Request) -> web.Response:
    ready = await store.ping()
    return web.json_response(
        {"status": "ready" if ready else "degraded", "database": ready},
        status=200 if ready else 503,
    )


def create_web_app() -> web.Application:
    app = web.Application(
        client_max_size=1_000_000,
        middlewares=[sanitized_errors, security_headers],
    )
    setup_auth_routes(app)
    setup_platform_routes(app)
    setup_tenant_routes(app)
    app.router.add_get("/", serve_index)
    app.router.add_get("/admin", serve_index)
    app.router.add_get("/admin/", serve_index)
    app.router.add_get("/health/live", health_live)
    app.router.add_get("/health/ready", health_ready)
    app.router.add_get("/health", health_ready)
    app.router.add_get("/kaithhealthcheck", health_live)
    app.router.add_get("/kaithheathcheck", health_live)
    app.router.add_static("/static/", STATIC_DIR, name="static")
    return app


def setup_api_routes(app: web.Application) -> None:
    """Compatibility helper for callers that construct their own app."""
    setup_auth_routes(app)
    setup_platform_routes(app)
    setup_tenant_routes(app)
