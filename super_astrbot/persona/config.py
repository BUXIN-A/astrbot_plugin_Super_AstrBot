"""拟人化学习配置：风格模仿 / 群组黑话 / 好感度。

三项子能力各自独立开关、独立参数，默认全部关闭 —— 它们会改变 Bot 的表达与语气，
属于「非侵入式增强」的例外，必须由使用者显式开启。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..spec.capabilities import as_bool, as_float, as_int, as_str, get_path
from ..support import PromptOverrides

DEFAULT_STYLE_APPROVAL = True
"""风格样本默认需人工审批：错误样本会直接改变 Bot 的说话方式。"""

DEFAULT_JARGON_APPROVAL = True
"""黑话默认需人工审批：推断出的词义可能完全错误。"""


@dataclass
class StyleConfig:
    """风格模仿（user→bot 邻接对抽出的表达模式）。"""

    enabled: bool = False
    approval_required: bool = DEFAULT_STYLE_APPROVAL
    max_patterns: int = 200
    min_user_chars: int = 4
    min_bot_chars: int = 2
    max_bot_chars: int = 300
    top_k: int = 3
    min_similarity: float = 0.12
    max_injected_chars: int = 600
    half_life_days: float = 30.0
    weight_floor: float = 0.05

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "StyleConfig":
        return cls(
            enabled=as_bool(get_path(config, "persona.style", False), False),
            approval_required=as_bool(
                get_path(config, "persona.style_approval_required", DEFAULT_STYLE_APPROVAL),
                DEFAULT_STYLE_APPROVAL,
            ),
            max_patterns=as_int(
                get_path(config, "persona.style_max_patterns", 200), 200, low=10, high=2000
            ),
            min_user_chars=as_int(
                get_path(config, "persona.style_min_user_chars", 4), 4, low=1, high=100
            ),
            min_bot_chars=as_int(
                get_path(config, "persona.style_min_bot_chars", 2), 2, low=1, high=100
            ),
            max_bot_chars=as_int(
                get_path(config, "persona.style_max_bot_chars", 300), 300, low=20, high=1000
            ),
            top_k=as_int(get_path(config, "persona.style_top_k", 3), 3, low=1, high=10),
            min_similarity=as_float(
                get_path(config, "persona.style_min_similarity", 0.12), 0.12, low=0.0, high=1.0
            ),
            max_injected_chars=as_int(
                get_path(config, "persona.style_max_injected_chars", 600),
                600,
                low=100,
                high=4000,
            ),
            half_life_days=as_float(
                get_path(config, "persona.style_half_life_days", 30.0), 30.0, low=1.0, high=365.0
            ),
            weight_floor=as_float(
                get_path(config, "persona.style_weight_floor", 0.05), 0.05, low=0.0, high=1.0
            ),
        )


@dataclass
class JargonConfig:
    """群组黑话（统计预筛 + 模型推断词义）。"""

    enabled: bool = False
    approval_required: bool = DEFAULT_JARGON_APPROVAL
    min_frequency: int = 3
    min_chars: int = 2
    max_chars: int = 8
    candidate_limit: int = 8
    min_confidence: float = 0.6
    scan_interval_minutes: int = 360
    max_jargons: int = 200
    inject_max: int = 3
    max_injected_chars: int = 300
    provider_id: str = ""
    timeout_seconds: float = 90.0

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "JargonConfig":
        timeout = as_int(get_path(config, "runtime.llm_timeout_seconds", 45), 45, low=5, high=300)
        return cls(
            enabled=as_bool(get_path(config, "persona.jargon", False), False),
            approval_required=as_bool(
                get_path(config, "persona.jargon_approval_required", DEFAULT_JARGON_APPROVAL),
                DEFAULT_JARGON_APPROVAL,
            ),
            min_frequency=as_int(
                get_path(config, "persona.jargon_min_frequency", 3), 3, low=2, high=50
            ),
            min_chars=as_int(get_path(config, "persona.jargon_min_chars", 2), 2, low=2, high=8),
            max_chars=as_int(get_path(config, "persona.jargon_max_chars", 8), 8, low=3, high=16),
            candidate_limit=as_int(
                get_path(config, "persona.jargon_candidate_limit", 8), 8, low=1, high=30
            ),
            min_confidence=as_float(
                get_path(config, "persona.jargon_min_confidence", 0.6), 0.6, low=0.0, high=1.0
            ),
            scan_interval_minutes=as_int(
                get_path(config, "persona.jargon_scan_interval_minutes", 360),
                360,
                low=10,
                high=10080,
            ),
            max_jargons=as_int(
                get_path(config, "persona.jargon_max_jargons", 200), 200, low=10, high=2000
            ),
            inject_max=as_int(get_path(config, "persona.jargon_inject_max", 3), 3, low=1, high=10),
            max_injected_chars=as_int(
                get_path(config, "persona.jargon_max_injected_chars", 300),
                300,
                low=50,
                high=2000,
            ),
            provider_id=as_str(get_path(config, "persona.jargon_provider_id", "")),
            timeout_seconds=float(min(180, max(20, timeout * 2))),
        )


@dataclass
class AffinityConfig:
    """社交好感度（规则表 + 模型兜底）。"""

    enabled: bool = False
    initial_score: float = 0.5
    min_score: float = 0.0
    max_score: float = 1.0
    decay_half_life_days: float = 14.0
    daily_delta_cap: float = 0.3
    use_llm: bool = True
    provider_id: str = ""
    inject_enabled: bool = True
    max_injected_chars: int = 200
    timeout_seconds: float = 60.0

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "AffinityConfig":
        timeout = as_int(get_path(config, "runtime.llm_timeout_seconds", 45), 45, low=5, high=300)
        initial = as_float(
            get_path(config, "persona.affinity_initial_score", 0.5), 0.5, low=0.0, high=1.0
        )
        return cls(
            enabled=as_bool(get_path(config, "persona.affinity", False), False),
            initial_score=initial,
            min_score=as_float(
                get_path(config, "persona.affinity_min_score", 0.0), 0.0, low=0.0, high=1.0
            ),
            max_score=as_float(
                get_path(config, "persona.affinity_max_score", 1.0), 1.0, low=0.0, high=1.0
            ),
            decay_half_life_days=as_float(
                get_path(config, "persona.affinity_half_life_days", 14.0),
                14.0,
                low=1.0,
                high=365.0,
            ),
            daily_delta_cap=as_float(
                get_path(config, "persona.affinity_daily_delta_cap", 0.3),
                0.3,
                low=0.05,
                high=1.0,
            ),
            use_llm=as_bool(get_path(config, "persona.affinity_use_llm", True), True),
            provider_id=as_str(get_path(config, "persona.affinity_provider_id", "")),
            inject_enabled=as_bool(get_path(config, "persona.affinity_inject_enabled", True), True),
            max_injected_chars=as_int(
                get_path(config, "persona.affinity_max_injected_chars", 200),
                200,
                low=50,
                high=1000,
            ),
            timeout_seconds=float(min(120, max(15, timeout))),
        )


@dataclass
class PersonaConfig:
    """拟人化学习总配置。"""

    style: StyleConfig
    jargon: JargonConfig
    affinity: AffinityConfig
    prompts: PromptOverrides = field(default_factory=PromptOverrides)
    """用户自定义提示词（留空即用内置默认）。"""

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "PersonaConfig":
        result = cls(
            style=StyleConfig.from_mapping(config),
            jargon=JargonConfig.from_mapping(config),
            affinity=AffinityConfig.from_mapping(config),
        )
        result.prompts = PromptOverrides(config)
        return result

    @property
    def any_enabled(self) -> bool:
        return self.style.enabled or self.jargon.enabled or self.affinity.enabled
