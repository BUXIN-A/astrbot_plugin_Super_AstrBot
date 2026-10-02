"""自我学习领域（反思与整合）。

对外出口：

- ``ReflectionService``    反思执行、触发判定、待审队列审批
- ``ReflectionConfig``     反思配置
- ``ConsolidationService`` 周期性整合零散记忆（默认关闭）
- ``ConsolidationConfig``  整合配置
"""

from .config import (
    MODE_BOTH,
    MODE_INTERVAL,
    MODE_ROUNDS,
    ReflectionConfig,
)
from .consolidation import MODE_SCOPE, MODE_SEMANTIC, ConsolidationConfig, ConsolidationService
from .prompts import (
    REFLECTION_SYSTEM,
    build_reflection_prompt,
    build_weekly_prompt,
    parse_insights,
    parse_reflection,
)
from .service import ReflectionOutcome, ReflectionService

__all__ = [
    "ReflectionService",
    "ReflectionConfig",
    "ReflectionOutcome",
    "ConsolidationService",
    "ConsolidationConfig",
    "MODE_ROUNDS",
    "MODE_INTERVAL",
    "MODE_BOTH",
    "MODE_SCOPE",
    "MODE_SEMANTIC",
    "REFLECTION_SYSTEM",
    "build_reflection_prompt",
    "build_weekly_prompt",
    "parse_insights",
    "parse_reflection",
]
