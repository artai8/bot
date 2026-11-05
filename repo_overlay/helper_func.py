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
from urllib.parse import quote_plus

from pyrogram import filters
from pyrogram.enums import ChatMemberStatus
from pyrogram.errors import FloodWait
from pyrogram.errors.exceptions.bad_request_400 import UserNotParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from shortzy import Shortzy

from database.database import store
from delivery import get_messages as fetch_messages

logger = logging.getLogger(__name__)


async def is_admin(_filter: Any, client: Any, update: Any) -> bool:
    if not getattr(update, "from_user", None):
        return False
    return bool(client.tenant.is_admin(update.from_user.id))


async def is_not_banned(_filter: Any, client: Any, update: Any) -> bool:
    user = getattr(update, "from_user", None)
    if not user:
        return True
    if client.tenant.is_admin(user.id):
        return True
    ban_record = await store.is_banned(client.tenant.tenant_id, user.id)
    return ban_record is None


async def is_subscribed(_filter: Any, client: Any, update: Any) -> bool:
    """
    统一门禁校验过滤器：
    1. 当配置了 `force_sub_channels`（绑定了强制订阅频道）时：
       - 校验用户是否已加入所有指定频道/群组；
       - 若开启了 `force_sub_require_msg`（需发送一条任意消息才能解锁），
         额外校验该用户是否已在要求的频道/关联群组中发送过至少一条消息。
    2. 当未配置 `force_sub_channels`（不绑定强制订阅频道）时：
       - 若开启了 `pay_to_unlock`（需要付费才能解锁），校验用户是否已完成任一付费方式解锁且在有效期内。
    """
    user = getattr(update, "from_user", None)
    if not user:
        return True

    user_id = user.id
    if client.tenant.is_admin(user_id):
        return True

    tenant_id = client.tenant.tenant_id
    channels = [int(ch) for ch in client.tenant.setting("force_sub_channels", []) if ch]

    # -------------------------------------------------------------------------
    # 分支 A：已绑定强制订阅频道 -> 校验订阅状态 + 可选「发送任意消息」条件
    # -------------------------------------------------------------------------
    if channels:
        require_msg = bool(client.tenant.setting("force_sub_require_msg", False))
        fsub_status = await store.get_force_sub_status(tenant_id, user_id)
        msg_sent_channels = {int(c) for c in fsub_status.get("msg_sent_channels", [])}

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
                    tenant_id,
                    user_id,
                    {
                        "verified": False,
                        "verified_at": time.time(),
                        "channels": channels,
                        "msg_sent_channels": list(msg_sent_channels),
                        "block_reason": "not_joined",
                    },
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

        # 若开启了「需要在强制订阅的频道发送一条任意消息才能解锁」
        if require_msg:
            missing_msg_channels = [ch for ch in channels if ch not in msg_sent_channels]
            if missing_msg_channels:
                await store.update_force_sub_status(
                    tenant_id,
                    user_id,
                    {
                        "verified": False,
                        "verified_at": time.time(),
                        "channels": channels,
                        "msg_sent_channels": list(msg_sent_channels),
                        "block_reason": "msg_required",
                    },
                )
                return False

        await store.update_force_sub_status(
            tenant_id,
            user_id,
            {
                "verified": True,
                "verified_at": time.time(),
                "channels": channels,
                "msg_sent_channels": list(msg_sent_channels),
                "block_reason": None,
            },
        )
        return True

    # -------------------------------------------------------------------------
    # 分支 B：未绑定强制订阅频道 -> 检查是否开启「需要付费才能解锁」
    # -------------------------------------------------------------------------
    pay_to_unlock = bool(client.tenant.setting("pay_to_unlock", False))
    if pay_to_unlock:
        expire_seconds = int(client.tenant.setting("pay_unlock_expire", 0) or 0)
        is_unlocked = await store.is_paid_unlocked(
            tenant_id, user_id, expire_seconds=expire_seconds
        )
        return is_unlocked

    return True


