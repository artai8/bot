"""Platform configuration and immutable tenant defaults.

Only infrastructure settings live in this module. Tenant-specific settings are
stored in MongoDB and exposed through ``TenantContext``; runtime code must not
mutate this module.
"""

from __future__ import annotations

import copy
import logging
import os
import socket
import tempfile
import warnings
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from types import MappingProxyType
from typing import Any

from cryptography.fernet import Fernet


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return default if value in (None, "") else int(value)


def _env_int_list(name: str) -> list[int]:
    result: list[int] = []
    for item in os.environ.get(name, "").replace(",", " ").split():
        result.append(int(item))
    return list(dict.fromkeys(result))


DEFAULT_TENANT_SETTINGS = MappingProxyType(
    {
        "is_verify": parse_bool(os.environ.get("IS_VERIFY"), False),
        "verify_expire": _env_int("VERIFY_EXPIRE", 86400),
        "shortlink_url": os.environ.get("SHORTLINK_URL", ""),
        "shortlink_api": os.environ.get("SHORTLINK_API", ""),
        "tutorial_video": os.environ.get("TUT_VID", ""),
        "force_sub_channels": tuple(_env_int_list("FORCE_SUB_CHANNELS")),
        "bound_channels": tuple(),
        "protect_content": parse_bool(os.environ.get("PROTECT_CONTENT"), False),
        "disable_channel_button": parse_bool(os.environ.get("DISABLE_CHANNEL_BUTTON"), False),
        "custom_caption": os.environ.get("CUSTOM_CAPTION", ""),
        "auto_delete_time": _env_int("AUTO_DELETE_TIME", 0),
        "share_code_length": _env_int("SHARE_CODE_LENGTH", 8),
        "rate_limit_max": _env_int("RATE_LIMIT_MAX", 10),
        "rate_limit_window": _env_int("RATE_LIMIT_WINDOW", 60),
        "custom_buttons": os.environ.get("CUSTOM_BUTTONS", ""),
        "show_promo": parse_bool(os.environ.get("SHOW_PROMO"), True),
        "promo_text": os.environ.get("PROMO_TEXT", "\n\n<i>由 德华分享机器人 驱动</i>"),
        "start_message": os.environ.get(
            "START_MESSAGE",
            "你好 {first} 👋\n\n我是文件分享机器人，可以存储私密文件并通过分享码供他人获取。\n\n发送 /help 查看使用帮助。",
        ),
        "force_sub_message": os.environ.get(
            "FORCE_SUB_MESSAGE",
            "你好 {first} 👋\n\n<b>使用本机器人前，请先加入以下频道/群组：</b>\n\n加入后点击「重试」即可使用。",
        ),
        "auto_delete_message": os.environ.get(
            "AUTO_DELETE_MSG",
            "\n\n⚠️ <b>此消息将在 {time} 后自动删除。</b>\n<b>请及时保存或转发重要内容。</b>",
        ),
        "user_reply_text": "❌ 请勿直接向我发送消息，我只是一个文件分享机器人！\n\n发送 <b>分享码</b> 获取文件，或使用 /help 查看命令帮助。",
        "about_text": (
            "<b>○ 创建者：<a href='tg://user?id={owner_id}'>点击查看</a>\n"
            "○ 开发语言：<code>Python3</code>\n"
            "○ 框架：<code>Python Telegram Client</code></b>"
        ),
        "help_text": (
            "🤖 <b>使用说明：</b>\n\n"
            "• 发送 <b>分享码</b>即可获取文件\n"
            "• 点击文件链接访问共享内容\n\n"
            "/start - 启动机器人\n/help - 显示此帮助"
        ),
        "admin_help_text": (
            "👑 <b>管理员命令：</b>\n\n"
            "/share - 开始分享文件\n/myshares - 查看我的分享\n"
            "/batch - 批量生成链接\n/genlink - 生成单个链接\n"
            "/users - 查看用户数量\n/broadcast - 广播消息\n"
            "/stats - 机器人统计\n/ban - 封禁用户\n"
            "/unban - 解封用户\n/banned - 已封禁列表\n"
            "/ping - 检测延迟\n/backup - 数据备份\n/help - 显示帮助"
        ),
        "keyword_button_text": os.environ.get("KEYWORD_BUTTON_TEXT", "🔗 获取资源"),
    }
)


def tenant_defaults() -> dict[str, Any]:
    defaults = copy.deepcopy(dict(DEFAULT_TENANT_SETTINGS))
    defaults["force_sub_channels"] = list(defaults["force_sub_channels"])
    defaults["bound_channels"] = list(defaults["bound_channels"])
    return defaults


