"""记忆生命周期：写入、索引、遗忘、衰减、重建与崩溃修复。

关键设计：

- **写入带可恢复日志**：先登记 ``write_ops`` 再落库；插入成功但索引/向量失败时
  **保留 running 状态**交由启动修复，而不是回滚已写入的记忆（记忆本体已可见即算成功）；
- **只有 active 记忆建索引**：``buffered`` 对话缓冲不建 FTS/向量索引，
  从机制上避免「每句话都进检索」；
- **维护任务用 keyset 分页**：边遍历边更新 ``updated_at`` 不会导致漏处理。
"""

from __future__ import annotations

import json
import time
from typing import Any, Sequence
from uuid import uuid4

from ..spec.scopes import MemoryScope, ScopeType
from ..storage import Database, MemoryRepository, VectorRepository
from ..support import tokenize
from .config import MemoryConfig
from .models import (
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_BUFFERED,
    STATUS_FORGOTTEN,
    MemoryDraft,
    MemoryItem,
)

_MAINTENANCE_BATCH = 200
_ARCHIVE_AFTER_DAYS = 90.0
"""无访问、低重要度的记忆在该天数后被归档（不再参与检索）。"""


class MemoryLifecycle:
    """记忆的写入与维护。"""

    def __init__(
        self,
        *,
        db: Database,
        memories: MemoryRepository,
        vectors: VectorRepository,
        embedding: Any | None,
        config: MemoryConfig,
        logger: Any | None = None,
    ) -> None:
        self._db = db
        self._memories = memories
        self._vectors = vectors
        self._embedding = embedding
        self._config = config
        self._logger = logger

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #

    async def add(
        self,
        draft: MemoryDraft,
        *,
        index: bool = True,
        with_vector: bool = True,
    ) -> int:
        """写入一条记忆，返回记忆 ID。

        即使索引/向量失败也会返回 ID（记忆本体已落库），并留给启动修复补全。
        """
        now = time.time()
        op_id = f"mem-add-{uuid4().hex[:12]}"
        await self._begin_op(op_id, draft)

        memory_id = await self._memories.insert(
            scope_type=draft.scope_type,
            scope_id=draft.scope_id,
            kind=draft.kind,
            content=draft.content,
            importance=draft.importance,
            confidence=draft.confidence,
            source=draft.source,
            tags=draft.tags,
            created_at=now,
            status=draft.status,
        )

        try:
            await self._db.advance_write_op(op_id, "index", {"memory_id": memory_id})
            await self._apply_indexes(
                memory_id, draft.content, draft.status, now, index=index, with_vector=with_vector
            )
            await self._db.finish_write_op(op_id)
        except Exception as exc:  # noqa: BLE001 - 索引失败不影响记忆本体可用
            self._warn("记忆 %s 的索引/向量写入失败，将在启动时修复：%s", memory_id, exc)
        return memory_id

    async def add_many(self, drafts: Sequence[MemoryDraft]) -> list[int]:
        """批量写入（逐条走 add 以保证索引与日志语义一致）。"""
        ids: list[int] = []
        for draft in drafts:
            ids.append(await self.add(draft))
        return ids

    async def _apply_indexes(
        self,
        memory_id: int,
        content: str,
        status: str,
        now: float,
        *,
        index: bool = True,
        with_vector: bool = True,
    ) -> None:
        if status != STATUS_ACTIVE or not content.strip():
            return
        if index and self._config.fts_enabled:
            await self._memories.index_tokens(memory_id, " ".join(tokenize(content)))
        if with_vector and self._vector_ready():
            vector = await self._embedding.embed(content)  # type: ignore[union-attr]
            if vector:
                await self._vectors.upsert(
                    memory_id,
                    self._embedding.fingerprint(),
                    vector,
                    at=now,  # type: ignore[union-attr]
                )

    def _vector_ready(self) -> bool:
        return bool(
            self._config.vector_enabled
            and self._embedding is not None
            and getattr(self._embedding, "available", False)
        )

    async def _begin_op(self, op_id: str, draft: MemoryDraft) -> None:
        try:
            await self._db.begin_write_op(
                op_id,
                "memory_add",
                "insert",
                {
                    "scope": f"{draft.scope_type}:{draft.scope_id}",
                    "kind": draft.kind,
                    "source": draft.source,
                },
            )
        except Exception as exc:  # noqa: BLE001 - 日志表不可用不应阻断写入
            self._warn("登记写日志失败（继续写入）：%s", exc)

    # ------------------------------------------------------------------ #
    # 状态变更
    # ------------------------------------------------------------------ #

    async def archive(self, ids: Sequence[int], *, now: float | None = None) -> int:
        return await self._mark(ids, STATUS_ARCHIVED, now=now)

    async def forget(self, ids: Sequence[int], *, now: float | None = None) -> int:
        return await self._mark(ids, STATUS_FORGOTTEN, now=now)

    async def _mark(self, ids: Sequence[int], status: str, *, now: float | None = None) -> int:
        if not ids:
            return 0
        moment = now if now is not None else time.time()
        count = await self._memories.update_status_bulk(ids, status, at=moment)
        # 索引与向量批量删除：逐条删除会产生 2N 次 SQL。
        await self._memories.delete_index_many(ids)
        await self._vectors.delete_many(ids)
        return count

    async def purge_buffer(self, scopes: Sequence[MemoryScope], *, now: float | None = None) -> int:
        """清理过期的对话缓冲。"""
        moment = now if now is not None else time.time()
        before = moment - self._config.buffer_retention_days * 86400.0
        return await self._memories.purge_status_before(
            scopes, status=STATUS_BUFFERED, before=before
        )

    async def trim_buffer(self, scopes: Sequence[MemoryScope]) -> int:
        """把对话缓冲裁剪到配置上限之内（保留最新）。"""
        return await self._memories.trim_status(
            scopes, status=STATUS_BUFFERED, keep=self._config.buffer_max_per_scope
        )

    # ------------------------------------------------------------------ #
    # 维护任务
    # ------------------------------------------------------------------ #

    async def run_decay(self, *, now: float | None = None) -> dict[str, int]:
        """每日衰减：重要度随时间指数衰减，长期无访问的低价值记忆归档。

        衰减速率会被访问次数削弱（被频繁使用的记忆更「抗衰」），
        这是对 livingmemory 衰减策略在本项目规模下的简化实现。
        """
        moment = now if now is not None else time.time()
        stats = {"scanned": 0, "decayed": 0, "archived": 0}
        after_id = 0
        rate = max(0.0, self._config.decay_rate_per_day)

        while True:
            rows = await self._memories.list_maintenance_after(
                status=STATUS_ACTIVE, after_id=after_id, limit=_MAINTENANCE_BATCH
            )
            if not rows:
                break
            for row in rows:
                item = MemoryItem.from_row(row)
                after_id = item.id
                stats["scanned"] += 1

                days = max(0.0, (moment - max(item.created_at, item.last_access_at)) / 86400.0)
                if days <= 0.0:
                    continue

                access_factor = min(1.0, item.access_count / 5.0)
                effective_rate = rate * (1.0 - 0.5 * access_factor)
                factor = max(0.0, 1.0 - effective_rate) ** days
                new_importance = max(self._config.decay_min_importance, item.importance * factor)
                if abs(new_importance - item.importance) > 1e-6:
                    await self._memories.update_fields(
                        item.id, importance=new_importance, updated_at=moment
                    )
                    stats["decayed"] += 1

                if (
                    new_importance <= self._config.decay_min_importance + 1e-9
                    and days >= _ARCHIVE_AFTER_DAYS
                    and item.access_count == 0
                ):
                    await self.archive([item.id], now=moment)
                    stats["archived"] += 1

            if len(rows) < _MAINTENANCE_BATCH:
                break
        return stats

    async def reindex(
        self,
        scopes: Sequence[MemoryScope] | None = None,
        *,
        now: float | None = None,
    ) -> dict[str, int]:
        """重建 FTS 与向量索引（用于``/sab reindex``与模型指纹变化后的补算）。"""
        moment = now if now is not None else time.time()
        scope_filter = (
            {(scope.scope_type.value, scope.scope_id) for scope in scopes} if scopes else None
        )
        stats = {"indexed": 0, "vectorized": 0, "skipped": 0}
        after_id = 0
        vector_ready = self._vector_ready()

        if vector_ready:
            await self.clear_stale_vectors()

        while True:
            rows = await self._memories.list_maintenance_after(
                status=STATUS_ACTIVE, after_id=after_id, limit=_MAINTENANCE_BATCH
            )
            if not rows:
                break
            for row in rows:
                item = MemoryItem.from_row(row)
                after_id = item.id
                if scope_filter and (item.scope_type, item.scope_id) not in scope_filter:
                    stats["skipped"] += 1
                    continue
                await self._memories.index_tokens(item.id, " ".join(tokenize(item.content)))
                stats["indexed"] += 1
                if vector_ready:
                    vector = await self._embedding.embed(item.content)  # type: ignore[union-attr]
                    if vector:
                        await self._vectors.upsert(
                            item.id,
                            self._embedding.fingerprint(),  # type: ignore[union-attr]
                            vector,
                            at=moment,
                        )
                        stats["vectorized"] += 1
            if len(rows) < _MAINTENANCE_BATCH:
                break
        return stats

    async def clear_stale_vectors(self) -> int:
        """清掉与当前模型指纹不一致的向量（模型更换后旧向量语义已失效）。"""
        if self._embedding is None or not getattr(self._embedding, "available", False):
            return 0
        fingerprint = self._embedding.fingerprint()  # type: ignore[union-attr]
        removed = await self._vectors.delete_other_fingerprints(fingerprint)
        if removed:
            self._info("清理了 %d 条过期向量（模型指纹变化）", removed)
        return removed

    async def repair_incomplete_writes(self, *, limit: int = 200) -> int:
        """修复中断的写入：补建索引/向量。

        返回修复的条目数。任何无法修复的日志都会被清理，避免反复重试。
        """
        try:
            rows = await self._db.load_open_write_ops()
        except Exception as exc:  # noqa: BLE001
            self._warn("读取写日志失败：%s", exc)
            return 0

        repaired = 0
        for row in rows[:limit]:
            op_id = str(row["op_id"])
            op_type = str(row["op_type"])
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError):
                payload = {}

            if op_type != "memory_add":
                await self._db.finish_write_op(op_id)
                continue

            memory_id = int(payload.get("memory_id") or 0)
            if memory_id <= 0:
                await self._db.finish_write_op(op_id)
                continue

            record = await self._memories.get(memory_id)
            if record is None:
                await self._db.finish_write_op(op_id)
                continue

            item = MemoryItem.from_row(record)
            if item.status == STATUS_ACTIVE:
                try:
                    await self._apply_indexes(memory_id, item.content, item.status, time.time())
                    repaired += 1
                except Exception as exc:  # noqa: BLE001
                    self._warn("修复记忆 %s 索引失败：%s", memory_id, exc)
            await self._db.finish_write_op(op_id)
        if repaired:
            self._info("已修复 %d 条中断的记忆写入", repaired)
        return repaired

    async def maintain(self, *, now: float | None = None) -> dict[str, Any]:
        """综合维护入口（供调度器每日调用）：衰减 + 清理过期对话缓冲。"""
        moment = now if now is not None else time.time()
        stats: dict[str, Any] = dict(await self.run_decay(now=moment))

        purged = 0
        for scope_type, scope_id in await self._memories.all_scopes(status=STATUS_BUFFERED):
            scope = MemoryScope(ScopeType.parse(scope_type), scope_id)
            purged += await self.purge_buffer([scope], now=moment)
        stats["buffer_purged"] = purged
        return stats

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #

    def _info(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.info(message, *args)

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)
