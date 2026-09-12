"""关键词检索路（SQLite FTS5，不可用时降级 LIKE）。"""

from __future__ import annotations

from typing import Sequence

from ...spec.scopes import MemoryScope
from ...storage import MemoryRepository
from ...support import build_match_query, tokenize
from .base import Candidate, build_candidates


class KeywordRetriever:
    """基于分词 + FTS5 的关键词检索。"""

    name = "keyword"

    def __init__(self, memories: MemoryRepository, *, logger=None) -> None:
        self._memories = memories
        self._logger = logger

    async def search(
        self,
        scopes: Sequence[MemoryScope],
        query: str,
        *,
        limit: int,
    ) -> list[Candidate]:
        tokens = tokenize(query)
        if not tokens:
            return []

        try:
            rows = await self._memories.fts_search(scopes, build_match_query(tokens), limit=limit)
        except Exception as exc:  # 检索路失败必须降级为空
            self._log_debug("FTS 检索失败，尝试 LIKE 降级：%s", exc)
            rows = []

        if rows:
            # bm25 越小越相关，已由 SQL 排序，直接取 ID 序列。
            return build_candidates([memory_id for memory_id, _ in rows], route=self.name)

        ids = await self._memories.like_search(scopes, tokens, limit=limit)
        return build_candidates(ids, route=self.name)

    def _log_debug(self, message: str, *args) -> None:
        if self._logger is not None:
            self._logger.debug(message, *args)
