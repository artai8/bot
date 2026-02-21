import asyncio
import logging
import random
import string
import time
import re

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated

from bot import Bot
import config as cfg
from helper_func import (
    subscribed, not_banned, decode, get_messages,
    get_shortlink, get_verify_status, update_verify_status,
    get_exp_time, rate_limiter, parse_buttons, ALL_COMMANDS
)
from database.database import (
    add_user, del_user, full_userbase, present_user,
    get_share, increment_share_access, increment_stat
)
from plugins.share import handle_share_code

logger = logging.getLogger(__name__)

SHARE_CODE_PATTERN = re.compile(r'^[A-Za-z0-9]{6,12}$')


def build_start_buttons():
    """构建首页按钮"""
    buttons = [
        [InlineKeyboardButton("ℹ️ 关于我", callback_data="about"),
         InlineKeyboardButton("📖 使用帮助", callback_data="help")]
    ]
    custom = parse_buttons(cfg.CUSTOM_BUTTONS)
    if custom:
        buttons.extend(custom)
    buttons.append([InlineKeyboardButton("❌ 关闭", callback_data="close")])
    return InlineKeyboardMarkup(buttons)


async def send_files_to_user(client, message, message_ids, protect=False):
    """发送文件给用户，支持自动删除"""
    try:
        msgs = await get_messages(client, message_ids)
    except Exception as e:
        logger.error(f"Error getting messages: {e}")
        await message.reply_text("❌ 出了点问题，请稍后再试！")
        return []

    # 过滤空消息
    msgs = [m for m in msgs if m and not m.empty]
    if not msgs:
        await message.reply_text("❌ 文件已不存在或已被删除。")
        return []

    snt_msgs = []
    for msg in msgs:
        if bool(cfg.CUSTOM_CAPTION) and bool(msg.document):
            caption = cfg.CUSTOM_CAPTION.format(
                previouscaption="" if not msg.caption else msg.caption.html,
                filename=msg.document.file_name
            )
        else:
            caption = "" if not msg.caption else msg.caption.html

        if cfg.SHOW_PROMO and cfg.PROMO_TEXT:
            caption += cfg.PROMO_TEXT

        if cfg.DISABLE_CHANNEL_BUTTON:
            reply_markup = msg.reply_markup
        else:
            reply_markup = None

        try:
            snt_msg = await msg.copy(
                chat_id=message.from_user.id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                protect_content=protect
            )
            await asyncio.sleep(0.5)
            snt_msgs.append(snt_msg)
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
                logger.error(f"Error copying message after flood wait: {e2}")
        except Exception as e:
            logger.error(f"Error copying message: {e}")

    if cfg.AUTO_DELETE_TIME > 0 and snt_msgs:
        time_str = get_exp_time(cfg.AUTO_DELETE_TIME)
        notification = await message.reply(
            cfg.AUTO_DELETE_MSG.format(time=time_str), quote=True
        )
        asyncio.get_event_loop().create_task(
            auto_delete_messages(snt_msgs, notification, cfg.AUTO_DELETE_TIME)
        )

    return snt_msgs


async def auto_delete_messages(messages, notification, delay):
    """自动删除消息的后台任务"""
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


async def handle_link_access(client, message, base64_string, protect=False):
    """处理 Base64 链接访问"""
    try:
        _string = await decode(base64_string)
    except Exception:
        return

    argument = _string.split("-")
    if len(argument) == 3:
        try:
            start = int(int(argument[1]) / abs(client.db_channel.id))
            end = int(int(argument[2]) / abs(client.db_channel.id))
        except Exception:
            return
        if start <= end:
            ids = list(range(start, end + 1))
        else:
            ids = list(range(start, end - 1, -1))
    elif len(argument) == 2:
        try:
            ids = [int(int(argument[1]) / abs(client.db_channel.id))]
        except Exception:
            return
    else:
        return

    temp_msg = await message.reply("⏳ 请稍候...")
    snt_msgs = await send_files_to_user(client, message, ids, protect)
    try:
        await temp_msg.delete()
    except Exception:
        pass
    await increment_stat('files_shared', len(snt_msgs))


