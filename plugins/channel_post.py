import asyncio
import logging
import random
import re
import string
import time as _time
from pyrogram import filters, Client
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from bot import Bot
import config as cfg
from helper_func import generate_share_code, ALL_COMMANDS
from database.database import (
    create_share, get_share, increment_stat,
    find_share_by_message_id, find_shares_by_keyword,
    find_share_by_group_text, update_share
)
from plugins.share import user_share_sessions

logger = logging.getLogger(__name__)
channel_media_groups = {}

# ============ 关联频道缓存 ============
_linked_chat_cache = {}   # group_id -> linked_channel_id | None
_linked_cache_ts = {}     # group_id -> timestamp
_LINKED_CACHE_TTL = 600   # 缓存10分钟

_NON_WORD_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def _generate_keywords(group_text: str):
    """从文本中随机取4个不重复的单字符作为关键词"""
    cleaned = _NON_WORD_RE.sub("", group_text or "")
    chars = list(dict.fromkeys(list(cleaned)))
    k = 4
    if len(chars) >= k:
        return random.sample(chars, k=k)
    keywords = chars[:]
    pool = list(string.ascii_letters + string.digits)
    random.shuffle(pool)
    for ch in pool:
        if len(keywords) >= k:
            break
        if ch not in keywords:
            keywords.append(ch)
    return keywords


def _get_bound_ids():
    """每次调用实时读取绑定频道配置"""
    channels = list(getattr(cfg, "BOUND_CHANNELS", []) or [])
    if cfg.CHANNEL_ID:
        channels.append(cfg.CHANNEL_ID)
    return set(channels)


async def _get_linked_channel_id(client, chat_id):
    """获取群组关联的频道 ID，带缓存避免频繁 API 调用触发 FloodWait"""
    now = _time.time()
    if chat_id in _linked_chat_cache and now - _linked_cache_ts.get(chat_id, 0) < _LINKED_CACHE_TTL:
        return _linked_chat_cache[chat_id]

    try:
        chat = await client.get_chat(chat_id)
        linked = getattr(chat, "linked_chat", None)
        linked_id = linked.id if linked else None
        _linked_chat_cache[chat_id] = linked_id
        _linked_cache_ts[chat_id] = now
        return linked_id
    except FloodWait as e:
        logger.warning(f"FloodWait getting chat {chat_id}: waiting {e.value}s")
        await asyncio.sleep(e.value)
        try:
            chat = await client.get_chat(chat_id)
            linked = getattr(chat, "linked_chat", None)
            linked_id = linked.id if linked else None
            _linked_chat_cache[chat_id] = linked_id
            _linked_cache_ts[chat_id] = now
            return linked_id
        except Exception as e2:
            logger.error(f"Failed to get chat {chat_id} after FloodWait: {e2}")
            return _linked_chat_cache.get(chat_id)
    except Exception as e:
        logger.error(f"Error getting chat {chat_id}: {e}")
        return _linked_chat_cache.get(chat_id)


