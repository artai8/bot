from pyrogram import Client, StopPropagation, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot import Bot
from helper_func import ALL_COMMANDS, admin_only, encode, get_message_id


async def _send_link(client: Client, message: Message, first: int, second: int | None = None):
    raw = f"get-{first * abs(client.db_channel.id)}"
    if second is not None:
        raw += f"-{second * abs(client.db_channel.id)}"
    encoded = await encode(raw)
    link = f"https://t.me/{client.username}?start={encoded}"
    await message.reply_text(
        f"<b>以下是您的链接：</b>\n\n{link}",
        quote=True,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔁 分享链接", url=f"https://telegram.me/share/url?url={link}")]]
        ),
    )


@Bot.on_message(filters.private & admin_only & filters.command("batch"))
async def batch(client: Client, message: Message):
    client.link_sessions[message.from_user.id] = {"mode": "batch", "step": "first"}
    await message.reply("请转发数据库频道中的 <b>第一条</b> 消息，或发送消息链接。", quote=True)


@Bot.on_message(filters.private & admin_only & filters.command("genlink"))
async def link_generator(client: Client, message: Message):
    client.link_sessions[message.from_user.id] = {"mode": "single"}
    await message.reply("请转发数据库频道中的消息，或发送消息链接。", quote=True)


@Bot.on_message(filters.private & admin_only & ~filters.command(ALL_COMMANDS), group=4)
async def link_generator_session_input(client: Client, message: Message):
    session = client.link_sessions.get(message.from_user.id)
    if not session:
        return
    message_id = await get_message_id(client, message)
    if not message_id:
        await message.reply("❌ 该消息不是来自当前租户的数据库频道", quote=True)
        raise StopPropagation
    if session["mode"] == "batch" and session.get("step") == "first":
        session.update({"step": "second", "first": message_id})
        await message.reply("请转发数据库频道中的 <b>最后一条</b> 消息。", quote=True)
        raise StopPropagation
    client.link_sessions.pop(message.from_user.id, None)
    await _send_link(
        client,
        message,
        session.get("first", message_id),
        message_id if session["mode"] == "batch" else None,
    )
    raise StopPropagation
