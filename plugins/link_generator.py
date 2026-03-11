import logging
from pyrogram import Client, StopPropagation, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot import Bot
from config import ADMINS
from helper_func import ALL_COMMANDS, encode, get_message_id

logger = logging.getLogger(__name__)

link_generator_sessions = {}


async def _send_batch_link(client: Client, message: Message, first_id: int, second_id: int):
    string = f"get-{first_id * abs(client.db_channel.id)}-{second_id * abs(client.db_channel.id)}"
    base64_string = await encode(string)
    link = f"https://t.me/{client.username}?start={base64_string}"
    reply_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔁 分享链接", url=f'https://telegram.me/share/url?url={link}')]]
    )
    await message.reply_text(
        f"<b>以下是您的链接：</b>\n\n{link}", quote=True, reply_markup=reply_markup
    )


async def _send_single_link(client: Client, message: Message, msg_id: int):
    base64_string = await encode(f"get-{msg_id * abs(client.db_channel.id)}")
    link = f"https://t.me/{client.username}?start={base64_string}"
    reply_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔁 分享链接", url=f'https://telegram.me/share/url?url={link}')]]
    )
    await message.reply_text(
        f"<b>以下是您的链接：</b>\n\n{link}", quote=True, reply_markup=reply_markup
    )


async def _prompt_current_step(message: Message, session: dict):
    if session["mode"] == "batch" and session["step"] == "first":
        await message.reply(
            "请转发数据库频道中的 <b>第一条</b> 消息（带引用）...\n\n或发送数据库频道的消息链接",
            quote=True
        )
        return

    if session["mode"] == "batch" and session["step"] == "second":
        await message.reply(
            "请转发数据库频道中的 <b>最后一条</b> 消息（带引用）...\n\n或发送数据库频道的消息链接",
            quote=True
        )
        return

    await message.reply(
        "请转发数据库频道中的消息（带引用）...\n\n或发送数据库频道的消息链接",
        quote=True
    )


@Bot.on_message(filters.private & filters.user(ADMINS) & filters.command('batch'))
async def batch(client: Client, message: Message):
    link_generator_sessions[message.from_user.id] = {
        "mode": "batch",
        "step": "first",
        "first_message_id": None,
    }
    await _prompt_current_step(message, link_generator_sessions[message.from_user.id])


@Bot.on_message(filters.private & filters.user(ADMINS) & filters.command('genlink'))
async def link_generator(client: Client, message: Message):
    link_generator_sessions[message.from_user.id] = {
        "mode": "genlink",
        "step": "single",
    }
    await _prompt_current_step(message, link_generator_sessions[message.from_user.id])


@Bot.on_message(
    filters.private & filters.user(ADMINS) & ~filters.command(ALL_COMMANDS),
    group=4
)
async def link_generator_session_input(client: Client, message: Message):
    session = link_generator_sessions.get(message.from_user.id)
    if not session:
        return

    msg_id = await get_message_id(client, message)
    if not msg_id:
        await message.reply(
            "❌ 错误\n\n该转发消息不是来自数据库频道",
            quote=True
        )
        await _prompt_current_step(message, session)
        raise StopPropagation

    if session["mode"] == "batch":
        if session["step"] == "first":
            session["first_message_id"] = msg_id
            session["step"] = "second"
            await _prompt_current_step(message, session)
            raise StopPropagation

        first_id = session.get("first_message_id")
        link_generator_sessions.pop(message.from_user.id, None)
        await _send_batch_link(client, message, first_id, msg_id)
        raise StopPropagation

    link_generator_sessions.pop(message.from_user.id, None)
    await _send_single_link(client, message, msg_id)
    raise StopPropagation
