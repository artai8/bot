import json
import logging
import os
import tempfile
from contextlib import suppress
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.types import Message

from bot import Bot
from database.database import store
from helper_func import admin_only

logger = logging.getLogger(__name__)


@Bot.on_message(filters.command("backup") & filters.private & admin_only)
async def backup_command(client: Client, message: Message):
    status = await message.reply("📦 正在创建备份...", quote=True)
    filename = ""
    try:
        tenant_id = client.tenant.tenant_id
        stats = await store.get_all_stats(tenant_id)
        banned = await store.get_banned_users(tenant_id)
        metadata = {
            "schema_version": 2,
            "tenant": {"id": tenant_id, "slug": client.tenant.slug},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stats": stats,
            "banned_users": [
                {"id": item["user_id"], "reason": item.get("reason", "")} for item in banned
            ],
        }
        user_count = 0
        share_count = 0
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix=f"backup_{client.tenant.slug}_",
            delete=False,
        ) as handle:
            filename = handle.name
            handle.write("{")
            for index, (key, value) in enumerate(metadata.items()):
                if index:
                    handle.write(",")
                json.dump(key, handle, ensure_ascii=False)
                handle.write(":")
                json.dump(value, handle, ensure_ascii=False, default=str)
            handle.write(',"users":[')
            async for user_id in store.iter_user_ids(tenant_id):
                if user_count:
                    handle.write(",")
                json.dump(user_id, handle)
                user_count += 1
            handle.write('],"shares":[')
            async for share in store.iter_shares(tenant_id):
                if share_count:
                    handle.write(",")
                public_share = {
                    key: value
                    for key, value in share.items()
                    if key not in {"_id", "tenant_id"}
                }
                json.dump(public_share, handle, ensure_ascii=False, default=str)
                share_count += 1
            handle.write("]}")
        await message.reply_document(
            filename,
            caption=(
                f"📦 <b>备份创建成功</b>\n\n"
                f"👥 用户数：{user_count}\n📁 分享数：{share_count}"
            ),
            quote=True,
        )
        await status.delete()
    except Exception:
        logger.exception("[%s] Backup failed", client.tenant.slug)
        await status.edit("❌ 备份失败，请检查服务日志。")
    finally:
        if filename:
            with suppress(OSError):
                os.remove(filename)