# ============ 讨论组关键词回复 ============
@Bot.on_message(
    filters.group & filters.incoming & filters.text,
    group=-1
)
async def discussion_keyword_reply(client: Client, message: Message):
    """绑定频道的讨论组中发送关键词，回复关联的分享链接"""
    keyword = (message.text or "").strip()
    if not keyword:
        return

    bound_ids = _get_bound_ids()
    if not bound_ids:
        return

    # 使用缓存判断当前群是否是绑定频道的讨论组
    linked_id = await _get_linked_channel_id(client, message.chat.id)
    if not linked_id or linked_id not in bound_ids:
        return

    # 全局搜索关键词
    shares = await find_shares_by_keyword(keyword, limit=6)
    if not shares:
        return

    bot_username = client.username or ""
    button_text = getattr(cfg, "KEYWORD_BUTTON_TEXT", "🔗 获取资源")

    buttons = []
    for i, s in enumerate(shares):
        code = s['_id']
        link = f"https://t.me/{bot_username}?start={code}"
        buttons.append([InlineKeyboardButton(f"{button_text} {i + 1}", url=link)])

    try:
        await message.reply_text(
            f"已获得\"{keyword}\"相关资源，共 {len(shares)} 条",
            quote=True,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        logger.info(f"Keyword reply: '{keyword}' -> {len(shares)} results in chat {message.chat.id}")
    except FloodWait as e:
        logger.warning(f"FloodWait replying in chat {message.chat.id}: {e.value}s")
        await asyncio.sleep(e.value)
        try:
            await message.reply_text(
                f"已获得\"{keyword}\"相关资源，共 {len(shares)} 条",
                quote=True,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
        except Exception as e2:
            logger.error(f"Failed to reply after FloodWait: {e2}")
    except Exception as e:
        logger.error(f"Error sending keyword reply in chat {message.chat.id}: {e}")


# ============ 管理员私聊文件收集 ============
@Bot.on_message(
    filters.private & filters.user(cfg.ADMINS) & ~filters.command(ALL_COMMANDS),
    group=5
)
async def channel_post(client: Client, message: Message):
    """管理员私聊发送文件 - 仅在分享会话中收集文件"""
    user_id = message.from_user.id

    if user_id in user_share_sessions:
        try:
            post_message = await message.copy(
                chat_id=client.db_channel.id,
                disable_notification=True
            )
            user_share_sessions[user_id]['messages'].append(post_message.id)
            count = len(user_share_sessions[user_id]['messages'])
            await message.reply(f"✅ 第 {count} 个文件已添加到分享中。", quote=True)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            post_message = await message.copy(
                chat_id=client.db_channel.id,
                disable_notification=True
            )
            user_share_sessions[user_id]['messages'].append(post_message.id)
        except Exception as e:
            logger.error(f"Error in share session: {e}")
            await message.reply("❌ 添加文件失败。", quote=True)
        return

    return


# ============ 频道媒体组处理 ============
async def _finalize_channel_media_group(client: Client, group_id: str):
    """处理频道媒体组，生成分享链接"""
    await asyncio.sleep(2.0)
    group = channel_media_groups.pop(group_id, None)
    if not group:
        return
    messages = group.get("messages", [])
    if not messages:
        return

    messages.sort(key=lambda m: m.id)

    first_msg = messages[0]
    existing = await find_share_by_message_id(first_msg.id)
    if existing:
        logger.info(f"Media group already has share: {existing['_id']}, skipping")
        return

    share_code = generate_share_code()
    while await get_share(share_code):
        share_code = generate_share_code()

    message_ids = [msg.id for msg in messages]

    group_text = ""
    for msg in messages:
        if msg.caption:
            group_text = msg.caption
            break
        elif msg.text:
            group_text = msg.text
            break

    group_text = (group_text or "").strip()
    if group_text:
        existing_share = await find_share_by_group_text(
            group_text, owner_id=client.db_channel.id
        )
    else:
        existing_share = None

    if existing_share:
        existing_ids = existing_share.get("message_ids", [])
        merged_ids = existing_ids[:]
        for mid in message_ids:
            if mid not in existing_ids:
                merged_ids.append(mid)

        updates = {}
        if merged_ids != existing_ids:
            updates["message_ids"] = merged_ids
        if not existing_share.get("keywords"):
            updates["keywords"] = _generate_keywords(group_text)

        if updates:
            await update_share(existing_share["_id"], updates)

        share_code = existing_share["_id"]
        share_link = f"https://t.me/{client.username}?start={share_code}"
        reply_markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton(
                "🔁 分享链接",
                url=f"https://telegram.me/share/url?url={share_link}"
            )]]
        )

        edited_any = False
        if not cfg.DISABLE_CHANNEL_BUTTON:
            try:
                await messages[0].edit_reply_markup(reply_markup)
                edited_any = True
            except FloodWait as e:
                await asyncio.sleep(e.value)
                try:
                    await messages[0].edit_reply_markup(reply_markup)
                    edited_any = True
                except Exception as ex:
                    logger.warning(f"Failed to edit media group message after flood wait: {ex}")
            except Exception as ex:
                logger.warning(f"Failed to edit media group message: {ex}")

        if not edited_any:
            try:
                await client.send_message(
                    chat_id=cfg.CHANNEL_ID,
                    text=(
                        f"✅ 分享链接：{share_link}\n"
                        f"分享码：<code>{share_code}</code>"
                    ),
                    reply_to_message_id=messages[0].id,
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🔗 打开链接", url=share_link)]]
                    ),
                    disable_web_page_preview=True
                )
            except Exception as ex:
                logger.error(f"Failed to send share link for media group: {ex}")

        logger.info(f"Media group merged into share: {share_code} with {len(message_ids)} files")
        return

    keywords = _generate_keywords(group_text)

    await create_share(
        share_code=share_code,
        owner_id=client.db_channel.id,
        message_ids=message_ids,
        title=f"媒体组-{share_code}",
        protect_content=False,
        group_text=group_text,
        keywords=keywords
    )
    await increment_stat('links_generated')

    share_link = f"https://t.me/{client.username}?start={share_code}"
    reply_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton(
            "🔁 分享链接",
            url=f"https://telegram.me/share/url?url={share_link}"
        )]]
    )

    edited_any = False
    if not cfg.DISABLE_CHANNEL_BUTTON:
        try:
            await messages[0].edit_reply_markup(reply_markup)
            edited_any = True
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                await messages[0].edit_reply_markup(reply_markup)
                edited_any = True
            except Exception as ex:
                logger.warning(f"Failed to edit media group message after flood wait: {ex}")
        except Exception as ex:
            logger.warning(f"Failed to edit media group message: {ex}")

    if not edited_any:
        try:
            await client.send_message(
                chat_id=cfg.CHANNEL_ID,
                text=(
                    f"✅ 分享链接：{share_link}\n"
                    f"分享码：<code>{share_code}</code>"
                ),
                reply_to_message_id=messages[0].id,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔗 打开链接", url=share_link)]]
                ),
                disable_web_page_preview=True
            )
        except Exception as ex:
            logger.error(f"Failed to send share link for media group: {ex}")

    logger.info(f"Media group share created: {share_code} with {len(message_ids)} files, keywords={keywords}")


