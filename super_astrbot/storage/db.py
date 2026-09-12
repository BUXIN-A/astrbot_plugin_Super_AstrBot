"""SQLite 持久化核心（基于标准库 ``sqlite3``，零第三方依赖）。

为什么不用 ``aiosqlite``：

- 宿主环境未必安装、且本地沙箱/精简环境的安装权限不可控；
- 标准库 ``sqlite3`` + ``asyncio.to_thread`` 已足够，且**只有一条代码路径**，
  避免「有 aiosqlite 走一套、没有走另一套」带来的行为分叉；
- 语音机器人场景下 SQLite 的吞吐远不是瓶颈。

并发模型：

- 单个连接（``check_same_thread=False``），**所有**数据库操作经一把 ``asyncio.Lock``
  串行化，杜绝事务交错；个人机器人场景下这是「简单且正确」的取舍。
- 每个操作通过 ``asyncio.to_thread`` 卸载到线程，不阻塞事件循环。

其它要点：

- 连接置为 ``isolation_level=None``（autocommit），跨语句原子性走 ``transaction()``；
- 迁移前自动备份，最多保留若干份；
- 跨表写入前登记 ``write_ops``，启动时可识别并重放未完成项；
- FTS5 不可用时 ``fts_available`` 为 False，检索层自动降级为 LIKE。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Sequence

from ..spec.errors import StorageError, safe_detail
from .migrations import CURRENT_VERSION, FTS_STATEMENTS, MIGRATIONS

_BACKUP_KEEP = 3


@dataclass(frozen=True)
class ExecuteResult:
    """写入结果，替代裸 cursor（跨线程传递更安全）。"""

    lastrowid: int = 0
    rowcount: int = 0


class Transaction:
    """事务句柄：只暴露参数化执行方法，自动把调用卸载到线程。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> ExecuteResult:
        return await asyncio.to_thread(self._execute_sync, sql, params)

    def _execute_sync(self, sql: str, params: Sequence[Any]) -> ExecuteResult:
        cursor = self._conn.execute(sql, params)
        try:
            return ExecuteResult(int(cursor.lastrowid or 0), int(cursor.rowcount or 0))
        finally:
            cursor.close()

    async def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> ExecuteResult:
        return await asyncio.to_thread(self._executemany_sync, sql, seq)

    def _executemany_sync(self, sql: str, seq: Iterable[Sequence[Any]]) -> ExecuteResult:
        cursor = self._conn.executemany(sql, seq)
        try:
            return ExecuteResult(0, int(cursor.rowcount or 0))
        finally:
            cursor.close()

    async def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return await asyncio.to_thread(self._query_sync, sql, params)

    def _query_sync(self, sql: str, params: Sequence[Any]) -> list[sqlite3.Row]:
        cursor = self._conn.execute(sql, params)
        try:
            return list(cursor.fetchall())
        finally:
            cursor.close()

    async def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        rows = await self.query(sql, params)
        return rows[0] if rows else None

    async def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = await self.query_one(sql, params)
        if row is None:
            return default
        value = row[0]
        return default if value is None else value


