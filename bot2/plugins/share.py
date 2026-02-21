import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait

from bot import Bot
from config import (
    ADMINS, PROTECT_CONTENT, CUSTOM_CAPTION,
    DISABLE_CHANNEL_BUTTON, PROMO_TEXT, SHOW_PROMO,
    AUTO_DELETE_TIME, AUTO_DELETE_MSG
)
from helper_func import not_banned, generate_share_code, get_messages, get_exp_time
from database.database import (
    create_share, get_share, increment_share_access,
    get_user_shares, update_share, delete_share,
    increment_stat, get_user_share_count
)

logger = logging.getLogger(__name__)

# 用户分享会话状态
user_share_sessions = {}


@Bot.on_message(filters.command('share') & filters.private & filters.user(ADMINS) & not_banned, group=2)
async def start_share(client: Client, message: Message):
    """开始分享流程"""
    user_id = message.from_user.id
    user_share_sessions[user_id] = {
        'messages': [],
        'protect': False,
        'title': ''
    }

    btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚫 禁止转发：关闭", callback_data="share_toggle_protect")],
        [InlineKeyboardButton("✅ 完成分享", callback_data="share_complete")],
        [InlineKeyboardButton("❌ 取消", callback_data="share_cancel")]
    ])

    await message.reply(
        "📤 <b>分享模式已开启！</b>\n\n"
        "请逐一发送文件（图片、视频、文档等）。\n"
        "发送完毕后，点击 <b>完成分享</b> 生成分享码。\n\n"
        "💡 可通过发送 <code>/title 标题内容</code> 来设置标题",
        reply_markup=btn, quote=True
    )


@Bot.on_message(filters.command('title') & filters.private & filters.user(ADMINS), group=2)
async def set_share_title(client: Client, message: Message):
    """设置分享标题"""
    user_id = message.from_user.id
    if user_id not in user_share_sessions:
        return await message.reply("❌ 当前没有进行中的分享会话，请先使用 /share 开始。", quote=True)

    title = " ".join(message.command[1:]) if len(message.command) > 1 else ""
    if not title:
        return await message.reply("📝 用法：<code>/title 你的标题</code>", quote=True)

    user_share_sessions[user_id]['title'] = title
    await message.reply(f"📝 标题已设置为：<b>{title}</b>", quote=True)


@Bot.on_message(filters.command('myshares') & filters.private & not_banned, group=2)
async def my_shares(client: Client, message: Message):
    """查看我的分享列表"""
    user_id = message.from_user.id
    page = 1
    if len(message.command) > 1:
        try:
            page = int(message.command[1])
        except ValueError:
            page = 1

    shares, total = await get_user_shares(user_id, page=page, per_page=5)

    if not shares:
        return await message.reply("📭 您还没有任何分享。\n\n使用 /share 创建第一个分享！", quote=True)

    text = f"📋 <b>我的分享</b>（第 {page} 页，共 {total} 个）\n\n"

    buttons = []
    for share in shares:
        code = share['_id']
        title = share.get('title', '未命名')
        access = share.get('access_count', 0)
        protect = "🔒" if share.get('protect_content', False) else "🔓"
        files = len(share.get('message_ids', []))

        text += f"{protect} <code>{code}</code> - {title}\n"
        text += f"   📁 {files} 个文件 | 👁 {access} 次查看\n\n"

        buttons.append([
            InlineKeyboardButton(f"📄 {code}", callback_data=f"share_detail_{code}"),
            InlineKeyboardButton("🗑", callback_data=f"share_delete_{code}")
        ])

    # 分页按钮
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"shares_page_{page - 1}"))
    total_pages = (total + 4) // 5
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"shares_page_{page + 1}"))
    if nav_buttons:
        buttons.append(nav_buttons)

    await message.reply(text, reply_markup=InlineKeyboardMarkup(buttons), quote=True)


