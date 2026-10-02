"""共情域：CogEmp 三阶段（识别 → 理解 → 共情）的进程内实现。"""

from .cogemp import (
    STAGE_EMPATHIZE,
    STAGE_IDENTIFY,
    STAGE_UNDERSTAND,
    EmotionHit,
    EmpathyPlan,
    build_guidance,
    identify,
    plan,
    understand,
)
from .config import EmpathyConfig
from .service import EmpathyService

__all__ = [
    "EmpathyConfig",
    "EmpathyService",
    "EmpathyPlan",
    "EmotionHit",
    "STAGE_IDENTIFY",
    "STAGE_UNDERSTAND",
    "STAGE_EMPATHIZE",
    "build_guidance",
    "identify",
    "plan",
    "understand",
]
