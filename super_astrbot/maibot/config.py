"""MaiBot 增强配置：表达模式按发送者个性化 + 学习产物统一时间衰减。

为什么把两个开关放在同一份配置：
这两项都服务于「复刻 MaiBot 风格的扩展学习」，且统一衰减的参数（半衰期、权重下限、
容量上限、剪枝门槛）本就需要成套配置才有意义；集中在一处可避免风格域与图谱域各自
维护一套阈值而漂移（AstrNa 的经验教训：多源参数必然漂移）。

默认值取向：总开关默认关闭（会改变 Bot 的语言风格，属于非侵入式增强的例外）；
子项默认开启，只有显式关闭才退化到既有行为。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..spec.capabilities import as_bool, as_float, as_int, get_path


@dataclass
class MaiBotConfig:
    """MaiBot 增强（算法复刻）配置。"""

    enabled: bool = False
    """能力总开关；关闭时本域完全旁路。"""

    expression_user_scope: bool = True
    """是否额外按发送者学习/注入表达样本（默认开，用于模仿特定群友）。"""

    time_decay: bool = True
    """是否由本域统一承担学习产物的时间衰减（关闭后需由各域自行维护）。"""

    half_life_days: float = 30.0
    """权重半衰期（天），风格样本与图谱实体共用。"""

    weight_floor: float = 0.05
    """衰减到低于该权重即归档，避免噪声残留。"""

    style_keep: int = 200
    """每个作用域保留的风格样本上限。"""

    graph_keep: int = 2000
    """每个作用域保留的图谱实体上限。"""

    prune_min_weight: float = 0.1
    """关系剪枝的权重门槛，低于该值的关系在维护时清理。"""

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "MaiBotConfig":
        return cls(
            enabled=as_bool(get_path(config, "maibot.enabled", False), False),
            expression_user_scope=as_bool(
                get_path(config, "maibot.expression_user_scope", True), True
            ),
            time_decay=as_bool(get_path(config, "maibot.time_decay", True), True),
            half_life_days=as_float(
                get_path(config, "maibot.time_decay_half_life_days", 30.0),
                30.0,
                low=1.0,
                high=365.0,
            ),
            weight_floor=as_float(
                get_path(config, "maibot.time_decay_weight_floor", 0.05),
                0.05,
                low=0.0,
                high=1.0,
            ),
            style_keep=as_int(get_path(config, "maibot.style_keep", 200), 200, low=10, high=2000),
            graph_keep=as_int(
                get_path(config, "maibot.graph_keep", 2000), 2000, low=100, high=50000
            ),
            prune_min_weight=as_float(
                get_path(config, "maibot.prune_min_weight", 0.1), 0.1, low=0.0, high=1.0
            ),
        )

    @property
    def active(self) -> bool:
        """供依赖解析/日志统一读取的「是否生效」判据。"""
        return self.enabled
