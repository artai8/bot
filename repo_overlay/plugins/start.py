from __future__ import annotations

import asyncio
import logging
import re
import secrets
import string
import time
from contextlib import suppress
from typing import Any

from pyrogram import Client, filters
from pyrogram.errors import InputUserDeactivated, UserIsBlocked
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from bot import Bot
from database.database import store
from delivery import DeliveryQueueFull, copy_share_to_user, telegram_retry
from helper_func import (
    admin_only,
    decode,
    get_exp_time,
    get_shortlink,
    get_verify_status,
    is_subscribed,
    not_banned,
    parse_buttons,
    send_force_sub_prompt,
    send_pay_unlock_prompt,
    subscribed,
    update_verify_status,
)
from plugins.share import handle_share_code

logger = logging.getLogger(__name__)

SHARE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
CDKEY_INPUT_PATTERN = re.compile(r"^CDK-[A-Za-z0-9]{6,24}$", re.IGNORECASE)
TXID_INPUT_PATTERN = re.compile(r"^(?:0x)?[a-fA-F0-9]{64}$")


# ==============================================================================
# 1. 强制订阅频道/关联群组「发送任意一条消息解锁」实时监听器
# ==============================================================================

@Bot.on_message((filters.group | filters.channel) & ~filters.bot, group=-1)
async def record_force_sub_channel_message(client: Client, message: Message) -> None:
    """
    监听用户在强制订阅频道或其关联讨论群中发送的任意消息：
    一旦匹配到当前租户配置的 `force_sub_channels`，立即记录该用户已在对应频道发言。
    """
    user = message.from_user
    if not user or not getattr(client, "tenant", None):
        return

    channels = [int(ch) for ch in client.tenant.setting("force_sub_channels", []) if ch]
    if not channels:
        return

    chat_id = int(message.chat.id)
    matched_channels: list[int] = []

    if chat_id in channels:
        matched_channels.append(chat_id)
    else:
        # 兼容：用户在频道的关联讨论群（linked_chat）内发言时，同步视为在主频道完成发言
        linked_map = getattr(client, "linked_discussion_map", {}) or {}
        if chat_id in linked_map:
            matched_channels.append(int(linked_map[chat_id]))
        else:
            # 若消息是对频道自动转发帖的评论（reply_to_message.forward_from_chat）
            reply = getattr(message, "reply_to_message", None)
            fwd_chat = getattr(reply, "forward_from_chat", None) if reply else None
            if fwd_chat and int(fwd_chat.id) in channels:
                matched_channels.append(int(fwd_chat.id))
                if not hasattr(client, "linked_discussion_map"):
                    client.linked_discussion_map = {}
                client.linked_discussion_map[chat_id] = int(fwd_chat.id)

    for target_channel_id in matched_channels:
        await store.mark_force_sub_message_sent(
            client.tenant.tenant_id,
            user.id,
            target_channel_id,
            message_id=int(message.id or 0),
        )


# ==============================================================================
# 2. 多种付费解锁方式处理（Stars 原生支付 / USDT / 卡密兑换 / 状态回调）
# ==============================================================================

@Bot.on_callback_query(filters.regex(r"^pay_(stars|usdt|cdkey|check):(.*)$"))
async def handle_payment_callbacks(client: Client, query: CallbackQuery) -> None:
    action, start_arg = query.matches[0].group(1), query.matches[0].group(2)
    user = query.from_user
    tenant_id = client.tenant.tenant_id

    if action == "check":
        expire_seconds = int(client.tenant.setting("pay_unlock_expire", 0) or 0)
        if await store.is_paid_unlocked(tenant_id, user.id, expire_seconds=expire_seconds):
            await query.answer("已验证解锁状态！", show_alert=False)
            await query.message.edit_text(
                "✅ <b>您的账号已完成付费解锁！</b>\n\n现在可以直接发送分享码或点击分享链接获取资源。"
            )
            if start_arg and start_arg != "check":
                await _handle_parameter(client, query.message, start_arg, override_user=user)
        else:
            await query.answer("尚未检测到有效解锁记录，请先完成任一方式支付或兑换卡密。", show_alert=True)
        return

    if action == "stars":
        stars_amount = int(client.tenant.setting("pay_amount_stars", 0) or 0)
        if stars_amount <= 0:
            await query.answer("当前未启用 Telegram Stars 支付", show_alert=True)
            return
        await query.answer()
        payload = f"unlock:{tenant_id}:{user.id}:{start_arg}:{int(time.time())}"
        try:
            await client.send_invoice(
                chat_id=user.id,
                title="解锁机器人访问权限",
                description=f"使用 {stars_amount} Telegram Stars 解锁当前机器人全部资源访问权限",
                payload=payload,
                currency="XTR",
                prices=[LabeledPrice(label="访问权限解锁", amount=stars_amount)],
            )
        except Exception as exc:
            logger.error("[%s] Failed to send Stars invoice: %s", client.tenant.slug, exc)
            await query.message.reply(
                "⚠️ 发起 Telegram Stars 账单失败，请改用卡密兑换或其他支付方式。"
            )
        return

    if action == "usdt":
        usdt_amount = str(client.tenant.setting("pay_amount_usdt", "") or "").strip()
        usdt_address = str(client.tenant.setting("pay_usdt_address", "") or "").strip()
        usdt_network = str(client.tenant.setting("pay_usdt_network", "TRC20") or "TRC20").strip()
        if not usdt_amount or not usdt_address:
            await query.answer("当前未配置 USDT 收款地址", show_alert=True)
            return
        await query.answer()
        text = (
            f"💎 <b>USDT ({usdt_network}) 转账解锁</b>\n\n"
            f"• 应付金额：<code>{usdt_amount}</code> USDT\n"
            f"• 转账网络：<b>{usdt_network}</b>\n"
            f"• 收款地址（点击复制）：\n<code>{usdt_address}</code>\n\n"
            "📌 <b>核销方式：</b>\n"
            "转账完成后，请直接在对话框发送命令：\n"
            "<code>/tx 您的64位交易哈希(TxID)</code>\n"
            "系统校验通过后将自动为您解锁访问权限。"
        )
        await query.message.reply(text, quote=True)
        return

    if action == "cdkey":
        await query.answer()
        text = (
            "🎫 <b>卡密 / 激活码兑换解锁</b>\n\n"
            "请直接在当前对话框发送您的激活码（格式如 <code>CDK-XXXXXXXX</code>），"
            "或使用命令：\n"
            "<code>/redeem 您的激活码</code>\n\n"
            "验证成功后立即自动解锁。"
        )
        await query.message.reply(text, quote=True)


