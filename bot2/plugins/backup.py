import os
import json
import logging
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message
from bot import Bot
from config import ADMINS
from database.database import full_userbase, get_all_stats, get_banned_users

logger = logging.getLogger(__name__)


@Bot.on_message(filters.command('backup') & filters.private & filters.user(ADMINS))
async def backup_command(client: Client, message: Message):
    msg = await message.reply("📦 正在创建备份...", quote=True)

    try:
        users = await full_userbase()
        stats = await get_all_stats()
        banned = await get_banned_users()

        backup_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "total_users": len(users),
            "users": users,
            "stats": stats,
            "banned_users": [
                {"id": b["_id"], "reason": b.get("reason", "")}
                for b in banned
            ]
        }

        filename = f"/tmp/backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w') as f:
            json.dump(backup_data, f, indent=2, default=str)

        await message.reply_document(
            document=filename,
            caption=(
                f"📦 <b>备份创建成功</b>\n\n"
                f"👥 用户数：{len(users)}\n"
                f"🚫 封禁数：{len(banned)}"
            ),
            quote=True
        )

        try:
            os.remove(filename)
        except Exception:
            pass

        await msg.delete()

    except Exception as e:
        logger.error(f"Backup error: {e}")
        await msg.edit(f"❌ 备份失败：{str(e)}")
