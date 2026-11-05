"""Tenant-aware Telegram handler helpers."""

from __future__ import annotations

import base64
import logging
import re
import secrets
import string
import time
from collections import OrderedDict
from contextlib import suppress
from typing import Any

from pyrogram import filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait
from pyrogram.errors.exceptions.bad_request_400 import UserNotParticipant
from pyrogram.types import MessageOriginChannel
from shortzy import Shortzy

from database.database import store
from delivery import get_messages as fetch_messages

logger = logging.getLogger(__name__)

ALL_COMMANDS = [
    "start",
    "help",
    "share",
    "myshares",
    "title",
    "users",
    "broadcast",
    "batch",
    "genlink",
    "stats",
    "ping",
    "ban",
    "unban",
    "banned",
    "id",
    "search",
    "backup",
]


async def is_admin(_filter: Any, client: Any, update: Any) -> bool:
    return bool(update.from_user and client.tenant.is_admin(update.from_user.id))


async def is_subscribed(_filter: Any, client: Any, update: Any) -> bool:
    if not update.from_user:
        return True
    channels = list(client.tenant.setting("force_sub_channels", []))
    if not channels or client.tenant.is_admin(update.from_user.id):
        return True
    user_id = update.from_user.id
    for channel_id in channels:
        try:
            member = await client.get_chat_member(channel_id, user_id)
            if member.status not in {
                ChatMemberStatus.OWNER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.MEMBER,
            }:
                raise UserNotParticipant
        except UserNotParticipant:
            await store.update_force_sub_status(
                client.tenant.tenant_id,
                user_id,
                {"verified": False, "verified_at": time.time(), "channels": channels},
            )
            return False
        except FloodWait as exc:
            logger.warning(
                "[%s] Subscription check throttled for %ss",
                client.tenant.slug,
                exc.value,
            )
            return False
        except Exception as exc:
            logger.error(
                "[%s] Subscription check failed for %s: %s",
                client.tenant.slug,
                channel_id,
                exc,
            )
            return False
    await store.update_force_sub_status(
        client.tenant.tenant_id,
        user_id,
        {"verified": True, "verified_at": time.time(), "channels": channels},
    )
    return True


async def is_not_banned(_filter: Any, client: Any, update: Any) -> bool:
    if not update.from_user or client.tenant.is_admin(update.from_user.id):
        return True
    banned = await store.is_banned(client.tenant.tenant_id, update.from_user.id)
    if not banned:
        return True
    with suppress(Exception):
        await update.reply(
            f"🚫 您已被封禁，无法使用本机器人。\n<b>原因：</b>{banned.get('reason', '未说明原因')}",
            quote=True,
        )
    return False


async def send_force_sub_prompt(client: Any, message: Any) -> None:
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    buttons = [
        [InlineKeyboardButton(f"📢 加入频道 {index}", url=link)]
        for index, link in enumerate(client.invitelinks.values(), 1)
        if link
    ]
    if getattr(message, "command", None) and len(message.command) > 1:
        buttons.append(
            [
                InlineKeyboardButton(
                    "🔄 重试",
                    url=f"https://t.me/{client.username}?start={message.command[1]}",
                )
            ]
        )
    if not buttons:
        await message.reply("⚠️ 管理员尚未正确配置强制加入频道。", quote=True)
        return
    user = message.from_user
    text = str(client.tenant.setting("force_sub_message")).format(
        first=user.first_name,
        last=user.last_name,
        username=None if not user.username else "@" + user.username,
        mention=user.mention,
        id=user.id,
    )
    await message.reply(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        quote=True,
        disable_web_page_preview=True,
    )


async def encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("ascii")).decode("ascii").strip("=")


async def decode(value: str) -> str:
    padded = value.strip("=") + "=" * (-len(value.strip("=")) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")


def generate_share_code(length: int = 8) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(int(length)))


async def get_messages(client: Any, message_ids: list[int]) -> list[Any]:
    return await fetch_messages(client, message_ids)


async def get_message_id(client: Any, message: Any) -> int:
    if message.forward_origin and isinstance(message.forward_origin, MessageOriginChannel):
        if message.forward_origin.chat.id == client.db_channel.id:
            return message.forward_origin.message_id
        return 0
    if message.forward_origin:
        return 0
    if not message.text:
        return 0
    match = re.match(r"https://t.me/(?:c/)?(.*)/(\d+)", message.text)
    if not match:
        return 0
    channel_id, message_id = match.group(1), int(match.group(2))
    if channel_id.isdigit() and f"-100{channel_id}" == str(client.db_channel.id):
        return message_id
    if channel_id == client.db_channel.username:
        return message_id
    return 0


async def get_verify_status(client: Any, user_id: int) -> dict[str, Any]:
    return await store.get_verify_status(client.tenant.tenant_id, user_id)


async def update_verify_status(
    client: Any,
    user_id: int,
    *,
    verify_token: str = "",
    is_verified: bool = False,
    verified_time: float = 0,
    link: str = "",
) -> None:
    current = await store.get_verify_status(client.tenant.tenant_id, user_id)
    current.update(
        {
            "verify_token": verify_token,
            "is_verified": is_verified,
            "verified_time": verified_time,
            "link": link,
        }
    )
    await store.update_verify_status(client.tenant.tenant_id, user_id, current)


async def get_shortlink(url: str, api: str, link: str) -> str:
    return await Shortzy(api_key=api, base_site=url).convert(link)


def get_exp_time(seconds: int) -> str:
    result = []
    for name, size in (("天", 86400), ("小时", 3600), ("分钟", 60), ("秒", 1)):
        if seconds >= size:
            count, seconds = divmod(seconds, size)
            result.append(f"{count}{name}")
    return " ".join(result)


def get_readable_time(seconds: int) -> str:
    days, seconds = divmod(max(0, int(seconds)), 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}天")
    if hours or parts:
        parts.append(f"{hours}时")
    if minutes or parts:
        parts.append(f"{minutes}分")
    parts.append(f"{seconds}秒")
    return ":".join(parts)


class RateLimiter:
    def __init__(
        self,
        max_requests: int = 10,
        window_seconds: int = 60,
        max_users: int = 10_000,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.max_users = max_users
        self.user_requests: OrderedDict[int, list[float]] = OrderedDict()

    def reconfigure(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.user_requests.clear()

    def is_limited(self, user_id: int) -> bool:
        now = time.monotonic()
        recent = [
            value
            for value in self.user_requests.get(user_id, [])
            if now - value < self.window_seconds
        ]
        if user_id not in self.user_requests and len(self.user_requests) >= self.max_users:
            self.user_requests.popitem(last=False)
        self.user_requests[user_id] = recent
        self.user_requests.move_to_end(user_id)
        if len(recent) >= self.max_requests:
            return True
        recent.append(now)
        return False

    def get_wait_time(self, user_id: int) -> int:
        requests = self.user_requests.get(user_id) or []
        if not requests:
            return 0
        return max(1, int(self.window_seconds - (time.monotonic() - requests[0])) + 1)


def parse_buttons(value: str) -> list[list[Any]]:
    from pyrogram.types import InlineKeyboardButton

    buttons = []
    row = []
    for part in (value or "").split(","):
        if "|" not in part:
            continue
        text, url = part.split("|", 1)
        row.append(InlineKeyboardButton(text.strip(), url=url.strip()))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return buttons


admin_only = filters.create(is_admin)
subscribed = filters.create(is_subscribed)
not_banned = filters.create(is_not_banned)
