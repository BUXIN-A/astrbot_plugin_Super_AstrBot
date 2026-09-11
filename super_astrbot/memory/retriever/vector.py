"""向量检索路（可选）。

实现方式为**应用层暴力余弦**：

- 不依赖 FAISS，也不要求宿主暴露向量库（AstrBot 的 ``FaissVecDB`` 与抽象基类
  存在签名不一致的历史包袱：``retrieve(k=...)`` vs ``retrieve(top_k=...)``，
  直接依赖容易踩坑）；
- 个人机器人规模（数千条记忆）下，纯 Python 余弦完全够用；
- 扫描条数由 ``vector_max_scan`` 限制，超限时只扫描最近的记忆，保证主流程延迟可控。

Embedding 不可用时本路直接返回空，融合层会自然地只用关键词路的结果。
"""

from __future__ import annotations

import math
from typing import Sequence

from ...harness.protocols import EmbeddingGateway
from ...spec.scopes import MemoryScope
from ...storage import MemoryRepository, VectorRepository
from .base import Candidate


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """余弦相似度；长度不一致或零向量返回 0。"""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


class VectorRetriever:
    """基于 Embedding 的语义检索。"""

    name = "vector"

    def __init__(
        self,
        embedding: EmbeddingGateway,
        vectors: VectorRepository,
        memories: MemoryRepository,
        *,
        max_scan: int = 5000,
        logger=None,
    ) -> None:
        self._embedding = embedding
        self._vectors = vectors
        self._memories = memories
        self._max_scan = max(100, int(max_scan or 5000))
        self._logger = logger

    async def search(
        self,
        scopes: Sequence[MemoryScope],
        query: str,
        *,
        limit: int,
    ) -> list[Candidate]:
        if not self._embedding.available or not query.strip():
            return []

        query_vector = await self._embedding.embed(query)
        if not query_vector:
            return []

        fingerprint = self._embedding.fingerprint()
        rows = await self._vectors.load(fingerprint, limit=self._max_scan)
        if not rows:
            return []

        allowed_rows = await self._memories.iter_active(scopes, limit=self._max_scan)
        allowed = {int(row["id"]) for row in allowed_rows}
        if not allowed:
            return []

        scored: list[tuple[int, float]] = []
        for memory_id, dim, blob in rows:
            if memory_id not in allowed:
                continue
            vector = VectorRepository.decode(blob, dim)
            if not vector:
                continue
            similarity = cosine_similarity(query_vector, vector)
            if similarity <= 0.0:
                continue
            scored.append((memory_id, similarity))

        if not scored:
            return []

        scored.sort(key=lambda item: item[1], reverse=True)
        selected = scored[: max(1, limit)]
        return [
            Candidate(
                memory_id=memory_id, rank=index, relevance=round(similarity, 6), route=self.name
            )
            for index, (memory_id, similarity) in enumerate(selected)
        ]

    def _log_debug(self, message: str, *args) -> None:
        if self._logger is not None:
            self._logger.debug(message, *args)