async def send_force_sub_prompt(client: Any, message: Any) -> None:
    """
    统一拦截提示分发器：
    - 若未绑定强制订阅频道且开启了 `pay_to_unlock`，展示多方式付费解锁面板；
    - 若绑定了强制订阅频道，展示频道加入按钮，并在开启 `force_sub_require_msg` 时明确提示用户在频道内发送一条任意消息。
    """
    channels = [int(ch) for ch in client.tenant.setting("force_sub_channels", []) if ch]
    pay_to_unlock = bool(client.tenant.setting("pay_to_unlock", False))

    if not channels and pay_to_unlock:
        await send_pay_unlock_prompt(client, message)
        return

    user = message.from_user
    tenant_id = client.tenant.tenant_id
    require_msg = bool(client.tenant.setting("force_sub_require_msg", False))
    fsub_status = await store.get_force_sub_status(tenant_id, user.id)
    msg_sent_channels = {int(c) for c in fsub_status.get("msg_sent_channels", [])}

    buttons: list[list[InlineKeyboardButton]] = []
    invitelinks = getattr(client, "invitelinks", {}) or {}

    for index, (ch_id, link) in enumerate(invitelinks.items(), 1):
        if not link:
            continue
        if require_msg and int(ch_id) in msg_sent_channels:
            btn_label = f"✅ 频道 {index} (已发言)"
        elif require_msg:
            btn_label = f"💬 加入频道 {index} 并发送一条消息"
        else:
            btn_label = f"📢 加入频道 {index}"
        buttons.append([InlineKeyboardButton(btn_label, url=link)])

    start_arg = ""
    if getattr(message, "command", None) and len(message.command) > 1:
        start_arg = message.command[1]
    elif getattr(message, "text", None) and not message.text.startswith("/"):
        start_arg = message.text.strip()

    retry_url = (
        f"https://t.me/{client.username}?start={start_arg}"
        if start_arg
        else f"https://t.me/{client.username}?start=check"
    )
    buttons.append([InlineKeyboardButton("🔄 我已完成，点击重试", url=retry_url)])

    if not buttons:
        await message.reply("⚠️ 管理员尚未正确配置强制加入频道。", quote=True)
        return

    base_template = str(
        client.tenant.setting(
            "force_sub_message",
            "你好 {first}\n\n<b>使用本机器人前，请先完成以下频道订阅要求：</b>",
        )
    )
    text = base_template.format(
        first=user.first_name or "",
        last=user.last_name or "",
        username=None if not user.username else "@" + user.username,
        mention=user.mention,
        id=user.id,
    )

    if require_msg:
        extra_prompt = str(
            client.tenant.setting(
                "force_sub_msg_prompt",
                "\n\n💬 <b>发言解锁要求：</b>加入上述频道/关联群组后，请在其中<b>发送任意一条消息</b>，随后点击下方「🔄 我已完成，点击重试」即可解锁。",
            )
        )
        text = f"{text}{extra_prompt}"

    await message.reply(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        quote=True,
        disable_web_page_preview=True,
    )


