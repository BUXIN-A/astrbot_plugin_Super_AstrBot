"""分层自适应检索。

- ``KeywordRetriever``：永远可用（FTS5，缺失时 LIKE 降级）；
- ``VectorRetriever``：可选，Embedding 不可用时自动让位；
- ``GraphRetriever``：可选，知识图谱的关联召回（图谱未启用时让位）；
- ``HybridRetriever``：编排各路并做 RRF 融合与多因子加权。
"""

from .base import Candidate, Retriever, RouteOutcome, build_candidates
from .graph import GraphRetriever
from .hybrid import HybridRetriever, RetrievalConfig, RetrievalResult
from .keyword import KeywordRetriever
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
]