# ============ 分享码获取（任何用户） ============
async def handle_share_code(client: Client, message: Message, code: str):
    """处理分享码获取"""
    share = await get_share(code)
    if not share:
        return False

    await increment_share_access(code)
    await increment_stat('share_accessed')

    message_ids = share.get('message_ids', [])
    protect = share.get('protect_content', PROTECT_CONTENT)

    if not message_ids:
        await message.reply("❌ 此分享没有包含任何文件。", quote=True)
        return True

    try:
        msgs = await get_messages(client, message_ids)
    except Exception as e:
        logger.error(f"Error getting share messages: {e}")
        await message.reply("❌ 获取文件时出错，请稍后再试。", quote=True)
        return True

    # 过滤掉空消息
    msgs = [m for m in msgs if m and not m.empty]
    if not msgs:
        await message.reply("❌ 文件已不存在或已被删除。", quote=True)
        return True

    group_text = share.get('group_text', '')
    if not group_text:
        for m in msgs:
            if m.caption:
                group_text = m.caption
                break
            elif m.text:
                group_text = m.text
                break

    header = f"📦 此分享包含 <b>{len(message_ids)}</b> 个文件"
    if group_text:
        header += f"\n📝 媒体组文字：{group_text}"
    await message.reply(header, quote=True)

    snt_msgs = []

    # 尝试以媒体组方式发送
    can_album = len(msgs) > 1 and all((m.photo or m.video) and not m.document for m in msgs)
    if can_album:
        media = []
        for i, m in enumerate(msgs):
            caption = None
            if i == 0:
                caption = m.caption.html if m.caption else ""
                if SHOW_PROMO and PROMO_TEXT:
                    caption = (caption or "") + PROMO_TEXT

            if m.photo:
                media.append(InputMediaPhoto(m.photo.file_id, caption=caption, parse_mode=ParseMode.HTML))
            elif m.video:
                media.append(InputMediaVideo(m.video.file_id, caption=caption, parse_mode=ParseMode.HTML))

        try:
            snt_msgs = await client.send_media_group(
                chat_id=message.from_user.id,
                media=media,
                protect_content=protect
            )
        except TypeError:
            try:
                snt_msgs = await client.send_media_group(
                    chat_id=message.from_user.id,
                    media=media
                )
            except Exception as e:
                logger.error(f"Error sending media group (fallback): {e}")
                snt_msgs = []
        except Exception as e:
            logger.error(f"Error sending media group: {e}")
            snt_msgs = []

    # 逐条发送（非媒体组或媒体组发送失败时）
    if not snt_msgs:
        for msg in msgs:
            if bool(CUSTOM_CAPTION) and bool(msg.document):
                caption = CUSTOM_CAPTION.format(
                    previouscaption="" if not msg.caption else msg.caption.html,
                    filename=msg.document.file_name
                )
            else:
                caption = "" if not msg.caption else msg.caption.html

            if SHOW_PROMO and PROMO_TEXT:
                caption += PROMO_TEXT

            reply_markup = msg.reply_markup if DISABLE_CHANNEL_BUTTON else None

            try:
                snt_msg = await msg.copy(
                    chat_id=message.from_user.id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                    protect_content=protect
                )
                snt_msgs.append(snt_msg)
                await asyncio.sleep(0.5)
            except FloodWait as e:
                await asyncio.sleep(e.value)
                try:
                    snt_msg = await msg.copy(
                        chat_id=message.from_user.id,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup,
                        protect_content=protect
                    )
                    snt_msgs.append(snt_msg)
                except Exception as e2:
                    logger.error(f"Error copying after flood wait: {e2}")
            except Exception as e:
                logger.error(f"Error copying share message: {e}")

    # 自动删除
    if AUTO_DELETE_TIME > 0 and snt_msgs:
        time_str = get_exp_time(AUTO_DELETE_TIME)
        notification = await message.reply(
            AUTO_DELETE_MSG.format(time=time_str), quote=True
        )
        asyncio.get_event_loop().create_task(
            _auto_delete(snt_msgs, notification, AUTO_DELETE_TIME)
        )

    await increment_stat('files_shared', len(snt_msgs))
    return True


async def _auto_delete(messages, notification, delay):
    await asyncio.sleep(delay)
    for msg in messages:
        try:
            await msg.delete()
        except Exception:
            pass
    try:
        await notification.delete()
    except Exception:
        pass
