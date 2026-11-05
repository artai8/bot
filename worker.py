"""Distributed Mongo-leased Telegram Bot worker."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from pyrogram.errors import InputUserDeactivated, UserIsBlocked

from bot import Bot
from config import CONFIG
from database.database import store
from delivery import DeliveryCapacity, forward_share_to_channels, telegram_retry
from security import TenantCrypto

logger = logging.getLogger(__name__)


@dataclass
class ManagedTenant:
    document: dict[str, Any]
    generation: int
    bot: Bot | None = None
    lifecycle_task: asyncio.Task | None = None
    error: str = ""
    retry_at: float = 0

    @property
    def core_fingerprint(self) -> tuple[str, int]:
        return (
            self.document.get("bot_token_ciphertext", ""),
            int(self.document.get("db_channel_id", 0)),
        )


class WorkerManager:
    def __init__(self):
        self.worker_id = CONFIG.worker_id
        self.crypto = TenantCrypto.from_key(CONFIG.tenant_master_key)
        self.delivery_capacity = DeliveryCapacity(CONFIG.worker_max_deliveries)
        self.managed: dict[str, ManagedTenant] = {}
        self.job_tasks: dict[str, asyncio.Task] = {}
        self.stopping = asyncio.Event()
        self.last_database_success = time.monotonic()

    async def run(self) -> None:
        CONFIG.validate(require_bot_api=True)
        await store.create_indexes()
        logger.info(
            "Worker %s starting with capacity %s", self.worker_id, CONFIG.worker_bot_capacity
        )
        scheduler = asyncio.create_task(self._scheduler_loop())
        jobs = asyncio.create_task(self._job_loop())
        try:
            await self.stopping.wait()
        finally:
            scheduler.cancel()
            jobs.cancel()
            await asyncio.gather(scheduler, jobs, return_exceptions=True)
            await self.shutdown()

    def request_stop(self) -> None:
        self.stopping.set()

    async def _scheduler_loop(self) -> None:
        while not self.stopping.is_set():
            try:
                await self.sync_once()
                self.last_database_success = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker scheduler iteration failed")
                unsafe_after = CONFIG.worker_lease_seconds - CONFIG.worker_heartbeat_seconds
                if time.monotonic() - self.last_database_success >= unsafe_after:
                    logger.error("Database lease safety window exceeded; stopping all bots")
                    await self._stop_all(release=False)
            await asyncio.sleep(CONFIG.worker_heartbeat_seconds)

    async def sync_once(self) -> None:
        enabled_documents = await store.list_tenants(enabled=True)
        enabled = {str(document["_id"]): document for document in enabled_documents}

        for tenant_id in list(self.managed):
            managed = self.managed[tenant_id]
            if tenant_id not in enabled:
                await self._stop_tenant(tenant_id, release=True)
                continue
            document = enabled[tenant_id]
            if managed.lifecycle_task and not managed.lifecycle_task.done():
                status = "starting"
            else:
                status = "running" if managed.bot else "error"
            renewed = await store.renew_lease(
                tenant_id,
                self.worker_id,
                managed.generation,
                CONFIG.worker_lease_seconds,
                status=status,
                last_error=managed.error,
                bot_username=managed.bot.username if managed.bot else "",
            )
            if not renewed:
                await self._stop_tenant(tenant_id, release=False)
                continue
            if managed.lifecycle_task and not managed.lifecycle_task.done():
                continue

            if int(document.get("config_version", 1)) != int(
                managed.document.get("config_version", 1)
            ):
                old_fingerprint = managed.core_fingerprint
                managed.document = document
                if old_fingerprint != managed.core_fingerprint:
                    self._schedule_lifecycle(
                        managed, lambda managed=managed: self._restart_bot(managed)
                    )
                elif managed.bot:
                    bot = managed.bot
                    self._schedule_lifecycle(
                        managed,
                        lambda bot=bot, document=document: bot.apply_tenant_document(document),
                    )
            elif not managed.bot and time.monotonic() >= managed.retry_at:
                managed.document = document
                self._schedule_lifecycle(
                    managed, lambda managed=managed: self._start_bot(managed)
                )

        available = CONFIG.worker_bot_capacity - len(self.managed)
        if available > 0:
            for tenant_id, document in enabled.items():
                if available <= 0:
                    break
                if tenant_id in self.managed:
                    continue
                lease = await store.claim_lease(
                    tenant_id, self.worker_id, CONFIG.worker_lease_seconds
                )
                if not lease:
                    continue
                managed = ManagedTenant(document=document, generation=int(lease["generation"]))
                self.managed[tenant_id] = managed
                self._schedule_lifecycle(
                    managed, lambda managed=managed: self._start_bot(managed)
                )
                available -= 1

    def _schedule_lifecycle(
        self,
        managed: ManagedTenant,
        operation: Callable[[], Awaitable[None]],
    ) -> None:
        if managed.lifecycle_task and not managed.lifecycle_task.done():
            return
        managed.lifecycle_task = asyncio.create_task(
            self._run_lifecycle(managed, operation)
        )

    async def _run_lifecycle(
        self,
        managed: ManagedTenant,
        operation: Callable[[], Awaitable[None]],
    ) -> None:
        current = asyncio.current_task()
        try:
            await operation()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            managed.error = str(exc)[:500]
            logger.exception("Tenant %s lifecycle task failed", managed.document.get("slug"))
        finally:
            if managed.lifecycle_task is current:
                managed.lifecycle_task = None

    async def _start_bot(self, managed: ManagedTenant) -> None:
        bot: Bot | None = None
        try:
            token = self.crypto.decrypt_bot_token(managed.document["bot_token_ciphertext"])
            bot = Bot(managed.document, token, self.delivery_capacity)
            await bot.start()
            managed.bot = bot
            managed.error = ""
        except asyncio.CancelledError:
            if bot:
                with suppress(Exception):
                    await bot.stop()
            managed.bot = None
            raise
        except Exception as exc:
            logger.exception("Tenant %s failed to start", managed.document.get("slug"))
            if bot:
                try:
                    await bot.stop()
                except Exception:
                    logger.exception("Failed to clean up partially started tenant bot")
            managed.bot = None
            managed.error = str(exc)[:500]
            managed.retry_at = time.monotonic() + 60

    async def _restart_bot(self, managed: ManagedTenant) -> None:
        if managed.bot:
            try:
                await managed.bot.stop()
            except Exception:
                logger.exception(
                    "Tenant %s failed to stop before restart", managed.document.get("slug")
                )
            managed.bot = None
        await self._start_bot(managed)

    async def _stop_tenant(self, tenant_id: str, *, release: bool) -> None:
        managed = self.managed.pop(tenant_id, None)
        if not managed:
            return
        if managed.lifecycle_task and not managed.lifecycle_task.done():
            managed.lifecycle_task.cancel()
            await asyncio.gather(managed.lifecycle_task, return_exceptions=True)
        if managed.bot:
            try:
                await managed.bot.stop()
            except Exception:
                logger.exception("Tenant %s failed to stop cleanly", tenant_id)
        if release:
            await store.release_lease(tenant_id, self.worker_id, managed.generation)

    async def _stop_all(self, *, release: bool) -> None:
        for tenant_id in list(self.managed):
            await self._stop_tenant(tenant_id, release=release)

    async def _job_loop(self) -> None:
        last_requeue = 0.0
        while not self.stopping.is_set():
            try:
                now = time.monotonic()
                if now - last_requeue > 30:
                    await store.requeue_expired_jobs()
                    last_requeue = now
                self.job_tasks = {
                    key: task for key, task in self.job_tasks.items() if not task.done()
                }
                busy_tenants = {getattr(task, "tenant_id", "") for task in self.job_tasks.values()}
                running_tenant_ids = [
                    tenant_id
                    for tenant_id, managed in self.managed.items()
                    if managed.bot and tenant_id not in busy_tenants
                ]
                stopped_tenant_ids = [
                    tenant_id
                    for tenant_id, managed in self.managed.items()
                    if not managed.bot and tenant_id not in busy_tenants
                ]
                if len(self.job_tasks) < CONFIG.worker_bot_capacity:
                    job = await store.claim_job(
                        self.worker_id,
                        running_tenant_ids,
                        CONFIG.worker_lease_seconds * 4,
                    )
                    if not job:
                        job = await store.claim_job(
                            self.worker_id,
                            stopped_tenant_ids,
                            CONFIG.worker_lease_seconds * 4,
                            job_types=["bot_restart"],
                        )
                    if job:
                        task = asyncio.create_task(self._process_job(job))
                        task.tenant_id = job["tenant_id"]  # type: ignore[attr-defined]
                        self.job_tasks[job["_id"]] = task
                        continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Worker job loop failed")
            await asyncio.sleep(0.5)

    async def _process_job(self, job: dict[str, Any]) -> None:
        tenant_id = job["tenant_id"]
        managed = self.managed.get(tenant_id)
        if not managed:
            await store.finish_job(
                job["_id"], self.worker_id, success=False, error="Tenant is not assigned"
            )
            return
        owner = asyncio.current_task()
        heartbeat = asyncio.create_task(self._job_heartbeat(job["_id"], owner))
        try:
            if job["type"] == "bot_restart":
                await self._restart_bot(managed)
                if not managed.bot:
                    raise RuntimeError("Bot restart failed")
                result = {"status": "running", "bot_username": managed.bot.username}
            elif not managed.bot:
                raise RuntimeError("Tenant bot is not running")
            elif job["type"] == "broadcast":
                result = await self._broadcast(managed.bot, job)
            elif job["type"] == "share_forward":
                share = await store.get_share(tenant_id, job["payload"]["code"])
                if not share:
                    raise RuntimeError("Share not found")
                result = await forward_share_to_channels(managed.bot, share, job["payload"])
            else:
                raise RuntimeError(f"Unsupported job type: {job['type']}")
            await store.finish_job(job["_id"], self.worker_id, success=True, result=result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Job %s failed", job["_id"])
            await store.finish_job(job["_id"], self.worker_id, success=False, error=str(exc))
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _job_heartbeat(self, job_id: str, owner: asyncio.Task | None) -> None:
        lease_seconds = CONFIG.worker_lease_seconds * 4
        last_success = time.monotonic()
        while True:
            await asyncio.sleep(CONFIG.worker_heartbeat_seconds)
            try:
                renewed = await store.renew_job_lease(job_id, self.worker_id, lease_seconds)
            except Exception:
                logger.exception("Job %s lease renewal failed", job_id)
                if time.monotonic() - last_success >= (
                    lease_seconds - CONFIG.worker_heartbeat_seconds
                ):
                    if owner:
                        owner.cancel()
                    return
                continue
            if not renewed:
                if owner:
                    owner.cancel()
                return
            last_success = time.monotonic()

    async def _broadcast(self, bot: Bot, job: dict[str, Any]) -> dict[str, int]:
        tenant_id = bot.tenant.tenant_id
        total = await store.get_user_count(tenant_id)
        progress = {"current": 0, "total": total, "successful": 0, "failed": 0}
        async for user_id in store.iter_user_ids(tenant_id):
            try:
                await telegram_retry(
                    lambda user_id=user_id: bot.send_message(
                        chat_id=user_id, text=job["payload"]["message"]
                    )
                )
                progress["successful"] += 1
            except (UserIsBlocked, InputUserDeactivated):
                progress["failed"] += 1
                await store.delete_user(tenant_id, user_id)
            except Exception:
                progress["failed"] += 1
            progress["current"] += 1
            if progress["current"] % 25 == 0 or progress["current"] == total:
                renewed = await store.update_job_progress(
                    job["_id"],
                    self.worker_id,
                    progress,
                    CONFIG.worker_lease_seconds * 4,
                )
                if not renewed:
                    raise RuntimeError("Job lease was lost")
            await asyncio.sleep(0.04)
        await store.increment_stat(tenant_id, "broadcasts")
        return progress

    async def shutdown(self) -> None:
        if self.job_tasks:
            for task in self.job_tasks.values():
                task.cancel()
            await asyncio.gather(*self.job_tasks.values(), return_exceptions=True)
        await self._stop_all(release=True)
        logger.info("Worker %s stopped", self.worker_id)
