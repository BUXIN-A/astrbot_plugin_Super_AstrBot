"""自动审核配置。

为什么要「信封式」默认值而不是直接信任配置：

- 自动审核一旦跑偏会批量改写待审结论，所以所有数值都做**上下界裁剪**，
  写错配置（例如批次写成 100000）不会把队列一次抽干、也不会把超时设成 1 秒；
- ``use_llm`` 单独做成开关：用户可能只想用规则兜底、完全不想为此花模型预算，
  此时规则拿不准就停在 ``unsure``，交回人工而不是调用模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..spec.capabilities import as_bool, as_float, as_int, as_str, as_str_tuple, get_path
from ..support import PromptOverrides

DEFAULT_INTERVAL_MINUTES = 30
DEFAULT_BATCH_LIMIT = 20
DEFAULT_MIN_CHARS = 6
DEFAULT_MAX_CHARS = 400
DEFAULT_MIN_CONFIDENCE = 0.6
DEFAULT_TIMEOUT_SECONDS = 60.0


@dataclass
class ReviewConfig:
    """自动审核参数。"""

    enabled: bool = False
    """总开关（``review.auto``）；关闭时 ``run_once`` 直接空转。"""

    use_llm: bool = True
    """规则不确定时是否用模型兜底（``review.auto_use_llm``）。"""

    provider_id: str = ""
    """指定模型提供商；空串表示走默认提供商。"""

    interval_minutes: int = DEFAULT_INTERVAL_MINUTES
    """扫描周期（分钟）。"""

    batch_limit: int = DEFAULT_BATCH_LIMIT
    """单次扫描最多处理多少条，避免一次占用过久。"""

    min_chars: int = DEFAULT_MIN_CHARS
    max_chars: int = DEFAULT_MAX_CHARS
    """文本长度区间；越界直接驳回（碎片与超长灌水都不该进长期数据）。"""

    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    """带 ``confidence`` 字段的来源低于该值不自动通过，交回人工。"""

    reject_sensitive: bool = True
    """命中敏感词直接驳回；关闭后敏感内容退回人工。"""

    blocked_words: tuple[str, ...] = ()
    """用户自定义敏感词（与内置词表并集使用）。"""

    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    """单次模型调用超时（复用全局 ``runtime.llm_timeout_seconds``）。"""

    prompts: PromptOverrides = field(default_factory=PromptOverrides)
    """用户自定义提示词（留空即用内置默认）。"""

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "ReviewConfig":
        result = cls(
            enabled=as_bool(get_path(config, "review.auto", False), False),
            use_llm=as_bool(get_path(config, "review.auto_use_llm", True), True),
            provider_id=as_str(get_path(config, "review.auto_provider_id", "")),
            interval_minutes=as_int(
                get_path(config, "review.auto_interval_minutes", DEFAULT_INTERVAL_MINUTES),
                DEFAULT_INTERVAL_MINUTES,
                low=5,
                high=1440,
            ),
            batch_limit=as_int(
                get_path(config, "review.auto_batch_limit", DEFAULT_BATCH_LIMIT),
                DEFAULT_BATCH_LIMIT,
                low=1,
                high=200,
            ),
            min_chars=as_int(
                get_path(config, "review.auto_min_chars", DEFAULT_MIN_CHARS),
                DEFAULT_MIN_CHARS,
                low=2,
                high=100,
            ),
            max_chars=as_int(
                get_path(config, "review.auto_max_chars", DEFAULT_MAX_CHARS),
                DEFAULT_MAX_CHARS,
                low=50,
                high=2000,
            ),
            min_confidence=as_float(
                get_path(config, "review.auto_min_confidence", DEFAULT_MIN_CONFIDENCE),
                DEFAULT_MIN_CONFIDENCE,
                low=0.0,
                high=1.0,
            ),
            reject_sensitive=as_bool(get_path(config, "review.auto_reject_sensitive", True), True),
            blocked_words=as_str_tuple(get_path(config, "review.auto_blocked_words", [])),
            timeout_seconds=as_float(
                get_path(config, "runtime.llm_timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
                DEFAULT_TIMEOUT_SECONDS,
                low=15.0,
                high=120.0,
            ),
        )
        result.prompts = PromptOverrides(config)
        return result
