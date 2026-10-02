"""PersonaForge 三层人格（进程内 vendor 子模块）。

来源：PersonaForge（心理学驱动的双过程人格一致性框架）。原项目为独立 Python 库
（依赖 torch / chromadb 做风格向量库），本项目按实施总纲 §0.5 只 **vendor 数据模型
与提示词结构**，去掉重型依赖，作为插件包内的普通子模块被进程内 import：

- 三层人格模型（核心特质 / 表层风格 / 动态状态）——见 ``model.py``；
- 选择性双过程内省（关键轮先想后说）——见 ``service.py``；
- 风格向量库 / 嵌入依赖不引入（由既有检索域承担类似职责）。

单人格约束：本模块只维护 **一个** 人格画像（bot 的稳定人格），
不提供多画像切换；群友差异由 ``members`` 域的「表层策略」承担。
"""

from .model import (
    BIG_FIVE_AXES,
    BIG_FIVE_LABELS,
    DEFENSE_LABELS,
    DEFENSE_MECHANISMS,
    EMOJI_FREQUENCIES,
    EMOJI_LABELS,
    PUNCTUATION_HABITS,
    PUNCTUATION_LABELS,
    SENTENCE_LABELS,
    SENTENCE_LENGTHS,
    VOCABULARY_LABELS,
    VOCABULARY_LEVELS,
    CoreTraits,
    DynamicState,
    PersonalityProfile,
    RelationshipInfo,
    SpeakingStyle,
    default_profile,
)
from .service import ForgeService

__all__ = [
    "BIG_FIVE_AXES",
    "BIG_FIVE_LABELS",
    "DEFENSE_LABELS",
    "DEFENSE_MECHANISMS",
    "EMOJI_FREQUENCIES",
    "EMOJI_LABELS",
    "PUNCTUATION_HABITS",
    "PUNCTUATION_LABELS",
    "SENTENCE_LABELS",
    "SENTENCE_LENGTHS",
    "VOCABULARY_LABELS",
    "VOCABULARY_LEVELS",
    "CoreTraits",
    "DynamicState",
    "PersonalityProfile",
    "RelationshipInfo",
    "SpeakingStyle",
    "default_profile",
    "ForgeService",
]
