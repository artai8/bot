"""Process-role entry point for the control plane and bot workers."""

from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress

from aiohttp import web

from config import CONFIG, _configure_logging
from database.database import store
from web import create_web_app
from worker import WorkerManager

logger = logging.getLogger(__name__)


async def run_web(stop_event: asyncio.Event | None = None) -> None:
    CONFIG.validate(require_bot_api=False, require_platform_auth=False)
    await store.create_indexes()
    runner = web.AppRunner(create_web_app())
    await runner.setup()
    site = web.TCPSite(runner, CONFIG.bind_host, CONFIG.port)
    await site.start()
    logger.info("Web control plane listening on %s:%s", CONFIG.bind_host, CONFIG.port)
    stop = stop_event or asyncio.Event()
    if stop_event is None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await runner.cleanup()


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    CONFIG.validate(require_bot_api=True, require_platform_auth=False)
    await store.create_indexes()
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
        if watcher and not watcher.done():
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher


def main() -> None:
    _configure_logging()
    role = (CONFIG.process_role or "all").lower()
    if role == "web":
        asyncio.run(run_web())
    elif role == "worker":
        asyncio.run(run_worker())
    elif role in {"all", "both"}:

        async def run_all() -> None:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                with suppress(NotImplementedError):
                    loop.add_signal_handler(sig, stop.set)
            await asyncio.gather(
                run_web(stop_event=stop),
                run_worker(stop_event=stop),
            )

        asyncio.run(run_all())
    else:
        raise ValueError(f"Unknown PROCESS_ROLE: {CONFIG.process_role}")


if __name__ == "__main__":
    main()
