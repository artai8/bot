"""Immutable per-tenant runtime context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import tenant_defaults


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
        owner_id = int(document["owner_id"])
        admin_ids = {int(value) for value in document.get("admin_ids", [])}
        admin_ids.add(owner_id)
        return cls(
            tenant_id=str(document["_id"]),
            slug=document["slug"],
            display_name=document.get("display_name") or document["slug"],
            owner_id=owner_id,
            admin_ids=frozenset(admin_ids),
            db_channel_id=int(document["db_channel_id"]),
            settings=settings,
            config_version=int(document.get("config_version", 1)),
        )

    def is_admin(self, user_id: int) -> bool:
        return int(user_id) in self.admin_ids

    def setting(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)


def normalize_channel_list(raw: Any) -> list[int]:
    if raw is None:
        return []
    values = raw if isinstance(raw, (list, tuple, set)) else str(raw).replace(",", " ").split()
    result: list[int] = []
    for value in values:
        try:
            channel_id = int(value)
        except (TypeError, ValueError):
            continue
        if channel_id not in result:
            result.append(channel_id)
    return result
