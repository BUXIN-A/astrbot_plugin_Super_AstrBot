"""注意力评分：纯函数版「这条消息值不值得回」。

设计原则：

- **只看本地信号，不调用模型**：评分必须零成本，否则「读空气」本身就变成开销；
- **可解释**：返回命中的信号列表，便于日志与 ``/sab status`` 排查为什么插话/沉默；
- **可测试**：不依赖时间与随机数，给定输入必然得到同一得分。

权重（常数固定，只有阈值可配）：被直接提及不在这里处理——那属于「必然回复」，
在 ``service`` 里先行短路，避免阈值把定向提问挡掉。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from ..support import jaccard, tokenize

DEFAULT_BASE_SCORE = 0.2
"""群里有对话发生的基础分。"""

DEFAULT_QUESTION_BONUS = 0.35
"""含疑问标记时的加分。"""

DEFAULT_ALIAS_BONUS = 0.4
"""称呼了 Bot 时的加分。"""

DEFAULT_TOPIC_WEIGHT = 0.3
"""话题延续度的权重（乘以与 Bot 近期发言的重合度 0~1）。"""

DEFAULT_LENGTH_BONUS = 0.15
"""长度适中（不过短也不过长）时的加分。"""

LONG_PENALTY = -0.15
SHORT_PENALTY = -0.2
BURST_PENALTY = -0.2

MIN_REASONABLE_CHARS = 4
MAX_REASONABLE_CHARS = 200
LONG_MESSAGE_CHARS = 400


@dataclass(frozen=True)
class AttentionWeights:
    """正向加分权重（可配置：整体调高即更主动插话）。"""

    base: float = DEFAULT_BASE_SCORE
    question: float = DEFAULT_QUESTION_BONUS
    alias: float = DEFAULT_ALIAS_BONUS
    topic: float = DEFAULT_TOPIC_WEIGHT
    length: float = DEFAULT_LENGTH_BONUS


DEFAULT_WEIGHTS = AttentionWeights()

_TOPIC_HISTORY = 3
"""与最近几条 Bot 发言比较话题延续度。"""

_QUESTION_MARKERS = (
    "？",
    "?",
    "吗",
    "呢",
    "怎么",
    "为什么",
    "为何",
    "如何",
    "哪",
    "多少",
    "谁",
    "能不能",
    "可不可以",
    "是不是",
    "有没有",
    "求",
    "帮",
)


@dataclass(frozen=True)
class AttentionScore:
    """一次评分的结果。"""

    value: float
    reasons: tuple[str, ...] = ()

    def describe(self) -> str:
        return f"{self.value:.2f}" + (f"（{'、'.join(self.reasons)}）" if self.reasons else "")


def has_question(text: str) -> bool:
    """是否包含疑问标记。"""
    return any(marker in text for marker in _QUESTION_MARKERS)


def alias_hit(text: str, aliases: Sequence[str]) -> bool:
    """是否出现 Bot 的称呼词（大小写不敏感）。"""
    lowered = text.lower()
    return any(alias.lower() in lowered for alias in aliases if alias)


def topic_overlap(tokens: Sequence[str], recent_texts: Iterable[str]) -> float:
    """与 Bot 最近发言的最大词袋重合度（0~1）。"""
    best = 0.0
    for text in list(recent_texts)[-_TOPIC_HISTORY:]:
        best = max(best, jaccard(tokens, tokenize(text)))
        if best >= 1.0:
            break
    return best


def score_message(
    text: str,
    *,
    aliases: Sequence[str] = (),
    recent_texts: Iterable[str] = (),
    burst: bool = False,
    weights: AttentionWeights | None = None,
) -> AttentionScore:
    """对一条群消息评分，返回 ``[0, 1]`` 区间内的得分与命中信号。"""
    stripped = text.strip()
    tokens = tokenize(stripped)
    if not tokens:
        return AttentionScore(0.0, ("无实义内容",))

    w = weights or DEFAULT_WEIGHTS
    score = w.base
    reasons: list[str] = []

    if has_question(stripped):
        score += w.question
        reasons.append("含疑问")
    if aliases and alias_hit(stripped, aliases):
        score += w.alias
        reasons.append("称呼了 Bot")
    overlap = topic_overlap(tokens, recent_texts)
    if overlap > 0:
        score += w.topic * overlap
        reasons.append(f"话题延续 {overlap:.2f}")

    char_count = len(stripped)
    if MIN_REASONABLE_CHARS <= char_count <= MAX_REASONABLE_CHARS:
        score += w.length
    elif char_count > LONG_MESSAGE_CHARS:
        score += LONG_PENALTY
        reasons.append("消息过长")
    elif char_count < MIN_REASONABLE_CHARS:
        score += SHORT_PENALTY
        reasons.append("消息过短")

    if burst:
        score += BURST_PENALTY
        reasons.append("连续刷屏")

    return AttentionScore(max(0.0, min(1.0, score)), tuple(reasons))
