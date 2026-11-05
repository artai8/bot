"""Tenant runtime context and channel normalization utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import DEFAULT_TENANT_SETTINGS, tenant_defaults


def normalize_channel_list(raw: Any) -> list[int]:
    if not raw:
        return []
    if isinstance(raw, (int, float)):
        val = int(raw)
        return [val] if val != 0 else []
    if isinstance(raw, str):
        items = raw.replace(",", " ").split()
    elif isinstance(raw, (list, tuple, set, frozenset)):
        items = list(raw)
    else:
        return []
    result: list[int] = []
    seen: set[int] = set()
    for item in items:
        try:
            cid = int(str(item).strip())
            if cid != 0 and cid not in seen:
                seen.add(cid)
                result.append(cid)
        except (ValueError, TypeError):
            continue
    return result


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    slug: str
    display_name: str
    owner_id: int
    admin_ids: frozenset[int]
    db_channel_id: int
    settings: dict[str, Any]
    config_version: int

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> TenantContext:
        settings = tenant_defaults()
        settings.update(document.get("settings") or {})
        settings["force_sub_channels"] = normalize_channel_list(settings.get("force_sub_channels"))
        settings["bound_channels"] = normalize_channel_list(settings.get("bound_channels"))
        raw_owner = document.get("owner_id")
        try:
            owner_id = int(raw_owner) if raw_owner is not None and str(raw_owner).strip() != "" else 0
        except (ValueError, TypeError):
            owner_id = 0

        admin_ids: set[int] = set()
        for value in document.get("admin_ids") or []:
            try:
                aid = int(value)
                if aid != 0:
                    admin_ids.add(aid)
            except (ValueError, TypeError):
                continue
        if owner_id != 0:
            admin_ids.add(owner_id)

        raw_db_channel = document.get("db_channel_id")
        try:
            db_channel_id = int(raw_db_channel) if raw_db_channel is not None and str(raw_db_channel).strip() != "" else 0
        except (ValueError, TypeError):
            db_channel_id = 0

        return cls(
            tenant_id=str(document["_id"]),
            slug=str(document.get("slug") or ""),
            display_name=str(document.get("display_name") or document.get("slug") or ""),
            owner_id=owner_id,
            admin_ids=frozenset(admin_ids),
            db_channel_id=db_channel_id,
            settings=settings,
            config_version=int(document.get("config_version", 1)),
        )

    def is_admin(self, user_id: int) -> bool:
        if not user_id:
            return False
        return int(user_id) == self.owner_id or int(user_id) in self.admin_ids

    def setting(self, key: str, default: Any = None) -> Any:
        if key in self.settings:
            return self.settings[key]
        if default is not None:
            return default
        return DEFAULT_TENANT_SETTINGS.get(key)
