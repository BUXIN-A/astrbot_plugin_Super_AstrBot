"""数据库迁移定义。

硬性规则（写入 ``SPEC.md`` 并被本模块强制）：

1. **只做增量**：只允许 ``CREATE`` / ``ALTER TABLE ADD COLUMN`` / ``CREATE INDEX``；
   禁止删列、改类型等破坏性操作（参考 self_learning 的教训：破坏性迁移一旦出错不可逆）。
2. **版本化**：每次结构变更追加一个新 ``Migration``，``version`` 严格递增。
3. **迁移前备份**：由 ``Database.migrate`` 负责，本模块只描述结构。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    statements: tuple[str, ...]


_FTS_STATEMENTS: tuple[str, ...] = (
    # 仅索引 tokens 列（分词由 support.text 统一负责），避免 unicode61 对中文整段成一个词。
    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_index USING fts5(tokens, tokenize='unicode61')",
)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        description="初始结构：记忆、向量、周记、反思、审批、写日志、运行状态",
        statements=(
            # ---------------- 记忆 ----------------
            """
            CREATE TABLE IF NOT EXISTS memories (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type     TEXT    NOT NULL,
                scope_id       TEXT    NOT NULL,
                kind           TEXT    NOT NULL DEFAULT 'fact',
                content        TEXT    NOT NULL,
                importance     REAL    NOT NULL DEFAULT 0.5,
                confidence     REAL    NOT NULL DEFAULT 0.8,
                source         TEXT    NOT NULL DEFAULT 'capture',
                tags           TEXT    NOT NULL DEFAULT '[]',
                created_at     REAL    NOT NULL,
                updated_at     REAL    NOT NULL,
                last_access_at REAL    NOT NULL DEFAULT 0,
                access_count   INTEGER NOT NULL DEFAULT 0,
                status         TEXT    NOT NULL DEFAULT 'active'
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope_type, scope_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind, status)",
            # ---------------- 向量（可选检索路） ----------------
            """
            CREATE TABLE IF NOT EXISTS memory_vectors (
                memory_id   INTEGER PRIMARY KEY,
                fingerprint TEXT    NOT NULL,
                dim         INTEGER NOT NULL,
                vector      BLOB    NOT NULL,
                updated_at  REAL    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_vectors_fingerprint ON memory_vectors(fingerprint)",
            # ---------------- 记忆关联（为后续图谱预留） ----------------
            """
            CREATE TABLE IF NOT EXISTS memory_links (
                src_id     INTEGER NOT NULL,
                dst_id     INTEGER NOT NULL,
                relation   TEXT    NOT NULL,
                weight     REAL    NOT NULL DEFAULT 1.0,
                created_at REAL    NOT NULL,
                PRIMARY KEY (src_id, dst_id, relation)
            )
            """,
            # ---------------- 周记 ----------------
            """
            CREATE TABLE IF NOT EXISTS journals (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type TEXT    NOT NULL,
                scope_id   TEXT    NOT NULL,
                content    TEXT    NOT NULL,
                tags       TEXT    NOT NULL DEFAULT '[]',
                emotion    INTEGER,
                event_time REAL    NOT NULL,
                memory_id  INTEGER,
                created_at REAL    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_journals_scope_time ON journals(scope_type, scope_id, event_time DESC)",
            # ---------------- 反思记录 ----------------
            """
            CREATE TABLE IF NOT EXISTS reflection_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type  TEXT    NOT NULL,
                scope_id    TEXT    NOT NULL,
                started_at  REAL    NOT NULL,
                finished_at REAL,
                status      TEXT    NOT NULL DEFAULT 'running',
                produced    INTEGER NOT NULL DEFAULT 0,
                detail      TEXT    NOT NULL DEFAULT '',
                error       TEXT    NOT NULL DEFAULT ''
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_reflection_scope ON reflection_logs(scope_type, scope_id, started_at DESC)",
            # ---------------- 待审队列 ----------------
            """
            CREATE TABLE IF NOT EXISTS pending_reviews (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type TEXT    NOT NULL,
                scope_id   TEXT    NOT NULL,
                origin     TEXT    NOT NULL DEFAULT 'reflection',
                payload    TEXT    NOT NULL,
                status     TEXT    NOT NULL DEFAULT 'pending',
                created_at REAL    NOT NULL,
                decided_at REAL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_reviews_status ON pending_reviews(status, created_at DESC)",
            # ---------------- 可恢复写日志 ----------------
            """
            CREATE TABLE IF NOT EXISTS write_ops (
                op_id      TEXT    PRIMARY KEY,
                op_type    TEXT    NOT NULL,
                step       TEXT    NOT NULL,
                payload    TEXT    NOT NULL DEFAULT '{}',
                status     TEXT    NOT NULL DEFAULT 'running',
                retries    INTEGER NOT NULL DEFAULT 0,
                created_at REAL    NOT NULL,
                updated_at REAL    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_write_ops_status ON write_ops(status, updated_at DESC)",
            # ---------------- 运行状态（调度幂等 / 游标 / 节流） ----------------
            """
            CREATE TABLE IF NOT EXISTS kv_state (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """,
        ),
    ),
)
"""迁移列表。当前 schema 版本 = 最后一项的 version。"""

CURRENT_VERSION: int = MIGRATIONS[-1].version if MIGRATIONS else 0

FTS_STATEMENTS: tuple[str, ...] = _FTS_STATEMENTS
"""FTS 建表语句单独管理：FTS5 可能不被某些 SQLite 构建支持，失败时需降级而非中断迁移。"""
