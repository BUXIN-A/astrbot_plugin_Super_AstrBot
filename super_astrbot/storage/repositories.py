"""仓储层：把 SQL 收敛在少数几个类中。

约定：

- 仓储只接受/返回**普通字典型数据**，不返回 ORM 对象，避免上层被存储细节污染；
- 所有写操作尽量走 ``Database.transaction()``（由高层的服务层决定事务边界与写日志）；
- 时间戳统一由调用方传入（便于测试注入固定时钟）。
"""

from __future__ import annotations

import json
import struct
from typing import Any, Iterable, Sequence

from ..spec.scopes import MemoryScope
from .db import Database


def _scope_where(scopes: Sequence[MemoryScope], alias: str = "") -> tuple[str, list[Any]]:
    """构造作用域过滤 SQL 片段与参数。"""
    prefix = f"{alias}." if alias else ""
    if not scopes:
        return "0", []
    clauses: list[str] = []
    params: list[Any] = []
    for scope in scopes:
        clauses.append(f"({prefix}scope_type=? AND {prefix}scope_id=?)")
        params.extend([scope.scope_type.value, scope.scope_id])
    return " OR ".join(clauses), params


def row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}


def _decode_tags(raw: Any) -> list[str]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


# --------------------------------------------------------------------------- #
# 记忆
# --------------------------------------------------------------------------- #


