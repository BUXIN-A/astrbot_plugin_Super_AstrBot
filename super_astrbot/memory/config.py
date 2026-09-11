"""记忆域配置映射。

把 ``_conf_schema.json`` 的嵌套配置一次性解析为强类型 ``MemoryConfig``，
其它模块只消费该对象，不再各自读配置 —— 避免 group_chat_plus 那种
「各处 ``config["key"]`` 直取、缺键即崩」的问题。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..spec.capabilities import get_path
from ..spec.scopes import ScopeType
from .retriever.hybrid import RetrievalConfig

INJECTION_EXTRA = "extra_user_content"
INJECTION_SYSTEM = "system_prompt"
INJECTION_DISABLED = "disabled"


def _int(
    config: Mapping[str, Any],
    path: str,
    default: int,
    *,
    low: int | None = None,
    high: int | None = None,
) -> int:
    raw = get_path(config, path, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _float(
    config: Mapping[str, Any],
    path: str,
    default: float,
    *,
    low: float | None = None,
    high: float | None = None,
) -> float:
    raw = get_path(config, path, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _bool(config: Mapping[str, Any], path: str, default: bool) -> bool:
    raw = get_path(config, path, default)
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on", "是", "开启"}
    return default


def _str(config: Mapping[str, Any], path: str, default: str) -> str:
    raw = get_path(config, path, default)
    return str(raw) if raw is not None else default


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
        method = _str(config, "memory.injection_method", INJECTION_EXTRA)
        if method not in {INJECTION_EXTRA, INJECTION_SYSTEM, INJECTION_DISABLED}:
            method = INJECTION_EXTRA
        return cls(
            capture=_bool(config, "memory.capture", True),
            capture_groups=_bool(config, "memory.capture_groups", True),
            capture_private=_bool(config, "memory.capture_private", True),
            default_scope=ScopeType.parse(get_path(config, "basic.default_scope", "session")),
            fts_enabled=_bool(config, "memory.fts_enabled", True),
            vector_enabled=_bool(config, "memory.vector_enabled", True),
            embedding_provider_id=_str(config, "memory.embedding_provider_id", ""),
            retrieval_top_k=_int(config, "memory.retrieval_top_k", 5, low=1, high=20),
            fusion_rrf_k=_int(config, "memory.fusion_rrf_k", 60, low=1, high=1000),
            half_life_days=_float(
                config, "memory.time_decay_half_life_days", 14.0, low=0.5, high=365.0
            ),
            weight_relevance=_float(config, "memory.weight_relevance", 0.55, low=0.0, high=1.0),
            weight_importance=_float(config, "memory.weight_importance", 0.2, low=0.0, high=1.0),
            weight_recency=_float(config, "memory.weight_recency", 0.25, low=0.0, high=1.0),
            min_score=_float(config, "memory.min_score", 0.05, low=0.0, high=1.0),
            dedup_similarity=_float(config, "memory.dedup_similarity", 0.92, low=0.3, high=1.0),
            vector_max_scan=_int(
                config, "runtime.vector_index_max_scan", 5000, low=100, high=200000
            ),
            journal_boost=_float(config, "journal.retrieval_boost", 0.15, low=0.0, high=1.0),
            injection_method=method,
            max_injected_chars=_int(config, "memory.max_injected_chars", 1800, low=100, high=20000),
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
        )
