"""分层自适应检索。

- ``KeywordRetriever``：永远可用（FTS5，缺失时 LIKE 降级）；
- ``VectorRetriever``：可选，Embedding 不可用时自动让位；
- ``GraphRetriever``：可选，知识图谱的关联召回（图谱未启用时让位）；
- ``Reranker``：可选重排序，模型不可用时回退本地词法重排；
- ``HybridRetriever``：编排各路并做 RRF 融合、重排序与多因子加权。
"""

from .base import Candidate, Retriever, RouteOutcome, build_candidates
from .graph import GraphRetriever
from .hybrid import HybridRetriever, RetrievalConfig, RetrievalResult
from .keyword import KeywordRetriever
from .rerank import (
    FALLBACK_LEXICAL,
    FALLBACK_MODES,
    FALLBACK_NONE,
    RERANK_LEXICAL,
    RERANK_OFF,
    RERANK_PROVIDER,
    Reranker,
    RerankOutcome,
    RerankSettings,
    lexical_scores,
    normalize_scores,
)
from .vector import VectorRetriever, cosine_similarity

__all__ = [
    "Candidate",
    "Retriever",
    "RouteOutcome",
    "build_candidates",
    "KeywordRetriever",
    "VectorRetriever",
    "GraphRetriever",
    "cosine_similarity",
    "HybridRetriever",
    "RetrievalConfig",
    "RetrievalResult",
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
