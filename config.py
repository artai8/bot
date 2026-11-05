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
import warnings
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from cryptography.fernet import Fernet


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "y"}:
            return True
        if normalized in {"0", "false", "no", "off", "n"}:
            return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_int_list(name: str) -> list[int]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return []
    result: list[int] = []
    for part in raw.replace(",", " ").split():
        try:
            result.append(int(part))
        except ValueError:
            continue
    return result


_BASE_TENANT_SETTINGS: dict[str, Any] = {
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
        "你好 {first} 👋\n\n我是文件分享机器人，可以通过专属分享链接获取资源。",
    ),
    "force_sub_message": os.environ.get(
        "FORCE_SUB_MESSAGE",
        "你好 {first} 👋\n\n在使用本机器人前，请先加入以下频道后再点击下方按钮重试：",
    ),
    "auto_delete_message": os.environ.get(
        "AUTO_DELETE_MSG",
        "\n\n⚠️ 此消息将在 {time} 后自动删除，请及时保存。",
    ),
    "user_reply_text": os.environ.get(
        "USER_REPLY_TEXT",
        "❌ 请勿直接向我发送消息，请通过分享链接访问资源。",
    ),
    "about_text": os.environ.get(
        "ABOUT_TEXT",
        "<b>关于本机器人</b>\n\n本机器人提供安全、便捷的文件分享服务。",
    ),
    "help_text": os.environ.get(
        "HELP_TEXT",
        "🤖 <b>使用说明：</b>\n点击分享链接即可获取文件。",
    ),
    "admin_help_text": os.environ.get(
        "ADMIN_HELP_TEXT",
        "🛠 <b>管理员指令：</b>\n/share - 创建合集\n/batch - 批量生成\n/genlink - 单条链接\n/stats - 统计信息\n/broadcast - 广播消息\n/ban - 封禁用户\n/unban - 解封用户\n/banned - 封禁列表\n/backup - 导出备份",
    ),
    "keyword_button_text": os.environ.get("KEYWORD_BUTTON_TEXT", "📥 获取资源"),
    "force_sub_require_msg": False,
    "force_sub_msg_prompt": "\n\n💬 <b>附加解锁条件：</b>加入后，请在该频道/关联讨论群内<b>发送一条任意消息</b>，随后点击下方",
    "pay_to_unlock": False,
    "pay_unlock_expire": 0,
    "pay_enable_stars": False,
    "pay_amount_stars": 50,
    "pay_enable_usdt": False,
    "pay_amount_usdt": "5.00",
    "pay_usdt_network": "TRC20",
    "pay_usdt_address": "",
    "pay_enable_cashier": False,
    "pay_custom_url": "",
    "pay_webhook_secret": "",
    "pay_enable_cdkey": True,
    "pay_unlock_message": "🔒 <b>当前资源需付费解锁后访问</b>\n\n请选择以下任意一种已启用的方式完成解锁：",
}

DEFAULT_TENANT_SETTINGS: MappingProxyType[str, Any] = MappingProxyType(_BASE_TENANT_SETTINGS)


def tenant_defaults() -> dict[str, Any]:
    data = copy.deepcopy(dict(DEFAULT_TENANT_SETTINGS))
    data["force_sub_channels"] = list(data.get("force_sub_channels") or [])
    data["bound_channels"] = list(data.get("bound_channels") or [])
    return data


