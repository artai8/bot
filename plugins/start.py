from __future__ import annotations

import asyncio
import logging
import re
import secrets
import string
import time
from contextlib import suppress

from pyrogram import Client, filters
from pyrogram.errors import InputUserDeactivated, UserIsBlocked
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import Bot
from database.database import store
from delivery import DeliveryQueueFull, copy_share_to_user, telegram_retry
from helper_func import (
    admin_only,
    decode,
    get_exp_time,
    get_shortlink,
    get_verify_status,
    not_banned,
    parse_buttons,
    send_force_sub_prompt,
    subscribed,
    update_verify_status,
)
from plugins.share import handle_share_code

logger = logging.getLogger(__name__)
SHARE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{6,12}$")


def build_start_buttons(client: Client):
    buttons = [
        [
            InlineKeyboardButton("ℹ️ 关于我", callback_data="about"),
            InlineKeyboardButton("📖 使用帮助", callback_data="help"),
        ]
    ]
    buttons.extend(parse_buttons(str(client.tenant.setting("custom_buttons", ""))))
    buttons.append([InlineKeyboardButton("❌ 关闭", callback_data="close")])
    return InlineKeyboardMarkup(buttons)


async def handle_link_access(client: Client, message: Message, encoded: str) -> bool:
    try:
        decoded = await decode(encoded)
        parts = decoded.split("-")
        if len(parts) == 3:
            start = int(int(parts[1]) / abs(client.db_channel.id))
            end = int(int(parts[2]) / abs(client.db_channel.id))
            step = 1 if start <= end else -1
            message_ids = list(range(start, end + step, step))
        elif len(parts) == 2:
            message_ids = [int(int(parts[1]) / abs(client.db_channel.id))]
        else:
            return False
    except Exception:
        return False
    waiting = await message.reply("⏳ 请稍候...")
    sent = []
    try:
        sent = await copy_share_to_user(
            client,
            message.from_user.id,
            message_ids,
            protect_content=bool(client.tenant.setting("protect_content", False)),
        )
    except DeliveryQueueFull:
        await waiting.edit("⏳ 当前取件请求较多，请稍后重试。")
        waiting = None
        return True
    except Exception:
        logger.exception("[%s] Legacy-link delivery failed", client.tenant.slug)
        await message.reply("❌ 获取文件失败，请稍后重试。", quote=True)
        return True
    finally:
        if waiting:
            with suppress(Exception):
                await waiting.delete()
    await store.increment_stat(client.tenant.tenant_id, "files_shared", len(sent))
    return True


def _welcome_text(client: Client, message: Message) -> str:
    user = message.from_user
    return str(client.tenant.setting("start_message")).format(
        first=user.first_name,
        last=user.last_name,
        username=None if not user.username else "@" + user.username,
        mention=user.mention,
        id=user.id,
    )


async def _complete_verification(client: Client, message: Message, token: str) -> bool:
    status = await get_verify_status(client, message.from_user.id)
    if status["verify_token"] != token:
        await message.reply("❌ 验证令牌无效或已过期。", quote=True)
        return False
    await update_verify_status(
        client,
        message.from_user.id,
        is_verified=True,
        verified_time=time.time(),
    )
    await store.increment_stat(client.tenant.tenant_id, "tokens_verified")
    await message.reply(
        f"✅ 验证成功！有效期：{get_exp_time(int(client.tenant.setting('verify_expire', 86400)))}",
        quote=True,
    )
    return True


async def _handle_parameter(client: Client, message: Message, parameter: str) -> None:
    if parameter.startswith("verify_"):
        await _complete_verification(client, message, parameter.split("_", 1)[1])
        return
    if SHARE_CODE_PATTERN.fullmatch(parameter) and await handle_share_code(
        client, message, parameter
    ):
        return
    if not await handle_link_access(client, message, parameter):
        await message.reply("❌ 分享链接或分享码无效。", quote=True)


