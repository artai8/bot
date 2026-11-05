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
    ALL_COMMANDS,
    admin_only,
    decode,
    get_exp_time,
    get_shortlink,
    get_verify_status,
    is_subscribed,
    not_banned,
    parse_buttons,
    send_force_sub_prompt,
    subscribed,
    update_verify_status,
)
from plugins.share import handle_share_code

logger = logging.getLogger(__name__)

SHARE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{6,12}$")
CDKEY_INPUT_PATTERN = re.compile(r"^CDK-[A-Za-z0-9]{6,24}$", re.IGNORECASE)
TXID_INPUT_PATTERN = re.compile(r"^(?:0x)?[a-fA-F0-9]{64}$")


# ==============================================================================
# 1. 强制订阅频道/关联讨论群「发送一条任意消息解锁」实时监听器
# ==============================================================================

@Bot.on_message((filters.group | filters.channel) & ~filters.bot, group=-1)
async def record_force_sub_channel_message(client: Client, message: Message) -> None:
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
        linked_map = getattr(client, "linked_discussion_map", {}) or {}
        if chat_id in linked_map:
            matched_channels.append(int(linked_map[chat_id]))
        else:
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
# 2. 多种可选付费解锁方式回调与命令处理
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
                await _handle_parameter(client, query.message, start_arg)
        else:
            await query.answer("尚未检测到有效解锁记录，请先完成任一已启用方式的支付或兑换卡密。", show_alert=True)
        return

    if action == "stars":
        if not bool(client.tenant.setting("pay_enable_stars", False)):
            await query.answer("当前未启用 Telegram Stars 支付", show_alert=True)
            return
        stars_amount = int(client.tenant.setting("pay_amount_stars", 50) or 0)
        if stars_amount <= 0:
            await query.answer("Telegram Stars 价格未配置", show_alert=True)
            return
        await query.answer()
        payload = f"unlock:{tenant_id}:{user.id}:{start_arg}:{int(time.time())}"
        try:
            await client.send_invoice(
                chat_id=user.id,
                title="解锁机器人访问权限",
                description=f"使用 {stars_amount} Telegram Stars 解锁当前机器人资源访问权限",
                payload=payload,
                currency="XTR",
                prices=[LabeledPrice(label="访问权限解锁", amount=stars_amount)],
            )
        except Exception as exc:
            logger.error("[%s] Failed to send Stars invoice: %s", client.tenant.slug, exc)
            await query.message.reply("⚠️ 发起 Telegram Stars 账单失败，请改用其他已启用的支付方式。")
        return

    if action == "usdt":
        if not bool(client.tenant.setting("pay_enable_usdt", False)):
            await query.answer("当前未启用 USDT 支付", show_alert=True)
            return
        usdt_amount = str(client.tenant.setting("pay_amount_usdt", "5.00") or "").strip()
        usdt_address = str(client.tenant.setting("pay_usdt_address", "") or "").strip()
        usdt_network = str(client.tenant.setting("pay_usdt_network", "TRC20") or "TRC20").strip()
        if not usdt_amount or not usdt_address:
            await query.answer("管理员尚未配置 USDT 收款地址", show_alert=True)
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
        if not bool(client.tenant.setting("pay_enable_cdkey", True)):
            await query.answer("当前未启用卡密兑换解锁", show_alert=True)
            return
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
        await _handle_parameter(client, message, parts[3])


@Bot.on_message(filters.command("redeem") & filters.private & not_banned, group=1)
async def redeem_command(client: Client, message: Message) -> None:
    if not bool(client.tenant.setting("pay_enable_cdkey", True)):
        await message.reply("⚠️ 当前未开启卡密兑换功能。", quote=True)
        return
    if len(message.command) < 2:
        await message.reply("用法：<code>/redeem 您的卡密</code>（例如 <code>/redeem CDK-8F92A1B0</code>）", quote=True)
        return
    code = message.command[1].strip()
    _, msg = await store.redeem_cdkey(
        tenant_id=client.tenant.tenant_id,
        user_id=message.from_user.id,
        code=code,
    )
    await message.reply(msg, quote=True)


@Bot.on_message(filters.command("tx") & filters.private & not_banned, group=1)
async def verify_tx_command(client: Client, message: Message) -> None:
    if not bool(client.tenant.setting("pay_enable_usdt", False)):
        await message.reply("⚠️ 当前未开启 USDT 交易哈希核验功能。", quote=True)
        return
    if len(message.command) < 2:
        await message.reply("用法：<code>/tx 您的64位USDT交易哈希(TxID)</code>", quote=True)
        return
    txid = message.command[1].strip()
    if not TXID_INPUT_PATTERN.match(txid):
        await message.reply("⚠️ 交易哈希 (TxID) 格式无效，请提供完整的 64 位十六进制哈希。", quote=True)
        return
    _, msg = await store.verify_usdt_tx(
        tenant_id=client.tenant.tenant_id,
        user_id=message.from_user.id,
        txid=txid,
    )
    await message.reply(msg, quote=True)