class MemoryRepository:
    """``memories`` 表访问。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(
        self,
        *,
        scope_type: str,
        scope_id: str,
        kind: str,
        content: str,
        importance: float,
        confidence: float,
        source: str,
        tags: Sequence[str],
        created_at: float,
        status: str = "active",
    ) -> int:
        cursor = await self._db.execute(
            "INSERT INTO memories(scope_type, scope_id, kind, content, importance, confidence,"
            " source, tags, created_at, updated_at, last_access_at, access_count, status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,0,0,?)",
            (
                scope_type,
                scope_id,
                kind,
                content,
                float(importance),
                float(confidence),
                source,
                json.dumps(list(tags), ensure_ascii=False),
                created_at,
                created_at,
                status,
            ),
        )
        return int(cursor.lastrowid)

    async def insert_many(self, items: Sequence[dict[str, Any]]) -> list[int]:
        """批量插入并返回自增 ID 列表（逐条插入以拿到 ID，整体置于一个事务内）。"""
        ids: list[int] = []
        async with self._db.transaction() as tx:
            for item in items:
                cursor = await tx.execute(
                    "INSERT INTO memories(scope_type, scope_id, kind, content, importance,"
                    " confidence, source, tags, created_at, updated_at, last_access_at,"
                    " access_count, status) VALUES (?,?,?,?,?,?,?,?,?,?,0,0,?)",
                    (
                        item["scope_type"],
                        item["scope_id"],
                        item["kind"],
                        item["content"],
                        float(item["importance"]),
                        float(item["confidence"]),
                        item["source"],
                        json.dumps(list(item.get("tags") or []), ensure_ascii=False),
                        item["created_at"],
                        item["created_at"],
                        str(item.get("status") or "active"),
                    ),
                )
                ids.append(int(cursor.lastrowid))
        return ids

    async def get(self, memory_id: int) -> dict[str, Any] | None:
        row = await self._db.query_one("SELECT * FROM memories WHERE id=?", (memory_id,))
        return None if row is None else row_to_dict(row)

    async def get_many(self, ids: Sequence[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = await self._db.query(
            f"SELECT * FROM memories WHERE id IN ({placeholders})", list(ids)
        )
        return [row_to_dict(row) for row in rows]

    async def update_fields(self, memory_id: int, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "content",
            "kind",
            "importance",
            "confidence",
            "source",
            "status",
            "updated_at",
            "last_access_at",
            "access_count",
        }
        assignments: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "tags":
                value = json.dumps(list(value), ensure_ascii=False)
            assignments.append(f"{key}=?")
            params.append(value)
        if not assignments:
            return
        params.append(memory_id)
        await self._db.execute(f"UPDATE memories SET {', '.join(assignments)} WHERE id=?", params)

    async def touch_access(self, ids: Sequence[int], at: float) -> None:
        """更新访问时间与计数（单条 SQL，避免读改写竞争）。"""
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        await self._db.execute(
            f"UPDATE memories SET last_access_at=?, access_count=access_count+1"
            f" WHERE id IN ({placeholders})",
            [at, *ids],
        )

    async def delete(self, memory_id: int) -> bool:
        cursor = await self._db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        return bool(getattr(cursor, "rowcount", 0))

    async def delete_by_scopes(self, scopes: Sequence[MemoryScope]) -> int:
        where, params = _scope_where(scopes)
        cursor = await self._db.execute(f"DELETE FROM memories WHERE ({where})", params)
        return int(getattr(cursor, "rowcount", 0) or 0)

    async def set_status(self, memory_id: int, status: str, *, at: float) -> None:
        await self._db.execute(
            "UPDATE memories SET status=?, updated_at=? WHERE id=?", (status, at, memory_id)
        )

    async def update_status_bulk(self, ids: Sequence[int], status: str, *, at: float) -> int:
        """批量改状态（反思消费缓冲记忆、归档、遗忘都用它）。"""
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        result = await self._db.execute(
            f"UPDATE memories SET status=?, updated_at=? WHERE id IN ({placeholders})",
            [status, at, *ids],
        )
        return int(result.rowcount or 0)

    async def list_by_status(
        self,
        scopes: Sequence[MemoryScope],
        *,
        status: str,
        limit: int,
        ascending: bool = True,
    ) -> list[dict[str, Any]]:
        """按状态取列表。``status='buffered'`` 即「待反思的对话缓冲」。"""
        where, params = _scope_where(scopes)
        order = "ASC" if ascending else "DESC"
        rows = await self._db.query(
            f"SELECT * FROM memories WHERE status=? AND ({where})"
            f" ORDER BY created_at {order} LIMIT ?",
            [status, *params, limit],
        )
        return [row_to_dict(row) for row in rows]

    async def trim_status(self, scopes: Sequence[MemoryScope], *, status: str, keep: int) -> int:
        """只保留最新 ``keep`` 条指定状态记录，其余删除（对话缓冲控量）。"""
        if keep <= 0:
            return 0
        where, params = _scope_where(scopes)
        result = await self._db.execute(
            f"DELETE FROM memories WHERE status=? AND ({where}) AND id NOT IN ("
            f" SELECT id FROM memories WHERE status=? AND ({where})"
            f" ORDER BY created_at DESC LIMIT ?)",
            [status, *params, status, *params, keep],
        )
        return int(result.rowcount or 0)

    async def count_by_status(self, scopes: Sequence[MemoryScope], status: str) -> int:
        where, params = _scope_where(scopes)
        return int(
            await self._db.scalar(
                f"SELECT COUNT(*) FROM memories WHERE status=? AND ({where})",
                [status, *params],
                default=0,
            )
        )

    async def purge_status_before(
        self, scopes: Sequence[MemoryScope], *, status: str, before: float
    ) -> int:
        """清理早于某时间点的指定状态记录（防止缓冲无限增长）。"""
        where, params = _scope_where(scopes)
        result = await self._db.execute(
            f"DELETE FROM memories WHERE status=? AND created_at<? AND ({where})",
            [status, before, *params],
        )
        return int(result.rowcount or 0)

    async def all_scopes(self, *, status: str = "active") -> list[tuple[str, str]]:
        """列出存在数据的作用域，供维护任务遍历。"""
        rows = await self._db.query(
            "SELECT DISTINCT scope_type, scope_id FROM memories WHERE status=?", (status,)
        )
        return [(str(row["scope_type"]), str(row["scope_id"])) for row in rows]

    async def list_maintenance_after(
        self, *, status: str, after_id: int, limit: int
    ) -> list[dict[str, Any]]:
        """按主键 keyset 分页（衰减/重建索引等维护任务专用）。

        用 keyset 而非 OFFSET：维护任务会更新 ``updated_at``，OFFSET 分页会因排序变化
        导致漏处理或重复处理。
        """
        rows = await self._db.query(
            "SELECT * FROM memories WHERE status=? AND id>? ORDER BY id ASC LIMIT ?",
            (status, after_id, limit),
        )
        return [row_to_dict(row) for row in rows]

    async def count(
        self, scopes: Sequence[MemoryScope] | None = None, *, status: str = "active"
    ) -> int:
        if scopes:
            where, params = _scope_where(scopes)
            sql = f"SELECT COUNT(*) FROM memories WHERE status=? AND ({where})"
            return int(await self._db.scalar(sql, [status, *params], default=0))
        return int(
            await self._db.scalar(
                "SELECT COUNT(*) FROM memories WHERE status=?", (status,), default=0
            )
        )

    async def list_recent(
        self,
        scopes: Sequence[MemoryScope],
        *,
        limit: int,
        status: str = "active",
    ) -> list[dict[str, Any]]:
        where, params = _scope_where(scopes)
        rows = await self._db.query(
            f"SELECT * FROM memories WHERE status=? AND ({where}) ORDER BY created_at DESC LIMIT ?",
            [status, *params, limit],
        )
        return [row_to_dict(row) for row in rows]

    async def iter_active(
        self,
        scopes: Sequence[MemoryScope],
        *,
        limit: int,
        status: str = "active",
    ) -> list[dict[str, Any]]:
        """按新近度取出用于向量扫描的候选。"""
        where, params = _scope_where(scopes)
        rows = await self._db.query(
            f"SELECT id, content, importance, confidence, created_at, last_access_at,"
            f" access_count, kind, source, scope_type, scope_id, tags, status, updated_at"
            f" FROM memories WHERE status=? AND ({where})"
            " ORDER BY created_at DESC LIMIT ?",
            [status, *params, limit],
        )
        return [row_to_dict(row) for row in rows]

    async def list_page(
        self,
        scopes: Sequence[MemoryScope],
        *,
        offset: int,
        limit: int,
        keyword: str = "",
        status: str = "active",
    ) -> list[dict[str, Any]]:
        """面板用的分页查询（关键词为简单的 LIKE 过滤）。"""
        clauses = ["status=?"]
        params: list[Any] = [status]
        where, scope_params = _scope_where(scopes)
        clauses.append(f"({where})")
        params.extend(scope_params)
        if keyword:
            clauses.append("content LIKE ?")
            params.append(f"%{keyword}%")
        params.extend([limit, offset])
        rows = await self._db.query(
            f"SELECT * FROM memories WHERE {' AND '.join(clauses)}"
            " ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        )
        return [row_to_dict(row) for row in rows]

    async def list_all_page(
        self,
        *,
        offset: int,
        limit: int,
        keyword: str = "",
        status: str = "active",
    ) -> list[dict[str, Any]]:
        """跨作用域分页（面板总览用）。"""
        clauses = ["status=?"]
        params: list[Any] = [status]
        if keyword:
            clauses.append("content LIKE ?")
            params.append(f"%{keyword}%")
        params.extend([limit, offset])
        rows = await self._db.query(
            f"SELECT * FROM memories WHERE {' AND '.join(clauses)}"
            " ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        )
        return [row_to_dict(row) for row in rows]

    async def count_all(self, *, status: str = "active") -> int:
        return int(
            await self._db.scalar(
                "SELECT COUNT(*) FROM memories WHERE status=?", (status,), default=0
            )
        )

    # ---------------------- FTS 索引维护 ---------------------- #
    async def index_tokens(self, memory_id: int, tokens_text: str) -> None:
        if not self._db.fts_available:
            return
        await self._db.execute("DELETE FROM memory_index WHERE rowid=?", (memory_id,))
        if tokens_text:
            await self._db.execute(
                "INSERT INTO memory_index(rowid, tokens) VALUES (?,?)", (memory_id, tokens_text)
            )

    async def delete_index(self, memory_id: int) -> None:
        if not self._db.fts_available:
            return
        await self._db.execute("DELETE FROM memory_index WHERE rowid=?", (memory_id,))

    async def fts_search(
        self,
        scopes: Sequence[MemoryScope],
        match_query: str,
        *,
        limit: int,
    ) -> list[tuple[int, float]]:
        """返回 ``[(memory_id, bm25_score)]``，分数越小越相关。"""
        if not self._db.fts_available or not match_query:
            return []
        where, params = _scope_where(scopes, alias="m")
        sql = (
            "SELECT m.id AS id, bm25(memory_index) AS score"
            " FROM memory_index JOIN memories m ON m.id = memory_index.rowid"
            " WHERE memory_index MATCH ? AND m.status='active' AND ("
            + where
            + ") ORDER BY score LIMIT ?"
        )
        rows = await self._db.query(sql, [match_query, *params, limit])
        return [(int(row["id"]), float(row["score"])) for row in rows]

    async def like_search(
        self,
        scopes: Sequence[MemoryScope],
        terms: Sequence[str],
        *,
        limit: int,
    ) -> list[int]:
        """FTS 不可用时的降级检索。"""
        if not terms:
            return []
        where, params = _scope_where(scopes)
        term_clauses = " OR ".join("content LIKE ?" for _ in terms)
        rows = await self._db.query(
            f"SELECT id FROM memories WHERE status='active' AND ({where})"
            f" AND ({term_clauses}) ORDER BY created_at DESC LIMIT ?",
            [*params, *[f"%{term}%" for term in terms], limit],
        )
        return [int(row["id"]) for row in rows]


# --------------------------------------------------------------------------- #
# 向量
# --------------------------------------------------------------------------- #


class VectorRepository:
    """``memory_vectors`` 表访问。向量以 ``float32`` 小端字节序列存储。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def encode(vector: Sequence[float]) -> bytes:
        return struct.pack(f"<{len(vector)}f", *vector)

    @staticmethod
    def decode(blob: bytes, dim: int) -> list[float]:
        if not blob or dim <= 0:
            return []
        try:
            return list(struct.unpack(f"<{dim}f", blob))
        except struct.error:
            return []

    async def upsert(
        self, memory_id: int, fingerprint: str, vector: Sequence[float], *, at: float
    ) -> None:
        await self._db.execute(
            "INSERT INTO memory_vectors(memory_id, fingerprint, dim, vector, updated_at)"
            " VALUES (?,?,?,?,?)"
            " ON CONFLICT(memory_id) DO UPDATE SET fingerprint=excluded.fingerprint,"
            " dim=excluded.dim, vector=excluded.vector, updated_at=excluded.updated_at",
            (memory_id, fingerprint, len(vector), self.encode(vector), at),
        )

    async def delete(self, memory_id: int) -> None:
        await self._db.execute("DELETE FROM memory_vectors WHERE memory_id=?", (memory_id,))

    async def load(self, fingerprint: str, *, limit: int) -> list[tuple[int, int, bytes]]:
        rows = await self._db.query(
            "SELECT memory_id, dim, vector FROM memory_vectors WHERE fingerprint=?"
            " ORDER BY updated_at DESC LIMIT ?",
            (fingerprint, limit),
        )
        return [(int(row["memory_id"]), int(row["dim"]), bytes(row["vector"])) for row in rows]

    async def count(self, fingerprint: str | None = None) -> int:
        if fingerprint:
            return int(
                await self._db.scalar(
                    "SELECT COUNT(*) FROM memory_vectors WHERE fingerprint=?",
                    (fingerprint,),
                    default=0,
                )
            )
        return int(await self._db.scalar("SELECT COUNT(*) FROM memory_vectors", default=0))

    async def clear(self, fingerprint: str | None = None) -> int:
        if fingerprint:
            cursor = await self._db.execute(
                "DELETE FROM memory_vectors WHERE fingerprint=?", (fingerprint,)
            )
        else:
            cursor = await self._db.execute("DELETE FROM memory_vectors")
        return int(cursor.rowcount or 0)

    async def delete_other_fingerprints(self, fingerprint: str) -> int:
        """删除与给定指纹不同的所有向量（模型更换后旧向量失效）。"""
        result = await self._db.execute(
            "DELETE FROM memory_vectors WHERE fingerprint<>?", (fingerprint,)
        )
        return int(result.rowcount or 0)

    async def missing_ids(self, ids: Sequence[int], fingerprint: str) -> list[int]:
        """返回尚无可用向量的记忆 ID（用于增量补算）。"""
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = await self._db.query(
            f"SELECT memory_id FROM memory_vectors WHERE fingerprint=? AND memory_id IN ({placeholders})",
            [fingerprint, *ids],
        )
        indexed = {int(row["memory_id"]) for row in rows}
        return [item for item in ids if item not in indexed]


