"""记忆服务：对上层（commands / learning / context 钩子）暴露的稳定门面。

分层意图：

- ``commands`` / ``learning`` 只调用本服务，不直接碰仓储与检索器；
- 本服务负责「作用域解析 → 检索 → 格式化 → 注入 → 访问计数」的完整动作，
  使钩子代码保持极薄。
"""

from __future__ import annotations

import time
from typing import Any, Sequence

from ..harness.protocols import InjectResult
from ..spec.scopes import MemoryScope, retrieval_scopes
from ..storage import JournalRepository, MemoryRepository
from .config import INJECTION_DISABLED, INJECTION_SYSTEM, MemoryConfig
from .formatter import build_memory_body, format_search_results
from .lifecycle import MemoryLifecycle
from .models import (
    KIND_EPISODE,
    KIND_FACT,
    SOURCE_CAPTURE,
    SOURCE_MANUAL,
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_BUFFERED,
    STATUS_PENDING,
    MemoryDraft,
    MemoryItem,
)
from .retriever import HybridRetriever, RetrievalResult

_BUFFER_CLEANUP_EVERY = 20
"""每写入 N 条缓冲才执行一次「裁剪 + 过期清理」。

每条消息都清理会造成写放大（两条 DELETE，其中一条还带子查询）；
按条数节流后由每日维护任务兜底，缓冲不会失控。
"""

_MAX_TRACKED_SCOPES = 512
"""缓冲写入计数器的作用域上限，超出即整体重置，避免长跑后字典无限膨胀。"""


