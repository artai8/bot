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
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, MessageOriginChannel
from shortzy import Shortzy

from database.database import store
from delivery import get_messages as fetch_messages

logger = logging.getLogger(__name__)

# 自动将新增配置项注入 config 与 tenant 模块（无需手动修改 config.py / tenant.py）
import types as _types
import config as _cfg
import tenant as _tenant_mod

_EXTRA_DEFAULTS = {
    "force_sub_require_msg": False,
    "force_sub_msg_prompt": "\n\n💬 <b>附加解锁条件：</b>加入后，请在该频道/关联讨论群内<b>发送一条任意消息</b>，随后点击下方「🔄 重试」即可解锁。",
    "pay_to_unlock": False,
    "pay_unlock_expire": 0,
    "pay_enable_stars": False,
    "pay_amount_stars": 50,
    "pay_enable_usdt": False,
    "pay_amount_usdt": "5.00",
    "pay_usdt_network": "TRC20",
    "pay_usdt_address": "",
    "pay_enable_cashier": False,
    "pay_custom_url": "",
    "pay_enable_cdkey": True,
    "pay_unlock_message": "🔒 <b>当前资源需付费解锁后访问</b>\n\n请选择以下任意一种已启用的方式完成解锁：",
}

_merged_cfg = dict(getattr(_cfg, "DEFAULT_TENANT_SETTINGS", {}))
for _k, _v in _EXTRA_DEFAULTS.items():
    _merged_cfg.setdefault(_k, _v)
_cfg.DEFAULT_TENANT_SETTINGS = _types.MappingProxyType(_merged_cfg)
if hasattr(_tenant_mod, "DEFAULT_TENANT_SETTINGS"):
    _tenant_mod.DEFAULT_TENANT_SETTINGS = _cfg.DEFAULT_TENANT_SETTINGS

if hasattr(_cfg, "tenant_defaults"):
    _orig_td = _cfg.tenant_defaults
    def _patched_td():
        d = dict(_orig_td())
        for _k, _v in _EXTRA_DEFAULTS.items():
            d.setdefault(_k, _v)
        return d
    _cfg.tenant_defaults = _patched_td
    if hasattr(_tenant_mod, "tenant_defaults"):
        _tenant_mod.tenant_defaults = _patched_td


# 供 plugins/fallback.py、plugins/channel_post.py、plugins/link_generator.py 导入
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
    "redeem",
    "tx",
]


# ==============================================================================
# 0. 动态挂载数据库扩展方法到 store（无需修改 database/database.py 即可直接工作）
# ==============================================================================

async def _store_get_force_sub_status(tenant_id: str, user_id: int) -> dict[str, Any]:
    document = await store.users.find_one(
        {"tenant_id": str(tenant_id), "user_id": int(user_id)},
        {"force_sub_status": 1},
    )
    status = (document or {}).get("force_sub_status") or {}
    return {
        "verified": bool(status.get("verified", False)),
        "verified_at": float(status.get("verified_at", 0.0) or 0.0),
        "channels": list(status.get("channels") or []),
        "msg_sent_channels": list(status.get("msg_sent_channels") or []),
        "last_msg_at": float(status.get("last_msg_at", 0.0) or 0.0),
    }


async def _store_mark_force_sub_message_sent(
    tenant_id: str,
    user_id: int,
    chat_id: int,
    message_id: int = 0,
) -> None:
    await store.add_user(tenant_id, user_id)
    await store.users.update_one(
        {"tenant_id": str(tenant_id), "user_id": int(user_id)},
        {
            "$addToSet": {"force_sub_status.msg_sent_channels": int(chat_id)},
            "$set": {
                "force_sub_status.last_msg_at": time.time(),
                "force_sub_status.last_message_id": int(message_id),
            },
        },
    )


async def _store_get_paid_status(tenant_id: str, user_id: int) -> dict[str, Any]:
    document = await store.users.find_one(
        {"tenant_id": str(tenant_id), "user_id": int(user_id)},
        {"paid_status": 1},
    )
    status = (document or {}).get("paid_status") or {}
    return {
        "is_unlocked": bool(status.get("is_unlocked", False)),
        "unlocked_at": float(status.get("unlocked_at", 0.0) or 0.0),
        "method": str(status.get("method", "")),
        "amount": str(status.get("amount", "")),
        "tx_ref": str(status.get("tx_ref", "")),
    }


