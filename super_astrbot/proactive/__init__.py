"""主动交互领域（P4）。

对外出口：

- ``ProactiveService``     双轨调度、竞态保护、免打扰与状态观测
- ``ProactiveConfig``      主动交互参数
- ``MemoryMaterialSource`` 基于记忆服务的素材源（可替换为替身）

本域只依赖 ``Host`` / ``LlmGateway`` / ``StateStore`` 协议，不 import ``astrbot``。
"""

from .config import ProactiveConfig, parse_daily_time
from .materials import MaterialSource, MemoryMaterialSource, ProactiveMaterial
from .service import TRACK_DAILY, TRACK_IDLE, Attempt, ProactiveService

__all__ = [
    "ProactiveService",
    "ProactiveConfig",
    "ProactiveMaterial",
    "MaterialSource",
    "MemoryMaterialSource",
    "Attempt",
    "TRACK_DAILY",
    "TRACK_IDLE",
    "parse_daily_time",
]