class Database:
    """异步 SQLite 数据库封装。"""

    def __init__(self, path: str | Path, *, logger: Any | None = None) -> None:
        self._path = Path(path)
        self._logger = logger
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        self._fts_available = False

    # ------------------------------------------------------------------ #
    # 属性
    # ------------------------------------------------------------------ #

    @property
    def path(self) -> Path:
        return self._path

    @property
    def connected(self) -> bool:
        return self._conn is not None

    @property
    def fts_available(self) -> bool:
        return self._fts_available

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    async def connect(self) -> None:
        if self._conn is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = await asyncio.to_thread(self._open_sync)
        except sqlite3.Error as exc:
            raise StorageError(f"打开数据库失败：{safe_detail(exc)}") from exc
        self._conn = conn
        await self.migrate()

    def _open_sync(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = None  # autocommit；显式事务由 transaction() 负责
        for pragma in (
            "PRAGMA journal_mode=WAL",
            "PRAGMA synchronous=NORMAL",
            "PRAGMA busy_timeout=5000",
            "PRAGMA foreign_keys=ON",
        ):
            try:
                conn.execute(pragma)
            except sqlite3.Error as exc:
                self._warn("设置 %s 失败：%s", pragma, safe_detail(exc))
        return conn

    async def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        try:
            await asyncio.to_thread(conn.close)
        except sqlite3.Error as exc:
            self._warn("关闭数据库失败：%s", safe_detail(exc))

    # ------------------------------------------------------------------ #
    # 基础执行
    # ------------------------------------------------------------------ #

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StorageError("数据库尚未连接")
        return self._conn

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> ExecuteResult:
        async with self._lock:
            return await Transaction(self._require_conn()).execute(sql, params)

    async def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> ExecuteResult:
        async with self._lock:
            return await Transaction(self._require_conn()).executemany(sql, seq)

    async def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        async with self._lock:
            return await Transaction(self._require_conn()).query(sql, params)

    async def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        async with self._lock:
            return await Transaction(self._require_conn()).query_one(sql, params)

    async def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        async with self._lock:
            return await Transaction(self._require_conn()).scalar(sql, params, default)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[Transaction]:
        """互斥事务；异常自动回滚。"""
        async with self._lock:
            conn = self._require_conn()
            tx = Transaction(conn)
            await tx.execute("BEGIN IMMEDIATE")
            try:
                yield tx
            except BaseException:
                try:
                    await tx.execute("ROLLBACK")
                except Exception as exc:
                    self._warn("事务回滚失败：%s", safe_detail(exc))
                raise
            else:
                await tx.execute("COMMIT")

    # ------------------------------------------------------------------ #
    # 迁移
    # ------------------------------------------------------------------ #

    async def migrate(self) -> None:
        """执行待应用迁移；迁移前自动备份。"""
        conn = self._require_conn()
        await Transaction(conn).execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "version INTEGER PRIMARY KEY, applied_at REAL NOT NULL,"
            "description TEXT NOT NULL DEFAULT '')"
        )
        current = int(
            await Transaction(conn).scalar(
                "SELECT COALESCE(MAX(version), 0) FROM schema_version", default=0
            )
        )

        pending = [item for item in MIGRATIONS if item.version > current]
        if pending:
            await self.backup()
            for item in pending:
                await self._apply_migration(item)
            self._info("数据库迁移完成：%s → %s", current, CURRENT_VERSION)

        await self._ensure_fts()

    async def _apply_migration(self, migration: Any) -> None:
        async with self.transaction() as tx:
            for statement in migration.statements:
                await tx.execute(statement)
            await tx.execute(
                "INSERT OR REPLACE INTO schema_version(version, applied_at, description) VALUES (?,?,?)",
                (migration.version, time.time(), migration.description),
            )

    async def _ensure_fts(self) -> None:
        """尝试建立 FTS5 索引表；不支持时置降级标记。"""
        conn = self._require_conn()
        try:
            for statement in FTS_STATEMENTS:
                await Transaction(conn).execute(statement)
            self._fts_available = True
        except sqlite3.Error as exc:
            self._fts_available = False
            self._warn("FTS5 不可用，关键词检索将降级为 LIKE：%s", safe_detail(exc))

    async def backup(self) -> Path | None:
        """备份数据库文件，返回备份路径（失败返回 None，不阻断迁移）。"""
        if not self._path.exists():
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self._path.with_name(f"{self._path.name}.bak-{stamp}")
        try:
            await asyncio.to_thread(shutil.copy2, self._path, target)
            await self._prune_backups()
            self._info("数据库已备份：%s", target.name)
            return target
        except OSError as exc:
            self._warn("数据库备份失败：%s", safe_detail(exc))
            return None

    async def _prune_backups(self) -> None:
        try:
            backups = sorted(
                self._path.parent.glob(f"{self._path.name}.bak-*"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        for stale in backups[_BACKUP_KEEP:]:
            try:
                await asyncio.to_thread(stale.unlink)
            except OSError:
                continue

    # ------------------------------------------------------------------ #
    # 可恢复写日志
    # ------------------------------------------------------------------ #

    async def begin_write_op(
        self, op_id: str, op_type: str, step: str, payload: dict[str, Any] | None = None
    ) -> None:
        now = time.time()
        await self.execute(
            "INSERT INTO write_ops(op_id, op_type, step, payload, status, retries,"
            " created_at, updated_at) VALUES (?,?,?,?, 'running', 0, ?, ?)"
            " ON CONFLICT(op_id) DO UPDATE SET step=excluded.step,"
            " payload=excluded.payload, status='running', updated_at=excluded.updated_at",
            (op_id, op_type, step, json.dumps(payload or {}, ensure_ascii=False), now, now),
        )

    async def advance_write_op(
        self,
        op_id: str,
        step: str,
        payload: dict[str, Any] | None = None,
        *,
        status: str = "running",
    ) -> None:
        await self.execute(
            "UPDATE write_ops SET step=?, payload=?, status=?, updated_at=? WHERE op_id=?",
            (step, json.dumps(payload or {}, ensure_ascii=False), status, time.time(), op_id),
        )

    async def finish_write_op(self, op_id: str) -> None:
        await self.execute("DELETE FROM write_ops WHERE op_id=?", (op_id,))

    async def load_open_write_ops(self) -> list[sqlite3.Row]:
        return await self.query(
            "SELECT op_id, op_type, step, payload, status, retries, created_at, updated_at"
            " FROM write_ops WHERE status='running' ORDER BY created_at ASC"
        )

    async def bump_write_op_retry(self, op_id: str, *, limit: int = 3) -> bool:
        """重试计数 +1；达到上限置为 ``failed`` 并返回 False。"""
        row = await self.query_one("SELECT retries FROM write_ops WHERE op_id=?", (op_id,))
        if row is None:
            return False
        retries = int(row["retries"]) + 1
        status = "running" if retries < limit else "failed"
        await self.execute(
            "UPDATE write_ops SET retries=?, status=?, updated_at=? WHERE op_id=?",
            (retries, status, time.time(), op_id),
        )
        return status == "running"

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #

    def _info(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.info(message, *args)

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)


class SqliteStateStore:
    """``loop.StateStore`` 的 SQLite 实现（供调度器做当日幂等）。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str, default: Any = None) -> Any:
        row = await self._db.query_one("SELECT value FROM kv_state WHERE key=?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return default

    async def set(self, key: str, value: Any) -> None:
        await self._db.execute(
            "INSERT INTO kv_state(key, value, updated_at) VALUES (?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), time.time()),
        )

    async def delete(self, key: str) -> None:
        await self._db.execute("DELETE FROM kv_state WHERE key=?", (key,))

    async def keys(self, prefix: str = "") -> list[str]:
        if prefix:
            escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = await self._db.query(
                "SELECT key FROM kv_state WHERE key LIKE ? ESCAPE '\\'", (escaped + "%",)
            )
        else:
            rows = await self._db.query("SELECT key FROM kv_state")
        return [str(row["key"]) for row in rows]
