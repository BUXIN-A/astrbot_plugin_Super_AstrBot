"""持久层测试：迁移幂等、FTS 与降级、状态存储、写日志、事务回滚。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from super_astrbot.spec.scopes import MemoryScope
from super_astrbot.storage import (
    CURRENT_VERSION,
    Database,
    MemoryRepository,
    SqliteStateStore,
    VectorRepository,
)


def test_migration_is_idempotent(tmp_path: Path) -> None:
    async def _run() -> tuple[int, int, bool]:
        db_path = tmp_path / "idempotent.db"
        first = Database(db_path)
        await first.connect()
        version_first = int(
            await first.scalar("SELECT MAX(version) FROM schema_version", default=0)
        )
        await first.close()

        second = Database(db_path)
        await second.connect()
        version_second = int(
            await second.scalar("SELECT MAX(version) FROM schema_version", default=0)
        )
        fts = second.fts_available
        await second.close()
        return version_first, version_second, fts

    version_first, version_second, _ = asyncio.run(_run())
    assert version_first == CURRENT_VERSION
    assert version_second == CURRENT_VERSION


def test_fts_and_like_fallback(tmp_path: Path) -> None:
    async def _run() -> tuple[list[tuple[int, float]], list[int]]:
        db = Database(tmp_path / "fts.db")
        await db.connect()
        repo = MemoryRepository(db)
        memory_id = await repo.insert(
            scope_type="session",
            scope_id="s1",
            kind="fact",
            content="用户上周项目上线遇到严重 bug，非常疲惫",
            importance=0.8,
            confidence=0.9,
            source="manual",
            tags=["工作"],
            created_at=1000.0,
        )
        # 手动写入分词结果（与线上一致：分词由 support.text 统一负责）
        from super_astrbot.spec.scopes import MemoryScope
        from super_astrbot.support import build_match_query, tokenize

        await repo.index_tokens(
            memory_id, " ".join(tokenize("用户上周项目上线遇到严重bug非常疲惫"))
        )
        scope = MemoryScope.for_session("s1")
        fts_hits = await repo.fts_search(
            (scope,), build_match_query(tokenize("项目 上线 疲惫")), limit=5
        )
        like_hits = await repo.like_search((scope,), ["上线"], limit=5)
        await db.close()
        return fts_hits, like_hits

    fts_hits, like_hits = asyncio.run(_run())
    assert fts_hits and fts_hits[0][0] == 1
    assert like_hits == [1]


def test_state_store_roundtrip(tmp_path: Path) -> None:
    async def _run() -> tuple[dict, str, list[str]]:
        db = Database(tmp_path / "state.db")
        await db.connect()
        store = SqliteStateStore(db)
        await store.set("job:daily", {"last_date": "2026-09-12", "runs": 3})
        value = await store.get("job:daily", {})
        missing = await store.get("nope", "default")
        await store.set("job:other", 1)
        keys = await store.keys("job:")
        await db.close()
        return value, missing, sorted(keys)

    value, missing, keys = asyncio.run(_run())
    assert value["runs"] == 3
    assert missing == "default"
    assert keys == ["job:daily", "job:other"]


def test_write_op_lifecycle_and_retry_limit(tmp_path: Path) -> None:
    async def _run() -> tuple[int, int, bool, bool]:
        db = Database(tmp_path / "wop.db")
        await db.connect()
        await db.begin_write_op("op-1", "memory_add", "insert", {"x": 1})
        open_ops = await db.load_open_write_ops()
        await db.advance_write_op("op-1", "index", {"memory_id": 7})
        await db.finish_write_op("op-1")
        after_finish = len(await db.load_open_write_ops())

        await db.begin_write_op("op-2", "memory_add", "insert")
        still_running = await db.bump_write_op_retry("op-2", limit=2)
        exceeded = await db.bump_write_op_retry("op-2", limit=2)
        await db.close()
        return len(open_ops), after_finish, still_running, exceeded

    opened, after_finish, still_running, exceeded = asyncio.run(_run())
    assert opened == 1
    assert after_finish == 0
    assert still_running is True
    assert exceeded is False


def test_transaction_rolls_back_on_error(tmp_path: Path) -> None:
    async def _run() -> int:
        db = Database(tmp_path / "tx.db")
        await db.connect()
        repo = MemoryRepository(db)
        try:
            async with db.transaction() as tx:
                await tx.execute(
                    "INSERT INTO memories(scope_type, scope_id, kind, content, importance,"
                    " confidence, source, tags, created_at, updated_at) VALUES"
                    " ('session','s1','fact','x',0.5,0.5,'manual','[]',1,1)"
                )
                raise RuntimeError("模拟失败")
        except RuntimeError:
            pass
        rows = await repo.count_all()
        await db.close()
        return rows

    assert asyncio.run(_run()) == 0


def test_like_keyword_escapes_wildcards(tmp_path: Path) -> None:
    """关键词里的 ``%`` / ``_`` 必须按字面匹配，否则筛选范围会被意外放大。"""

    async def _run() -> tuple[list[str], list[int]]:
        db = Database(tmp_path / "like.db")
        await db.connect()
        repo = MemoryRepository(db)
        for index, content in enumerate(("进度 50% 已完成", "进度 50点 已完成"), start=1):
            await repo.insert(
                scope_type="session",
                scope_id="s1",
                kind="fact",
                content=content,
                importance=0.5,
                confidence=0.8,
                source="manual",
                tags=[],
                created_at=float(index),
            )
        rows = await repo.list_all_page(offset=0, limit=10, keyword="50%")
        like_ids = await repo.like_search((MemoryScope.for_session("s1"),), ["50%"], limit=10)
        await db.close()
        return [str(row["content"]) for row in rows], like_ids

    contents, like_ids = asyncio.run(_run())
    assert contents == ["进度 50% 已完成"]
    assert like_ids == [1]


def test_vector_load_scoped_filters_by_scope(tmp_path: Path) -> None:
    """向量扫描必须在 SQL 层按作用域过滤，避免其它作用域挤占扫描额度。"""

    async def _run() -> list[int]:
        db = Database(tmp_path / "vec.db")
        await db.connect()
        memories = MemoryRepository(db)
        vectors = VectorRepository(db)
        mine = await memories.insert(
            scope_type="session",
            scope_id="mine",
            kind="fact",
            content="我的记忆",
            importance=0.5,
            confidence=0.8,
            source="manual",
            tags=[],
            created_at=1.0,
        )
        other = await memories.insert(
            scope_type="session",
            scope_id="other",
            kind="fact",
            content="别人的记忆",
            importance=0.5,
            confidence=0.8,
            source="manual",
            tags=[],
            created_at=2.0,
        )
        await vectors.upsert(mine, "fp", [0.1, 0.2], at=1.0)
        await vectors.upsert(other, "fp", [0.3, 0.4], at=2.0)

        rows = await vectors.load_scoped("fp", (MemoryScope.for_session("mine"),), limit=10)
        await db.close()
        return [row[0] for row in rows]

    assert asyncio.run(_run()) == [1]


def test_journals_table_has_bridge_columns(tmp_path: Path) -> None:
    """迁移 v4：``journals`` 增补标题与类型列，历史行由列默认值补齐。

    增量迁移必须保证旧数据可读：因此这里直接按「没有这两列」的方式插一行，
    再确认读回来时带上了默认标题位（空串）与默认类型（weekly）。
    """

    async def _run() -> tuple[list[str], dict]:
        from super_astrbot.storage import JournalRepository

        db = Database(tmp_path / "bridge.db")
        await db.connect()
        columns = [
            str(row["name"]) for row in await db.query("PRAGMA table_info(journals)")
        ]
        await db.execute(
            "INSERT INTO journals(scope_type, scope_id, content, tags, emotion,"
            " event_time, memory_id, created_at) VALUES ('global','*','旧库记录','[]',3,1.0,NULL,1.0)"
        )
        repo = JournalRepository(db)
        row = (await repo.export_all())[0]
        await db.close()
        return columns, row

    columns, row = asyncio.run(_run())
    assert "title" in columns and "entry_type" in columns
    assert row["content"] == "旧库记录"
    assert row["title"] == "", "历史行标题留空，由读取方按条目时间补默认标题"
    assert row["entry_type"] == "weekly"


def test_upgrade_from_v4_database_adds_identity_columns(tmp_path: Path) -> None:
    """真实升级路径：已有 v4 库（线上就是这种）→ 打开后补到 v5，历史数据不丢。

    做法：先用「只到 v4」的迁移清单建库并写入历史记忆（无身份列），
    再用完整清单打开同一个文件，验证 v5 增量迁移生效且旧行可读。
    """
    from super_astrbot.storage import IdentityRepository, MemoryRepository
    # db.py 在导入时就把 MIGRATIONS/CURRENT_VERSION 绑成了自己的名字，
    # 因此必须打在它那一侧，改 migrations 模块的变量不会影响已绑定的引用。
    from super_astrbot.storage import db as db_module

    async def _run() -> dict:
        db_path = tmp_path / "legacy.db"

        # 1) 造一个 v4 时代的库
        full = db_module.MIGRATIONS
        db_module.MIGRATIONS = tuple(m for m in full if m.version <= 4)
        db_module.CURRENT_VERSION = 4
        try:
            legacy = Database(db_path)
            await legacy.connect()
            await legacy.execute(
                "INSERT INTO memories(scope_type, scope_id, kind, content, importance,"
                " confidence, source, tags, created_at, updated_at, last_access_at,"
                " access_count, status) VALUES ('session','umo-a','fact','历史记忆',0.5,0.8,"
                "'capture','[]',1.0,1.0,0,0,'active')"
            )
            version_before = int(
                await legacy.scalar("SELECT MAX(version) FROM schema_version", default=0)
            )
            columns_before = [
                str(row["name"]) for row in await legacy.query("PRAGMA table_info(memories)")
            ]
            await legacy.close()
        finally:
            db_module.MIGRATIONS = full
            db_module.CURRENT_VERSION = full[-1].version

        # 2) 用当前代码打开同一个库 → v5 增量迁移
        upgraded = Database(db_path)
        await upgraded.connect()
        try:
            version_after = int(
                await upgraded.scalar("SELECT MAX(version) FROM schema_version", default=0)
            )
            columns_after = [
                str(row["name"]) for row in await upgraded.query("PRAGMA table_info(memories)")
            ]
            tables = [
                str(row["name"])
                for row in await upgraded.query(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
            row = (await MemoryRepository(upgraded).export_visible())[0]
            identities = IdentityRepository(upgraded)
            await identities.observe(umo="umo-a", sender_id="u1", sender_name="谷雨", now=2.0)
            observed = await identities.list_all()
            return {
                "version_before": version_before,
                "version_after": version_after,
                "columns_before": columns_before,
                "columns_after": columns_after,
                "tables": tables,
                "row": row,
                "observed": observed,
            }
        finally:
            await upgraded.close()

    data = asyncio.run(_run())
    assert data["version_before"] == 4 and data["version_after"] == CURRENT_VERSION
    assert "sender_id" not in data["columns_before"]
    for column in ("sender_id", "sender_name", "origin_umo"):
        assert column in data["columns_after"], f"v5 未补上 {column}"
    assert "identity_seen" in data["tables"]
    # 历史行原样可读，只是身份为空（不猜归属）
    assert data["row"]["content"] == "历史记忆"
    assert data["row"]["sender_id"] == "" and data["row"]["origin_umo"] == ""
    assert data["observed"][0]["sender_name"] == "谷雨"
