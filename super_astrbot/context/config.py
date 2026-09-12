"""上下文治理配置。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..spec.capabilities import as_bool, as_int, as_str, get_path
from ..support import PromptOverrides

DEFAULT_MAX_TOKENS = 6000
DEFAULT_KEEP_RECENT = 12
DEFAULT_MIN_MESSAGES = 8
MAX_SUMMARY_CHARS = 600


@dataclass
class ContextConfig:
    """请求级上下文治理参数。

    ``max_tokens`` 同时是「触发水位线」与「目标上限」：估算未超过它时完全不干预，
    超过时才依次做占位压缩与历史摘要。
    """

    enabled: bool = False
    max_tokens: int = DEFAULT_MAX_TOKENS
    keep_recent: int = DEFAULT_KEEP_RECENT
    min_messages: int = DEFAULT_MIN_MESSAGES
    summary_provider_id: str = ""
    prompts: PromptOverrides = field(default_factory=PromptOverrides)
    """用户自定义提示词（留空即用内置默认）。"""

    @property
    def protected_messages(self) -> int:
        """永不参与压缩的尾部消息条数（至少 2 条，保证模型能看到最近一轮问答）。"""
        return max(2, self.keep_recent)

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "ContextConfig":
        result = cls(
            enabled=as_bool(get_path(config, "context.enabled", False), False),
            max_tokens=as_int(
                get_path(config, "context.max_tokens", DEFAULT_MAX_TOKENS),
                DEFAULT_MAX_TOKENS,
                low=1000,
                high=60000,
            ),
            keep_recent=as_int(
                get_path(config, "context.keep_recent", DEFAULT_KEEP_RECENT),
                DEFAULT_KEEP_RECENT,
                low=2,
                high=100,
            ),
            min_messages=as_int(
                get_path(config, "context.min_messages", DEFAULT_MIN_MESSAGES),
                DEFAULT_MIN_MESSAGES,
                low=4,
                high=100,
            ),
            summary_provider_id=as_str(get_path(config, "context.summary_provider_id", "")),
        )
        result.prompts = PromptOverrides(config)
        return result
