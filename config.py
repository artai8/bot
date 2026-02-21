import os
import logging
import tempfile
from logging.handlers import RotatingFileHandler

# ============ 基础配置 ============
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
APP_ID = int(os.environ.get("APP_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "0"))
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
PORT = os.environ.get("PORT", "8585")
RUN_BOT = os.environ.get("RUN_BOT", "True").lower() == "true"

# ============ 数据库 ============
DB_URI = os.environ.get("DATABASE_URL", "")
DB_NAME = os.environ.get("DATABASE_NAME", "Cluster0")

# ============ 短链接验证 ============
SHORTLINK_URL = os.environ.get("SHORTLINK_URL", "")
SHORTLINK_API = os.environ.get("SHORTLINK_API", "")
VERIFY_EXPIRE = int(os.environ.get('VERIFY_EXPIRE', 86400))
IS_VERIFY = os.environ.get("IS_VERIFY", "False").lower() == "true"
TUT_VID = os.environ.get("TUT_VID", "")

# ============ 强制订阅 ============
FORCE_SUB_CHANNEL = int(os.environ.get("FORCE_SUB_CHANNEL", "0"))
FORCE_SUB_CHANNELS = []
_force_channels = os.environ.get("FORCE_SUB_CHANNELS", "").split()
for ch in _force_channels:
    try:
        FORCE_SUB_CHANNELS.append(int(ch))
    except ValueError:
        pass
if FORCE_SUB_CHANNEL and FORCE_SUB_CHANNEL not in FORCE_SUB_CHANNELS:
    FORCE_SUB_CHANNELS.append(FORCE_SUB_CHANNEL)

TG_BOT_WORKERS = int(os.environ.get("TG_BOT_WORKERS", "4"))

BOUND_CHANNELS = []

# ============ 消息模板 ============
START_MSG = os.environ.get(
    "START_MESSAGE",
    "你好 {first} 👋\n\n我是文件分享机器人，可以存储私密文件并通过分享码供他人获取。\n\n发送 /help 查看使用帮助。"
)
FORCE_MSG = os.environ.get(
    "FORCE_SUB_MESSAGE",
    "你好 {first} 👋\n\n<b>使用本机器人前，请先加入以下频道/群组：</b>\n\n加入后点击「重试」即可使用。"
)
CUSTOM_CAPTION = os.environ.get("CUSTOM_CAPTION", None)
PROTECT_CONTENT = os.environ.get('PROTECT_CONTENT', "False").lower() == "true"
DISABLE_CHANNEL_BUTTON = os.environ.get("DISABLE_CHANNEL_BUTTON", "False").lower() == "true"

# ============ 自动删除 ============
AUTO_DELETE_TIME = int(os.environ.get("AUTO_DELETE_TIME", "0"))
AUTO_DELETE_MSG = os.environ.get(
    "AUTO_DELETE_MSG",
    "\n\n⚠️ <b>此消息将在 {time} 后自动删除。</b>\n<b>请及时保存或转发重要内容。</b>"
)

# ============ 分享码 ============
SHARE_CODE_LENGTH = int(os.environ.get("SHARE_CODE_LENGTH", "8"))

# ============ 速率限制 ============
RATE_LIMIT_MAX = int(os.environ.get("RATE_LIMIT_MAX", "10"))
RATE_LIMIT_WINDOW = int(os.environ.get("RATE_LIMIT_WINDOW", "60"))

# ============ 自定义按钮 ============
CUSTOM_BUTTONS = os.environ.get("CUSTOM_BUTTONS", "")

# ============ 推广语 ============
PROMO_TEXT = os.environ.get("PROMO_TEXT", "\n\n<i>由 德华分享机器人 驱动</i>")
SHOW_PROMO = os.environ.get("SHOW_PROMO", "True").lower() == "true"

BOT_STATS_TEXT = "<b>机器人运行时间</b>\n{uptime}"
USER_REPLY_TEXT = "❌ 请勿直接向我发送消息，我只是一个文件分享机器人！\n\n发送 <b>分享码</b> 获取文件，或使用 /help 查看命令帮助。"
ABOUT_TEXT = os.environ.get(
    "ABOUT_TEXT",
    "<b>○ 创建者：<a href='tg://user?id={owner_id}'>点击查看</a>\n"
    "○ 开发语言：<code>Python3</code>\n"
    "○ 框架：<code>Pyrogram {pyrogram_version}</code></b>"
)
HELP_TEXT = os.environ.get(
    "HELP_TEXT",
    "🤖 <b>使用说明：</b>\n\n"
    "• 发送 <b>分享码</b>（8位字符）即可获取文件\n"
    "• 点击文件链接访问共享内容\n\n"
    "/start - 启动机器人\n"
    "/help - 显示此帮助"
)
ADMIN_HELP_TEXT = os.environ.get(
    "ADMIN_HELP_TEXT",
    "👑 <b>管理员命令：</b>\n\n"
    "/start - 启动机器人\n"
    "/share - 开始分享文件\n"
    "/myshares - 查看我的分享\n"
    "/batch - 批量生成链接\n"
    "/genlink - 生成单个链接\n"
    "/users - 查看用户数量\n"
    "/broadcast - 广播消息\n"
    "/stats - 机器人统计\n"
    "/ban - 封禁用户\n"
    "/unban - 解封用户\n"
    "/banned - 已封禁列表\n"
    "/ping - 检测延迟\n"
    "/backup - 数据备份\n"
    "/help - 显示此帮助\n\n"
    "💡 <b>分享码：</b>用户发送分享码即可获取文件"
)
KEYWORD_BUTTON_TEXT = os.environ.get("KEYWORD_BUTTON_TEXT", "🔗 获取资源")

# ============ 管理员 ============
try:
    ADMINS = []
    for x in (os.environ.get("ADMINS", "").split()):
        ADMINS.append(int(x))
except ValueError:
    raise Exception("Your Admins list does not contain valid integers.")

if OWNER_ID:
    ADMINS.append(OWNER_ID)
ADMINS = list(set(ADMINS))

# ============ WebUI ============
WEB_ADMIN_PASSWORD = os.environ.get("WEB_ADMIN_PASSWORD", "admin123")
WEB_SECRET_KEY = os.environ.get("WEB_SECRET_KEY", "")
WEB_TOKEN_EXPIRE = int(os.environ.get("WEB_TOKEN_EXPIRE", "86400"))

# ============ 日志 ============
LOG_FILE_NAME = "filesharingbot.txt"

try:
    tempfile.NamedTemporaryFile(dir='.', delete=True)
    _file_writable = True
except OSError:
    _file_writable = False

_handlers = [logging.StreamHandler()]
if _file_writable:
    _handlers.append(
        RotatingFileHandler(LOG_FILE_NAME, maxBytes=50000000, backupCount=10)
    )

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s",
    datefmt='%d-%b-%y %H:%M:%S',
    handlers=_handlers
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)


def LOGGER(name: str) -> logging.Logger:
    return logging.getLogger(name)
