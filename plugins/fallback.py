from datetime import datetime, timezone

from pyrogram import filters
from pyrogram.types import Message

from bot import Bot
from database.database import store
from helper_func import ALL_COMMANDS, admin_only, get_readable_time, not_banned, subscribed


@Bot.on_message(filters.command("stats") & admin_only, group=2)
async def stats(bot: Bot, message: Message):
    tenant_id = bot.tenant.tenant_id
    uptime = get_readable_time(int((datetime.now(timezone.utc) - bot.uptime).total_seconds()))
    user_count = await store.get_user_count(tenant_id)
    all_stats = await store.get_all_stats(tenant_id)
    total_shares = await store.get_total_shares(tenant_id)
    banned_count = await store.get_banned_count(tenant_id)
    recent_users = await store.get_recent_users(tenant_id, 7)
    await message.reply(
        f"📊 <b>机器人统计</b>\n\n⏱ <b>运行时间：</b>{uptime}\n\n"
        f"👥 总用户：<code>{user_count}</code>\n"
        f"├ 7天新增：<code>{recent_users}</code>\n"
        f"└ 已封禁：<code>{banned_count}</code>\n\n"
        f"📦 分享总数：<code>{total_shares}</code>\n"
        f"├ 已分享文件：<code>{all_stats.get('files_shared', 0)}</code>\n"
        f"└ 已生成链接：<code>{all_stats.get('links_generated', 0)}</code>",
        quote=True,
    )


@Bot.on_message(filters.command("ping") & filters.private & subscribed, group=2)
async def ping(client: Bot, message: Message):
    start = datetime.now(timezone.utc)
    response = await message.reply("🏓 检测中...", quote=True)
    telegram_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
    db_start = datetime.now(timezone.utc)
    database_ok = await store.ping()
    database_ms = (datetime.now(timezone.utc) - db_start).total_seconds() * 1000
    await response.edit(
        f"🏓 <b>延迟检测</b>\n\n⚡ <b>Telegram：</b><code>{telegram_ms:.2f}ms</code>\n"
        f"🗄 <b>数据库：</b><code>{database_ms:.2f}ms</code> {'✅' if database_ok else '❌'}"
    )


@Bot.on_message(
    filters.private & filters.incoming & not_banned & ~filters.command(ALL_COMMANDS),
    group=99,
)
async def register_user(client: Bot, message: Message):
    if message.from_user:
        await store.add_user(client.tenant.tenant_id, message.from_user.id)