# ============ /start 命令（已订阅用户） ============
@Bot.on_message(filters.command('start') & filters.private & subscribed & not_banned, group=2)
async def start_command(client: Client, message: Message):
    user_id = message.from_user.id

    # 添加用户
    if not await present_user(user_id):
        try:
            await add_user(user_id)
        except Exception as e:
            logger.error(f"Error adding user {user_id}: {e}")

    # ========== 管理员直通 ==========
    if user_id in cfg.ADMINS:
        if len(message.command) > 1:
            param = message.command[1]

            # 验证回调
            if param.startswith("verify_"):
                token = param.split("_", 1)[1]
                verify_status = await get_verify_status(user_id)
                if verify_status['verify_token'] != token:
                    return await message.reply("❌ 验证令牌无效或已过期。")
                await update_verify_status(user_id, is_verified=True, verified_time=time.time())
                await increment_stat('tokens_verified')
                return await message.reply(
                    f"✅ 验证成功！有效期：{get_exp_time(cfg.VERIFY_EXPIRE)}", quote=True
                )

            # 分享码
            if SHARE_CODE_PATTERN.match(param):
                result = await handle_share_code(client, message, param)
                if not result:
                    # 分享码不存在，尝试作为 base64 链接处理
                    await handle_link_access(client, message, param)
            else:
                # Base64 链接
                await handle_link_access(client, message, param)
        else:
            await message.reply_text(
                text=cfg.START_MSG.format(
                    first=message.from_user.first_name,
                    last=message.from_user.last_name,
                    username=None if not message.from_user.username else '@' + message.from_user.username,
                    mention=message.from_user.mention,
                    id=message.from_user.id
                ),
                reply_markup=build_start_buttons(),
                disable_web_page_preview=True,
                quote=True
            )
        return

    # ========== 普通用户：速率限制 ==========
    if rate_limiter.is_limited(user_id):
        wait = rate_limiter.get_wait_time(user_id)
        return await message.reply(f"⏳ 请求过于频繁！请等待 {wait} 秒后再试。", quote=True)

    # ========== 普通用户：验证系统 ==========
    verify_status = await get_verify_status(user_id)
    if verify_status['is_verified'] and cfg.VERIFY_EXPIRE < (time.time() - verify_status['verified_time']):
        await update_verify_status(user_id, is_verified=False)
        verify_status = await get_verify_status(user_id)

    # 处理验证回调
    if len(message.command) > 1 and message.command[1].startswith("verify_"):
        token = message.command[1].split("_", 1)[1]
        if verify_status['verify_token'] != token:
            return await message.reply("❌ 验证令牌无效或已过期，请点击 /start 重新获取。")
        await update_verify_status(user_id, is_verified=True, verified_time=time.time())
        await increment_stat('tokens_verified')
        return await message.reply(
            f"✅ 验证成功！有效期：{get_exp_time(cfg.VERIFY_EXPIRE)}",
            protect_content=False, quote=True
        )

    # 需要验证但未验证
    if cfg.IS_VERIFY and not verify_status['is_verified']:
        token = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
        await update_verify_status(user_id, verify_token=token, link="")
        link = await get_shortlink(
            cfg.SHORTLINK_URL, cfg.SHORTLINK_API,
            f'https://telegram.dog/{client.username}?start=verify_{token}'
        )
        btn = [[InlineKeyboardButton("🔐 点击验证", url=link)]]
        if cfg.TUT_VID:
            btn.append([InlineKeyboardButton('📖 如何使用', url=cfg.TUT_VID)])
        return await message.reply(
            f"🔐 您的验证已过期，请重新验证。\n\n"
            f"⏱ 验证有效期：{get_exp_time(cfg.VERIFY_EXPIRE)}\n\n"
            f"完成验证即可使用机器人 {get_exp_time(cfg.VERIFY_EXPIRE)}。",
            reply_markup=InlineKeyboardMarkup(btn),
            protect_content=False, quote=True
        )

    # ========== 普通用户：链接访问 ==========
    if len(message.command) > 1:
        param = message.command[1]
        if SHARE_CODE_PATTERN.match(param):
            result = await handle_share_code(client, message, param)
            if not result:
                await handle_link_access(client, message, param)
        else:
            await handle_link_access(client, message, param)
    # ========== 默认欢迎 ==========
    else:
        await message.reply_text(
            text=cfg.START_MSG.format(
                first=message.from_user.first_name,
                last=message.from_user.last_name,
                username=None if not message.from_user.username else '@' + message.from_user.username,
                mention=message.from_user.mention,
                id=message.from_user.id
            ),
            reply_markup=build_start_buttons(),
            disable_web_page_preview=True,
            quote=True
        )


# ============ /help 命令 ============
@Bot.on_message(filters.command('help') & filters.private & subscribed & not_banned, group=2)
async def help_command(client: Client, message: Message):
    user_id = message.from_user.id
    text = cfg.ADMIN_HELP_TEXT if user_id in cfg.ADMINS else cfg.HELP_TEXT
    await message.reply_text(text, disable_web_page_preview=True, quote=True)


