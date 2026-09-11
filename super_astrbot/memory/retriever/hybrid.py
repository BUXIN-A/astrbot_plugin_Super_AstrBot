"""混合检索编排：多路并行 → RRF 融合 → 多因子加权 → 去重 → 截断。

可靠性约定：

- 每条检索路独立超时，**单路失败只影响该路**（返回空的 ``RouteOutcome`` 并记录错误），
  绝不让整次召回失败；
- 所有结果都附带 ``score_breakdown``，可通过 ``/sab why`` 或面板查看打分明细；
- 参数全部来自配置，检索层不自带魔法默认（便于调参与回归）。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Sequence

from ...spec.errors import safe_detail
from ...spec.scopes import MemoryScope
from ...storage import MemoryRepository
from ..models import KIND_JOURNAL, SOURCE_JOURNAL, STATUS_ACTIVE, MemoryItem
from .base import RouteOutcome
from .fusion import dedupe_by_similarity, normalize, recency_score, rrf_fuse


@dataclass
class RetrievalConfig:
    """检索参数（由配置映射而来）。"""

    top_k: int = 5
    rrf_k: int = 60
    weight_relevance: float = 0.55
    weight_importance: float = 0.2
    weight_recency: float = 0.25
    min_score: float = 0.05
    dedup_similarity: float = 0.92
    half_life_days: float = 14.0
    journal_boost: float = 0.15
    route_timeout: float = 6.0
    candidate_multiplier: int = 4


@dataclass
class RetrievalResult:
    """一次检索的完整结果与诊断信息。"""

    query: str = ""
    items: list[MemoryItem] = field(default_factory=list)
    outcomes: list[RouteOutcome] = field(default_factory=list)
    elapsed_ms: float = 0.0
    degraded: str = ""
    """若发生降级（例如向量路不可用），这里说明原因。"""

    @property
    def route_summary(self) -> str:
        parts = []
        for outcome in self.outcomes:
            if outcome.error:
                parts.append(f"{outcome.route}=失败({outcome.error})")
            else:
                parts.append(f"{outcome.route}={outcome.hit_count}")
        return "、".join(parts) if parts else "无检索路"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


class HybridRetriever:
    """分层自适应检索的统一入口。"""

    def __init__(
        self,
        *,
        routes: Sequence[object],
        memories: MemoryRepository,
        config: RetrievalConfig,
        logger=None,
    ) -> None:
        self._routes = list(routes)
        self._memories = memories
        self._config = config
        self._logger = logger

    @property
    def route_names(self) -> list[str]:
        return [str(getattr(route, "name", "unknown")) for route in self._routes]

    async def search(
        self,
        scopes: Sequence[MemoryScope],
        query: str,
        *,
        limit: int | None = None,
        now: float | None = None,
    ) -> RetrievalResult:
        started = time.perf_counter()
        text = (query or "").strip()
        if not text or not self._routes:
            return RetrievalResult(query=text, elapsed_ms=0.0)

        top_k = max(1, int(limit or self._config.top_k))
        fetch = max(top_k * max(1, self._config.candidate_multiplier), top_k)

        outcomes = await asyncio.gather(
            *(self._run_route(route, scopes, text, fetch) for route in self._routes)
        )
        outcome_list = list(outcomes)
        degraded = next((item.error for item in outcome_list if item.error), "")

        fused = rrf_fuse(outcome_list, k=self._config.rrf_k)
        if not fused:
            return RetrievalResult(
                query=text,
                outcomes=outcome_list,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                degraded=degraded,
            )

        rows = await self._memories.get_many(list(fused.keys()))
        items_by_id = {
            item.id: item
            for item in (MemoryItem.from_row(row) for row in rows)
            if item.status == STATUS_ACTIVE
        }

        normalized = normalize(fused)
        current = now if now is not None else time.time()
        config = self._config

        scored: list[MemoryItem] = []
        for memory_id, item in items_by_id.items():
            relevance = normalized.get(memory_id, 0.0)
            importance = _clamp(item.importance)
            recency = recency_score(
                item.created_at,
                item.last_access_at,
                now=current,
                half_life_days=config.half_life_days,
            )
            score = (
                config.weight_relevance * relevance
                + config.weight_importance * importance
                + config.weight_recency * recency
            )
            boost = 0.0
            if item.kind == KIND_JOURNAL or item.source == SOURCE_JOURNAL:
                boost = config.journal_boost
                score += boost

            item.score = round(score, 6)
            item.score_breakdown = {
                "relevance": round(relevance, 6),
                "importance": round(importance, 6),
                "recency": round(recency, 6),
                "journal_boost": round(boost, 6),
                "rrf_raw": round(fused.get(memory_id, 0.0), 8),
                "access_count": float(item.access_count),
            }
            if score >= config.min_score:
                scored.append(item)

        scored.sort(key=lambda entry: entry.score, reverse=True)
        selected = dedupe_by_similarity(scored, threshold=config.dedup_similarity, max_items=top_k)

        return RetrievalResult(
            query=text,
            items=selected,
            outcomes=outcome_list,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            degraded=degraded,
        )

    async def _run_route(
        self,
        route: object,
        scopes: Sequence[MemoryScope],
        query: str,
        limit: int,
    ) -> RouteOutcome:
        name = str(getattr(route, "name", "unknown"))
        started = time.perf_counter()
        try:
            candidates = await asyncio.wait_for(
                route.search(scopes, query, limit=limit),  # type: ignore[attr-defined]
                timeout=self._config.route_timeout,
            )
            return RouteOutcome(
                route=name,
                candidates=list(candidates),
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
        except asyncio.TimeoutError:
            return RouteOutcome(
                route=name,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                error=f"超时(>{self._config.route_timeout:.0f}s)",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 单路失败必须隔离
            if self._logger is not None:
                self._logger.debug("检索路 %s 失败：%s", name, safe_detail(exc))
            return RouteOutcome(
                route=name,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                error=safe_detail(exc),
            )
