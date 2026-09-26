"""记忆域配置映射。

把 ``_conf_schema.json`` 的嵌套配置一次性解析为强类型 ``MemoryConfig``，
其它模块只消费该对象，不再各自读配置 —— 避免 group_chat_plus 那种
「各处 ``config["key"]`` 直取、缺键即崩」的问题。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..spec.capabilities import as_bool, as_float, as_int, as_str, get_path
from ..spec.scopes import ScopeType
from .identity import DEFAULT_IDENTITY_STRATEGY, normalize_strategy
from .retriever.hybrid import RetrievalConfig
from .retriever.rerank import FALLBACK_LEXICAL, FALLBACK_MODES, RerankSettings

INJECTION_EXTRA = "extra_user_content"
INJECTION_SYSTEM = "system_prompt"
INJECTION_DISABLED = "disabled"


@dataclass
class MemoryConfig:
    """记忆域全部可调参数。"""

    # --- 开关与范围 ---
    capture: bool = True
    capture_groups: bool = True
    capture_private: bool = True
    default_scope: ScopeType = ScopeType.SESSION

    # --- 检索 ---
    fts_enabled: bool = True
    vector_enabled: bool = True
    embedding_provider_id: str = ""
    retrieval_top_k: int = 5
    fusion_rrf_k: int = 60
    half_life_days: float = 14.0
    weight_relevance: float = 0.55
    weight_importance: float = 0.2
    weight_recency: float = 0.25
    min_score: float = 0.05
    dedup_similarity: float = 0.92
    vector_max_scan: int = 5000
    journal_boost: float = 0.15
    identity_strategy: str = DEFAULT_IDENTITY_STRATEGY
    """用户作用域键的来源策略：sender_id / sender_name / auto（见 ``memory.identity``）。"""

    # --- 重排序（Rerank） ---
    rerank_enabled: bool = False
    rerank_provider_id: str = ""
    rerank_candidates: int = 20
    rerank_min_candidates: int = 3
    rerank_weight: float = 0.7
    rerank_fallback: str = FALLBACK_LEXICAL
    rerank_timeout: float = 8.0

    # --- 注入 ---
    injection_method: str = INJECTION_EXTRA
    max_injected_chars: int = 1800

    # --- 生命周期 ---
    buffer_retention_days: float = 7.0
    decay_rate_per_day: float = 0.02
    decay_min_importance: float = 0.05
    buffer_max_per_scope: int = 400

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "MemoryConfig":
        method = as_str(get_path(config, "memory.injection_method", INJECTION_EXTRA))
        if method not in {INJECTION_EXTRA, INJECTION_SYSTEM, INJECTION_DISABLED}:
            method = INJECTION_EXTRA
        fallback = (
            as_str(get_path(config, "memory.rerank_fallback", FALLBACK_LEXICAL)).strip().lower()
        )
        if fallback not in FALLBACK_MODES:
            fallback = FALLBACK_LEXICAL
        return cls(
            capture=as_bool(get_path(config, "memory.capture", True), True),
            capture_groups=as_bool(get_path(config, "memory.capture_groups", True), True),
            capture_private=as_bool(get_path(config, "memory.capture_private", True), True),
            default_scope=ScopeType.parse(get_path(config, "basic.default_scope", "session")),
            identity_strategy=normalize_strategy(
                get_path(config, "basic.identity_strategy", DEFAULT_IDENTITY_STRATEGY)
            ),
            fts_enabled=as_bool(get_path(config, "memory.fts_enabled", True), True),
            vector_enabled=as_bool(get_path(config, "memory.vector_enabled", True), True),
            embedding_provider_id=as_str(get_path(config, "memory.embedding_provider_id", "")),
            retrieval_top_k=as_int(
                get_path(config, "memory.retrieval_top_k", 5), 5, low=1, high=20
            ),
            fusion_rrf_k=as_int(get_path(config, "memory.fusion_rrf_k", 60), 60, low=1, high=1000),
            half_life_days=as_float(
                get_path(config, "memory.time_decay_half_life_days", 14.0),
                14.0,
                low=0.5,
                high=365.0,
            ),
            weight_relevance=as_float(
                get_path(config, "memory.weight_relevance", 0.55), 0.55, low=0.0, high=1.0
            ),
            weight_importance=as_float(
                get_path(config, "memory.weight_importance", 0.2), 0.2, low=0.0, high=1.0
            ),
            weight_recency=as_float(
                get_path(config, "memory.weight_recency", 0.25), 0.25, low=0.0, high=1.0
            ),
            min_score=as_float(get_path(config, "memory.min_score", 0.05), 0.05, low=0.0, high=1.0),
            dedup_similarity=as_float(
                get_path(config, "memory.dedup_similarity", 0.92), 0.92, low=0.3, high=1.0
            ),
            vector_max_scan=as_int(
                get_path(config, "runtime.vector_index_max_scan", 5000),
                5000,
                low=100,
                high=200000,
            ),
            journal_boost=as_float(
                get_path(config, "journal.retrieval_boost", 0.15), 0.15, low=0.0, high=1.0
            ),
            rerank_enabled=as_bool(get_path(config, "memory.rerank_enabled", False), False),
            rerank_provider_id=as_str(get_path(config, "memory.rerank_provider_id", "")),
            rerank_candidates=as_int(
                get_path(config, "memory.rerank_candidates", 20), 20, low=2, high=100
            ),
            rerank_min_candidates=as_int(
                get_path(config, "memory.rerank_min_candidates", 3), 3, low=2, high=20
            ),
            rerank_weight=as_float(
                get_path(config, "memory.rerank_weight", 0.7), 0.7, low=0.0, high=1.0
            ),
            rerank_fallback=fallback,
            rerank_timeout=as_float(
                get_path(config, "runtime.rerank_timeout_seconds", 8.0),
                8.0,
                low=1.0,
                high=60.0,
            ),
            injection_method=method,
            max_injected_chars=as_int(
                get_path(config, "memory.max_injected_chars", 1800), 1800, low=100, high=20000
            ),
        )

    def retrieval_config(self) -> RetrievalConfig:
        return RetrievalConfig(
            top_k=self.retrieval_top_k,
            rrf_k=self.fusion_rrf_k,
            weight_relevance=self.weight_relevance,
            weight_importance=self.weight_importance,
            weight_recency=self.weight_recency,
            min_score=self.min_score,
            dedup_similarity=self.dedup_similarity,
            half_life_days=self.half_life_days,
            journal_boost=self.journal_boost,
            rerank=RerankSettings(
                enabled=self.rerank_enabled,
                fallback=self.rerank_fallback,
                candidates=self.rerank_candidates,
                min_candidates=self.rerank_min_candidates,
                weight=self.rerank_weight,
                timeout=self.rerank_timeout,
            ),
        )
