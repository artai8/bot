"""Shared message delivery, backpressure and Telegram retry logic."""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait
from pyrogram.types import InputMediaPhoto, InputMediaVideo

logger = logging.getLogger(__name__)


class DeliveryQueueFull(Exception):
    pass


class DeliveryCapacity:
    def __init__(self, maximum: int):
        self.semaphore = asyncio.Semaphore(maximum)


class TenantDeliveryLimiter:
    def __init__(self, shared: DeliveryCapacity, maximum: int, pending_limit: int):
        self.shared = shared
        self.semaphore = asyncio.Semaphore(maximum)
        self.maximum = maximum
        self.pending_limit = pending_limit
        self.pending = 0
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._lock:
            if self.pending >= self.maximum + self.pending_limit:
                raise DeliveryQueueFull
            self.pending += 1
        try:
            # Acquire the tenant slot first so one tenant's waiters cannot
            # reserve every worker-wide slot while blocked on their own limit.
            async with self.semaphore, self.shared.semaphore:
                yield
        finally:
            async with self._lock:
                self.pending -= 1


async def telegram_retry(operation, *, attempts: int = 3):
    for attempt in range(attempts):
        try:
            return await operation()
        except FloodWait as exc:
            if attempt + 1 >= attempts:
                raise
            await asyncio.sleep(exc.value)


async def get_messages(client: Any, message_ids: list[int]) -> list[Any]:
    messages: list[Any] = []
    for offset in range(0, len(message_ids), 200):
        batch = message_ids[offset : offset + 200]

        async def fetch(batch=batch):
            return await client.get_messages(chat_id=client.db_channel.id, message_ids=batch)

        try:
            result = await telegram_retry(fetch)
        except Exception as exc:
            logger.error("[%s] Unable to fetch messages: %s", client.tenant.slug, exc)
            continue
        if not isinstance(result, list):
            result = [result]
        messages.extend(message for message in result if message and not message.empty)
    return messages


async def copy_share_to_user(
    client: Any,
    target_user_id: int,
    message_ids: list[int],
    *,
    protect_content: bool,
) -> list[Any]:
    settings = client.tenant.settings
    async with client.delivery_limiter.slot():
        source_messages = await get_messages(client, message_ids)
        sent: list[Any] = []
        for source in source_messages:
            if settings.get("custom_caption") and source.document:
                caption = str(settings["custom_caption"]).format(
                    previouscaption="" if not source.caption else source.caption.html,
                    filename=source.document.file_name,
                )
            else:
                caption = "" if not source.caption else source.caption.html
            if settings.get("show_promo") and settings.get("promo_text"):
                caption += str(settings["promo_text"])
            reply_markup = None if settings.get("disable_channel_button") else source.reply_markup

            async def copy(source=source, caption=caption, reply_markup=reply_markup):
                return await source.copy(
                    chat_id=target_user_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                    protect_content=protect_content,
                )

            sent.append(await telegram_retry(copy))
            await asyncio.sleep(0.25)
    return sent


async def delete_later(messages: list[Any], notification: Any | None, delay: int) -> None:
    await asyncio.sleep(delay)
    for message in messages:
        with suppress(Exception):
            await message.delete()
    if notification:
        with suppress(Exception):
            await notification.delete()


async def forward_share_to_channels(
    client: Any, share: dict[str, Any], payload: dict[str, Any]
) -> dict[str, int]:
    message_ids = list(share.get("message_ids") or [])
    indices: list[int] = []
    for value in payload.get("forward_indices") or []:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= index <= len(message_ids):
            indices.append(index)
    if payload.get("forward_all"):
        selected = message_ids
    elif indices:
        selected = [message_ids[index - 1] for index in sorted(set(indices))]
    else:
        try:
            count = int(payload.get("forward_count") or 2)
        except (TypeError, ValueError):
            count = 2
        selected = message_ids[: max(1, min(count, len(message_ids)))]
    messages = await get_messages(client, selected)
    if not messages:
        raise RuntimeError("Messages not found")

    keywords = share.get("keywords") or []
    suffix = ""
    if keywords:
        suffix = (
            f"\n<blockquote>在评论区输入（{html.escape(str(keywords[0]))}）查看资源</blockquote>"
        )
    caption = html.escape(share.get("group_text") or "")
    caption = f"{caption}\n{suffix}" if caption and suffix else caption or suffix
    channels = list(client.tenant.setting("bound_channels", []))
    if not channels:
        raise RuntimeError("No bound channels")

    successful = 0
    failed = 0
    can_album = len(messages) > 1 and all(
        (message.photo or message.video) and not message.document for message in messages
    )
    for channel_id in channels:
        try:
            if can_album:
                media = []
                for index, message in enumerate(messages):
                    item_caption = caption if index == 0 else ""
                    media_type = InputMediaPhoto if message.photo else InputMediaVideo
                    source = message.photo if message.photo else message.video
                    media.append(
                        media_type(source.file_id, caption=item_caption, parse_mode=ParseMode.HTML)
                    )
                await telegram_retry(
                    lambda channel_id=channel_id, media=media: client.send_media_group(
                        chat_id=channel_id, media=media
                    )
                )
            else:
                for index, message in enumerate(messages):
                    item_caption = caption if index == 0 else ""
                    if message.text and not message.media:
                        text = item_caption or message.text
                        await telegram_retry(
                            lambda text=text, channel_id=channel_id: client.send_message(
                                chat_id=channel_id, text=text, parse_mode=ParseMode.HTML
                            )
                        )
                    else:
                        await telegram_retry(
                            lambda message=message, item_caption=item_caption, channel_id=channel_id: (
                                message.copy(
                                    chat_id=channel_id,
                                    caption=item_caption,
                                    parse_mode=ParseMode.HTML,
                                )
                            )
                        )
                    await asyncio.sleep(0.25)
            successful += 1
        except Exception as exc:
            logger.warning("[%s] Forward to %s failed: %s", client.tenant.slug, channel_id, exc)
            failed += 1
    return {"successful": successful, "failed": failed}
