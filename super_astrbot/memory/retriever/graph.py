"""图谱检索路。

它不直接读数据库，而是复用 ``graph`` 域的 ``expand``：查询词先命中实体，再取
实体关联的记忆（可选两跳）。这样「图怎么扩」的规则只实现一次，检索路只负责把
权重转成融合层需要的候选。与其它检索路一致：失败一律吞掉返回空。
"""

from __future__ import annotations

from typing import Any, Sequence

from ...spec.scopes import MemoryScope
from .base import Candidate


class GraphRetriever:
    """基于记忆知识图谱的关联召回。"""

    name = "graph"

    def __init__(self, service: Any, *, logger: Any | None = None) -> None:
        self._service = service
        self._logger = logger

    async def search(
        self,
        scopes: Sequence[MemoryScope],
        query: str,
        *,
        limit: int,
    ) -> list[Candidate]:
        if not query.strip() or limit <= 0:
            return []
        try:
            expanded = await self._service.expand(scopes, query, limit=limit)
        except Exception as exc:  # noqa: BLE001 - 检索路失败必须降级为空
            self._debug("图谱检索失败：%s", exc)
            return []
        if not expanded:
            return []

        total = len(expanded)
        peak = max((weight for _, weight in expanded), default=0.0)
        candidates: list[Candidate] = []
        for index, (memory_id, weight) in enumerate(expanded):
            relevance = weight / peak if peak > 0 else 1.0 - index / total
            candidates.append(
                Candidate(
                    memory_id=int(memory_id),
                    rank=index,
                    relevance=round(min(1.0, max(0.0, relevance)), 6),
                    route=self.name,
                )
            )
        return candidates

    def _debug(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.debug(message, *args)


__all__ = ["GraphRetriever"]
