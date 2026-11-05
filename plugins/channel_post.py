from __future__ import annotations

import asyncio
import logging
import re
import secrets
import string
import time

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import Bot
from database.database import store
from delivery import telegram_retry
from helper_func import ALL_COMMANDS, admin_only, generate_share_code

NON_WORD_RE = re.compile(r"[\s\W_]+", re.UNICODE)
LINKED_CACHE_TTL = 600
logger = logging.getLogger(__name__)


def _generate_keywords(text: str) -> list[str]:
    characters = list(dict.fromkeys(NON_WORD_RE.sub("", text or "")))
    if len(characters) >= 4:
        return secrets.SystemRandom().sample(characters, 4)
    for value in string.ascii_letters + string.digits:
        if value not in characters:
            characters.append(value)
        if len(characters) == 4:
            break
    return characters


async def _linked_channel_id(client: Client, chat_id: int):
    cached_at = client.linked_cache_ts.get(chat_id, 0)
    if time.monotonic() - cached_at < LINKED_CACHE_TTL and chat_id in client.linked_chat_cache:
        return client.linked_chat_cache[chat_id]
    try:
        chat = await telegram_retry(lambda: client.get_chat(chat_id))
        linked = getattr(chat, "linked_chat", None)
        result = linked.id if linked else None
        client.linked_chat_cache[chat_id] = result
        client.linked_cache_ts[chat_id] = time.monotonic()
        return result
    except Exception:
        return client.linked_chat_cache.get(chat_id)


@Bot.on_message(filters.group & filters.incoming & filters.text, group=-1)
async def discussion_keyword_reply(client: Client, message: Message):
    keyword = (message.text or "").strip()
    if not keyword:
        return
    bound = {client.tenant.db_channel_id, *client.tenant.setting("bound_channels", [])}
    linked = await _linked_channel_id(client, message.chat.id)
    if linked not in bound:
        return
    shares = await store.find_shares_by_keyword(client.tenant.tenant_id, keyword, 6)
    if not shares:
        return
    label = str(client.tenant.setting("keyword_button_text", "🔗 获取资源"))
    buttons = [
        [
            InlineKeyboardButton(
                f"{label} {index}",
                url=f"https://t.me/{client.username}?start={share['code']}",
            )
        ]
        for index, share in enumerate(shares, 1)
    ]
    await telegram_retry(
        lambda: message.reply_text(
            f"已获得“{keyword}”相关资源，共 {len(shares)} 条",
            quote=True,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    )


@Bot.on_message(
    filters.private & admin_only & ~filters.command(ALL_COMMANDS),
    group=5,
)
async def collect_share_file(client: Client, message: Message):
    session = client.share_sessions.get(message.from_user.id)
    if not session:
        return
    try:
        stored = await telegram_retry(
            lambda: message.copy(client.db_channel.id, disable_notification=True)
        )
        session["messages"].append(stored.id)
        await message.reply(f"✅ 第 {len(session['messages'])} 个文件已添加到分享中。", quote=True)
    except Exception:
        await message.reply("❌ 添加文件失败。", quote=True)


async def _attach_share_link(client: Client, message: Message, code: str) -> None:
    link = f"https://t.me/{client.username}?start={code}"
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔁 分享链接", url=f"https://telegram.me/share/url?url={link}")]]
    )
    if not client.tenant.setting("disable_channel_button", False):
        try:
            await telegram_retry(lambda: message.edit_reply_markup(markup))
            return
        except Exception as exc:
            logger.debug("[%s] Unable to attach channel markup: %s", client.tenant.slug, exc)
    await telegram_retry(
        lambda: client.send_message(
            client.db_channel.id,
            f"✅ 分享链接：{link}\n分享码：<code>{code}</code>",
            reply_to_message_id=message.id,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 打开链接", url=link)]]),
            disable_web_page_preview=True,
        )
    )


async def _finalize_media_group(client: Client, group_id: str) -> None:
    await asyncio.sleep(2)
    group = client.channel_media_groups.pop(group_id, None)
    messages = sorted((group or {}).get("messages", []), key=lambda item: item.id)
    if not messages:
        return
    tenant_id = client.tenant.tenant_id
    if await store.find_share_by_message_id(tenant_id, messages[0].id):
        return
    text = next(
        (str(item.caption or item.text).strip() for item in messages if item.caption or item.text),
        "",
    )
    existing = (
        await store.find_share_by_group_text(tenant_id, text, owner_id=client.db_channel.id)
        if text
        else None
    )
    ids = [item.id for item in messages]
    if existing:
        await store.merge_share_messages(
            tenant_id,
            existing["code"],
            ids,
            _generate_keywords(text) if not existing.get("keywords") else None,
        )
        code = existing["code"]
    else:
        requested = generate_share_code(int(client.tenant.setting("share_code_length", 8)))
        share = await store.create_share(
            tenant_id,
            requested,
            client.db_channel.id,
            ids,
            title=f"媒体组-{requested}",
            group_text=text,
            keywords=_generate_keywords(text),
            code_length=int(client.tenant.setting("share_code_length", 8)),
        )
        code = share["code"]
        await store.increment_stat(tenant_id, "links_generated")
    await _attach_share_link(client, messages[0], code)


@Bot.on_message(filters.channel & filters.incoming, group=1)
async def new_post(client: Client, message: Message):
    if message.chat.id != client.db_channel.id:
        return
    if message.media_group_id:
        group_id = str(message.media_group_id)
        group = client.channel_media_groups.setdefault(group_id, {"messages": []})
        group["messages"].append(message)
        task = group.get("task")
        if task and not task.done():
            task.cancel()
        group["task"] = client.create_background_task(
            _finalize_media_group(client, group_id)
        )
        return
    tenant_id = client.tenant.tenant_id
    if await store.find_share_by_message_id(tenant_id, message.id):
        return
    text = str(message.caption or message.text or "").strip()
    requested = generate_share_code(int(client.tenant.setting("share_code_length", 8)))
    share = await store.create_share(
        tenant_id,
        requested,
        client.db_channel.id,
        [message.id],
        title=f"文件-{requested}",
        group_text=text,
        keywords=_generate_keywords(text),
        code_length=int(client.tenant.setting("share_code_length", 8)),
    )
    await store.increment_stat(tenant_id, "links_generated")
    await _attach_share_link(client, message, share["code"])