@Bot.on_message(filters.command("start") & filters.private & subscribed & not_banned, group=2)
async def start_command(client: Client, message: Message):
    tenant_id = client.tenant.tenant_id
    user_id = message.from_user.id
    await store.add_user(tenant_id, user_id)

    if client.tenant.is_admin(user_id):
        if len(message.command) > 1:
            await _handle_parameter(client, message, message.command[1])
        else:
            await message.reply_text(
                _welcome_text(client, message),
                reply_markup=build_start_buttons(client),
                disable_web_page_preview=True,
                quote=True,
            )
        return

    if client.rate_limiter.is_limited(user_id):
        wait = client.rate_limiter.get_wait_time(user_id)
        return await message.reply(f"⏳ 请求过于频繁！请等待 {wait} 秒后再试。", quote=True)

    status = await get_verify_status(client, user_id)
    expire = int(client.tenant.setting("verify_expire", 86400))
    if status["is_verified"] and expire < time.time() - float(status["verified_time"] or 0):
        await update_verify_status(client, user_id, is_verified=False)
        status = await get_verify_status(client, user_id)

    if len(message.command) > 1 and message.command[1].startswith("verify_"):
        await _complete_verification(client, message, message.command[1].split("_", 1)[1])
        return

    if client.tenant.setting("is_verify", False) and not status["is_verified"]:
        token = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(10))
        await update_verify_status(client, user_id, verify_token=token)
        verification_url = f"https://t.me/{client.username}?start=verify_{token}"
        short_url = str(client.tenant.setting("shortlink_url", ""))
        short_api = str(client.tenant.setting("shortlink_api", ""))
        if short_url and short_api:
            try:
                verification_url = await get_shortlink(short_url, short_api, verification_url)
            except Exception:
                logger.exception("[%s] Short-link generation failed", client.tenant.slug)
        buttons = [[InlineKeyboardButton("🔐 点击验证", url=verification_url)]]
        tutorial = str(client.tenant.setting("tutorial_video", ""))
        if tutorial:
            buttons.append([InlineKeyboardButton("📖 如何使用", url=tutorial)])
        return await message.reply(
            f"🔐 请先完成验证。\n\n⏱ 验证有效期：{get_exp_time(expire)}",
            reply_markup=InlineKeyboardMarkup(buttons),
            quote=True,
        )

    if len(message.command) > 1:
        await _handle_parameter(client, message, message.command[1])
    else:
        await message.reply_text(
            _welcome_text(client, message),
            reply_markup=build_start_buttons(client),
            disable_web_page_preview=True,
            quote=True,
        )


@Bot.on_message(filters.command("help") & filters.private & subscribed & not_banned, group=2)
async def help_command(client: Client, message: Message):
    key = "admin_help_text" if client.tenant.is_admin(message.from_user.id) else "help_text"
    await message.reply_text(
        str(client.tenant.setting(key)), disable_web_page_preview=True, quote=True
    )


@Bot.on_message(
    filters.command(["start", "help", "id", "ping", "myshares"]) & filters.private & ~subscribed,
    group=3,
)
async def not_joined(client: Client, message: Message):
    await send_force_sub_prompt(client, message)


@Bot.on_message(filters.command("users") & filters.private & admin_only, group=2)
async def get_users(client: Bot, message: Message):
    count = await store.get_user_count(client.tenant.tenant_id)
    await message.reply(f"👥 当前共有 {count} 位用户使用本机器人", quote=True)


@Bot.on_message(filters.private & filters.command("broadcast") & admin_only, group=2)
async def send_text(client: Bot, message: Message):
    if not message.reply_to_message:
        return await message.reply("<code>请回复一条消息来使用此命令。</code>")
    markup = None
    if len(message.command) > 1:
        rows = parse_buttons(" ".join(message.command[1:]))
        markup = InlineKeyboardMarkup(rows) if rows else None
    waiting = await message.reply("<i>📢 正在广播消息...</i>")
    progress = {"total": 0, "successful": 0, "blocked": 0, "deleted": 0, "failed": 0}
    tenant_id = client.tenant.tenant_id
    async for chat_id in store.iter_user_ids(tenant_id):
        try:
            await telegram_retry(
                lambda chat_id=chat_id: message.reply_to_message.copy(chat_id, reply_markup=markup)
            )
            progress["successful"] += 1
        except UserIsBlocked:
            await store.delete_user(tenant_id, chat_id)
            progress["blocked"] += 1
        except InputUserDeactivated:
            await store.delete_user(tenant_id, chat_id)
            progress["deleted"] += 1
        except Exception:
            progress["failed"] += 1
        progress["total"] += 1
        await asyncio.sleep(0.04)
    await store.increment_stat(tenant_id, "broadcasts")
    await waiting.edit(
        "<b><u>📢 广播完成</u></b>\n\n"
        f"总用户数：<code>{progress['total']}</code>\n"
        f"发送成功：<code>{progress['successful']}</code>\n"
        f"已屏蔽：<code>{progress['blocked']}</code>\n"
        f"已注销：<code>{progress['deleted']}</code>\n"
        f"发送失败：<code>{progress['failed']}</code>"
    )
