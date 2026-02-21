import logging
from pyrogram import Client, filters
from pyrogram.types import Message
from bot import Bot
from config import ADMINS
from database.database import ban_user, unban_user, get_banned_users, get_banned_count

logger = logging.getLogger(__name__)


@Bot.on_message(filters.command('ban') & filters.private & filters.user(ADMINS))
async def ban_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply("<b>用法：</b><code>/ban 用户ID 原因</code>", quote=True)

    try:
        user_id = int(message.command[1])
    except ValueError:
        return await message.reply("❌ 无效的用户ID", quote=True)

    if user_id in ADMINS:
        return await message.reply("❌ 不能封禁管理员！", quote=True)

    reason = " ".join(message.command[2:]) if len(message.command) > 2 else "未说明原因"
    await ban_user(user_id, reason)
    await message.reply(
        f"✅ 用户 <code>{user_id}</code> 已被封禁。\n<b>原因：</b>{reason}",
        quote=True
    )


@Bot.on_message(filters.command('unban') & filters.private & filters.user(ADMINS))
async def unban_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply("<b>用法：</b><code>/unban 用户ID</code>", quote=True)

    try:
        user_id = int(message.command[1])
    except ValueError:
        return await message.reply("❌ 无效的用户ID", quote=True)

    await unban_user(user_id)
    await message.reply(f"✅ 用户 <code>{user_id}</code> 已解除封禁。", quote=True)


@Bot.on_message(filters.command('banned') & filters.private & filters.user(ADMINS))
async def banned_list(client: Client, message: Message):
    users = await get_banned_users()
    if not users:
        return await message.reply("✅ 当前没有被封禁的用户。", quote=True)

    text = f"🚫 <b>已封禁用户（{len(users)} 人）：</b>\n\n"
    for user in users[:50]:
        text += f"• <code>{user['_id']}</code> - {user.get('reason', '未说明原因')}\n"
    if len(users) > 50:
        text += f"\n... 还有 {len(users) - 50} 人"
    await message.reply(text, quote=True)
