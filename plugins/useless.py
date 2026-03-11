import re
import logging
from bot import Bot
from pyrogram.types import Message
from pyrogram import filters
from config import ADMINS, BOT_STATS_TEXT
from datetime import datetime
from helper_func import get_readable_time, not_banned, ALL_COMMANDS, subscribed
from database.database import (
    add_user, present_user, full_userbase, get_all_stats,
    get_total_shares, get_banned_count, get_recent_users
)

logger = logging.getLogger(__name__)


@Bot.on_message(filters.command('stats') & filters.user(ADMINS), group=2)
async def stats(bot: Bot, message: Message):
    now = datetime.now()
    delta = now - bot.uptime
    uptime = get_readable_time(delta.seconds)

    users = await full_userbase()
    all_stats = await get_all_stats()
    total_shares = await get_total_shares()
    banned_count = await get_banned_count()
    recent_users = await get_recent_users(7)

    text = f"""📊 <b>机器人统计</b>

⏱ <b>运行时间：</b>{uptime}

👥 <b>用户统计：</b>
   ├ 总用户数：<code>{len(users)}</code>
   ├ 新增（7天）：<code>{recent_users}</code>
   └ 已封禁：<code>{banned_count}</code>

📦 <b>分享统计：</b>
   ├ 分享总数：<code>{total_shares}</code>
   ├ 已分享文件：<code>{all_stats.get('files_shared', 0)}</code>
   └ 已生成链接：<code>{all_stats.get('links_generated', 0)}</code>

📈 <b>活动统计：</b>
   ├ 分享被访问：<code>{all_stats.get('share_accessed', 0)}</code>
   ├ 验证通过：<code>{all_stats.get('tokens_verified', 0)}</code>
   └ 广播次数：<code>{all_stats.get('broadcasts', 0)}</code>
"""
    await message.reply(text, quote=True)


@Bot.on_message(filters.command('ping') & filters.private & subscribed, group=2)
async def ping(client, message: Message):
    import time
    from database.database import ping_db
    start_time = time.time()
    msg = await message.reply("🏓 检测中...", quote=True)
    end_time = time.time()

    telegram_ping = round((end_time - start_time) * 1000, 2)
    db_start = time.time()
    db_status = await ping_db()
    db_ping = round((time.time() - db_start) * 1000, 2)

    await msg.edit(
        f"🏓 <b>延迟检测</b>\n\n"
        f"⚡ <b>Telegram：</b><code>{telegram_ping}ms</code>\n"
        f"🗄 <b>数据库：</b><code>{db_ping}ms</code> {'✅' if db_status else '❌'}"
    )


@Bot.on_message(
    filters.private & filters.incoming & not_banned
    & ~filters.command(ALL_COMMANDS),
    group=99  # 最低优先级，确保不拦截其他 handler
)
async def useless(client, message: Message):
    """兜底 handler：仅用于注册新用户，不拦截任何命令和分享码"""
    if not message.from_user:
        return

    user_id = message.from_user.id

    if not await present_user(user_id):
        try:
            await add_user(user_id)
        except Exception as e:
            logger.error(f"Error adding user {user_id}: {e}")

    return
