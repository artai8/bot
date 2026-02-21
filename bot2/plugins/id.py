from pyrogram import filters, enums
from pyrogram.types import Message
from bot import Bot


@Bot.on_message(filters.command("id") & filters.private)
async def showid(client, message):
    if message.chat.type == enums.ChatType.PRIVATE:
        user_id = message.chat.id
        await message.reply_text(
            f"<b>您的用户ID：</b><code>{user_id}</code>", quote=True
        )
