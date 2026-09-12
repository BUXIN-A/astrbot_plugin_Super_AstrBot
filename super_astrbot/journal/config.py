"""周记领域配置。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..spec.capabilities import as_bool, as_int, get_path


@dataclass
class JournalConfig:
    """周记配置。"""

    enabled: bool = True
    allow_user_write: bool = True
    admin_only_write: bool = False
    weekly_reflection: bool = True
    weekly_weekday: int = 6
    weekly_hour: int = 22
    default_tags: tuple[str, ...] = ("生活",)

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "JournalConfig":
        raw_tags = get_path(config, "journal.default_tags", ["生活"])
        if isinstance(raw_tags, (list, tuple)):
            tags = tuple(str(item).strip().lstrip("#") for item in raw_tags if str(item).strip())
        else:
            tags = ("生活",)
        return cls(
            enabled=as_bool(get_path(config, "journal.enabled", True), True),
            allow_user_write=as_bool(get_path(config, "journal.allow_user_write", True), True),
            admin_only_write=as_bool(get_path(config, "journal.admin_only_write", False), False),
            weekly_reflection=as_bool(get_path(config, "journal.weekly_reflection", True), True),
            weekly_weekday=as_int(
                get_path(config, "journal.weekly_reflection_weekday", 6), 6, low=0, high=6
            ),
            weekly_hour=as_int(
                get_path(config, "journal.weekly_reflection_hour", 22), 22, low=0, high=23
            ),
            default_tags=tags or ("生活",),
        )

    def can_write(self, *, is_admin: bool) -> bool:
        """判断当前发送者是否有权写周记。"""
        if not self.enabled:
            return False
        if self.admin_only_write:
            return is_admin
        return self.allow_user_write or is_admin