async def send_pay_unlock_prompt(
    client: Any, message: Any, start_arg: str | None = None
) -> None:
    """
    当未绑定强制订阅频道且启用 `pay_to_unlock` 时，向用户发送支持多种支付方式的付费解锁面板：
    1. Telegram Stars 官方星币直付（自动回调秒解锁）
    2. USDT (TRC20 / BEP20) 链上转账 + 交易哈希核验
    3. 外部收银台 / 聚合支付跳转（微信 / 支付宝 / Stripe / 发卡网）
    4. 卡密 / 激活码 (CDKey) 直接兑换解锁
    """
    user = message.from_user
    if start_arg is None:
        if getattr(message, "command", None) and len(message.command) > 1:
            start_arg = message.command[1]
        elif getattr(message, "text", None) and not message.text.startswith("/"):
            start_arg = message.text.strip()
        else:
            start_arg = ""

    stars_amount = int(client.tenant.setting("pay_amount_stars", 0) or 0)
    usdt_amount = str(client.tenant.setting("pay_amount_usdt", "") or "").strip()
    usdt_address = str(client.tenant.setting("pay_usdt_address", "") or "").strip()
    usdt_network = str(client.tenant.setting("pay_usdt_network", "TRC20") or "TRC20").strip()
    custom_url = str(client.tenant.setting("pay_custom_url", "") or "").strip()
    enable_cdkey = bool(client.tenant.setting("pay_enable_cdkey", True))
    expire_seconds = int(client.tenant.setting("pay_unlock_expire", 0) or 0)

    buttons: list[list[InlineKeyboardButton]] = []

    # 方式 1：Telegram Stars 原生支付
    if stars_amount > 0:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"⭐ Telegram Stars 支付 ({stars_amount} Stars)",
                    callback_data=f"pay_stars:{start_arg[:32]}",
                )
            ]
        )

    # 方式 2：USDT 加密货币转账
    if usdt_amount and usdt_address:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"💎 USDT ({usdt_network}) 支付 ({usdt_amount} USDT)",
                    callback_data=f"pay_usdt:{start_arg[:32]}",
                )
            ]
        )

    # 方式 3：第三方在线收银台 / 聚合支付链接
    if custom_url:
        formatted_url = (
            custom_url.replace("{user_id}", str(user.id))
            .replace("{tenant_id}", quote_plus(str(client.tenant.tenant_id)))
            .replace("{username}", quote_plus(user.username or ""))
        )
        buttons.append(
            [InlineKeyboardButton("💳 在线收银台支付 (微信/支付宝/发卡)", url=formatted_url)]
        )

    # 方式 4：卡密 / 激活码兑换
    if enable_cdkey:
        buttons.append(
            [
                InlineKeyboardButton(
                    "🎫 输入卡密 / 激活码兑换",
                    callback_data=f"pay_cdkey:{start_arg[:32]}",
                )
            ]
        )

    retry_cb = f"pay_check:{start_arg[:32]}"
    buttons.append([InlineKeyboardButton("🔄 我已完成支付，点击验证状态", callback_data=retry_cb)])

    base_msg = str(
        client.tenant.setting(
            "pay_unlock_message",
            "🔒 <b>当前资源需付费解锁后访问</b>\n\n请选择以下任意一种方式完成解锁：",
        )
    ).format(
        first=user.first_name or "",
        last=user.last_name or "",
        username=None if not user.username else "@" + user.username,
        mention=user.mention,
        id=user.id,
    )

    validity_line = (
        f"\n• 解锁有效期：<b>{get_exp_time(expire_seconds)}</b>"
        if expire_seconds > 0
        else "\n• 解锁有效期：<b>永久有效</b>"
    )
    uid_line = f"\n• 您的用户 ID：<code>{user.id}</code>"

    await message.reply(
        f"{base_msg}\n{validity_line}{uid_line}",
        reply_markup=InlineKeyboardMarkup(buttons),
        quote=True,
        disable_web_page_preview=True,
    )


async def get_verify_status(client: Any, user_id: int) -> dict[str, Any]:
    return await store.get_verify_status(client.tenant.tenant_id, user_id)


async def update_verify_status(
    client: Any,
    user_id: int,
    verify_token: str = "",
    is_verified: bool = False,
    verified_time: float = 0.0,
    link: str = "",
) -> None:
    current = await store.get_verify_status(client.tenant.tenant_id, user_id)
    current.update(
        {
            "verify_token": verify_token or current.get("verify_token", ""),
            "is_verified": bool(is_verified),
            "verified_time": float(verified_time or time.time()),
            "link": link or current.get("link", ""),
        }
    )
    await store.update_verify_status(client.tenant.tenant_id, user_id, current)


async def get_shortlink(url: str, api: str, link: str) -> str:
    if not url or not api:
        return link
    shortzy = Shortzy(api_key=api, base_site=url)
    return await shortzy.convert(link)


def get_exp_time(seconds: int) -> str:
    periods = [("天", 86400), ("小时", 3600), ("分钟", 60), ("秒", 1)]
    parts: list[str] = []
    remaining = max(0, int(seconds))
    for label, unit in periods:
        if remaining >= unit:
            val, remaining = divmod(remaining, unit)
            parts.append(f"{val}{label}")
    return "".join(parts) or "0秒"


def get_readable_time(seconds: int) -> str:
    return get_exp_time(seconds)


def parse_buttons(value: str) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    if not value or not value.strip():
        return rows
    for line in value.strip().splitlines():
        row: list[InlineKeyboardButton] = []
        for seg in line.split("|"):
            if "-" not in seg:
                continue
            text, url = seg.split("-", 1)
            text, url = text.strip(), url.strip()
            if text and url:
                row.append(InlineKeyboardButton(text, url=url))
        if row:
            rows.append(row)
    return rows


def encode(string_val: str) -> str:
    return base64.urlsafe_b64encode(string_val.encode("utf-8")).decode("ascii").rstrip("=")


def decode(base64_string: str) -> str:
    padding = "=" * (-len(base64_string) % 4)
    return base64.urlsafe_b64decode((base64_string + padding).encode("ascii")).decode("utf-8")


admin_only = filters.create(is_admin)
subscribed = filters.create(is_subscribed)
not_banned = filters.create(is_not_banned)
