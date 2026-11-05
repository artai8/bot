"""A single tenant's Telegram bot runtime with bounded session & cache eviction."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from collections import OrderedDict
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

SESSION_TTL_SECONDS = 1800  # 30 minutes TTL for interactive upload/link sessions
MAX_ACTIVE_SESSIONS = 500
MAX_LINKED_CACHE_SIZE = 1000


class BoundedSessionDict(OrderedDict):
    """Dict with automatic TTL expiration and LRU capacity limit."""

    def __init__(self, max_size: int = MAX_ACTIVE_SESSIONS, ttl: float = SESSION_TTL_SECONDS):
        super().__init__()
        self.max_size = max_size
        self.ttl = ttl
        self._timestamps: dict[Any, float] = {}

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, ts in self._timestamps.items() if now - ts > self.ttl]
        for k in expired:
            super().pop(k, None)
            self._timestamps.pop(k, None)

    def __setitem__(self, key: Any, value: Any) -> None:
        self._evict_expired()
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        self._timestamps[key] = time.monotonic()
        while len(self) > self.max_size:
            oldest, _ = self.popitem(last=False)
            self._timestamps.pop(oldest, None)

    def get(self, key: Any, default: Any = None) -> Any:
        self._evict_expired()
        return super().get(key, default)

    def pop(self, key: Any, *args: Any) -> Any:
        self._timestamps.pop(key, None)
        return super().pop(key, *args)


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
        self.share_sessions: dict[int, dict[str, Any]] = BoundedSessionDict()
        self.link_sessions: dict[int, dict[str, Any]] = BoundedSessionDict()
        self.channel_media_groups: dict[str, dict[str, Any]] = BoundedSessionDict(max_size=200, ttl=300)
        self.linked_chat_cache: dict[int, int | None] = BoundedSessionDict(max_size=MAX_LINKED_CACHE_SIZE, ttl=600)
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
                    links[channel_id] = link
            except Exception as exc:
                logger.warning("[%s] Could not export invite link for %s: %s", self.tenant.slug, channel_id, exc)
        self.invitelinks = links
        self.invitelink = next(iter(links.values()), None)

    def create_task(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.background_tasks.add(task)

        def _on_done(t: asyncio.Task) -> None:
            self.background_tasks.discard(t)
            if not t.cancelled() and t.exception():
                logger.error("[%s] Background task failed: %s", self.tenant.slug, t.exception())

        task.add_done_callback(_on_done)
        return task

    async def stop(self, block: bool = True) -> None:
        for task in list(self.background_tasks):
            task.cancel()
        if self.background_tasks:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        await super().stop(block=block)
