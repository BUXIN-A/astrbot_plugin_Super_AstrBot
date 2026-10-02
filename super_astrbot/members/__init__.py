"""群友识别域：身份聚合 → 稳定档案 → 差异化对话策略。

单一职责：把「谁在说话」变成一份可注入的档案，让单人格 Bot 对每位群友
使用不同的语气 / 称呼 / 话题 / 禁忌，而不切换人格本身（拟人化的表层微调）。
"""

from .config import MembersConfig
from .service import MembersService

__all__ = ["MembersConfig", "MembersService"]