# --------------------------------------------------------------------------- #
# 周记
# --------------------------------------------------------------------------- #


class JournalRepository:
    """``journals`` 表访问。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(
        self,
        *,
        scope_type: str,
        scope_id: str,
        content: str,
        tags: Sequence[str],
        emotion: int | None,
        event_time: float,
        memory_id: int | None,
        created_at: float,
    ) -> int:
        cursor = await self._db.execute(
            "INSERT INTO journals(scope_type, scope_id, content, tags, emotion, event_time,"
            " memory_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                scope_type,
                scope_id,
                content,
                json.dumps(list(tags), ensure_ascii=False),
                emotion,
                event_time,
                memory_id,
                created_at,
            ),
        )
        return int(cursor.lastrowid)

    async def get(self, journal_id: int) -> dict[str, Any] | None:
        row = await self._db.query_one("SELECT * FROM journals WHERE id=?", (journal_id,))
        return None if row is None else row_to_dict(row)

    async def list_recent(
        self, scopes: Sequence[MemoryScope], *, limit: int
    ) -> list[dict[str, Any]]:
        where, params = _scope_where(scopes)
        rows = await self._db.query(
            f"SELECT * FROM journals WHERE ({where}) ORDER BY event_time DESC LIMIT ?",
            [*params, limit],
        )
        return [row_to_dict(row) for row in rows]

    async def list_between(
        self, scopes: Sequence[MemoryScope], *, start: float, end: float, limit: int = 200
    ) -> list[dict[str, Any]]:
        where, params = _scope_where(scopes)
        rows = await self._db.query(
            f"SELECT * FROM journals WHERE ({where}) AND event_time>=? AND event_time<?"
            " ORDER BY event_time ASC LIMIT ?",
            [*params, start, end, limit],
        )
        return [row_to_dict(row) for row in rows]

    async def list_page(
        self, scopes: Sequence[MemoryScope], *, offset: int, limit: int
    ) -> list[dict[str, Any]]:
        where, params = _scope_where(scopes)
        rows = await self._db.query(
            f"SELECT * FROM journals WHERE ({where}) ORDER BY event_time DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        return [row_to_dict(row) for row in rows]

    async def delete(self, journal_id: int) -> bool:
        cursor = await self._db.execute("DELETE FROM journals WHERE id=?", (journal_id,))
        return bool(getattr(cursor, "rowcount", 0))

    async def count(self, scopes: Sequence[MemoryScope]) -> int:
        where, params = _scope_where(scopes)
        return int(
            await self._db.scalar(
                f"SELECT COUNT(*) FROM journals WHERE ({where})", params, default=0
            )
        )

    async def all_scopes(self) -> list[tuple[str, str]]:
        """列出存在周记的作用域（供周度洞察遍历）。"""
        rows = await self._db.query("SELECT DISTINCT scope_type, scope_id FROM journals")
        return [(str(row["scope_type"]), str(row["scope_id"])) for row in rows]

    async def list_all_page(self, *, offset: int, limit: int) -> list[dict[str, Any]]:
        """跨作用域分页（面板总览用）。"""
        rows = await self._db.query(
            "SELECT * FROM journals ORDER BY event_time DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [row_to_dict(row) for row in rows]

    async def count_all(self) -> int:
        return int(await self._db.scalar("SELECT COUNT(*) FROM journals", default=0))


# --------------------------------------------------------------------------- #
# 反思与审批
# --------------------------------------------------------------------------- #


class ReflectionRepository:
    """``reflection_logs`` 表访问。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def start(self, *, scope_type: str, scope_id: str, started_at: float) -> int:
        cursor = await self._db.execute(
            "INSERT INTO reflection_logs(scope_type, scope_id, started_at, status, produced,"
            " detail, error) VALUES (?,?,?, 'running', 0, '', '')",
            (scope_type, scope_id, started_at),
        )
        return int(cursor.lastrowid)

    async def finish(
        self,
        log_id: int,
        *,
        finished_at: float,
        status: str,
        produced: int,
        detail: str = "",
        error: str = "",
    ) -> None:
        await self._db.execute(
            "UPDATE reflection_logs SET finished_at=?, status=?, produced=?, detail=?, error=?"
            " WHERE id=?",
            (finished_at, status, produced, detail, error, log_id),
        )

    async def list_recent(
        self, scopes: Sequence[MemoryScope], *, limit: int
    ) -> list[dict[str, Any]]:
        where, params = _scope_where(scopes)
        rows = await self._db.query(
            f"SELECT * FROM reflection_logs WHERE ({where}) ORDER BY started_at DESC LIMIT ?",
            [*params, limit],
        )
        return [row_to_dict(row) for row in rows]

    async def last_finished_at(self, scopes: Sequence[MemoryScope]) -> float:
        where, params = _scope_where(scopes)
        value = await self._db.scalar(
            f"SELECT MAX(finished_at) FROM reflection_logs WHERE ({where}) AND status='ok'",
            params,
            default=0.0,
        )
        return float(value or 0.0)