@dataclass(frozen=True)
class PlatformConfig:
    api_id: int = 0
    api_hash: str = ""
    database_url: str = ""
    database_name: str = "filesharing"
    bind_host: str = "0.0.0.0"
    port: int = 7860
    process_role: str = "all"
    platform_admin_id: str = "admin"
    platform_admin_password: str = "admin"
    platform_admin_path: str = "admin"
    tenant_master_key: str = ""
    session_expire_seconds: int = 86400
    worker_id: str = ""
    worker_bot_capacity: int = 10
    worker_heartbeat_seconds: int = 10
    worker_lease_seconds: int = 30
    worker_max_deliveries: int = 100
    tenant_max_deliveries: int = 10
    tenant_pending_deliveries: int = 200
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> PlatformConfig:
        admin_pw = os.environ.get("PLATFORM_ADMIN_PASSWORD", "").strip()
        legacy_pw = os.environ.get("WEB_ADMIN_PASSWORD", "").strip()
        if not admin_pw and legacy_pw:
            warnings.warn(
                "WEB_ADMIN_PASSWORD is deprecated; use PLATFORM_ADMIN_PASSWORD or database admin settings",
                FutureWarning,
                stacklevel=2,
            )
            admin_pw = legacy_pw
        if not admin_pw:
            admin_pw = "admin"

        admin_id = os.environ.get("PLATFORM_ADMIN_ID", "").strip() or "admin"
        admin_path = os.environ.get("PLATFORM_ADMIN_PATH", "").strip().strip("/") or "admin"

        db_url = (
            os.environ.get("DATABASE_URL", "").strip()
            or os.environ.get("MONGODB_URI", "").strip()
        )
        db_name = (
            os.environ.get("DATABASE_NAME", "").strip()
            or os.environ.get("MONGODB_DATABASE", "").strip()
            or "filesharing"
        )
        master_key = os.environ.get("TENANT_MASTER_KEY", "").strip()

        return cls(
            api_id=_env_int("API_ID", _env_int("TELEGRAM_API_ID", 0)),
            api_hash=os.environ.get("API_HASH", os.environ.get("TELEGRAM_API_HASH", "")).strip(),
            database_url=db_url,
            database_name=db_name,
            bind_host=os.environ.get("WEB_BIND_HOST", os.environ.get("BIND_HOST", "0.0.0.0")).strip() or "0.0.0.0",
            port=_env_int("PORT", 7860),
            process_role=os.environ.get("PROCESS_ROLE", "all").strip().lower() or "all",
            platform_admin_id=admin_id,
            platform_admin_password=admin_pw,
            platform_admin_path=admin_path,
            tenant_master_key=master_key,
            session_expire_seconds=_env_int("WEB_TOKEN_EXPIRE", 86400),
            worker_id=os.environ.get("WORKER_ID", f"{socket.gethostname()}-{os.getpid()}"),
            worker_bot_capacity=_env_int("WORKER_BOT_CAPACITY", 10),
            worker_heartbeat_seconds=_env_int("WORKER_HEARTBEAT_SECONDS", 10),
            worker_lease_seconds=_env_int("WORKER_LEASE_SECONDS", 30),
            worker_max_deliveries=_env_int("WORKER_MAX_DELIVERIES", 100),
            tenant_max_deliveries=_env_int("TENANT_MAX_DELIVERIES", 10),
            tenant_pending_deliveries=_env_int("TENANT_PENDING_DELIVERIES", 200),
            log_level=os.environ.get("LOG_LEVEL", "INFO").strip() or "INFO",
        )

    def validate(self, require_bot_api: bool = True, require_platform_auth: bool = False) -> None:
        missing: list[str] = []
        if not self.database_url:
            missing.append("DATABASE_URL")
        if not self.tenant_master_key:
            missing.append("TENANT_MASTER_KEY")
        if require_bot_api:
            if not self.api_id:
                missing.append("API_ID")
            if not self.api_hash:
                missing.append("API_HASH")
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

        try:
            Fernet(self.tenant_master_key.encode("ascii"))
        except Exception as exc:
            raise RuntimeError("TENANT_MASTER_KEY must be a valid Fernet key") from exc

        if self.process_role not in {"all", "both", "web", "worker"}:
            raise RuntimeError(f"Invalid PROCESS_ROLE: {self.process_role}")

        for field_name in (
            "session_expire_seconds",
            "worker_bot_capacity",
            "worker_heartbeat_seconds",
            "worker_lease_seconds",
            "worker_max_deliveries",
            "tenant_max_deliveries",
            "tenant_pending_deliveries",
        ):
            if getattr(self, field_name) <= 0:
                raise RuntimeError(f"{field_name} must be greater than 0")


def _configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


Config = PlatformConfig
CONFIG = PlatformConfig.from_env()
