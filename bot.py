import os
import asyncio
from aiohttp import web
from plugins import web_server

import pyromod.listen
from pyrogram import Client
from pyrogram.enums import ParseMode, ChatMemberStatus, ChatType
from pyrogram.errors import FloodWait
from pyrogram.types import BotCommand
import sys
from datetime import datetime

from config import (
    API_HASH, APP_ID, LOGGER, TG_BOT_TOKEN, TG_BOT_WORKERS,
    CHANNEL_ID, PORT
)
import config as cfg
from database.database import create_indexes
from web.api import set_bot_instance
import pyrogram.utils

pyrogram.utils.MIN_CHAT_ID = -999999999999
pyrogram.utils.MIN_CHANNEL_ID = -100999999999999


class Bot(Client):
    def __init__(self):
        super().__init__(
            name="Bot",
            api_hash=API_HASH,
            api_id=APP_ID,
            plugins={"root": "plugins"},
            workers=TG_BOT_WORKERS,
            bot_token=TG_BOT_TOKEN,
            workdir="/tmp",
            in_memory=True
        )
        self.LOGGER = LOGGER
        self.invitelink = None
        self.invitelinks = {}

    async def start(self):
        # ===== 先启动 Web 服务器 =====
        self.LOGGER(__name__).info("Starting web server first...")
        try:
            app_runner = web.AppRunner(await web_server())
            await app_runner.setup()
            await web.TCPSite(app_runner, "0.0.0.0", int(PORT)).start()
            self.LOGGER(__name__).info(f"Web server started on port {PORT}")
        except Exception as e:
            self.LOGGER(__name__).warning(f"Web server error: {e}")

        # ===== 启动 Bot =====
        try:
            await super().start()
        except FloodWait as e:
            self.LOGGER(__name__).warning(f"FloodWait: waiting {e.value} seconds...")
            await asyncio.sleep(e.value)
            await super().start()
        except Exception as e:
            self.LOGGER(__name__).warning(f"Bot start error: {e}")
            sys.exit()

        usr_bot_me = await self.get_me()
        self.uptime = datetime.now()
        self.username = usr_bot_me.username

        # ===== 尽早设置 bot 实例，让 WebUI 能获取到 =====
        set_bot_instance(self)
        self.LOGGER(__name__).info(f"Bot instance set, username: @{self.username}")

        try:
            await create_indexes()
        except Exception as e:
            self.LOGGER(__name__).warning(f"Error creating indexes: {e}")

        # ===== 先从数据库热加载配置，包含强制订阅频道 =====
        from web.api import _apply_runtime_config
        try:
            await _apply_runtime_config()
            self.LOGGER(__name__).info("Runtime config preloaded from database")
        except Exception as e:
            self.LOGGER(__name__).warning(f"Failed to preload runtime config: {e}")

        # ===== 强制订阅频道 =====
        channels = (getattr(cfg, 'FORCE_SUB_CHANNELS', None) or
                    ([getattr(cfg, 'FORCE_SUB_CHANNEL', 0)] if getattr(cfg, 'FORCE_SUB_CHANNEL', 0) else []))

        for channel_id in channels:
            if not channel_id:
                continue
            try:
                link = (await self.get_chat(channel_id)).invite_link
                if not link:
                    await self.export_chat_invite_link(channel_id)
                    link = (await self.get_chat(channel_id)).invite_link
                self.invitelinks[channel_id] = link
            except Exception as a:
                self.LOGGER(__name__).warning(f"Error with force sub channel {channel_id}: {a}")
                self.LOGGER(__name__).warning(
                    "Make sure Bot is Admin in channel with Invite Users via Link Permission"
                )
                sys.exit()

        if self.invitelinks:
            self.invitelink = list(self.invitelinks.values())[0]

        # ===== 数据库频道 =====
        try:
            db_channel = await self.get_chat(CHANNEL_ID)
            self.db_channel = db_channel
            member = await self.get_chat_member(chat_id=db_channel.id, user_id="me")
            if db_channel.type == ChatType.CHANNEL:
                if member.status not in [ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR]:
                    self.LOGGER(__name__).warning("Bot is not admin in DB Channel")
                    sys.exit()
            else:
                if member.status not in [
                    ChatMemberStatus.OWNER,
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.MEMBER
                ]:
                    self.LOGGER(__name__).warning("Bot is not a member of DB Channel")
                    sys.exit()
        except Exception as e:
            self.LOGGER(__name__).warning(f"Error with DB Channel: {e}")
            self.LOGGER(__name__).warning("Make sure bot is Admin in DB Channel")
            sys.exit()

        self.set_parse_mode(ParseMode.HTML)

        try:
            await self.set_bot_commands([
                BotCommand("start", "启动"),
                BotCommand("share", "上传"),
                BotCommand("myshares", "我的上传"),
                BotCommand("help", "帮助")
            ])
        except Exception as e:
            self.LOGGER(__name__).warning(f"Failed to set bot commands: {e}")

        self.LOGGER(__name__).info(f"Bot Running! Username: @{self.username}")
        self.LOGGER(__name__).info(f"Admin Panel: http://0.0.0.0:{PORT}/admin")

        # 从数据库加载动态配置
        try:
            await _apply_runtime_config()
            self.LOGGER(__name__).info("Runtime config loaded from database")
        except Exception as e:
            self.LOGGER(__name__).warning(f"Failed to load runtime config: {e}")

    async def stop(self, *args):
        await super().stop()
        self.LOGGER(__name__).info("Bot stopped.")
