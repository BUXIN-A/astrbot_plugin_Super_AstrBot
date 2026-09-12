"""仓储层：把 SQL 收敛在少数几个类中。

约定：

- 仓储只接受/返回**普通字典型数据**，不返回 ORM 对象，避免上层被存储细节污染；
- 单条写走 ``Database.execute``（autocommit），需要跨语句原子性时由服务层显式
  使用 ``Database.transaction()``（事务边界与写日志由服务层决定）；
- 时间戳统一由调用方传入（便于测试注入固定时钟）。
"""

from __future__ import annotations

import json
import struct
from typing import Any, Mapping, Sequence

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


def _like_pattern(term: str) -> str:
    """把关键词包装成 LIKE 模式并转义通配符。

    不转义时用户输入里的 ``%`` / ``_`` 会被当作通配符，导致匹配范围意外放大。
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    return {key: row[key] for key in row.keys()}


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
            clauses.append("content LIKE ? ESCAPE '\\'")
            params.append(_like_pattern(keyword))
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
        kind: str = "",
    ) -> list[dict[str, Any]]:
        """跨作用域分页（面板总览用），支持按状态/类型/关键词过滤。"""
        clauses = ["status=?"]
        params: list[Any] = [status]
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        if keyword:
            clauses.append("content LIKE ? ESCAPE '\\'")
            params.append(_like_pattern(keyword))
        params.extend([limit, offset])
        rows = await self._db.query(
            f"SELECT * FROM memories WHERE {' AND '.join(clauses)}"
            " ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        )
        return [row_to_dict(row) for row in rows]

    async def count_filtered(
        self, *, status: str = "active", kind: str = "", keyword: str = ""
    ) -> int:
        """与 ``list_all_page`` 同条件的总数（面板分页需要）。"""
        clauses = ["status=?"]
        params: list[Any] = [status]
        if kind:
            clauses.append("kind=?")
            params.append(kind)
        if keyword:
            clauses.append("content LIKE ? ESCAPE '\\'")
            params.append(_like_pattern(keyword))
        return int(
            await self._db.scalar(
                f"SELECT COUNT(*) FROM memories WHERE {' AND '.join(clauses)}",
                params,
                default=0,
            )
        )

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

    async def delete_index_many(self, ids: Sequence[int]) -> None:
        """批量删除 FTS 索引（批量归档/遗忘时避免逐条删除）。"""
        if not ids or not self._db.fts_available:
            return
        placeholders = ",".join("?" for _ in ids)
        await self._db.execute(
            f"DELETE FROM memory_index WHERE rowid IN ({placeholders})", list(ids)
        )

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
        term_clauses = " OR ".join("content LIKE ? ESCAPE '\\'" for _ in terms)
        rows = await self._db.query(
            f"SELECT id FROM memories WHERE status='active' AND ({where})"
            f" AND ({term_clauses}) ORDER BY created_at DESC LIMIT ?",
            [*params, *[_like_pattern(term) for term in terms], limit],
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

    async def delete_many(self, ids: Sequence[int]) -> int:
        """批量删除向量（批量归档/遗忘时避免逐条删除）。"""
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        result = await self._db.execute(
            f"DELETE FROM memory_vectors WHERE memory_id IN ({placeholders})", list(ids)
        )
        return int(result.rowcount or 0)

    async def load_scoped(
        self, fingerprint: str, scopes: Sequence[MemoryScope], *, limit: int
    ) -> list[tuple[int, int, bytes]]:
        """按作用域加载可用向量（仅 ``active`` 记忆）。

        必须在 SQL 层与 ``memories`` 联结后按作用域过滤：若先全库取 ``limit`` 条
        再在应用层过滤，其它作用域的向量会挤占扫描额度，当前作用域的向量可能
        一条都取不到（进而让向量路静默失效）。
        """
        where, params = _scope_where(scopes, alias="m")
        rows = await self._db.query(
            "SELECT v.memory_id AS memory_id, v.dim AS dim, v.vector AS vector"
            " FROM memory_vectors v JOIN memories m ON m.id = v.memory_id"
            f" WHERE v.fingerprint=? AND m.status='active' AND ({where})"
            " ORDER BY v.updated_at DESC LIMIT ?",
            [fingerprint, *params, limit],
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

    async def list_all_pending(self, *, limit: int) -> list[dict[str, Any]]:
        """跨作用域读取待审队列（面板默认视角）。"""
        rows = await self._db.query(
            "SELECT * FROM pending_reviews WHERE status='pending'"
            " ORDER BY created_at ASC, id ASC LIMIT ?",
            (limit,),
        )
        return [row_to_dict(row) for row in rows]

    async def count_all_pending(self) -> int:
        return int(
            await self._db.scalar(
                "SELECT COUNT(*) FROM pending_reviews WHERE status='pending'", default=0
            )
        )


# --------------------------------------------------------------------------- #
# 拟人化学习
# --------------------------------------------------------------------------- #


class StyleRepository:
    """``style_patterns`` 表访问（user→bot 邻接对抽出的表达模式）。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(
        self,
        *,
        scope_type: str,
        scope_id: str,
        situation: str,
        expression: str,
        weight: float,
        source: str,
        created_at: float,
    ) -> int | None:
        """插入一条表达模式；同作用域内完全重复的模式返回 ``None``。"""
        result = await self._db.execute(
            "INSERT OR IGNORE INTO style_patterns(scope_type, scope_id, situation, expression,"
            " weight, hits, source, created_at, updated_at, status)"
            " VALUES (?,?,?,?,?,0,?,?,?, 'active')",
            (
                scope_type,
                scope_id,
                situation,
                expression,
                float(weight),
                source,
                created_at,
                created_at,
            ),
        )
        # OR IGNORE 命中唯一索引时不会写入，此时 lastrowid 不可信，必须看 rowcount。
        if not int(result.rowcount or 0):
            return None
        return int(result.lastrowid or 0) or None

    async def get(self, pattern_id: int) -> dict[str, Any] | None:
        row = await self._db.query_one("SELECT * FROM style_patterns WHERE id=?", (pattern_id,))
        return None if row is None else row_to_dict(row)

    async def list_active(
        self, scopes: Sequence[MemoryScope], *, limit: int = 200
    ) -> list[dict[str, Any]]:
        where, params = _scope_where(scopes)
        rows = await self._db.query(
            f"SELECT * FROM style_patterns WHERE status='active' AND ({where})"
            " ORDER BY weight DESC, id DESC LIMIT ?",
            [*params, limit],
        )
        return [row_to_dict(row) for row in rows]

    async def list_page(
        self, scopes: Sequence[MemoryScope], *, offset: int, limit: int
    ) -> list[dict[str, Any]]:
        where, params = _scope_where(scopes)
        rows = await self._db.query(
            f"SELECT * FROM style_patterns WHERE status='active' AND ({where})"
            " ORDER BY weight DESC, id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        return [row_to_dict(row) for row in rows]

    async def list_all_page(self, *, offset: int, limit: int) -> list[dict[str, Any]]:
        rows = await self._db.query(
            "SELECT * FROM style_patterns WHERE status='active'"
            " ORDER BY weight DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [row_to_dict(row) for row in rows]

    async def update_usage(
        self,
        pattern_ids: Sequence[int],
        *,
        weights: Mapping[int, float] | None = None,
        at: float,
    ) -> None:
        """注入命中后累计 hits；``weights`` 中给出的模式同时刷新权重。"""
        for pattern_id in pattern_ids:
            await self._db.execute(
                "UPDATE style_patterns SET hits=hits+1, weight=COALESCE(?, weight),"
                " updated_at=? WHERE id=?",
                ((weights or {}).get(pattern_id), at, pattern_id),
            )

    async def apply_decay(self, *, factor: float, floor: float, at: float) -> int:
        """整体按 ``factor`` 衰减，返回被归档（权重低于 ``floor``）的条数。"""
        await self._db.execute(
            "UPDATE style_patterns SET weight=weight*?, updated_at=? WHERE status='active'",
            (float(factor), at),
        )
        archived = await self._db.execute(
            "UPDATE style_patterns SET status='archived', updated_at=? WHERE status='active'"
            " AND weight<?",
            (at, float(floor)),
        )
        return int(archived.rowcount or 0)

    async def trim(self, scopes: Sequence[MemoryScope], *, keep: int, at: float) -> int:
        """只保留权重最高的 ``keep`` 条，其余归档（容量控制）。"""
        if keep <= 0:
            return 0
        where, params = _scope_where(scopes)
        result = await self._db.execute(
            f"UPDATE style_patterns SET status='archived', updated_at=? WHERE status='active'"
            f" AND ({where}) AND id NOT IN ("
            f" SELECT id FROM style_patterns WHERE status='active' AND ({where})"
            f" ORDER BY weight DESC, id DESC LIMIT ?)",
            [at, *params, *params, keep],
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def delete(self, pattern_id: int) -> bool:
        cursor = await self._db.execute("DELETE FROM style_patterns WHERE id=?", (pattern_id,))
        return bool(getattr(cursor, "rowcount", 0))

    async def clear_scopes(self, scopes: Sequence[MemoryScope]) -> int:
        where, params = _scope_where(scopes)
        cursor = await self._db.execute(f"DELETE FROM style_patterns WHERE ({where})", params)
        return int(getattr(cursor, "rowcount", 0) or 0)

    async def count(self, scopes: Sequence[MemoryScope]) -> int:
        where, params = _scope_where(scopes)
        return int(
            await self._db.scalar(
                f"SELECT COUNT(*) FROM style_patterns WHERE status='active' AND ({where})",
                params,
                default=0,
            )
        )

    async def all_scopes(self) -> list[tuple[str, str]]:
        """列出存在表达模式的作用域（每日容量淘汰需要逐作用域处理）。"""
        rows = await self._db.query(
            "SELECT DISTINCT scope_type, scope_id FROM style_patterns WHERE status='active'"
        )
        return [(str(row["scope_type"]), str(row["scope_id"])) for row in rows]

    async def count_all(self) -> int:
        return int(
            await self._db.scalar(
                "SELECT COUNT(*) FROM style_patterns WHERE status='active'", default=0
            )
        )


class JargonRepository:
    """``jargons`` 表访问（群组黑话词条）。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert(
        self,
        *,
        scope_type: str,
        scope_id: str,
        term: str,
        meaning: str,
        confidence: float,
        samples: Sequence[str],
        created_at: float,
        last_seen_at: float,
    ) -> int:
        """新增或更新词条：同词重复学习时累加证据数并刷新含义。"""
        cursor = await self._db.execute(
            "INSERT INTO jargons(scope_type, scope_id, term, meaning, confidence, evidence,"
            " samples, created_at, updated_at, last_seen_at, status)"
            " VALUES (?,?,?,?,?,1,?,?,?,?, 'active')"
            " ON CONFLICT(scope_type, scope_id, term) DO UPDATE SET"
            " meaning=excluded.meaning, confidence=excluded.confidence,"
            " evidence=jargons.evidence+1, samples=excluded.samples,"
            " updated_at=excluded.updated_at, last_seen_at=excluded.last_seen_at,"
            " status='active'",
            (
                scope_type,
                scope_id,
                term,
                meaning,
                float(confidence),
                json.dumps(list(samples), ensure_ascii=False),
                created_at,
                created_at,
                last_seen_at,
            ),
        )
        return int(cursor.lastrowid or 0)

    async def list_active(
        self, scopes: Sequence[MemoryScope], *, limit: int = 200
    ) -> list[dict[str, Any]]:
        where, params = _scope_where(scopes)
        rows = await self._db.query(
            f"SELECT * FROM jargons WHERE status='active' AND ({where})"
            " ORDER BY confidence DESC, evidence DESC, id DESC LIMIT ?",
            [*params, limit],
        )
        return [row_to_dict(row) for row in rows]

    async def existing_terms(self, scopes: Sequence[MemoryScope]) -> set[str]:
        where, params = _scope_where(scopes)
        rows = await self._db.query(f"SELECT term FROM jargons WHERE ({where})", params)
        return {str(row["term"]) for row in rows}

    async def delete(self, jargon_id: int) -> bool:
        cursor = await self._db.execute("DELETE FROM jargons WHERE id=?", (jargon_id,))
        return bool(getattr(cursor, "rowcount", 0))

    async def clear_scopes(self, scopes: Sequence[MemoryScope]) -> int:
        where, params = _scope_where(scopes)
        cursor = await self._db.execute(f"DELETE FROM jargons WHERE ({where})", params)
        return int(getattr(cursor, "rowcount", 0) or 0)

    async def list_all_page(self, *, offset: int, limit: int) -> list[dict[str, Any]]:
        rows = await self._db.query(
            "SELECT * FROM jargons WHERE status='active'"
            " ORDER BY confidence DESC, evidence DESC, id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [row_to_dict(row) for row in rows]

    async def count(self, scopes: Sequence[MemoryScope]) -> int:
        where, params = _scope_where(scopes)
        return int(
            await self._db.scalar(
                f"SELECT COUNT(*) FROM jargons WHERE status='active' AND ({where})",
                params,
                default=0,
            )
        )

    async def count_all(self) -> int:
        return int(
            await self._db.scalar("SELECT COUNT(*) FROM jargons WHERE status='active'", default=0)
        )


class AffinityRepository:
    """``affinity_state`` 表访问（按会话 + 对象累积的好感度）。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, scope_type: str, scope_id: str, target_id: str) -> dict[str, Any] | None:
        row = await self._db.query_one(
            "SELECT * FROM affinity_state WHERE scope_type=? AND scope_id=? AND target_id=?",
            (scope_type, scope_id, target_id),
        )
        return None if row is None else row_to_dict(row)

    async def upsert(
        self,
        *,
        scope_type: str,
        scope_id: str,
        target_id: str,
        score: float,
        mood: str,
        interactions: int,
        last_interaction: float,
        updated_at: float,
    ) -> None:
        await self._db.execute(
            "INSERT INTO affinity_state(scope_type, scope_id, target_id, score, mood,"
            " interactions, last_interaction, updated_at) VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(scope_type, scope_id, target_id) DO UPDATE SET"
            " score=excluded.score, mood=excluded.mood, interactions=excluded.interactions,"
            " last_interaction=excluded.last_interaction, updated_at=excluded.updated_at",
            (
                scope_type,
                scope_id,
                target_id,
                float(score),
                mood,
                int(interactions),
                float(last_interaction),
                updated_at,
            ),
        )

    async def list_by_scope(
        self, scope_type: str, scope_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = await self._db.query(
            "SELECT * FROM affinity_state WHERE scope_type=? AND scope_id=?"
            " ORDER BY score DESC, interactions DESC LIMIT ?",
            (scope_type, scope_id, limit),
        )
        return [row_to_dict(row) for row in rows]

    async def list_all_page(self, *, offset: int, limit: int) -> list[dict[str, Any]]:
        rows = await self._db.query(
            "SELECT * FROM affinity_state ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [row_to_dict(row) for row in rows]

    async def delete(self, scope_type: str, scope_id: str, target_id: str) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM affinity_state WHERE scope_type=? AND scope_id=? AND target_id=?",
            (scope_type, scope_id, target_id),
        )
        return bool(getattr(cursor, "rowcount", 0))

    async def clear_scopes(self, scopes: Sequence[MemoryScope]) -> int:
        where, params = _scope_where(scopes)
        cursor = await self._db.execute(f"DELETE FROM affinity_state WHERE ({where})", params)
        return int(getattr(cursor, "rowcount", 0) or 0)

    async def count_all(self) -> int:
        return int(await self._db.scalar("SELECT COUNT(*) FROM affinity_state", default=0))