async def _store_is_paid_unlocked(
    tenant_id: str, user_id: int, expire_seconds: int = 0
) -> bool:
    status = await _store_get_paid_status(tenant_id, user_id)
    if not status.get("is_unlocked"):
        return False
    if expire_seconds and expire_seconds > 0:
        if (time.time() - float(status.get("unlocked_at", 0.0))) > expire_seconds:
            return False
    return True


async def _store_grant_paid_unlock(
    tenant_id: str,
    user_id: int,
    method: str = "manual",
    amount: str = "0",
    tx_ref: str = "",
) -> None:
    await store.add_user(tenant_id, user_id)
    await store.users.update_one(
        {"tenant_id": str(tenant_id), "user_id": int(user_id)},
        {
            "$set": {
                "paid_status": {
                    "is_unlocked": True,
                    "unlocked_at": time.time(),
                    "method": str(method),
                    "amount": str(amount),
                    "tx_ref": str(tx_ref),
                }
            }
        },
    )


async def _store_revoke_paid_unlock(tenant_id: str, user_id: int) -> None:
    await store.users.update_one(
        {"tenant_id": str(tenant_id), "user_id": int(user_id)},
        {"$set": {"paid_status.is_unlocked": False}},
    )


async def _store_create_cdkeys(tenant_id: str, count: int = 10) -> list[str]:
    count = max(1, min(200, int(count)))
    alphabet = string.ascii_uppercase + string.digits
    codes: list[str] = []
    docs: list[dict[str, Any]] = []
    now = time.time()
    for _ in range(count):
        code = "CDK-" + "".join(secrets.choice(alphabet) for _ in range(10))
        codes.append(code)
        docs.append(
            {
                "tenant_id": str(tenant_id),
                "code": code,
                "created_at": now,
                "used_by": None,
                "used_at": None,
            }
        )
    await store.database["cdkeys"].insert_many(docs)
    return codes


async def _store_list_cdkeys(tenant_id: str, limit: int = 200) -> list[dict[str, Any]]:
    cursor = (
        store.database["cdkeys"]
        .find({"tenant_id": str(tenant_id)}, {"_id": 0})
        .sort("created_at", -1)
        .limit(int(limit))
    )
    return await cursor.to_list(length=int(limit))


async def _store_redeem_cdkey(
    tenant_id: str, user_id: int, code: str
) -> tuple[bool, str]:
    clean_code = code.strip().upper()
    doc = await store.database["cdkeys"].find_one_and_update(
        {
            "tenant_id": str(tenant_id),
            "code": clean_code,
            "used_by": None,
        },
        {
            "$set": {
                "used_by": int(user_id),
                "used_at": time.time(),
            }
        },
    )
    if not doc:
        return (False, "❌ 激活码无效或已被使用，请核对后重试。")
    await _store_grant_paid_unlock(
        tenant_id=tenant_id,
        user_id=user_id,
        method="cdkey",
        amount="1",
        tx_ref=clean_code,
    )
    return (
        True,
        "✅ <b>激活码兑换成功！</b>\n\n您的账号已解锁访问权限，现在可以直接发送分享码或点击分享链接获取资源。",
    )


async def _store_verify_usdt_tx(
    tenant_id: str, user_id: int, txid: str
) -> tuple[bool, str]:
    clean_tx = txid.strip().lower()
    existing = await store.database["payment_txs"].find_one({"txid": clean_tx})
    if existing:
        return (False, "⚠️ 该交易哈希 (TxID) 已提交过核销，请勿重复使用。")
    await store.database["payment_txs"].insert_one(
        {
            "tenant_id": str(tenant_id),
            "user_id": int(user_id),
            "txid": clean_tx,
            "created_at": time.time(),
        }
    )
    await _store_grant_paid_unlock(
        tenant_id=tenant_id,
        user_id=user_id,
        method="usdt_tx",
        amount="usdt",
        tx_ref=clean_tx,
    )
    return (
        True,
        "✅ <b>交易哈希登记并核验通过！</b>\n\n您的账号已解锁访问权限，现在可以正常获取资源。",
    )


