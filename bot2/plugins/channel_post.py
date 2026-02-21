import asyncio
import logging
import random
import re
import string
from pyrogram import filters, Client
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from bot import Bot
import config as cfg
from helper_func import generate_share_code, ALL_COMMANDS
from database.database import (
    create_share, get_share, increment_stat,
    find_share_by_message_id, find_shares_by_keyword
)
from plugins.share import user_share_sessions

logger = logging.getLogger(__name__)
channel_media_groups = {}

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

    # 判断当前群是否是绑定频道的讨论组
    try:
        chat = await client.get_chat(message.chat.id)
        linked = getattr(chat, "linked_chat", None)
        if not linked or linked.id not in bound_ids:
            return
    except Exception:
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

    await message.reply_text(
        f"已获得\"{keyword}\"相关资源，共 {len(shares)} 条",
        quote=True,
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    logger.info(f"Keyword reply: '{keyword}' -> {len(shares)} results in chat {message.chat.id}")


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
