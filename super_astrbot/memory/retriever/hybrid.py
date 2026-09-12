"""混合检索编排：多路并行 → RRF 融合 → 重排序 → 多因子加权 → 去重 → 截断。

可靠性约定：

- 每条检索路独立超时，**单路失败只影响该路**（返回空的 ``RouteOutcome`` 并记录错误），
  绝不让整次召回失败；
- 重排序是可选增强，失败/不可用时按配置回退（模型 → 本地词法重排 → 纯融合排名），
  同样不让召回失败；
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
from .rerank import (
    RERANK_LEXICAL,
    RERANK_OFF,
    RERANK_PROVIDER,
    Reranker,
    RerankOutcome,
    RerankSettings,
)


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
    rerank: RerankSettings = field(default_factory=RerankSettings)


@dataclass
class RetrievalResult:
    """一次检索的完整结果与诊断信息。"""

    query: str = ""
    items: list[MemoryItem] = field(default_factory=list)
    outcomes: list[RouteOutcome] = field(default_factory=list)
    elapsed_ms: float = 0.0
    degraded: str = ""
    """若发生降级（例如向量路不可用），这里说明原因。"""

    rerank_source: str = RERANK_OFF
    """本次实际采用的重排序来源：``provider`` / ``lexical`` / ``off``。"""

    rerank_note: str = ""
    """未走重排序模型时的原因（人类可读）。"""

    @property
    def route_summary(self) -> str:
        parts = []
        for outcome in self.outcomes:
            if outcome.error:
                parts.append(f"{outcome.route}=失败({outcome.error})")
            else:
                parts.append(f"{outcome.route}={outcome.hit_count}")
        return "、".join(parts) if parts else "无检索路"

    @property
    def rerank_summary(self) -> str:
        """一行重排序说明；未启用时返回空串（避免在常规输出里刷屏）。"""
        if self.rerank_source == RERANK_PROVIDER:
            return "重排序=模型"
        if self.rerank_source == RERANK_LEXICAL:
            note = f"（{self.rerank_note}）" if self.rerank_note else ""
            return f"重排序=本地词法兜底{note}"
        if self.rerank_note:
            return f"重排序=未生效（{self.rerank_note}）"
        return ""


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
        reranker: Reranker | None = None,
        logger=None,
    ) -> None:
        self._routes = list(routes)
        self._memories = memories
        self._config = config
        self._reranker = reranker or Reranker(config=config.rerank, logger=logger)
        self._logger = logger

    @property
    def route_names(self) -> list[str]:
        return [str(getattr(route, "name", "unknown")) for route in self._routes]

    @property
    def rerank_note(self) -> str:
        """重排序当前状态（供状态接口展示）。"""
        return self._reranker.describe() if self._reranker is not None else "未启用"

    def configure_rerank(self, config: RetrievalConfig, gateway: object | None) -> None:
        """热切换重排序配置与网关（能力开关、提供商变更后调用）。"""
        self._config = config
        if self._reranker is None:
            self._reranker = Reranker(config=config.rerank, gateway=gateway, logger=self._logger)
        else:
            self._reranker.reconfigure(config.rerank, gateway)

    def add_route(self, route: object) -> bool:
        """新增/替换一条检索路（同名视为替换）。

        用于运行时热切换：例如 ProviderManager 就绪后启用向量路，
        或用户从控制台关闭关键词路。
        """
        name = str(getattr(route, "name", ""))
        if not name:
            return False
        self._routes = [item for item in self._routes if str(getattr(item, "name", "")) != name]
        self._routes.append(route)
        return True

    def remove_route(self, name: str) -> bool:
        """移除指定检索路；返回是否真的移除了。"""
        before = len(self._routes)
        self._routes = [item for item in self._routes if str(getattr(item, "name", "")) != name]
        return len(self._routes) != before

    def has_route(self, name: str) -> bool:
        return any(str(getattr(item, "name", "")) == name for item in self._routes)

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
        settings = self._config.rerank
        if settings.enabled:
            # 启用重排序时先多召回，让模型在更大的候选池里挑出真正相关的 top_k。
            fetch = max(fetch, max(2, int(settings.candidates)))

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
        relevance, rerank = await self._apply_rerank(text, items_by_id, normalized)
        current = now if now is not None else time.time()
        config = self._config

        scored: list[MemoryItem] = []
        for memory_id, item in items_by_id.items():
            relevance_value = relevance.get(memory_id, 0.0)
            importance = _clamp(item.importance)
            recency = recency_score(
                item.created_at,
                item.last_access_at,
                now=current,
                half_life_days=config.half_life_days,
            )
            score = (
                config.weight_relevance * relevance_value
                + config.weight_importance * importance
                + config.weight_recency * recency
            )
            boost = 0.0
            if item.kind == KIND_JOURNAL or item.source == SOURCE_JOURNAL:
                boost = config.journal_boost
                score += boost

            item.score = round(score, 6)
            item.score_breakdown = {
                "relevance": round(relevance_value, 6),
                "importance": round(importance, 6),
                "recency": round(recency, 6),
                "journal_boost": round(boost, 6),
                "rrf_raw": round(fused.get(memory_id, 0.0), 8),
                "access_count": float(item.access_count),
                "rerank_source": rerank.source,
            }
            if rerank.applied:
                item.score_breakdown["rerank"] = round(rerank.scores.get(memory_id, 0.0), 6)
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
            rerank_source=rerank.source,
            rerank_note=rerank.note,
        )

    async def _apply_rerank(
        self,
        query: str,
        items_by_id: dict[int, MemoryItem],
        normalized: dict[int, float],
    ) -> tuple[dict[int, float], RerankOutcome]:
        """重排序并按 ``rerank_weight`` 与融合分混合相关性。

        未生效时原样返回融合分——调用方无需关心是否真的重排序过。
        """
        reranker = self._reranker
        if reranker is None or not reranker.enabled or not items_by_id:
            return normalized, RerankOutcome({}, RERANK_OFF)

        # 按融合分降序送候选：池子越小越省，越大越准，上限由 rerank_candidates 控制。
        ordered = sorted(
            items_by_id.values(), key=lambda item: normalized.get(item.id, 0.0), reverse=True
        )
        outcome = await reranker.apply(query, ordered)
        if not outcome.applied:
            return normalized, outcome

        weight = _clamp(self._config.rerank.weight)
        blended = {
            memory_id: weight * outcome.scores.get(memory_id, 0.0) + (1.0 - weight) * value
            for memory_id, value in normalized.items()
        }
        return blended, outcome

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