# ============ 未加入频道处理（兜底） ============
@Bot.on_message(filters.command('start') & filters.private, group=3)
async def not_joined(client: Client, message: Message):
    buttons = []

    # 构建频道按钮 - 添加空值校验
    if hasattr(client, 'invitelinks') and client.invitelinks:
        for idx, (channel_id, link) in enumerate(client.invitelinks.items(), 1):
            if link and link.strip():
                buttons.append([InlineKeyboardButton(f"📢 加入频道 {idx}", url=link)])
    elif hasattr(client, 'invitelink') and client.invitelink:
        if client.invitelink.strip():
            buttons.append([InlineKeyboardButton("📢 加入频道", url=client.invitelink)])

    if not buttons:
        try:
            channels = list(getattr(cfg, 'FORCE_SUB_CHANNELS', []) or [])
            idx = 1
            for channel_id in channels:
                try:
                    chat = await client.get_chat(channel_id)
                    link = chat.invite_link
                    if not link:
                        await client.export_chat_invite_link(channel_id)
                        link = (await client.get_chat(channel_id)).invite_link
                    if link and link.strip():
                        buttons.append([InlineKeyboardButton(f"📢 加入频道 {idx}", url=link)])
                        idx += 1
                except Exception:
                    continue
        except Exception:
            pass

    # 重试按钮
    if len(message.command) > 1:
        buttons.append([
            InlineKeyboardButton(
                text='🔄 重试',
                url=f"https://t.me/{client.username}?start={message.command[1]}"
            )
        ])

    # 关键修复：按钮为空时不传 reply_markup，避免 REPLY_MARKUP_INVALID
    if not buttons:
        await message.reply(
            text="⚠️ 管理员尚未配置强制加入的频道，请联系管理员。",
            quote=True,
            disable_web_page_preview=True
        )
        return

    await message.reply(
        text=cfg.FORCE_MSG.format(
            first=message.from_user.first_name,
            last=message.from_user.last_name,
            username=None if not message.from_user.username else '@' + message.from_user.username,
            mention=message.from_user.mention,
            id=message.from_user.id
        ),
        reply_markup=InlineKeyboardMarkup(buttons),
        quote=True,
        disable_web_page_preview=True
    )


# ============ 管理员命令 ============
WAIT_MSG = """<b>⏳ 处理中...</b>"""
REPLY_ERROR = """<code>请回复一条消息来使用此命令。</code>"""


@Bot.on_message(filters.command('users') & filters.private & filters.user(cfg.ADMINS), group=2)
async def get_users(client: Bot, message: Message):
    msg = await client.send_message(chat_id=message.chat.id, text=WAIT_MSG)
    users = await full_userbase()
    await msg.edit(f"👥 当前共有 {len(users)} 位用户使用本机器人")


@Bot.on_message(filters.private & filters.command('broadcast') & filters.user(cfg.ADMINS), group=2)
async def send_text(client: Bot, message: Message):
    if not message.reply_to_message:
        msg = await message.reply(REPLY_ERROR)
        await asyncio.sleep(8)
        await msg.delete()
        return

    broadcast_msg = message.reply_to_message
    btn_markup = None

    if len(message.command) > 1:
        btn_text = " ".join(message.command[1:])
        btn_rows = parse_buttons(btn_text)
        if btn_rows:
            btn_markup = InlineKeyboardMarkup(btn_rows)

    query = await full_userbase()
    total = 0
    successful = 0
    blocked = 0
    deleted = 0
    unsuccessful = 0

    pls_wait = await message.reply("<i>📢 正在广播消息...这可能需要一些时间</i>")

    for chat_id in query:
        try:
            await broadcast_msg.copy(chat_id, reply_markup=btn_markup)
            successful += 1
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                await broadcast_msg.copy(chat_id, reply_markup=btn_markup)
                successful += 1
            except Exception:
                unsuccessful += 1
        except UserIsBlocked:
            await del_user(chat_id)
            blocked += 1
        except InputUserDeactivated:
            await del_user(chat_id)
            deleted += 1
        except Exception as e:
            logger.error(f"Error broadcasting to {chat_id}: {e}")
            unsuccessful += 1
        total += 1

    await increment_stat('broadcasts')

    status = f"""<b><u>📢 广播完成</u>

总用户数：<code>{total}</code>
发送成功：<code>{successful}</code>
已屏蔽机器人：<code>{blocked}</code>
已注销账户：<code>{deleted}</code>
发送失败：<code>{unsuccessful}</code></b>"""

    return await pls_wait.edit(status)
