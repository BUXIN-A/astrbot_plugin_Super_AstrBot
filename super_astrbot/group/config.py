"""群聊语义配置。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..spec.capabilities import as_bool, as_float, as_int, as_str_tuple, get_path

DEFAULT_ATTENTION_THRESHOLD = 0.55
DEFAULT_COOLDOWN_SECONDS = 90
DEFAULT_MAX_PER_HOUR = 6
DEFAULT_MERGE_WINDOW_SECONDS = 1.2
DEFAULT_MERGE_MAX_MESSAGES = 4
DEFAULT_MERGE_MAX_CHARS = 800


@dataclass
class GroupConfig:
    """群聊「读空气」参数。"""

    enabled: bool = False
    attention_threshold: float = DEFAULT_ATTENTION_THRESHOLD
    """插话所需的注意力得分下限（0~1）。越高越「沉默」。"""

    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS
    """两次主动插话之间的最短间隔（秒）。"""

    max_per_hour: int = DEFAULT_MAX_PER_HOUR
    """同一会话每小时最多主动插话次数。"""

    merge_window_seconds: float = DEFAULT_MERGE_WINDOW_SECONDS
    """并发合并窗口（秒）：窗口内到达的同一会话消息合并为一次请求；0 表示关闭。"""

    merge_max_messages: int = DEFAULT_MERGE_MAX_MESSAGES
    merge_max_chars: int = DEFAULT_MERGE_MAX_CHARS

    whitelist: tuple[str, ...] = ()
    """只在这些群生效（空表示不限）；填群号或完整会话 UMO。"""

    blacklist: tuple[str, ...] = ()
    """这些群不主动插话（优先级高于白名单）。"""

    bot_aliases: tuple[str, ...] = ()
    """Bot 的称呼词；出现即视为点名（仅在未被 @ 时作为加分信号）。"""

    def allows(self, *, umo: str, group_id: str) -> bool:
        """名单判定：只约束「主动插话」，不影响被直接提及时的回复。"""
        candidates = {umo, group_id, umo.rsplit(":", 1)[-1]}
        candidates.discard("")
        if self.blacklist and candidates & set(self.blacklist):
            return False
        if self.whitelist:
            return bool(candidates & set(self.whitelist))
        return True

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "GroupConfig":
        return cls(
            enabled=as_bool(get_path(config, "group.enabled", False), False),
            attention_threshold=as_float(
                get_path(config, "group.attention_threshold", DEFAULT_ATTENTION_THRESHOLD),
                DEFAULT_ATTENTION_THRESHOLD,
                low=0.05,
                high=1.0,
            ),
            cooldown_seconds=as_int(
                get_path(config, "group.cooldown_seconds", DEFAULT_COOLDOWN_SECONDS),
                DEFAULT_COOLDOWN_SECONDS,
                low=0,
                high=7200,
            ),
            max_per_hour=as_int(
                get_path(config, "group.max_per_hour", DEFAULT_MAX_PER_HOUR),
                DEFAULT_MAX_PER_HOUR,
                low=1,
                high=120,
            ),
            merge_window_seconds=as_float(
                get_path(config, "group.merge_window_seconds", DEFAULT_MERGE_WINDOW_SECONDS),
                DEFAULT_MERGE_WINDOW_SECONDS,
                low=0.0,
                high=10.0,
            ),
            merge_max_messages=as_int(
                get_path(config, "group.merge_max_messages", DEFAULT_MERGE_MAX_MESSAGES),
                DEFAULT_MERGE_MAX_MESSAGES,
                low=1,
                high=20,
            ),
            merge_max_chars=as_int(
                get_path(config, "group.merge_max_chars", DEFAULT_MERGE_MAX_CHARS),
                DEFAULT_MERGE_MAX_CHARS,
                low=100,
                high=8000,
            ),
            whitelist=as_str_tuple(get_path(config, "group.whitelist", [])),
            blacklist=as_str_tuple(get_path(config, "group.blacklist", [])),
            bot_aliases=as_str_tuple(get_path(config, "group.bot_aliases", [])),
        )
