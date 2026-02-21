import logging
from aiohttp import web
from .route import routes
from web.api import setup_api_routes

logger = logging.getLogger(__name__)


async def web_server():
    web_app = web.Application(client_max_size=30000000)
    web_app.add_routes(routes)
    logger.info("Base routes registered")

    try:
        setup_api_routes(web_app)
        logger.info("API routes registered")
    except Exception as e:
        logger.error(f"Failed to register API routes: {e}")
        import traceback
        logger.error(traceback.format_exc())

    return web_app
