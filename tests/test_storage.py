"""持久层测试：迁移幂等、FTS 与降级、状态存储、写日志、事务回滚。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from super_astrbot.storage import (
    CURRENT_VERSION,
    Database,
    MemoryRepository,
    SqliteStateStore,
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
