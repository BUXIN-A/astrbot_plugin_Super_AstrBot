"""自我学习（反思）配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..spec.capabilities import as_bool, as_int, as_str, get_path
from ..support import PromptOverrides

MODE_ROUNDS = "rounds"
MODE_INTERVAL = "interval"
MODE_BOTH = "both"
VALID_MODES = (MODE_ROUNDS, MODE_INTERVAL, MODE_BOTH)


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
    prompts: PromptOverrides = field(default_factory=PromptOverrides)
    """用户自定义提示词（留空即用内置默认）。"""

    @property
    def max_facts(self) -> int:
        return max(1, min(20, self.max_facts_per_run))

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "ReflectionConfig":
        mode = as_str(get_path(config, "reflection.mode", MODE_ROUNDS))
        if mode not in VALID_MODES:
            mode = MODE_ROUNDS
        base_timeout = as_int(
            get_path(config, "runtime.llm_timeout_seconds", 45), 45, low=5, high=300
        )
        result = cls(
            enabled=as_bool(get_path(config, "reflection.enabled", True), True),
            mode=mode,
            trigger_rounds=as_int(
                get_path(config, "reflection.trigger_rounds", 30), 30, low=2, high=1000
            ),
            interval_minutes=as_int(
                get_path(config, "reflection.interval_minutes", 720), 720, low=1, high=10080
            ),
            provider_id=as_str(get_path(config, "reflection.provider_id", "")),
            min_messages=as_int(get_path(config, "reflection.min_messages", 8), 8, low=2, high=200),
            approval_required=as_bool(
                get_path(config, "reflection.approval_required", False), False
            ),
            max_facts_per_run=as_int(
                get_path(config, "reflection.max_facts_per_run", 5), 5, low=1, high=20
            ),
            cooldown_minutes=as_int(
                get_path(config, "reflection.cooldown_minutes", 60), 60, low=1, high=10080
            ),
            # 反思输出较长，给 2 倍超时并封顶，避免单个任务长期占用预算。
            timeout_seconds=float(min(180, max(20, base_timeout * 2))),
        )
        result.prompts = PromptOverrides(config)
        return result
