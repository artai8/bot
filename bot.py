"""A single tenant's Telegram bot runtime."""

from __future__ import annotations

import asyncio
import logging
import tempfile
from collections.abc import Coroutine
from datetime import datetime, timezone
from typing import Any

import pyrogram.utils
from pyrogram import Client
from pyrogram.enums import ChatMemberStatus, ChatType, ParseMode
from pyrogram.types import BotCommand

from config import CONFIG
from delivery import DeliveryCapacity, TenantDeliveryLimiter
from helper_func import RateLimiter
from tenant import TenantContext

pyrogram.utils.MIN_CHAT_ID = -999999999999
pyrogram.utils.MIN_CHANNEL_ID = -100999999999999
logger = logging.getLogger(__name__)


class Bot(Client):
    def __init__(
        self,
        tenant_document: dict[str, Any],
        bot_token: str,
        delivery_capacity: DeliveryCapacity,
    ):
        self.tenant_document = tenant_document
        self.tenant = TenantContext.from_document(tenant_document)
        super().__init__(
            name=f"tenant-{self.tenant.tenant_id}",
            api_hash=CONFIG.api_hash,
            api_id=CONFIG.api_id,
            plugins={"root": "plugins"},
            workers=4,
            bot_token=bot_token,
            workdir=tempfile.gettempdir(),
            in_memory=True,
        )
        self.invitelink: str | None = None
        self.invitelinks: dict[int, str] = {}
        self.share_sessions: dict[int, dict[str, Any]] = {}
        self.link_sessions: dict[int, dict[str, Any]] = {}
        self.channel_media_groups: dict[str, dict[str, Any]] = {}
        self.linked_chat_cache: dict[int, int | None] = {}
        self.linked_cache_ts: dict[int, float] = {}
        self.background_tasks: set[asyncio.Task] = set()
        self.rate_limiter = RateLimiter(
            int(self.tenant.setting("rate_limit_max", 10)),
            int(self.tenant.setting("rate_limit_window", 60)),
        )
        self.delivery_limiter = TenantDeliveryLimiter(
            delivery_capacity,
            CONFIG.tenant_max_deliveries,
            CONFIG.tenant_pending_deliveries,
        )
        self.username = ""
        self.uptime = datetime.now(timezone.utc)

    async def start(self) -> Bot:
        await super().start()
        me = await self.get_me()
        self.username = me.username or ""
        self.uptime = datetime.now(timezone.utc)
        self.db_channel = await self.get_chat(self.tenant.db_channel_id)
        member = await self.get_chat_member(self.db_channel.id, "me")
        allowed = {ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR}
        if self.db_channel.type != ChatType.CHANNEL:
            allowed.add(ChatMemberStatus.MEMBER)
        if member.status not in allowed:
            await super().stop()
            raise RuntimeError("Bot lacks database-channel access")
        self.set_parse_mode(ParseMode.HTML)
        await self.refresh_invite_links()
        try:
            await self.set_bot_commands(
                [
                    BotCommand("start", "启动"),
                    BotCommand("share", "上传"),
                    BotCommand("myshares", "我的上传"),
                    BotCommand("help", "帮助"),
                ]
            )
        except Exception as exc:
            logger.warning("[%s] Unable to set commands: %s", self.tenant.slug, exc)
        logger.info("[%s] Bot started as @%s", self.tenant.slug, self.username)
        return self

    async def apply_tenant_document(self, document: dict[str, Any]) -> None:
        previous_channels = set(self.tenant.setting("force_sub_channels", []))
        self.tenant_document = document
        self.tenant = TenantContext.from_document(document)
        self.rate_limiter.reconfigure(
            int(self.tenant.setting("rate_limit_max", 10)),
            int(self.tenant.setting("rate_limit_window", 60)),
        )
        if previous_channels != set(self.tenant.setting("force_sub_channels", [])):
            await self.refresh_invite_links()

    async def refresh_invite_links(self) -> None:
        links: dict[int, str] = {}
        for channel_id in self.tenant.setting("force_sub_channels", []):
            try:
                chat = await self.get_chat(channel_id)
                link = chat.invite_link
                if not link:
                    link = await self.export_chat_invite_link(channel_id)
                if link:
                    links[int(channel_id)] = link
            except Exception as exc:
                logger.warning(
                    "[%s] Force-sub channel %s unavailable: %s",
                    self.tenant.slug,
                    channel_id,
                    exc,
                )
        self.invitelinks = links
        self.invitelink = next(iter(links.values()), None)

    def create_background_task(self, coroutine: Coroutine[Any, Any, Any]) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return task

    async def _cancel_background_tasks(self) -> None:
        tasks = list(self.background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.background_tasks.clear()
        self.channel_media_groups.clear()

    async def stop(self, *args: Any) -> None:
        try:
            await self._cancel_background_tasks()
            await super().stop()
        finally:
            logger.info("[%s] Bot stopped", self.tenant.slug)
