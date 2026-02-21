import time
from aiohttp import web
from datetime import datetime

routes = web.RouteTableDef()
start_time = time.time()


@routes.get("/", allow_head=True)
async def root_route_handler(request):
    return web.json_response({
        "status": "alive",
        "bot": "dehua-share-bot",
        "uptime_seconds": int(time.time() - start_time)
    })


@routes.get("/health")
async def health_check(request):
    from database.database import ping_db
    db_ok = await ping_db()
    return web.json_response({
        "status": "healthy" if db_ok else "degraded",
        "database": "connected" if db_ok else "disconnected",
        "uptime_seconds": int(time.time() - start_time),
        "timestamp": datetime.utcnow().isoformat()
    })


# Leapcell 平台健康检查（两个拼写都要支持）
@routes.get("/kaithhealthcheck")
async def leapcell_health_1(request):
    return web.json_response({"status": "ok"})


@routes.get("/kaithheathcheck")
async def leapcell_health_2(request):
    return web.json_response({"status": "ok"})