# ============ 频道新消息自动生成分享链接 ============
@Bot.on_message(
    filters.channel & filters.incoming & filters.chat(cfg.CHANNEL_ID),
    group=1
)
async def new_post(client: Client, message: Message):
    """频道新消息自动生成分享链接"""

    if message.media_group_id:
        group_id = str(message.media_group_id)
        group = channel_media_groups.get(group_id)
        if not group:
            channel_media_groups[group_id] = {"messages": [message]}
            channel_media_groups[group_id]["task"] = asyncio.create_task(
                _finalize_channel_media_group(client, group_id)
            )
        else:
            group["messages"].append(message)
            task = group.get("task")
            if task and not task.done():
                task.cancel()
            group["task"] = asyncio.create_task(
                _finalize_channel_media_group(client, group_id)
            )
        return

    existing = await find_share_by_message_id(message.id)
    if existing:
        logger.info(
            f"Message {message.id} already has share: {existing['_id']}, skipping"
        )
        return

    share_code = generate_share_code()
    while await get_share(share_code):
        share_code = generate_share_code()

    group_text = ""
    if message.caption:
        group_text = message.caption
    elif message.text:
        group_text = message.text

    keywords = _generate_keywords(group_text)

    await create_share(
        share_code=share_code,
        owner_id=client.db_channel.id,
        message_ids=[message.id],
        title=f"文件-{share_code}",
        protect_content=False,
        group_text=group_text,
        keywords=keywords
    )
    await increment_stat('links_generated')

    share_link = f"https://t.me/{client.username}?start={share_code}"
    reply_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton(
            "🔁 分享链接",
            url=f"https://telegram.me/share/url?url={share_link}"
        )]]
    )

    edited = False
    if not cfg.DISABLE_CHANNEL_BUTTON:
        try:
            await message.edit_reply_markup(reply_markup)
            edited = True
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                await message.edit_reply_markup(reply_markup)
                edited = True
            except Exception as ex:
                logger.warning(f"Failed to edit message after flood wait: {ex}")
        except Exception as ex:
            logger.warning(f"Failed to edit channel message {message.id}: {ex}")

    if not edited:
        try:
            await client.send_message(
                chat_id=cfg.CHANNEL_ID,
                text=(
                    f"✅ 分享链接：{share_link}\n"
                    f"分享码：<code>{share_code}</code>"
                ),
                reply_to_message_id=message.id,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔗 打开链接", url=share_link)]]
                ),
                disable_web_page_preview=True
            )
        except Exception as ex:
            logger.error(
                f"Failed to send share link for message {message.id}: {ex}"
            )

    logger.info(f"Single message share created: {share_code} for message {message.id}, keywords={keywords}")
