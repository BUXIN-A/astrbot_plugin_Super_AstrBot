"""主动交互配置。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..spec.capabilities import as_bool, as_int, as_str, as_str_tuple, get_path

DEFAULT_DAILY_TIME = "10:00"
DEFAULT_DAILY_MAX = 1
DEFAULT_BUSY_GUARD_MINUTES = 10
DEFAULT_IDLE_MINUTES = 180
DEFAULT_IDLE_CHECK_MINUTES = 15
DEFAULT_QUIET_START = 23
DEFAULT_QUIET_END = 8
DEFAULT_MAX_CHARS = 80
DEFAULT_MATERIAL_MEMORIES = 8
DEFAULT_MATERIAL_JOURNALS = 3


def parse_daily_time(value: Any) -> tuple[int, int]:
    """把 ``"HH:MM"`` 解析为 ``(hour, minute)``；非法输入回退到 10:00。"""
    text = as_str(value, DEFAULT_DAILY_TIME).strip()
    hour_text, _, minute_text = text.partition(":")
    try:
        hour = int(hour_text)
        minute = int(minute_text or 0)
    except (TypeError, ValueError):
        return 10, 0
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return 10, 0
    return hour, minute


@dataclass
class ProactiveConfig:
    """主动交互参数。"""

    enabled: bool = False
    targets: tuple[str, ...] = ()
    """目标会话 UMO 列表；只对这些会话主动发送，不做自动发现。"""

    daily_enabled: bool = True
    daily_hour: int = 10
    daily_minute: int = 0

    idle_enabled: bool = False
    idle_minutes: int = DEFAULT_IDLE_MINUTES
    idle_check_minutes: int = DEFAULT_IDLE_CHECK_MINUTES

    busy_guard_minutes: int = DEFAULT_BUSY_GUARD_MINUTES
    """静默不足该时长即视为「正在聊天」，两条轨道都不打扰。"""

    daily_max: int = DEFAULT_DAILY_MAX
    """单个会话每日主动消息上限（两条轨道共享）。"""

    quiet_start: int = DEFAULT_QUIET_START
    quiet_end: int = DEFAULT_QUIET_END
    """免打扰时段（本地时间整点，支持跨午夜；起止相同表示不启用）。"""

    max_chars: int = DEFAULT_MAX_CHARS
    material_memories: int = DEFAULT_MATERIAL_MEMORIES
    material_journals: int = DEFAULT_MATERIAL_JOURNALS
    provider_id: str = ""

    def in_quiet_hours(self, hour: int) -> bool:
        """当前小时是否处于免打扰时段。"""
        start, end = self.quiet_start, self.quiet_end
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "ProactiveConfig":
        hour, minute = parse_daily_time(
            get_path(config, "proactive.daily_time", DEFAULT_DAILY_TIME)
        )
        return cls(
            enabled=as_bool(get_path(config, "proactive.enabled", False), False),
            targets=as_str_tuple(get_path(config, "proactive.targets", [])),
            daily_enabled=as_bool(get_path(config, "proactive.daily_enabled", True), True),
            daily_hour=hour,
            daily_minute=minute,
            idle_enabled=as_bool(get_path(config, "proactive.idle_enabled", False), False),
            idle_minutes=as_int(
                get_path(config, "proactive.idle_minutes", DEFAULT_IDLE_MINUTES),
                DEFAULT_IDLE_MINUTES,
                low=5,
                high=1440,
            ),
            idle_check_minutes=as_int(
                get_path(config, "proactive.idle_check_minutes", DEFAULT_IDLE_CHECK_MINUTES),
                DEFAULT_IDLE_CHECK_MINUTES,
                low=1,
                high=180,
            ),
            busy_guard_minutes=as_int(
                get_path(config, "proactive.busy_guard_minutes", DEFAULT_BUSY_GUARD_MINUTES),
                DEFAULT_BUSY_GUARD_MINUTES,
                low=0,
                high=240,
            ),
            daily_max=as_int(
                get_path(config, "proactive.daily_max", DEFAULT_DAILY_MAX),
                DEFAULT_DAILY_MAX,
                low=1,
                high=24,
            ),
            quiet_start=as_int(
                get_path(config, "proactive.quiet_start", DEFAULT_QUIET_START),
                DEFAULT_QUIET_START,
                low=0,
                high=23,
            ),
            quiet_end=as_int(
                get_path(config, "proactive.quiet_end", DEFAULT_QUIET_END),
                DEFAULT_QUIET_END,
                low=0,
                high=23,
            ),
            max_chars=as_int(
                get_path(config, "proactive.max_chars", DEFAULT_MAX_CHARS),
                DEFAULT_MAX_CHARS,
                low=10,
                high=500,
            ),
            material_memories=as_int(
                get_path(config, "proactive.material_memories", DEFAULT_MATERIAL_MEMORIES),
                DEFAULT_MATERIAL_MEMORIES,
                low=0,
                high=50,
            ),
            material_journals=as_int(
                get_path(config, "proactive.material_journals", DEFAULT_MATERIAL_JOURNALS),
                DEFAULT_MATERIAL_JOURNALS,
                low=0,
                high=20,
            ),
            provider_id=as_str(get_path(config, "proactive.provider_id", "")),
        )