# ==============================================================================
# 3. 原版 /start 与内置命令完整实现（100% 保留 Commit 0ac32a7 原版逻辑）
# ==============================================================================

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
        if len(message.command) > 1 and message.command[1] != "check":
            await _handle_parameter(client, message, message.command[1])
        else:
            await message.reply_text(
                _welcome_text(client, message),
                reply_markup=build_start_buttons(client),
                disable_web_page_preview=True,
                quote=True,
            )
        return

    if hasattr(client, "rate_limiter") and client.rate_limiter.is_limited(user_id):
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
            f"🔐 请先完成验证。\n\n⏱️ 验证有效期：{get_exp_time(expire)}",
            reply_markup=InlineKeyboardMarkup(buttons),
            quote=True,
        )

    if len(message.command) > 1 and message.command[1] != "check":
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
    await store.add_user(client.tenant.tenant_id, message.from_user.id)
    await send_force_sub_prompt(client, message)


@Bot.on_message(
    filters.command(["start", "help", "id", "ping", "myshares"]) & filters.private & ~not_banned,
    group=1,
)
async def banned_user(client: Client, message: Message):
    await message.reply_text("❌ 你已被封禁，无法使用此 Bot。", quote=True)


@Bot.on_message(filters.command("id") & filters.private & subscribed & not_banned, group=2)
async def id_command(client: Client, message: Message):
    await message.reply_text(f"👤 你的 Telegram ID：`{message.from_user.id}`", quote=True)


@Bot.on_message(filters.command("ping") & filters.private & subscribed & not_banned, group=2)
async def ping_command(client: Client, message: Message):
    start = time.perf_counter()
    reply = await message.reply("🏓 测速中...", quote=True)
    delta = (time.perf_counter() - start) * 1000
    await reply.edit(f"🏓 响应延迟：{delta:.2f} ms")


@Bot.on_message(filters.command("myshares") & filters.private & subscribed & not_banned, group=2)
async def myshares_command(client: Client, message: Message):
    if not client.tenant.is_admin(message.from_user.id):
        await message.reply("❌ 该命令仅管理员可用。", quote=True)
        return
    shares, _ = await store.list_shares(client.tenant.tenant_id, 1, 10)
    if not shares:
        await message.reply("📭 暂无分享记录。", quote=True)
        return
    lines = ["<b>📋 最近 10 条分享：</b>\n"]
    for share in shares:
        title = share.get("title") or share.get("code")
        lines.append(f"• <code>{share.get('code')}</code> - {title}")
    await message.reply("\n".join(lines), quote=True)


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


@Bot.on_callback_query(filters.regex(r"^(about|help|close|pwd_.*)$"))
async def cb_handler(client: Client, query: CallbackQuery):
    data = query.data
    if data == "about":
        owner_id = getattr(client.tenant, "owner_id", 0)
        text = str(client.tenant.setting("about_text", "")).format(owner_id=owner_id)
        await query.message.edit_text(
            text or "文件分享机器人",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ 关闭", callback_data="close")]]),
        )
    elif data == "help":
        key = "admin_help_text" if client.tenant.is_admin(query.from_user.id) else "help_text"
        await query.message.edit_text(
            str(client.tenant.setting(key)),
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ 关闭", callback_data="close")]]),
        )
    elif data.startswith("pwd_"):
        await query.answer("请输入访问密码：", show_alert=True)
    elif data == "close":
        await query.message.delete()


@Bot.on_message(
    filters.private
    & filters.incoming
    & not_banned
    & ~filters.command(ALL_COMMANDS),
    group=1,
)
async def intercept_cdkey_or_unsubscribed(client: Client, message: Message) -> None:
    text = (message.text or "").strip()
    if text and CDKEY_INPUT_PATTERN.match(text) and bool(client.tenant.setting("pay_enable_cdkey", True)):
        _, msg = await store.redeem_cdkey(
            tenant_id=client.tenant.tenant_id,
            user_id=message.from_user.id,
            code=text,
        )
        await message.reply(msg, quote=True)
        message.stop_propagation()
        return

    if not await is_subscribed(None, client, message):
        await send_force_sub_prompt(client, message)
        message.stop_propagation()
