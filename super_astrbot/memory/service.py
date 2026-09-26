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
from ..storage import (
    DEFAULT_JOURNAL_SORT,
    DEFAULT_MEMORY_SORT,
    IdentityRepository,
    JournalRepository,
    MemoryRepository,
)
from .config import INJECTION_DISABLED, INJECTION_SYSTEM, MemoryConfig
from .formatter import build_memory_body, format_search_results
from .identity import MemoryIdentity, describe_observation
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
    SOURCE_WEEKLY,
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
        identities: IdentityRepository | None = None,
        logger: Any | None = None,
    ) -> None:
        self._config = config
        self._lifecycle = lifecycle
        self._retriever = retriever
        self._memories = memories
        self._journals = journals
        self._injector = injector
        self._embedding = embedding
        self._identities = identities
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
        identity: MemoryIdentity | None = None,
        created_at: float | None = None,
        updated_at: float | None = None,
        last_access_at: float | None = None,
        access_count: int | None = None,
    ) -> int:
        """便捷写入：直接给作用域与文本。

        ``identity`` 记录说话者（平台 ID / 昵称 / 来源会话），是「跨会话识别用户」
        与历史归因的依据；不传则留空（例如反思产出这类派生记忆）。

        ``created_at`` / ``updated_at`` / ``last_access_at`` / ``access_count`` 仅在
        导入历史数据时传入——不传就按写入时刻算。**导入备份必须传**，否则恢复出来的
        记忆会全部盖上导入时间，时间线与衰减判断随之失真。
        """
        who = identity or MemoryIdentity()
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
                sender_id=who.sender_id,
                sender_name=who.sender_name,
                origin_umo=who.origin_umo or scope.scope_id,
                created_at=created_at,
                updated_at=updated_at,
                last_access_at=last_access_at,
                access_count=access_count,
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
        identity: MemoryIdentity | None = None,
    ) -> int | None:
        """把一段对话放入缓冲（不建索引、不参与检索），作为反思原料。"""
        content = (text or "").strip()
        if not content:
            return None
        moment = now if now is not None else time.time()
        who = identity or MemoryIdentity()
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
                sender_id=who.sender_id,
                sender_name=who.sender_name,
                origin_umo=who.origin_umo or scope.scope_id,
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
        """待反思的缓冲条数（当前作用域 + 全局并集）。

        注意：这不是「该用户的全部缓冲」。``default_scope=session`` 时每个会话各自成域，
        本方法只看得到调用方所在的那个域（外加全局），跨会话的缓冲总量见
        ``count_buffer_total``。
        """
        return await self._memories.count_by_status(retrieval_scopes(scope), STATUS_BUFFERED)

    async def count_buffer_total(self) -> int:
        """全库待反思缓冲总数（跨所有作用域与用户）。

        供反思触发判定做「聚合兜底」：缓冲按会话分片时，单个会话可能长期达不到轮数阈值，
        但总量其实早已足够。
        """
        return await self._memories.count_by_status_all(STATUS_BUFFERED)

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
        except Exception as exc:  # 计数失败不影响对话
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
        source: str = "",
        sort: str = DEFAULT_MEMORY_SORT,
    ) -> list[MemoryItem]:
        """跨作用域列出记忆（面板总览用）。"""
        rows = await self._memories.list_all_page(
            offset=offset,
            limit=limit,
            keyword=keyword,
            status=status,
            kind=kind,
            source=source,
            sort=sort,
        )
        return [MemoryItem.from_row(row) for row in rows]

    async def count_filtered(
        self,
        *,
        status: str = STATUS_ACTIVE,
        kind: str = "",
        source: str = "",
        keyword: str = "",
    ) -> int:
        """与 ``list_all`` 同条件的总数。"""
        return await self._memories.count_filtered(
            status=status, kind=kind, source=source, keyword=keyword
        )

    async def list_weeklies(
        self, *, offset: int = 0, limit: int = 20, keyword: str = ""
    ) -> list[MemoryItem]:
        """列出每周总结（周度洞察产出，``source=weekly_reflection``）。"""
        return await self.list_all(
            offset=offset, limit=limit, keyword=keyword, source=SOURCE_WEEKLY
        )

    async def count_weeklies(self, *, keyword: str = "") -> int:
        return await self.count_filtered(keyword=keyword, source=SOURCE_WEEKLY)

    async def export_weeklies(self) -> list[dict[str, Any]]:
        """导出全部每周总结（面板导出 JSON 用）。"""
        return await self._memories.export_by_source(SOURCE_WEEKLY)

    async def export_all_memories(self) -> list[dict[str, Any]]:
        """导出全部有效记忆（排除已遗忘与对话缓冲，备份用）。"""
        return await self._memories.export_visible()

    async def update_content(self, memory_id: int, content: str, *, at: float | None = None) -> bool:
        """更新记忆正文并重建索引（面板编辑记忆 / 每周总结，现实桥编辑时连带同步）。

        行不存在或内容为空时返回 ``False``：面板据此提示「记录不存在或内容为空」，
        而不是假装保存成功。
        """
        text = (content or "").strip()
        if not text:
            return False
        try:
            target = int(memory_id)
        except (TypeError, ValueError):
            return False
        moment = at if at is not None else time.time()
        if await self._memories.get(target) is None:
            return False
        await self._memories.update_fields(target, content=text, updated_at=moment)
        await self._lifecycle.refresh_indexes(target, text, now=moment)
        return True

    async def list_all_journals(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
        keyword: str = "",
        sort: str = DEFAULT_JOURNAL_SORT,
        entry_type: str = "",
    ) -> list[dict[str, Any]]:
        return await self._journals.list_all_page(
            offset=offset, limit=limit, keyword=keyword, sort=sort, entry_type=entry_type
        )

    async def count_all_journals(self, *, keyword: str = "", entry_type: str = "") -> int:
        """现实桥记录总数（跨作用域，面板统计用）。"""
        return await self._journals.count_all(keyword=keyword, entry_type=entry_type)

    # ------------------------------------------------------------------ #
    # 身份与作用域维护
    # ------------------------------------------------------------------ #

    async def observe_identity(
        self,
        *,
        umo: str,
        platform: str = "",
        sender_id: str = "",
        sender_name: str = "",
        scope: MemoryScope | None = None,
        user_key: str = "",
        now: float | None = None,
    ) -> None:
        """记录一次身份观测；仓储缺失时静默跳过（不影响主链路）。"""
        if self._identities is None or not umo:
            return
        moment = now if now is not None else time.time()
        try:
            await self._identities.observe(
                umo=umo,
                platform=platform,
                sender_id=sender_id,
                sender_name=sender_name,
                scope_type=scope.scope_type.value if scope else "",
                scope_id=scope.scope_id if scope else "",
                user_key=user_key,
                now=moment,
            )
        except Exception as exc:  # 观测失败绝不能影响对话
            if self._logger is not None:
                self._logger.debug("身份观测写入失败：%s", exc)

    async def identity_observations(self, *, limit: int = 200) -> dict[str, Any]:
        """身份观测明细 + 稳定性判定（面板「身份诊断」用）。"""
        if self._identities is None:
            return {"items": [], "total": 0, "analysis": describe_observation([])}
        rows = await self._identities.list_all(limit=limit)
        return {
            "items": rows,
            "total": await self._identities.count(),
            "analysis": describe_observation(rows),
        }

    async def clear_identity_observations(self) -> int:
        if self._identities is None:
            return 0
        return await self._identities.clear()

    async def scope_distribution(self, *, limit: int = 50) -> dict[str, Any]:
        """记忆作用域分布（迁移前必须先看这张表）。"""
        return await self._memories.scope_distribution(limit=limit)

    async def migrate_scope(
        self,
        *,
        to: str,
        from_scope_type: str = "",
        ids: Sequence[int] | None = None,
        only_attributed: bool = True,
        dry_run: bool = True,
    ) -> dict[str, int]:
        """按策略迁移记忆作用域（详见 ``MemoryRepository.migrate_scope``）。"""
        return await self._memories.migrate_scope(
            to=to,
            from_scope_type=from_scope_type,
            ids=ids,
            only_attributed=only_attributed,
            dry_run=dry_run,
        )

    async def migrate_journal_scope(
        self, *, scope_type: str, to_scope_type: str, to_scope_id: str, dry_run: bool = True
    ) -> dict[str, int]:
        """现实桥记录的同步迁移（与记忆迁移配套调用）。"""
        return await self._journals.migrate_scope(
            scope_type=scope_type,
            to_scope_type=to_scope_type,
            to_scope_id=to_scope_id,
            dry_run=dry_run,
        )

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

    async def reindex(self, scope: MemoryScope | None = None) -> dict[str, Any]:
        """重建索引，并把「为什么没建向量」一并说清楚。

        向量路不可用时 ``vectorized`` 恒为 0；不解释原因的话，运维只会看到
        「重建完成、向量 0 条」而误以为插件坏了——实际是没配嵌入提供商。
        """
        scopes = retrieval_scopes(scope) if scope is not None else None
        stats: dict[str, Any] = dict(await self._lifecycle.reindex(scopes))
        vector_ready = bool(
            self._embedding is not None and getattr(self._embedding, "available", False)
        )
        stats["vector_ready"] = vector_ready
        if vector_ready:
            stats["note"] = ""
        else:
            stats["note"] = (
                "向量路未启用：未检测到可用的嵌入提供商，本次只重建了关键词索引。"
                "在插件配置的「记忆与检索」里指定嵌入模型后重试即可。"
            )
        return stats

    async def reindex_keywords(self, *, clear: bool = False) -> dict[str, int]:
        """只重建关键词索引（恢复后的索引补偿，不调用嵌入接口）。

        ``clear=True`` 对应覆盖恢复：先清空 FTS 再按新正文重建。
        """
        return await self._lifecycle.reindex_keywords(clear=clear)

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
