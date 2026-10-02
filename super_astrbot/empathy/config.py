"""共情管线配置（CogEmp 三阶段）。

默认取向：总开关默认开（P0 融合能力），但仅在**识别到情绪信号**时才注入指引，
中性对话零影响；三阶段默认全开，可单独关闭用于定位问题。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..spec.capabilities import as_bool, as_float, as_int, get_path


@dataclass
class EmpathyConfig:
    """共情管线参数。"""

    enabled: bool = True
    """能力总开关；关闭时本域完全旁路。"""

    stage_identify: bool = True
    """阶段①情绪识别；关闭后整条管线不产出（无信号可依）。"""

    stage_understand: bool = True
    """阶段②原因理解；关闭后指引里不含话题域线索。"""

    stage_empathize: bool = True
    """阶段③认知共情；关闭后不注入任何指引（保留事件留痕）。"""

    temperature: float = 0.55
    """共情温度（0~1）：低=克制理性，高=温暖陪伴。"""

    min_intensity: float = 0.35
    """触发注入的最低情绪强度；低于门槛只记录不注入。"""

    max_injected_chars: int = 300
    """指引文本字符上限。"""

    log_limit: int = 50
    """共情日志默认返回条数上限。"""

    def stages(self) -> tuple[str, ...]:
        from .cogemp import STAGE_EMPATHIZE, STAGE_IDENTIFY, STAGE_UNDERSTAND

        active: list[str] = []
        if self.stage_identify:
            active.append(STAGE_IDENTIFY)
        if self.stage_understand:
            active.append(STAGE_UNDERSTAND)
        if self.stage_empathize:
            active.append(STAGE_EMPATHIZE)
        return tuple(active)

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "EmpathyConfig":
        return cls(
            enabled=as_bool(get_path(config, "empathy.enabled", True), True),
            stage_identify=as_bool(get_path(config, "empathy.stage_identify", True), True),
            stage_understand=as_bool(get_path(config, "empathy.stage_understand", True), True),
            stage_empathize=as_bool(get_path(config, "empathy.stage_empathize", True), True),
            temperature=as_float(
                get_path(config, "empathy.temperature", 0.55), 0.55, low=0.0, high=1.0
            ),
            min_intensity=as_float(
                get_path(config, "empathy.min_intensity", 0.35), 0.35, low=0.0, high=1.0
            ),
            max_injected_chars=as_int(
                get_path(config, "empathy.max_injected_chars", 300), 300, low=80, high=1200
            ),
            log_limit=as_int(get_path(config, "empathy.log_limit", 50), 50, low=5, high=200),
        )
