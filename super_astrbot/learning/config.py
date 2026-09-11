"""自我学习（反思）配置。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..spec.capabilities import get_path

MODE_ROUNDS = "rounds"
MODE_INTERVAL = "interval"
MODE_BOTH = "both"
VALID_MODES = (MODE_ROUNDS, MODE_INTERVAL, MODE_BOTH)


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


def _str(config: Mapping[str, Any], path: str, default: str) -> str:
    raw = get_path(config, path, default)
    return str(raw) if raw is not None else default


@dataclass
class ReflectionConfig:
    """反思参数。"""

    enabled: bool = True
    mode: str = MODE_ROUNDS
    trigger_rounds: int = 30
    interval_minutes: int = 720
    provider_id: str = ""
    min_messages: int = 8
    approval_required: bool = False
    max_facts_per_run: int = 5
    cooldown_minutes: int = 60
    timeout_seconds: float = 90.0

    @property
    def max_facts(self) -> int:
        return max(1, min(20, self.max_facts_per_run))

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "ReflectionConfig":
        mode = _str(config, "reflection.mode", MODE_ROUNDS)
        if mode not in VALID_MODES:
            mode = MODE_ROUNDS
        base_timeout = _int(config, "runtime.llm_timeout_seconds", 45, low=5, high=300)
        return cls(
            enabled=_bool(config, "reflection.enabled", True),
            mode=mode,
            trigger_rounds=_int(config, "reflection.trigger_rounds", 30, low=2, high=1000),
            interval_minutes=_int(config, "reflection.interval_minutes", 720, low=1, high=10080),
            provider_id=_str(config, "reflection.provider_id", ""),
            min_messages=_int(config, "reflection.min_messages", 8, low=2, high=200),
            approval_required=_bool(config, "reflection.approval_required", False),
            max_facts_per_run=_int(config, "reflection.max_facts_per_run", 5, low=1, high=20),
            cooldown_minutes=_int(config, "reflection.cooldown_minutes", 60, low=1, high=10080),
            # 反思输出较长，给 2 倍超时并封顶，避免单个任务长期占用预算。
            timeout_seconds=float(min(180, max(20, base_timeout * 2))),
        )