class MemoryService:
    """记忆领域唯一对外入口。"""

    def __init__(
        self,
        *,
        config: MemoryConfig,
        lifecycle: MemoryLifecycle,
        retriever: HybridRetriever,
        memories: MemoryRepository,
        journals: JournalRepository,
        injector: Any | None = None,
        embedding: Any | None = None,
        logger: Any | None = None,
    ) -> None:
        self._config = config
        self._lifecycle = lifecycle
        self._retriever = retriever
        self._memories = memories
        self._journals = journals
        self._injector = injector
        self._embedding = embedding
        self._logger = logger
        self._buffer_writes: dict[str, int] = {}

    @property
    def config(self) -> MemoryConfig:
        return self._config

    @property
    def route_names(self) -> list[str]:
        return self._retriever.route_names

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #

    async def remember(self, draft: MemoryDraft) -> int:
        """写入一条正式记忆。"""
        return await self._lifecycle.add(draft)

    async def remember_text(
        self,
        scope: MemoryScope,
        content: str,
        *,
        kind: str = KIND_FACT,
        importance: float = 0.5,
        confidence: float = 0.8,
        source: str = SOURCE_MANUAL,
        tags: Sequence[str] | None = None,
        status: str = STATUS_ACTIVE,
    ) -> int:
        """便捷写入：直接给作用域与文本。"""
        return await self._lifecycle.add(
            MemoryDraft(
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
                content=content.strip(),
                kind=kind,
                importance=importance,
                confidence=confidence,
                source=source,
                tags=list(tags or []),
                status=status,
            )
        )

    async def buffer_episode(
        self,
        scope: MemoryScope,
        text: str,
        *,
        source: str = SOURCE_CAPTURE,
        tags: Sequence[str] | None = None,
        now: float | None = None,
    ) -> int | None:
        """把一段对话放入缓冲（不建索引、不参与检索），作为反思原料。"""
        content = (text or "").strip()
        if not content:
            return None
        moment = now if now is not None else time.time()
        memory_id = await self._lifecycle.add(
            MemoryDraft(
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
                content=content,
                kind=KIND_EPISODE,
                importance=0.1,
                confidence=0.6,
                source=source,
                tags=list(tags or []),
                status=STATUS_BUFFERED,
            ),
            index=False,
            with_vector=False,
        )
        await self._maybe_cleanup_buffer(scope, now=moment)
        return memory_id

    async def _maybe_cleanup_buffer(self, scope: MemoryScope, *, now: float) -> None:
        """按写入条数节流地裁剪与清理缓冲（见 ``_BUFFER_CLEANUP_EVERY``）。"""
        if len(self._buffer_writes) > _MAX_TRACKED_SCOPES:
            self._buffer_writes.clear()
        key = scope.key
        writes = self._buffer_writes.get(key, 0) + 1
        if writes < _BUFFER_CLEANUP_EVERY:
            self._buffer_writes[key] = writes
            return
        self._buffer_writes[key] = 0
        await self._lifecycle.trim_buffer([scope])
        await self._lifecycle.purge_buffer([scope], now=now)

    async def buffer_material(self, scope: MemoryScope, *, limit: int = 50) -> list[MemoryItem]:
        """取出待反思的对话缓冲（跨「当前作用域 + 全局」）。"""
        scopes = retrieval_scopes(scope)
        rows = await self._memories.list_by_status(
            scopes, status=STATUS_BUFFERED, limit=limit, ascending=True
        )
        return [MemoryItem.from_row(row) for row in rows]

    async def consume_buffer(self, ids: Sequence[int], *, now: float | None = None) -> int:
        """标记缓冲已被反思消费（归档，不再重复参与反思）。"""
        return await self._lifecycle.archive(ids, now=now)

    async def count_buffer(self, scope: MemoryScope) -> int:
        """待反思的缓冲条数。"""
        return await self._memories.count_by_status(retrieval_scopes(scope), STATUS_BUFFERED)

    async def delete(self, ids: Sequence[int]) -> int:
        return await self._lifecycle.forget(ids)

    # ------------------------------------------------------------------ #
    # 检索与注入
    # ------------------------------------------------------------------ #

    async def recall(
        self,
        scope: MemoryScope,
        query: str,
        *,
        limit: int | None = None,
        now: float | None = None,
    ) -> RetrievalResult:
        """召回相关记忆（跨「当前作用域 + 全局」）。"""
        return await self._retriever.search(retrieval_scopes(scope), query, limit=limit, now=now)

    def build_body(self, result: RetrievalResult, *, max_chars: int | None = None) -> str:
        """把检索结果格式化为注入正文（不含边界标记）。"""
        budget = max_chars if max_chars is not None else self._config.max_injected_chars
        return build_memory_body(result.items, max_chars=budget)

    async def inject(self, request: Any, result: RetrievalResult) -> InjectResult:
        """把记忆注入到 LLM 请求对象中，并更新访问计数。"""
        method = self._config.injection_method
        if method == INJECTION_DISABLED:
            return InjectResult(applied=False, reason="配置为仅检索不注入")
        if not result.items:
            return InjectResult(applied=False, reason="无可用记忆")

        body = self.build_body(result)
        if not body:
            return InjectResult(applied=False, reason="记忆正文为空")

        if self._injector is None:
            return InjectResult(applied=False, reason="注入器不可用")

        # extra_user_content 使用 auto 模式：优先临时内容块，宿主不支持时回退系统提示词，
        # 避免因框架版本差异导致「配了却不生效」。
        prefer = "system_prompt" if method == INJECTION_SYSTEM else "auto"
        inject_result = self._injector.inject(request, [body], prefer=prefer)
        if inject_result.applied:
            await self.touch(result.items)
        return inject_result

    async def touch(self, items: Sequence[MemoryItem], *, now: float | None = None) -> None:
        """更新访问计数（用于强化常被使用的记忆）。"""
        ids = [item.id for item in items if item.id > 0]
        if not ids:
            return
        moment = now if now is not None else time.time()
        try:
            await self._memories.touch_access(ids, moment)
        except Exception as exc:  # noqa: BLE001 - 计数失败不影响对话
            self._warn("更新记忆访问计数失败：%s", exc)

    # ------------------------------------------------------------------ #
    # 查询与统计
    # ------------------------------------------------------------------ #

    async def get_memory(self, memory_id: int) -> MemoryItem | None:
        """按 ID 取一条记忆（面板详情用）。"""
        record = await self._memories.get(memory_id)
        return None if record is None else MemoryItem.from_row(record)

    async def list_memories(
        self,
        scope: MemoryScope,
        *,
        offset: int = 0,
        limit: int = 20,
        keyword: str = "",
    ) -> list[MemoryItem]:
        rows = await self._memories.list_page(
            retrieval_scopes(scope), offset=offset, limit=limit, keyword=keyword
        )
        return [MemoryItem.from_row(row) for row in rows]

    async def list_journals(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 20
    ) -> list[dict[str, Any]]:
        return await self._journals.list_page(retrieval_scopes(scope), offset=offset, limit=limit)

    async def stats(self, scope: MemoryScope) -> dict[str, Any]:
        scopes = retrieval_scopes(scope)
        active = await self._memories.count(scopes, status=STATUS_ACTIVE)
        buffered = await self._memories.count_by_status(scopes, STATUS_BUFFERED)
        journals = await self._journals.count(scopes)
        return {
            "scope": scope.key,
            "scopes": [item.key for item in scopes],
            "active": active,
            "buffered": buffered,
            "journals": journals,
            "routes": self.route_names,
            "vector_available": bool(
                self._embedding is not None and getattr(self._embedding, "available", False)
            ),
            "injection_method": self._config.injection_method,
        }

    async def list_all(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
        keyword: str = "",
        status: str = STATUS_ACTIVE,
        kind: str = "",
    ) -> list[MemoryItem]:
        """跨作用域列出记忆（面板总览用）。"""
        rows = await self._memories.list_all_page(
            offset=offset, limit=limit, keyword=keyword, status=status, kind=kind
        )
        return [MemoryItem.from_row(row) for row in rows]

    async def count_filtered(
        self, *, status: str = STATUS_ACTIVE, kind: str = "", keyword: str = ""
    ) -> int:
        """与 ``list_all`` 同条件的总数。"""
        return await self._memories.count_filtered(status=status, kind=kind, keyword=keyword)

    async def list_all_journals(self, *, offset: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        return await self._journals.list_all_page(offset=offset, limit=limit)

    async def count_all_journals(self) -> int:
        """周记总数（跨作用域，面板统计用）。"""
        return await self._journals.count_all()

    async def stats_all(self) -> dict[str, Any]:
        """全局统计（面板总览用）。"""
        return {
            "active": await self._memories.count_all(status=STATUS_ACTIVE),
            "buffered": await self._memories.count_all(status=STATUS_BUFFERED),
            "pending": await self._memories.count_all(status=STATUS_PENDING),
            "archived": await self._memories.count_all(status=STATUS_ARCHIVED),
            "journals": await self._journals.count_all(),
            "routes": self.route_names,
            "vector_available": bool(
                self._embedding is not None and getattr(self._embedding, "available", False)
            ),
            "injection_method": self._config.injection_method,
        }

    def format_results(self, result: RetrievalResult, *, with_score: bool = False) -> str:
        """命令回显格式化。"""
        return format_search_results(result.items, with_score=with_score)

    # ------------------------------------------------------------------ #
    # 维护
    # ------------------------------------------------------------------ #

    async def maintain(self, *, now: float | None = None) -> dict[str, Any]:
        return await self._lifecycle.maintain(now=now)

    async def repair(self) -> int:
        return await self._lifecycle.repair_incomplete_writes()

    async def reindex(self, scope: MemoryScope | None = None) -> dict[str, int]:
        scopes = retrieval_scopes(scope) if scope is not None else None
        return await self._lifecycle.reindex(scopes)

    async def reset_scope(self, scope: MemoryScope) -> dict[str, int]:
        """清空某作用域的记忆（含正式记忆与对话缓冲）。

        只作用于传入的这一个作用域：全局作用域必须在显式传入
        ``MemoryScope.global_scope()`` 时才会被清空。
        注意不能用 ``retrieval_scopes(scope)`` —— 它的语义是「检索时并集」，
        总会附带全局作用域，会让一次会话级重置连带删掉全局共享记忆。
        """
        scopes = (scope,)
        forgotten = 0
        buffered = 0

        while True:
            rows = await self._memories.list_recent(scopes, limit=200)
            if not rows:
                break
            ids = [int(row["id"]) for row in rows]
            forgotten += await self._lifecycle.forget(ids)
            if len(ids) < 200:
                break

        while True:
            rows = await self._memories.list_by_status(scopes, status=STATUS_BUFFERED, limit=200)
            if not rows:
                break
            ids = [int(row["id"]) for row in rows]
            buffered += await self._lifecycle.forget(ids)
            if len(ids) < 200:
                break

        return {"active": forgotten, "buffered": buffered}

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)
