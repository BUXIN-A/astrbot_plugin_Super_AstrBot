"""群聊语义领域（P3）。

对外出口：

- ``GroupChatService``  读空气决策、冷却与配额、并发合并
- ``GroupConfig``       群聊语义参数

本域只消费 ``EventView`` / ``GroupSignals`` 与 ``GroupDecision``，
不 import ``astrbot``，也不直接改写事件对象（落地由 harness 负责）。
"""

from .attention import AttentionScore, score_message
from .config import GroupConfig
from .service import GroupChatService

__all__ = [
    "GroupChatService",
    "GroupConfig",
    "AttentionScore",
    "score_message",
]
