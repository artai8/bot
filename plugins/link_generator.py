import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot import Bot
from config import ADMINS
from helper_func import encode, get_message_id

logger = logging.getLogger(__name__)


@Bot.on_message(filters.private & filters.user(ADMINS) & filters.command('batch'))
async def batch(client: Client, message: Message):
    while True:
        try:
            first_message = await client.ask(
                text="请转发数据库频道中的 <b>第一条</b> 消息（带引用）...\n\n或发送数据库频道的消息链接",
                chat_id=message.from_user.id,
                filters=(filters.forwarded | (filters.text & ~filters.forwarded)),
                timeout=60
            )
        except Exception:
            return
        f_msg_id = await get_message_id(client, first_message)
        if f_msg_id:
            break
        else:
            await first_message.reply(
                "❌ 错误\n\n该转发消息不是来自数据库频道", quote=True
            )
            continue

    while True:
        try:
            second_message = await client.ask(
                text="请转发数据库频道中的 <b>最后一条</b> 消息（带引用）...\n\n或发送数据库频道的消息链接",
                chat_id=message.from_user.id,
                filters=(filters.forwarded | (filters.text & ~filters.forwarded)),
                timeout=60
            )
        except Exception:
            return
        s_msg_id = await get_message_id(client, second_message)
        if s_msg_id:
            break
        else:
            await second_message.reply(
                "❌ 错误\n\n该转发消息不是来自数据库频道", quote=True
            )
            continue

    string = f"get-{f_msg_id * abs(client.db_channel.id)}-{s_msg_id * abs(client.db_channel.id)}"
    base64_string = await encode(string)
    link = f"https://t.me/{client.username}?start={base64_string}"
    reply_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔁 分享链接", url=f'https://telegram.me/share/url?url={link}')]]
    )
    await second_message.reply_text(
        f"<b>以下是您的链接：</b>\n\n{link}", quote=True, reply_markup=reply_markup
    )


@Bot.on_message(filters.private & filters.user(ADMINS) & filters.command('genlink'))
async def link_generator(client: Client, message: Message):
    while True:
        try:
            channel_message = await client.ask(
                text="请转发数据库频道中的消息（带引用）...\n\n或发送数据库频道的消息链接",
                chat_id=message.from_user.id,
                filters=(filters.forwarded | (filters.text & ~filters.forwarded)),
                timeout=60
            )
        except Exception:
            return
        msg_id = await get_message_id(client, channel_message)
        if msg_id:
            break
        else:
            await channel_message.reply(
                "❌ 错误\n\n该转发消息不是来自数据库频道", quote=True
            )
            continue

    base64_string = await encode(f"get-{msg_id * abs(client.db_channel.id)}")
    link = f"https://t.me/{client.username}?start={base64_string}"
    reply_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔁 分享链接", url=f'https://telegram.me/share/url?url={link}')]]
    )
    await channel_message.reply_text(
        f"<b>以下是您的链接：</b>\n\n{link}", quote=True, reply_markup=reply_markup
    )