class ReviewRepository:
    """``pending_reviews`` 表访问（审批制写入）。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(
        self,
        *,
        scope_type: str,
        scope_id: str,
        origin: str,
        payload: dict[str, Any],
        created_at: float,
    ) -> int:
        cursor = await self._db.execute(
            "INSERT INTO pending_reviews(scope_type, scope_id, origin, payload, status, created_at)"
            " VALUES (?,?,?,?, 'pending', ?)",
            (scope_type, scope_id, origin, json.dumps(payload, ensure_ascii=False), created_at),
        )
        return int(cursor.lastrowid)

    async def list_pending(
        self, scopes: Sequence[MemoryScope], *, limit: int
    ) -> list[dict[str, Any]]:
        where, params = _scope_where(scopes)
        # 以 id 作为次级排序键：同一秒创建的记录较多时，仅按 created_at 排序结果不确定。
        rows = await self._db.query(
            f"SELECT * FROM pending_reviews WHERE status='pending' AND ({where})"
            " ORDER BY created_at ASC, id ASC LIMIT ?",
            [*params, limit],
        )
        return [row_to_dict(row) for row in rows]

    async def get(self, review_id: int) -> dict[str, Any] | None:
        row = await self._db.query_one("SELECT * FROM pending_reviews WHERE id=?", (review_id,))
        return None if row is None else row_to_dict(row)

    async def set_status(self, review_id: int, status: str, *, at: float) -> bool:
        cursor = await self._db.execute(
            "UPDATE pending_reviews SET status=?, decided_at=? WHERE id=? AND status='pending'",
            (status, at, review_id),
        )
        return bool(getattr(cursor, "rowcount", 0))

    async def count_pending(self, scopes: Sequence[MemoryScope]) -> int:
        where, params = _scope_where(scopes)
        return int(
            await self._db.scalar(
                f"SELECT COUNT(*) FROM pending_reviews WHERE status='pending' AND ({where})",
                params,
                default=0,
            )
        )


def decode_tags(raw: Any) -> list[str]:
    """对外暴露标签解码（仓储内部也使用）。"""
    return _decode_tags(raw)


def coerce_tags(value: Iterable[Any] | None) -> list[str]:
    """把任意标签容器规整为字符串列表。"""
    if not value:
        return []
    return [str(item).strip().lstrip("#") for item in value if str(item).strip()]
