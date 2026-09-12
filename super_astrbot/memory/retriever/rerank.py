"""候选重排序：优先外部 Rerank 模型，不可用时按配置回退。

为什么把「回退」做成一等公民：

- 重排序模型是**外部依赖**：未配置、被删除、额度耗尽、超时、返回空都会发生；
- 检索在对话主链路上，任何一次重排序失败都**不能**让召回失败或明显变慢；
- 因此这里叠三层保护：候选不足不调用模型 → 调用失败熔断（冷却期内不再尝试）→
  回退到零成本词法重排或纯融合排名。

分数口径：提供商返回的相关性分先 min-max 归一化（不同模型量纲差异极大，甚至可能带负值），
再与融合分按 ``rerank_weight`` 混合 —— 留出余量可避免「模型分」整体压过重要性与新近度。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ...support import tokenize
from ..models import MemoryItem

RERANK_OFF = "off"
"""未启用（或回退策略为「不重排」）。"""

RERANK_PROVIDER = "provider"
"""由外部重排序模型完成。"""

RERANK_LEXICAL = "lexical"
"""由本地词法重排完成（零成本兜底）。"""

FALLBACK_NONE = "none"
FALLBACK_LEXICAL = "lexical"
FALLBACK_MODES = (FALLBACK_NONE, FALLBACK_LEXICAL)

_FAILURE_THRESHOLD = 3
"""连续失败多少次后熔断（避免每次都白等一次超时）。"""

_COOLDOWN_SECONDS = 300.0
"""熔断冷却时长（秒）；期间直接走回退策略。"""

_LEXICAL_COVERAGE_WEIGHT = 0.65
"""词法重排中「查询词覆盖率」的占比，其余为 Dice 相似度。"""


@dataclass
class RerankSettings:
    """重排序参数（由配置映射而来）。"""

    enabled: bool = False
    fallback: str = FALLBACK_LEXICAL
    candidates: int = 20
    min_candidates: int = 3
    weight: float = 0.7
    timeout: float = 8.0


@dataclass
class RerankOutcome:
    """一次重排序的结果。"""

    scores: dict[int, float]
    """``{memory_id: 0~1 归一化重排序分}``；未生效时为空。"""

    source: str = RERANK_OFF
    note: str = ""
    """回退/跳过的原因（人类可读），仅在未走模型时有值。"""

    @property
    def applied(self) -> bool:
        return self.source in (RERANK_PROVIDER, RERANK_LEXICAL)


def normalize_scores(values: Mapping[int, float]) -> dict[int, float]:
    """min-max 归一化到 0~1；全等或空时统一给 1.0（保持调用方原有顺序）。"""
    if not values:
        return {}
    low = min(values.values())
    high = max(values.values())
    span = high - low
    if span <= 0:
        return {key: 1.0 for key in values}
    return {key: (value - low) / span for key, value in values.items()}


def lexical_scores(query: str, candidates: Sequence[MemoryItem]) -> dict[int, float]:
    """零成本词法重排：查询词覆盖率 + Dice 相似度。

    只依赖分词结果，不需要模型或网络。「用户提问中的关键词是否出现在这条记忆里」
    是长期记忆召回最常见也最有效的信号，因此把它作为模型不可用时的兜底是划算的。
    """
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return {}

    raw: dict[int, float] = {}
    for item in candidates:
        tokens = set(tokenize(item.content))
        if not tokens:
            raw[item.id] = 0.0
            continue
        overlap = len(query_tokens & tokens)
        coverage = overlap / len(query_tokens)
        dice = 2.0 * overlap / (len(query_tokens) + len(tokens))
        raw[item.id] = (1.0 - _LEXICAL_COVERAGE_WEIGHT) * dice + (
            _LEXICAL_COVERAGE_WEIGHT * coverage
        )
    return normalize_scores(raw)


class Reranker:
    """对融合后的候选做重排序，并在失败时按策略回退。

    ``observer`` 为可选回调 ``(source, ok, duration_ms, candidates)``，
    由应用层转换为运行指标（检索层不直接依赖监控域）。
    """

    def __init__(
        self,
        *,
        config: RerankSettings | None = None,
        gateway: Any | None = None,
        clock: Callable[[], float] | None = None,
        observer: Callable[[str, bool, float, int], None] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._config = config or RerankSettings()
        self._gateway = gateway
        self._clock = clock or time.monotonic
        self._observer = observer
        self._logger = logger
        self._failures = 0
        self._open_until = 0.0

    # ------------------------------------------------------------------ #
    # 配置与状态
    # ------------------------------------------------------------------ #

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled)

    def reconfigure(self, config: RerankSettings, gateway: Any | None) -> None:
        """热切换：更新配置与网关，并清空熔断状态（配置变更后应重新尝试）。"""
        self._config = config
        self._gateway = gateway
        self._failures = 0
        self._open_until = 0.0

    def describe(self) -> str:
        """一行状态描述，供面板/命令展示。"""
        if not self.enabled:
            return "未启用"
        if self._circuit_open():
            return "模型熔断中，已回退"
        gateway = self._gateway
        if gateway is None or not getattr(gateway, "available", False):
            return (
                "模型不可用，已回退" if self._config.fallback == FALLBACK_LEXICAL else "模型不可用"
            )
        model = ""
        getter = getattr(gateway, "model", None)
        if callable(getter):
            try:
                model = str(getter() or "")
            except Exception:  # 展示失败不影响流程
                model = ""
        return f"模型{'（' + model + '）' if model else ''}"

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #

    async def apply(self, query: str, candidates: Sequence[MemoryItem]) -> RerankOutcome:
        """对候选重排序；任何异常都不会外泄，最差也退化为「不重排」。"""
        if not self.enabled or not candidates:
            return RerankOutcome({}, RERANK_OFF)

        minimum = max(2, int(self._config.min_candidates or 2))
        if len(candidates) < minimum:
            return self._fallback(query, candidates, f"候选不足 {minimum} 条")
        if self._circuit_open():
            return self._fallback(query, candidates, "重排序模型连续失败，暂时跳过")
        gateway = self._gateway
        if gateway is None or not getattr(gateway, "available", False):
            return self._fallback(query, candidates, "重排序模型不可用")

        documents = [item.content or "" for item in candidates]
        started = time.perf_counter()
        try:
            hits = await asyncio.wait_for(
                gateway.rerank(query, documents),
                timeout=max(0.1, float(self._config.timeout)),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 失败一律回退，绝不打断检索
            return self._on_failure(query, candidates, f"调用失败：{_brief(exc)}", started)
        duration_ms = (time.perf_counter() - started) * 1000.0

        if not hits:
            return self._on_failure(query, candidates, "重排序返回空结果", started)

        # 未被评分的候选给最低原始分，避免「未评分项」混进前列。
        raw = {item.id: 0.0 for item in candidates}
        for hit in hits:
            if 0 <= hit.index < len(candidates):
                memory_id = candidates[hit.index].id
                # 同一候选出现多次时保留最高分，避免后写的低分覆盖。
                raw[memory_id] = max(raw[memory_id], float(hit.score))

        self._failures = 0
        self._notify(RERANK_PROVIDER, True, duration_ms, len(candidates))
        return RerankOutcome(normalize_scores(raw), RERANK_PROVIDER)

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    def _on_failure(
        self,
        query: str,
        candidates: Sequence[MemoryItem],
        note: str,
        started: float,
    ) -> RerankOutcome:
        """记录失败（含熔断计数）并回退；失败细节只写调试日志，不打搅用户。"""
        duration_ms = (time.perf_counter() - started) * 1000.0
        self._failures += 1
        if self._failures >= _FAILURE_THRESHOLD:
            self._open_until = self._clock() + _COOLDOWN_SECONDS
        if self._logger is not None:
            self._logger.debug("重排序不可用（%s），已按策略回退", note)
        self._notify(RERANK_PROVIDER, False, duration_ms, len(candidates))
        return self._fallback(query, candidates, note)

    def _fallback(
        self,
        query: str,
        candidates: Sequence[MemoryItem],
        note: str,
    ) -> RerankOutcome:
        if self._config.fallback == FALLBACK_LEXICAL:
            scores = lexical_scores(query, candidates)
            if scores:
                return RerankOutcome(scores, RERANK_LEXICAL, note)
        return RerankOutcome({}, RERANK_OFF, note)

    def _circuit_open(self) -> bool:
        return self._clock() < self._open_until

    def _notify(self, source: str, ok: bool, duration_ms: float, candidates: int) -> None:
        if self._observer is None:
            return
        try:
            self._observer(source, ok, duration_ms, candidates)
        except Exception:  # 埋点失败绝不影响检索
            pass


def _brief(exc: BaseException) -> str:
    """异常的一句话描述（超时特殊处理，便于对照配置值排查）。"""
    if isinstance(exc, asyncio.TimeoutError):
        return "超时"
    text = str(exc).strip() or exc.__class__.__name__
    return text if len(text) <= 80 else text[:80] + "…"


__all__ = [
    "FALLBACK_LEXICAL",
    "FALLBACK_MODES",
    "FALLBACK_NONE",
    "RERANK_LEXICAL",
    "RERANK_OFF",
    "RERANK_PROVIDER",
    "RerankOutcome",
    "RerankSettings",
    "Reranker",
    "lexical_scores",
    "normalize_scores",
]
