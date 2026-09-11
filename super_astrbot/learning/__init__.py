"""自我学习领域（反思）。

对外出口：

- ``ReflectionService``  反思执行、触发判定、待审队列审批
- ``ReflectionConfig``   反思配置
"""

from .config import (
    MODE_BOTH,
    MODE_INTERVAL,
    MODE_ROUNDS,
    ReflectionConfig,
)
from .prompts import (
    REFLECTION_SYSTEM,
    build_reflection_prompt,
    build_weekly_prompt,
    parse_insights,
)
from .service import ReflectionOutcome, ReflectionService

__all__ = [
    "ReflectionService",
    "ReflectionConfig",
    "ReflectionOutcome",
    "MODE_ROUNDS",
    "MODE_INTERVAL",
    "MODE_BOTH",
    "REFLECTION_SYSTEM",
    "build_reflection_prompt",
    "build_weekly_prompt",
    "parse_insights",
]