@dataclass(frozen=True)
class PlatformConfig:
    api_id: int
    api_hash: str
    database_url: str
    database_name: str
    bind_host: str
    port: int
    process_role: str
    platform_admin_password: str
    tenant_master_key: str
    session_expire_seconds: int
    worker_id: str
    worker_bot_capacity: int
    worker_heartbeat_seconds: int
    worker_lease_seconds: int
    worker_max_deliveries: int
    tenant_max_deliveries: int
    tenant_pending_deliveries: int

    @classmethod
    def from_env(cls) -> PlatformConfig:
        platform_password = os.environ.get("PLATFORM_ADMIN_PASSWORD", "")
        legacy_password = os.environ.get("WEB_ADMIN_PASSWORD", "")
        if not platform_password and legacy_password:
            warnings.warn(
                "WEB_ADMIN_PASSWORD is deprecated; use PLATFORM_ADMIN_PASSWORD",
                FutureWarning,
                stacklevel=2,
            )
            platform_password = legacy_password

        return cls(
            api_id=_env_int("API_ID", 0),
            api_hash=os.environ.get("API_HASH", ""),
            database_url=os.environ.get("DATABASE_URL", ""),
            database_name=os.environ.get("DATABASE_NAME", "filesharing"),
            bind_host=os.environ.get("WEB_BIND_HOST", "0.0.0.0"),  # nosec
            port=_env_int("PORT", 7860),
            process_role=os.environ.get("PROCESS_ROLE", "all").strip().lower(),
            platform_admin_password=platform_password,
            tenant_master_key=os.environ.get("TENANT_MASTER_KEY", ""),
            session_expire_seconds=_env_int("WEB_TOKEN_EXPIRE", 86400),
            worker_id=os.environ.get("WORKER_ID", f"{socket.gethostname()}-{os.getpid()}"),
            worker_bot_capacity=_env_int("WORKER_BOT_CAPACITY", 10),
            worker_heartbeat_seconds=_env_int("WORKER_HEARTBEAT_SECONDS", 10),
            worker_lease_seconds=_env_int("WORKER_LEASE_SECONDS", 30),
            worker_max_deliveries=_env_int("WORKER_MAX_DELIVERIES", 100),
            tenant_max_deliveries=_env_int("TENANT_MAX_DELIVERIES", 10),
            tenant_pending_deliveries=_env_int("TENANT_PENDING_DELIVERIES", 200),
        )

    def validate(
        self, *, require_bot_api: bool = False, require_platform_auth: bool = False
    ) -> None:
        missing = []
        if not self.database_url:
            missing.append("DATABASE_URL")
        if not self.tenant_master_key:
            missing.append("TENANT_MASTER_KEY")
        if require_bot_api and not self.api_id:
            missing.append("API_ID")
        if require_bot_api and not self.api_hash:
            missing.append("API_HASH")
        if require_platform_auth and not self.platform_admin_password:
            missing.append("PLATFORM_ADMIN_PASSWORD")
        if missing:
            raise RuntimeError("Missing required environment variables: " + ", ".join(missing))
        try:
            Fernet(self.tenant_master_key.encode("ascii"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("TENANT_MASTER_KEY must be a valid Fernet key") from exc
        if require_platform_auth and len(self.platform_admin_password) < 12:
            raise RuntimeError("PLATFORM_ADMIN_PASSWORD must contain at least 12 characters")
        if require_platform_auth and len(self.platform_admin_password) > 1024:
            raise RuntimeError("PLATFORM_ADMIN_PASSWORD must not exceed 1024 characters")
        if (
            require_platform_auth
            and self.platform_admin_password
            # This is the public marker from .env.example, not a credential.
            == "replace-with-a-long-random-password"  # nosec B105
        ):
            raise RuntimeError("PLATFORM_ADMIN_PASSWORD is still set to the example value")
        if self.process_role not in {"all", "web", "worker"}:
            raise RuntimeError("PROCESS_ROLE must be one of: all, web, worker")
        positive = {
            "WEB_TOKEN_EXPIRE": self.session_expire_seconds,
            "WORKER_BOT_CAPACITY": self.worker_bot_capacity,
            "WORKER_HEARTBEAT_SECONDS": self.worker_heartbeat_seconds,
            "WORKER_LEASE_SECONDS": self.worker_lease_seconds,
            "WORKER_MAX_DELIVERIES": self.worker_max_deliveries,
            "TENANT_MAX_DELIVERIES": self.tenant_max_deliveries,
            "TENANT_PENDING_DELIVERIES": self.tenant_pending_deliveries,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise RuntimeError("Configuration values must be positive: " + ", ".join(invalid))
        if self.worker_heartbeat_seconds >= self.worker_lease_seconds:
            raise RuntimeError("WORKER_HEARTBEAT_SECONDS must be less than WORKER_LEASE_SECONDS")
        if self.worker_lease_seconds < self.worker_heartbeat_seconds * 2:
            raise RuntimeError(
                "WORKER_LEASE_SECONDS must be at least twice WORKER_HEARTBEAT_SECONDS"
            )


CONFIG = PlatformConfig.from_env()


def _configure_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        with tempfile.NamedTemporaryFile(dir=".", delete=True):
            pass
        handlers.append(
            RotatingFileHandler("filesharingbot.txt", maxBytes=50_000_000, backupCount=10)
        )
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s",
        datefmt="%d-%b-%y %H:%M:%S",
        handlers=handlers,
    )
    logging.getLogger("pyrogram").setLevel(logging.WARNING)


_configure_logging()
