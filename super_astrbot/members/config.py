"""群友识别配置。

默认值取向：默认开启（P0 融合能力），但只有「已建档 / 已蒸馏」的群友才会注入
策略文本——没有任何档案时行为与未安装本域完全一致，属于零影响增强。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..spec.capabilities import as_bool, as_float, as_int, get_path


@dataclass
class MembersConfig:
    """群友识别与差异化策略参数。"""

    enabled: bool = True
    """能力总开关；关闭时本域完全旁路（不建档案、不注入）。"""

    inject_strategy: bool = True
    """是否把群友策略注入提示词；默认开启，仅在存在档案时生效。"""

    max_members: int = 200
    """档案聚合时最多处理多少条身份观测，防止超大群拖慢面板。"""

    max_injected_chars: int = 320
    """策略注入文本的字符上限，避免挤占上下文。"""

    distill_top_memories: int = 5
    """在线蒸馏时取样多少条该群友的高权重记忆作为摘要素材。"""

    profile_ttl_days: float = 60.0
    """档案的「新鲜度」窗口（天）：超过该时长未互动的档案标记为久未联系。"""

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "MembersConfig":
        return cls(
            enabled=as_bool(get_path(config, "members.enabled", True), True),
            inject_strategy=as_bool(
                get_path(config, "members.inject_strategy", True), True
            ),
            max_members=as_int(
                get_path(config, "members.max_members", 200), 200, low=10, high=5000
            ),
            max_injected_chars=as_int(
                get_path(config, "members.max_injected_chars", 320), 320, low=80, high=1200
            ),
            distill_top_memories=as_int(
                get_path(config, "members.distill_top_memories", 5), 5, low=1, high=20
            ),
            profile_ttl_days=as_float(
                get_path(config, "members.profile_ttl_days", 60.0),
                60.0,
                low=1.0,
                high=3650.0,
            ),
        )

    @property
    def active(self) -> bool:
        return self.enabled
