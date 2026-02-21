import base64
import re
import asyncio
import logging
import random
import string
import time
from collections import defaultdict

from pyrogram import filters
from pyrogram.enums import ChatMemberStatus
from config import (
    ADMINS,
    SHARE_CODE_LENGTH, RATE_LIMIT_MAX, RATE_LIMIT_WINDOW
)
import config as cfg
from pyrogram.errors.exceptions.bad_request_400 import UserNotParticipant
from pyrogram.errors import FloodWait
from shortzy import Shortzy
from database.database import (
    user_data, db_verify_status, db_update_verify_status,
    is_banned as check_banned
)

logger = logging.getLogger(__name__)


# ============ 所有命令列表（用于过滤） ============
ALL_COMMANDS = [
    'start', 'help', 'share', 'myshares', 'title',
    'users', 'broadcast', 'batch', 'genlink', 'stats',
    'ping', 'ban', 'unban', 'banned', 'id', 'search', 'backup'
]


# ============ 订阅检查（多频道） ============
async def is_subscribed(filter, client, update):
    channels = (getattr(cfg, 'FORCE_SUB_CHANNELS', None) or
                ([getattr(cfg, 'FORCE_SUB_CHANNEL', 0)] if getattr(cfg, 'FORCE_SUB_CHANNEL', 0) else []))
    if not channels:
        return True
    user_id = update.from_user.id
    if user_id in ADMINS:
        return True

    for channel_id in channels:
        try:
            member = await client.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status not in [
                ChatMemberStatus.OWNER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.MEMBER
            ]:
                return False
        except UserNotParticipant:
            return False
        except Exception as e:
            logger.error(f"Error checking sub for channel {channel_id}: {e}")
            return False
    return True


# ============ 封禁检查 ============
async def is_not_banned(filter, client, update):
    if not update.from_user:
        return True
    user_id = update.from_user.id
    if user_id in ADMINS:
        return True
    banned = await check_banned(user_id)
    if banned:
        try:
            await update.reply(
                f"🚫 您已被封禁，无法使用本机器人。\n<b>原因：</b>{banned.get('reason', '未说明原因')}",
                quote=True
            )
        except Exception:
            pass
        return False
    return True


# ============ 编码解码 ============
async def encode(string):
    string_bytes = string.encode("ascii")
    base64_bytes = base64.urlsafe_b64encode(string_bytes)
    base64_string = (base64_bytes.decode("ascii")).strip("=")
    return base64_string


async def decode(base64_string):
    base64_string = base64_string.strip("=")
    base64_bytes = (base64_string + "=" * (-len(base64_string) % 4)).encode("ascii")
    string_bytes = base64.urlsafe_b64decode(base64_bytes)
    string = string_bytes.decode("ascii")
    return string


# ============ 分享码生成 ============
def generate_share_code(length=None):
    if length is None:
        length = SHARE_CODE_LENGTH
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))


# ============ 消息获取 ============
async def get_messages(client, message_ids):
    messages = []
    total_messages = 0
    while total_messages != len(message_ids):
        temb_ids = message_ids[total_messages:total_messages + 200]
        try:
            msgs = await client.get_messages(
                chat_id=client.db_channel.id,
                message_ids=temb_ids
            )
        except FloodWait as e:
            await asyncio.sleep(e.value)
            msgs = await client.get_messages(
                chat_id=client.db_channel.id,
                message_ids=temb_ids
            )
        except Exception as e:
            logger.error(f"Error getting messages: {e}")
            msgs = []
        total_messages += len(temb_ids)
        messages.extend(msgs)
    return messages


async def get_message_id(client, message):
    if message.forward_from_chat:
        if message.forward_from_chat.id == client.db_channel.id:
            return message.forward_from_message_id
        else:
            return 0
    elif message.forward_sender_name:
        return 0
    elif message.text:
        pattern = r"https://t.me/(?:c/)?(.*)/(\d+)"
        matches = re.match(pattern, message.text)
        if not matches:
            return 0
        channel_id = matches.group(1)
        msg_id = int(matches.group(2))
        if channel_id.isdigit():
            if f"-100{channel_id}" == str(client.db_channel.id):
                return msg_id
        else:
            if channel_id == client.db_channel.username:
                return msg_id
    else:
        return 0


# ============ 验证系统 ============
async def get_verify_status(user_id):
    verify = await db_verify_status(user_id)
    return verify


async def update_verify_status(user_id, verify_token="", is_verified=False, verified_time=0, link=""):
    current = await db_verify_status(user_id)
    current['verify_token'] = verify_token
    current['is_verified'] = is_verified
    current['verified_time'] = verified_time
    current['link'] = link
    await db_update_verify_status(user_id, current)


async def get_shortlink(url, api, link):
    shortzy = Shortzy(api_key=api, base_site=url)
    link = await shortzy.convert(link)
    return link


# ============ 时间格式化 ============
def get_exp_time(seconds):
    periods = [('天', 86400), ('小时', 3600), ('分钟', 60), ('秒', 1)]
    result = ''
    for period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            result += f'{int(period_value)}{period_name} '
    return result.strip()


def get_readable_time(seconds: int) -> str:
    count = 0
    up_time = ""
    time_list = []
    time_suffix_list = ["秒", "分", "时", "天"]
    while count < 4:
        count += 1
        remainder, result = divmod(seconds, 60) if count < 3 else divmod(seconds, 24)
        if seconds == 0 and remainder == 0:
            break
        time_list.append(int(result))
        seconds = int(remainder)
    hmm = len(time_list)
    for x in range(hmm):
        time_list[x] = str(time_list[x]) + time_suffix_list[x]
    if len(time_list) == 4:
        up_time += f"{time_list.pop()}, "
    time_list.reverse()
    up_time += ":".join(time_list)
    return up_time


# ============ 速率限制 ============
class RateLimiter:
    def __init__(self, max_requests: int = RATE_LIMIT_MAX, window_seconds: int = RATE_LIMIT_WINDOW):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.user_requests = defaultdict(list)

    def is_limited(self, user_id: int) -> bool:
        now = time.time()
        self.user_requests[user_id] = [
            t for t in self.user_requests[user_id]
            if now - t < self.window_seconds
        ]
        if len(self.user_requests[user_id]) >= self.max_requests:
            return True
        self.user_requests[user_id].append(now)
        return False

    def get_wait_time(self, user_id: int) -> int:
        if not self.user_requests[user_id]:
            return 0
        oldest = self.user_requests[user_id][0]
        return int(self.window_seconds - (time.time() - oldest)) + 1


rate_limiter = RateLimiter()


# ============ 自定义按钮解析 ============
def parse_buttons(button_str: str):
    """解析按钮字符串 格式: 文字1|链接1,文字2|链接2"""
    from pyrogram.types import InlineKeyboardButton
    buttons = []
    if not button_str or not button_str.strip():
        return buttons
    parts = button_str.split(",")
    row = []
    for part in parts:
        part = part.strip()
        if "|" in part:
            text, url = part.split("|", 1)
            row.append(InlineKeyboardButton(text.strip(), url=url.strip()))
            if len(row) >= 2:
                buttons.append(row)
                row = []
    if row:
        buttons.append(row)
    return buttons


# ============ 过滤器 ============
subscribed = filters.create(is_subscribed)
not_banned = filters.create(is_not_banned)