@Bot.on_pre_checkout_query()
async def handle_pre_checkout(client: Client, query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@Bot.on_message(filters.private & filters.successful_payment, group=-1)
async def handle_successful_payment(client: Client, message: Message) -> None:
    payment = message.successful_payment
    user = message.from_user
    if not payment or not user:
        return

    tenant_id = client.tenant.tenant_id
    await store.grant_paid_unlock(
        tenant_id=tenant_id,
        user_id=user.id,
        method="telegram_stars",
        amount=str(payment.total_amount),
        tx_ref=payment.telegram_payment_charge_id or payment.invoice_payload,
    )
    await message.reply(
        f"✅ <b>支付成功！已收到 {payment.total_amount} Telegram Stars</b>\n\n"
        "您的账号已成功解锁访问权限。"
    )

    parts = (payment.invoice_payload or "").split(":", 4)
    if len(parts) >= 4 and parts[3] and parts[3] != "check":
        await _handle_parameter(client, message, parts[3], override_user=user)


@Bot.on_message(filters.command("redeem") & filters.private & not_banned, group=1)
async def redeem_command(client: Client, message: Message) -> None:
    if len(message.command) < 2:
        await message.reply("用法：<code>/redeem 您的卡密</code>（例如 <code>/redeem CDK-8F92A1B0</code>）", quote=True)
        return
    code = message.command[1].strip()
    await _process_cdkey_redemption(client, message, code)


@Bot.on_message(filters.command("tx") & filters.private & not_banned, group=1)
async def verify_tx_command(client: Client, message: Message) -> None:
    if len(message.command) < 2:
        await message.reply("用法：<code>/tx 您的64位USDT交易哈希(TxID)</code>", quote=True)
        return
    txid = message.command[1].strip()
    if not TXID_INPUT_PATTERN.match(txid):
        await message.reply("⚠️ 交易哈希 (TxID) 格式无效，请提供完整的 64 位十六进制哈希。", quote=True)
        return

    ok, msg = await store.verify_usdt_tx(
        tenant_id=client.tenant.tenant_id,
        user_id=message.from_user.id,
        txid=txid,
    )
    await message.reply(msg, quote=True)


async def _process_cdkey_redemption(client: Client, message: Message, code: str) -> None:
    ok, msg = await store.redeem_cdkey(
        tenant_id=client.tenant.tenant_id,
        user_id=message.from_user.id,
        code=code,
    )
    await message.reply(msg, quote=True)


# ==============================================================================
# 3. /start 与常规访问流程
# ==============================================================================

def build_start_buttons(client: Client) -> InlineKeyboardMarkup | None:
    custom = str(client.tenant.setting("custom_buttons", "") or "").strip()
    rows = parse_buttons(custom)
    rows.append(
        [
            InlineKeyboardButton("📖 使用帮助", callback_data="help"),
            InlineKeyboardButton("ℹ️ 关于", callback_data="about"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _welcome_text(client: Client, message: Message) -> str:
    user = message.from_user
    template = str(
        client.tenant.setting(
            "start_message",
            "你好 {first}\n\n我是文件分享机器人，发送分享码即可获取文件。",
        )
    )
    return template.format(
        first=user.first_name or "",
        last=user.last_name or "",
        username=None if not user.username else "@" + user.username,
        mention=user.mention,
        id=user.id,
    )


async def _complete_verification(client: Client, message: Message, token: str) -> bool:
    user = message.from_user
    status = await get_verify_status(client, user.id)
    if status.get("verify_token") != token:
        await message.reply("⚠️ 验证链接无效或已过期，请重新获取。", quote=True)
        return False
    await update_verify_status(
        client,
        user.id,
        verify_token="",
        is_verified=True,
        verified_time=time.time(),
    )
    await message.reply("✅ 短链验证通过，现在可以正常获取资源。", quote=True)
    return True


async def handle_link_access(client: Client, message: Message, encoded: str) -> bool:
    try:
        decoded = decode(encoded)
    except Exception:
        return False
    if not decoded.startswith("get-"):
        return False
    await handle_share_code(client, message, encoded)
    return True


async def _handle_parameter(
    client: Client,
    message: Message,
    parameter: str,
    override_user: Any = None,
) -> None:
    user = override_user or message.from_user
    if parameter.startswith("verify_"):
        token = parameter.split("verify_", 1)[1]
        await _complete_verification(client, message, token)
        return

    if bool(client.tenant.setting("is_verify", False)) and not client.tenant.is_admin(user.id):
        status = await get_verify_status(client, user.id)
        expire = int(client.tenant.setting("verify_expire", 86400) or 86400)
        if not status.get("is_verified") or (time.time() - float(status.get("verified_time", 0))) > expire:
            token = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
            verify_url = f"https://t.me/{client.username}?start=verify_{token}"
            short_url = await get_shortlink(
                str(client.tenant.setting("shortlink_url", "")),
                str(client.tenant.setting("shortlink_api", "")),
                verify_url,
            )
            await update_verify_status(client, user.id, verify_token=token, is_verified=False, link=short_url)
            buttons = [[InlineKeyboardButton("🔗 点击完成验证", url=short_url)]]
            tut = str(client.tenant.setting("tutorial_video", "") or "").strip()
            if tut:
                buttons.append([InlineKeyboardButton("📺 查看验证教程", url=tut)])
            await message.reply(
                f"🔐 <b>请先完成人机/短链验证</b>\n\n验证一次后在 <b>{get_exp_time(expire)}</b> 内有效。",
                reply_markup=InlineKeyboardMarkup(buttons),
                quote=True,
            )
            return

    if SHARE_CODE_PATTERN.match(parameter):
        await handle_share_code(client, message, parameter)
        return

    await handle_link_access(client, message, parameter)


@Bot.on_message(filters.command("start") & filters.private & subscribed & not_banned, group=2)
async def start_command(client: Client, message: Message) -> None:
    await store.add_user(client.tenant.tenant_id, message.from_user.id)
    if len(message.command) > 1 and message.command[1] != "check":
        await _handle_parameter(client, message, message.command[1])
        return

    await message.reply(
        _welcome_text(client, message),
        reply_markup=build_start_buttons(client),
        quote=True,
        disable_web_page_preview=True,
    )


@Bot.on_message(filters.command("help") & filters.private & subscribed & not_banned, group=2)
async def help_command(client: Client, message: Message) -> None:
    text = str(
        client.tenant.setting(
            "admin_help_text" if client.tenant.is_admin(message.from_user.id) else "help_text",
            "发送分享码即可获取对应文件。",
        )
    )
    await message.reply(text, quote=True, disable_web_page_preview=True)


@Bot.on_message(
    filters.command(["start", "help", "id", "ping", "myshares"]) & filters.private & ~subscribed,
    group=3,
)
async def not_joined(client: Client, message: Message) -> None:
    await store.add_user(client.tenant.tenant_id, message.from_user.id)
    await send_force_sub_prompt(client, message)


@Bot.on_message(
    filters.private
    & not_banned
    & ~filters.command(
        [
            "start", "help", "about", "ban", "unban", "banned",
            "broadcast", "users", "stats", "share", "myshares",
            "batch", "genlink", "ping", "backup", "redeem", "tx",
        ]
    ),
    group=4,
)
async def handle_private_text_or_cdkey(client: Client, message: Message) -> None:
    text = (message.text or "").strip()
    if not text:
        return

    # 允许尚未付费解锁的用户直接在对话框粘贴 CDK-XXXX 卡密完成兑换
    if CDKEY_INPUT_PATTERN.match(text):
        await _process_cdkey_redemption(client, message, text)
        return

    # 检查强制订阅（含频道发言要求）或付费解锁门禁
    if not await is_subscribed(None, client, message):
        await send_force_sub_prompt(client, message)
        return

    if SHARE_CODE_PATTERN.match(text):
        await handle_share_code(client, message, text)
        return

    reply_text = str(
        client.tenant.setting(
            "user_reply_text",
            "请发送有效的分享码获取文件，或发送 /help 查看帮助。",
        )
    )
    await message.reply(reply_text, quote=True)
