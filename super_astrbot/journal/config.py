"""周记领域配置。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..spec.capabilities import get_path


def _bool(config: Mapping[str, Any], path: str, default: bool) -> bool:
    raw = get_path(config, path, default)
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on", "是", "开启"}
    return default


def _int(config: Mapping[str, Any], path: str, default: int, *, low: int, high: int) -> int:
    raw = get_path(config, path, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


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
            enabled=_bool(config, "journal.enabled", True),
            allow_user_write=_bool(config, "journal.allow_user_write", True),
            admin_only_write=_bool(config, "journal.admin_only_write", False),
            weekly_reflection=_bool(config, "journal.weekly_reflection", True),
            weekly_weekday=_int(config, "journal.weekly_reflection_weekday", 6, low=0, high=6),
            weekly_hour=_int(config, "journal.weekly_reflection_hour", 22, low=0, high=23),
            default_tags=tags or ("生活",),
        )

    def can_write(self, *, is_admin: bool) -> bool:
        """判断当前发送者是否有权写周记。"""
        if not self.enabled:
            return False
        if self.admin_only_write:
            return is_admin
        return self.allow_user_write or is_admin
