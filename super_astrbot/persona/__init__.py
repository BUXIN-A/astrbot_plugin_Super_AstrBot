"""拟人化学习域：风格模仿、群组黑话、社交好感度。

三项能力共用一条链路：**先在对话中学习 → 再在请求前注入**。
学习结果默认进入审查队列（可配置），批准后才真正影响对话；好感度是数值累积，
不走审查但可随时清空。
"""

from .affinity import AffinityOutcome, AffinityService
from .config import AffinityConfig, JargonConfig, PersonaConfig, StyleConfig
from .jargon import SOURCE_JARGON, JargonOutcome, JargonService
from .service import PersonaService, PersonaStats, summarize_reviews
from .style import SOURCE_STYLE, StyleOutcome, StyleSelection, StyleService

__all__ = [
    "PersonaService",
    "PersonaConfig",
    "PersonaStats",
    "StyleService",
    "StyleConfig",
    "StyleOutcome",
    "StyleSelection",
    "JargonService",
    "JargonConfig",
    "JargonOutcome",
    "AffinityService",
    "AffinityConfig",
    "AffinityOutcome",
    "SOURCE_STYLE",
    "SOURCE_JARGON",
    "summarize_reviews",
]
