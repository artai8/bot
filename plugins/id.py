from pyrogram import enums, filters

from bot import Bot
from helper_func import subscribed


@Bot.on_message(filters.command("id") & filters.private & subscribed, group=2)
async def show_id(_client, message):
    if message.chat.type == enums.ChatType.PRIVATE:
        await message.reply_text(f"<b>您的用户ID：</b><code>{message.chat.id}</code>", quote=True)
