"""Process-role entry point for the control plane and bot workers."""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from aiohttp import web

from config import CONFIG
from database.database import store
from web import create_web_app
from worker import WorkerManager

logger = logging.getLogger(__name__)


async def run_web(stop_event: asyncio.Event | None = None) -> None:
    CONFIG.validate(require_platform_auth=True)
    await store.create_indexes()
    runner = web.AppRunner(create_web_app())
    await runner.setup()
    site = web.TCPSite(runner, CONFIG.bind_host, CONFIG.port)
    await site.start()
    logger.info("Web control plane listening on port %s", CONFIG.port)
    stop = stop_event or asyncio.Event()
    if stop_event is None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await runner.cleanup()


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    manager = WorkerManager()
    watcher = None
    if stop_event is None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, manager.request_stop)
    else:

        async def watch_stop() -> None:
            await stop_event.wait()
            manager.request_stop()

        watcher = asyncio.create_task(watch_stop())
    try:
        await manager.run()
    finally:
        if watcher:
            watcher.cancel()


async def main() -> None:
    if CONFIG.process_role == "web":
        await run_web()
    elif CONFIG.process_role == "worker":
        await run_worker()
    else:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        await asyncio.gather(run_web(stop), run_worker(stop))


if __name__ == "__main__":
    asyncio.run(main())