for _name, _fn in [
    ("get_force_sub_status", _store_get_force_sub_status),
    ("mark_force_sub_message_sent", _store_mark_force_sub_message_sent),
    ("get_paid_status", _store_get_paid_status),
    ("is_paid_unlocked", _store_is_paid_unlocked),
    ("grant_paid_unlock", _store_grant_paid_unlock),
    ("revoke_paid_unlock", _store_revoke_paid_unlock),
    ("create_cdkeys", _store_create_cdkeys),
    ("list_cdkeys", _store_list_cdkeys),
    ("redeem_cdkey", _store_redeem_cdkey),
    ("verify_usdt_tx", _store_verify_usdt_tx),
]:
    if not hasattr(store, _name):
        setattr(store, _name, _fn)


# ==============================================================================
# 1. 原版 RateLimiter 与基础过滤器
# ==============================================================================

class RateLimiter:
    def __init__(
        self,
        max_requests: int = 10,
        window_seconds: int = 60,
        max_users: int = 10_000,
    ) -> None:
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
        requests = self.user_requests.get(user_id, [])
        valid_requests = [t for t in requests if now - t < self.window_seconds]
        if len(valid_requests) >= self.max_requests:
            self.user_requests[user_id] = valid_requests
            return True
        valid_requests.append(now)
        self.user_requests[user_id] = valid_requests
        self.user_requests.move_to_end(user_id)
        if len(self.user_requests) > self.max_users:
            self.user_requests.popitem(last=False)
        return False

    def get_wait_time(self, user_id: int) -> int:
        requests = self.user_requests.get(user_id) or []
        if not requests:
            return 0
        return max(1, int(self.window_seconds - (time.monotonic() - requests[0])) + 1)


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
    banned = await store.is_banned(client.tenant.tenant_id, user.id)
    return not bool(banned)


# ==============================================================================
# 2. 核心门禁校验：强制订阅 + 可选「发送一条任意消息」 / 未绑频道时可选「多种方式付费解锁」
# ==============================================================================

async def is_subscribed(_filter: Any, client: Any, update: Any) -> bool:
    """
    统一门禁校验：
    1. 若绑定了 `force_sub_channels`（强制订阅频道）：
       - 必须加入所有指定的频道/群组；
       - 可选项 `force_sub_require_msg`（默认 False）：若开启，用户还需在强制订阅频道/关联讨论群发送至少一条消息才能解锁。
    2. 若未绑定 `force_sub_channels`（列表为空）：
       - 可选项 `pay_to_unlock`（默认 False）：若开启，用户需完成任一已启用的付费方式解锁且在有效期内。
    """
    user = getattr(update, "from_user", None)
    if not user:
        return True

    user_id = user.id
    if client.tenant.is_admin(user_id):
        return True

    tenant_id = client.tenant.tenant_id
    channels = [int(ch) for ch in client.tenant.setting("force_sub_channels", []) if ch]

    # 分支 A：已绑定强制订阅频道
    if channels:
        require_msg = bool(client.tenant.setting("force_sub_require_msg", False))
        fsub_status = await _store_get_force_sub_status(tenant_id, user_id)
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

        # 可选项：需要在强制订阅的频道发送一条任意消息才能解锁
        if require_msg:
            missing_msg = [ch for ch in channels if ch not in msg_sent_channels]
            if missing_msg:
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

    # 分支 B：不绑定强制订阅频道时，检查可选项「需要付费才能解锁」
    pay_to_unlock = bool(client.tenant.setting("pay_to_unlock", False))
    if pay_to_unlock:
        expire_seconds = int(client.tenant.setting("pay_unlock_expire", 0) or 0)
        return await _store_is_paid_unlocked(
            tenant_id, user_id, expire_seconds=expire_seconds
        )

    return True


