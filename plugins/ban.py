from pyrogram import Client, filters
from pyrogram.types import Message

from bot import Bot
from database.database import store
from helper_func import admin_only


@Bot.on_message(filters.command("ban") & filters.private & admin_only)
async def ban_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply("<b>用法：</b><code>/ban 用户ID 原因</code>", quote=True)
    try:
        user_id = int(message.command[1])
    except ValueError:
        return await message.reply("❌ 无效的用户ID", quote=True)
    if client.tenant.is_admin(user_id):
        return await message.reply("❌ 不能封禁管理员！", quote=True)
    reason = " ".join(message.command[2:]) or "未说明原因"
    await store.ban_user(client.tenant.tenant_id, user_id, reason)
    await message.reply(
        f"✅ 用户 <code>{user_id}</code> 已被封禁。\n<b>原因：</b>{reason}", quote=True
    )


@Bot.on_message(filters.command("unban") & filters.private & admin_only)
async def unban_command(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply("<b>用法：</b><code>/unban 用户ID</code>", quote=True)
    try:
        user_id = int(message.command[1])
    except ValueError:
        return await message.reply("❌ 无效的用户ID", quote=True)
    await store.unban_user(client.tenant.tenant_id, user_id)
    await message.reply(f"✅ 用户 <code>{user_id}</code> 已解除封禁。", quote=True)


@Bot.on_message(filters.command("banned") & filters.private & admin_only)
async def banned_list(client: Client, message: Message):
    users = await store.get_banned_users(client.tenant.tenant_id)
    if not users:
        return await message.reply("✅ 当前没有被封禁的用户。", quote=True)
    lines = [
        f"• <code>{user['user_id']}</code> - {user.get('reason', '未说明原因')}"
        for user in users[:50]
    ]
    suffix = f"\n... 还有 {len(users) - 50} 人" if len(users) > 50 else ""
    await message.reply(
        f"🚫 <b>已封禁用户（{len(users)} 人）：</b>\n\n" + "\n".join(lines) + suffix,
        quote=True,
    )
