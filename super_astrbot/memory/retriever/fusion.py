"""融合与后处理：RRF 排名融合、多因子加权、词袋去重。

为什么用 RRF（Reciprocal Rank Fusion）而不是分数相加：

- 关键词路（bm25）与向量路（余弦）的分数**口径完全不同**，直接相加没有意义；
- RRF 只依赖排名：``score = Σ 1/(k + rank)``，天然对冲了不同检索路的量纲差异；
- k 越大越平滑（更倾向「多路都命中」），k 越小越偏向单路高分。
"""

from __future__ import annotations

from typing import Iterable, Sequence

from ...support import jaccard, tokenize
from ..models import MemoryItem
from .base import RouteOutcome


def rrf_fuse(outcomes: Iterable[RouteOutcome], *, k: int = 60) -> dict[int, float]:
    """把多条检索路的结果融合为 ``{memory_id: rrf_score}``。"""
    safe_k = max(1, int(k or 60))
    fused: dict[int, float] = {}
    for outcome in outcomes:
        for candidate in outcome.candidates:
            fused[candidate.memory_id] = fused.get(candidate.memory_id, 0.0) + 1.0 / (
                safe_k + candidate.rank + 1
            )
    return fused


def normalize(values: dict[int, float]) -> dict[int, float]:
    """把分数线性归一化到 0~1（最大值映射为 1）。"""
    if not values:
        return {}
    maximum = max(values.values())
    if maximum <= 0:
        return {key: 0.0 for key in values}
    return {key: value / maximum for key, value in values.items()}


def recency_score(
    created_at: float,
    last_access_at: float,
    *,
    now: float,
    half_life_days: float,
) -> float:
    """指数时间衰减：``0.5 ** (age_days / half_life_days)``。"""
    half_life = max(0.1, float(half_life_days or 14.0))
    base = max(float(created_at or 0.0), float(last_access_at or 0.0))
    if base <= 0:
        return 0.0
    age_days = max(0.0, (now - base) / 86400.0)
    return 0.5 ** (age_days / half_life)


def dedupe_by_similarity(
    items: Sequence[MemoryItem],
    *,
    threshold: float,
    max_items: int,
) -> list[MemoryItem]:
    """按词袋 Jaccard 去重，保留分数更高的那条。

    ``items`` 需已按分数降序排列。
    """
    if threshold >= 1.0:
        return list(items[:max_items])

    kept: list[MemoryItem] = []
    kept_tokens: list[set[str]] = []
    for item in items:
        tokens = set(tokenize(item.content))
        if not tokens:
            kept.append(item)
            if len(kept) >= max_items:
                break
            kept_tokens.append(tokens)
            continue
        duplicate = False
        for existing in kept_tokens:
            if jaccard(tokens, existing) >= threshold:
                duplicate = True
                break
        if duplicate:
            continue
        kept.append(item)
        kept_tokens.append(tokens)
        if len(kept) >= max_items:
            break
    return kept