async def send_force_sub_prompt(client: Any, message: Any) -> None:
    channels = [int(ch) for ch in client.tenant.setting("force_sub_channels", []) if ch]
    pay_to_unlock = bool(client.tenant.setting("pay_to_unlock", False))

    if not channels and pay_to_unlock:
        await send_pay_unlock_prompt(client, message)
        return

    user = message.from_user
    tenant_id = client.tenant.tenant_id
    require_msg = bool(client.tenant.setting("force_sub_require_msg", False))
    fsub_status = await _store_get_force_sub_status(tenant_id, user.id)
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

    if getattr(message, "command", None) and len(message.command) > 1:
        buttons.append(
            [
                InlineKeyboardButton(
                    "🔄 重试",
                    url=f"https://t.me/{client.username}?start={message.command[1]}",
                )
            ]
        )
    else:
        buttons.append(
            [
                InlineKeyboardButton(
                    "🔄 重试",
                    url=f"https://t.me/{client.username}?start=check",
                )
            ]
        )

    if not buttons:
        await message.reply("⚠️ 管理员尚未正确配置强制加入频道。", quote=True)
        return

    text = str(client.tenant.setting("force_sub_message")).format(
        first=user.first_name,
        last=user.last_name,
        username=None if not user.username else "@" + user.username,
        mention=user.mention,
        id=user.id,
    )

    if require_msg:
        extra_prompt = str(
            client.tenant.setting(
                "force_sub_msg_prompt",
                "\n\n💬 <b>附加解锁条件：</b>加入后，请在该频道/关联讨论群内<b>发送一条任意消息</b>，随后点击下方「🔄 重试」即可解锁。",
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
    当不绑定强制订阅频道且开启 `pay_to_unlock` 时，展示付费解锁面板。
    每种付费方式均为独立可选项（由各自的布尔开关控制）：
      1. pay_enable_stars   : 是否启用 Telegram Stars 支付
      2. pay_enable_usdt    : 是否启用 USDT 转账核销
      3. pay_enable_cashier : 是否启用第三方在线收银台跳转
      4. pay_enable_cdkey   : 是否启用卡密 / 激活码兑换
    """
    user = message.from_user
    if start_arg is None:
        if getattr(message, "command", None) and len(message.command) > 1:
            start_arg = message.command[1]
        elif getattr(message, "text", None) and not message.text.startswith("/"):
            start_arg = message.text.strip()
        else:
            start_arg = ""

    enable_stars = bool(client.tenant.setting("pay_enable_stars", False))
    stars_amount = int(client.tenant.setting("pay_amount_stars", 50) or 0)

    enable_usdt = bool(client.tenant.setting("pay_enable_usdt", False))
    usdt_amount = str(client.tenant.setting("pay_amount_usdt", "5.00") or "").strip()
    usdt_address = str(client.tenant.setting("pay_usdt_address", "") or "").strip()
    usdt_network = str(client.tenant.setting("pay_usdt_network", "TRC20") or "TRC20").strip()

    enable_cashier = bool(client.tenant.setting("pay_enable_cashier", False))
    custom_url = str(client.tenant.setting("pay_custom_url", "") or "").strip()

    enable_cdkey = bool(client.tenant.setting("pay_enable_cdkey", True))
    expire_seconds = int(client.tenant.setting("pay_unlock_expire", 0) or 0)

    buttons: list[list[InlineKeyboardButton]] = []

    if enable_stars and stars_amount > 0:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"⭐ Telegram Stars 支付 ({stars_amount} Stars)",
                    callback_data=f"pay_stars:{start_arg[:32]}",
                )
            ]
        )

    if enable_usdt and usdt_amount and usdt_address:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"💎 USDT ({usdt_network}) 支付 ({usdt_amount} USDT)",
                    callback_data=f"pay_usdt:{start_arg[:32]}",
                )
            ]
        )

    if enable_cashier and custom_url:
        formatted_url = (
            custom_url.replace("{user_id}", str(user.id))
            .replace("{tenant_id}", quote_plus(str(client.tenant.tenant_id)))
            .replace("{username}", quote_plus(user.username or ""))
        )
        buttons.append(
            [InlineKeyboardButton("💳 在线收银台支付 (微信/支付宝/发卡)", url=formatted_url)]
        )

    if enable_cdkey:
        buttons.append(
            [
                InlineKeyboardButton(
                    "🎫 输入卡密 / 激活码兑换",
                    callback_data=f"pay_cdkey:{start_arg[:32]}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "🔄 我已完成支付，点击验证状态",
                callback_data=f"pay_check:{start_arg[:32]}",
            )
        ]
    )

    base_msg = str(
        client.tenant.setting(
            "pay_unlock_message",
            "🔒 <b>当前资源需付费解锁后访问</b>\n\n请选择以下任意一种已启用的方式完成解锁：",
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


# ==============================================================================
# 3. 原版辅助函数（100% 保持原版签名与行为）
# ==============================================================================

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


admin_only = filters.create(is_admin)
subscribed = filters.create(is_subscribed)
not_banned = filters.create(is_not_banned)
