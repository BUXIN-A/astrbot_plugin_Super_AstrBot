"""恢复模式（merge / replace）与整库恢复流程的测试。

对应两条需求：

1. **format 2 的完全覆盖**：恢复后业务表与备份逐表一致，导入前多出来的行必须消失；
   多表写入整体原子——任何一张表失败都不能留下「清了没灌上」或半套覆盖的中间状态；
   且覆盖前必须先落盘数据库快照（回滚凭据）。
2. **format 1 的整库恢复引导**：包内有数据库快照时给出一键整库恢复入口，失败自动回滚；
   没有快照时明确告知「无法恢复」，绝不给出误导性的「还能补导」文案。
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path

from super_astrbot.app import SuperAstrBotApp
from super_astrbot.backup import BACKUP_TABLES
from super_astrbot.spec.scopes import MemoryScope

from .test_app_integration import FakeContext, FakeStar
from .test_identity_backup import UMO, _schema_config, _seed_all_tables


async def _backup_and_bytes(app: SuperAstrBotApp, *, notes: str = "") -> tuple[Path, bytes]:
    artifact = await app.panel_backup_build({"notes": notes})
    path = Path(artifact["path"])
    return path, path.read_bytes()


def _rewrite_zip(raw: bytes, mutate) -> bytes:
    """按包内条目名改写备份包（用于注入坏数据 / 伪造旧格式）。"""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    mutate(entries)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# 完全覆盖（replace）
# --------------------------------------------------------------------------- #


def test_replace_mode_deletes_rows_absent_from_backup(tmp_path: Path) -> None:
    """replace：备份里没有的行必须消失，14 张表逐表与备份一致，且快照可用于回滚。"""

    async def _run() -> dict:
        source = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config=_schema_config(basic={"default_scope": "user"}),
            data_dir=tmp_path / "src",
        )
        await source.start()
        try:
            await _seed_all_tables(source)
            path, raw = await _backup_and_bytes(source)
            with zipfile.ZipFile(path) as archive:
                expected = {
                    name.split("/")[1][:-5]: json.loads(archive.read(name).decode("utf-8"))
                    for name in archive.namelist()
                    if name.startswith("tables/")
                }
        finally:
            await source.shutdown()

        target = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config=_schema_config(),
            data_dir=tmp_path / "dst",
        )
        await target.start()
        try:
            await target.panel_backup_import(raw, mode="merge")
            # 恢复之后再写入「备份里没有」的数据：replace 必须把它们抹掉
            await target._memory_service.remember_text(
                MemoryScope.for_session(UMO), "备份之后新增、覆盖时必须消失的记忆"
            )
            await target._db.execute(
                "INSERT OR REPLACE INTO identity_seen(umo, platform, sender_id, sender_name,"
                " scope_type, scope_id, user_key, first_seen, last_seen, events)"
                " VALUES ('extra-umo','webchat','x','新增观测','session','extra-umo','x',1,1,1)"
            )
            before_counts = {table: await target._db.count_rows(table) for table in BACKUP_TABLES}

            result = await target.panel_backup_import(raw, mode="replace")
            after = {table: await target._db.dump_table(table) for table in BACKUP_TABLES}
            counts = {table: len(rows) for table, rows in after.items()}
            contents = [item.content for item in await target.memory.list_all(offset=0, limit=20)]
            umos = {row["umo"] for row in after["identity_seen"]}
            # 覆盖后关键词检索：应能命中恢复出来的正文，且不残留旧索引
            hits = await target.memory.recall(MemoryScope.for_session(UMO), "全表备份的记忆")
            return {
                "result": result,
                "expected": expected,
                "after": after,
                "counts": counts,
                "before_counts": before_counts,
                "contents": contents,
                "umos": umos,
                "hit_contents": [item.content for item in hits.items],
            }
        finally:
            await target.shutdown()

    data = asyncio.run(_run())
    result = data["result"]
    assert result["ok"] is True and result["mode"] == "replace"
    assert "完全覆盖" in result["message"]

    # (b) 14 张表逐表行数与备份一致
    for table in BACKUP_TABLES:
        assert data["counts"][table] == len(data["expected"][table]), (
            f"{table} 行数与备份不一致：{data['counts'][table]} vs {len(data['expected'][table])}"
        )
    # (a) 备份中不存在的行已删除
    assert "备份之后新增、覆盖时必须消失的记忆" not in data["contents"]
    assert "extra-umo" not in data["umos"]
    assert data["before_counts"]["memories"] == data["counts"]["memories"] + 1
    # (c) 关键词索引重建后检索正常（命中恢复出来的正文）
    assert any("全表备份的记忆" in text for text in data["hit_contents"]), data["hit_contents"]
    # (d) 快照先于任何 DELETE 落盘
    assert Path(result["backup"]).is_file()


def test_replace_mode_reports_deletion_estimate_in_preview(tmp_path: Path) -> None:
    """预览（dry_run）不写库，但要给出「将写入 / 预计删除」用于二次确认。"""

    async def _run() -> dict:
        source = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config=_schema_config(),
            data_dir=tmp_path / "src",
        )
        await source.start()
        try:
            await _seed_all_tables(source)
            _, raw = await _backup_and_bytes(source)
        finally:
            await source.shutdown()

        target = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config=_schema_config(),
            data_dir=tmp_path / "dst",
        )
        await target.start()
        try:
            before = {table: await target._db.count_rows(table) for table in BACKUP_TABLES}
            preview = await target.panel_backup_import(raw, mode="replace", dry_run=True)
            after = {table: await target._db.count_rows(table) for table in BACKUP_TABLES}
            return {"preview": preview, "before": before, "after": after}
        finally:
            await target.shutdown()

    data = asyncio.run(_run())
    preview = data["preview"]
    assert preview["ok"] is True and preview["mode"] == "replace"
    assert preview["rows_written"] > 0
    assert set(preview["existing"]) == set(BACKUP_TABLES)
    assert preview["existing"]["memories"] == 0, "目标库本来是空的"
    assert preview["estimated_deleted_total"] == 0
    assert "预览" in preview["message"]
    # 预览不写库：库保持原样
    assert data["after"] == data["before"]


def test_replace_mode_rolls_back_all_tables_on_failure(tmp_path: Path) -> None:
    """原子性：某张表写入失败时，本次已处理的表整体回滚，不留半清半灌。"""

    async def _run() -> dict:
        source = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config=_schema_config(),
            data_dir=tmp_path / "src",
        )
        await source.start()
        try:
            await _seed_all_tables(source)
            _, raw = await _backup_and_bytes(source)
        finally:
            await source.shutdown()

        # 破坏第 3 张表（memory_links）里的一行：created_at 是 NOT NULL
        def corrupt(entries: dict[str, bytes]) -> None:
            rows = json.loads(entries["tables/memory_links.json"].decode("utf-8"))
            assert rows, "需要至少一行才能构造约束冲突"
            rows[0]["created_at"] = None
            entries["tables/memory_links.json"] = json.dumps(rows, ensure_ascii=False).encode("utf-8")

        broken = _rewrite_zip(raw, corrupt)

        target = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config=_schema_config(),
            data_dir=tmp_path / "dst",
        )
        await target.start()
        try:
            # 先合并恢复一次让 id 与备份对齐，再写入「备份之后新增」的行：
            # 新行会拿到更大的 id，不会与备份里的同主键行相撞（否则合并会按备份优先覆盖它）
            await target.panel_backup_import(raw, mode="merge")
            await target._memory_service.remember_text(
                MemoryScope.for_session(UMO), "恢复前就存在的记忆"
            )
            before = {table: await target._db.dump_table(table) for table in BACKUP_TABLES}

            result = await target.panel_backup_import(broken, mode="replace")
            after = {table: await target._db.dump_table(table) for table in BACKUP_TABLES}
            contents = [item.content for item in await target.memory.list_all(offset=0, limit=20)]
            return {"result": result, "before": before, "after": after, "contents": contents}
        finally:
            await target.shutdown()

    data = asyncio.run(_run())
    result = data["result"]
    assert result["ok"] is False
    assert "回滚" in result["message"]
    assert result["backup"], "失败时必须把快照路径告诉用户，便于整库回退"
    assert "快照" in result["hint"]
    # 所有表都保持恢复前状态：没有出现「一半覆盖、一半没动」
    for table in BACKUP_TABLES:
        assert data["after"][table] == data["before"][table], f"{table} 未回滚到恢复前状态"
    assert "恢复前就存在的记忆" in data["contents"]


def test_merge_mode_keeps_rows_created_after_backup(tmp_path: Path) -> None:
    """merge 回归：备份之外新增的行在恢复后仍然存在（默认模式行为不变）。"""

    async def _run() -> dict:
        source = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config=_schema_config(),
            data_dir=tmp_path / "src",
        )
        await source.start()
        try:
            await _seed_all_tables(source)
            _, raw = await _backup_and_bytes(source)
        finally:
            await source.shutdown()

        target = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config=_schema_config(),
            data_dir=tmp_path / "dst",
        )
        await target.start()
        try:
            first = await target.panel_backup_import(raw)  # 不传 mode → 默认 merge
            await target._memory_service.remember_text(
                MemoryScope.for_session(UMO), "备份之后新增、合并恢复必须保留的记忆"
            )
            second = await target.panel_backup_import(raw, mode="merge")
            contents = [item.content for item in await target.memory.list_all(offset=0, limit=20)]
            return {"first": first, "second": second, "contents": contents}
        finally:
            await target.shutdown()

    data = asyncio.run(_run())
    assert data["first"]["mode"] == "merge", "默认必须是 merge，行为与既有版本一致"
    assert data["second"]["mode"] == "merge"
    assert "合并" in data["second"]["message"]
    assert "备份之后新增、合并恢复必须保留的记忆" in data["contents"]


# --------------------------------------------------------------------------- #
# 旧格式（format 1）包：整库恢复引导
# --------------------------------------------------------------------------- #


def _legacy_package(*, with_snapshot: bool, snapshot: bytes = b"") -> bytes:
    """构造 format 1 包：只有 data/ 视图（+ 可选的数据库快照）。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", "旧格式备份")
        archive.writestr(
            "manifest.json",
            json.dumps({"kind": "super_astrbot.backup", "format": 1}, ensure_ascii=False),
        )
        archive.writestr("data/memories.json", json.dumps([], ensure_ascii=False))
        if with_snapshot:
            archive.writestr("database/super_astrbot.db", snapshot)
    return buffer.getvalue()


def test_legacy_package_with_snapshot_offers_full_database_restore(tmp_path: Path) -> None:
    """含快照的 v1 包：导入后可一键整库恢复，且恢复失败能回滚原库。"""

    async def _run() -> dict:
        # 造一个「备份时刻」的库：内容与当前库不同，替换后能看出差别
        old = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config=_schema_config(),
            data_dir=tmp_path / "old",
        )
        await old.start()
        try:
            await old._memory_service.remember_text(
                MemoryScope.for_session(UMO), "备份时刻的记忆"
            )
            backup_path, _ = await _backup_and_bytes(old)
            with zipfile.ZipFile(backup_path) as archive:
                snapshot = archive.read("database/super_astrbot.db")
        finally:
            await old.shutdown()

        app = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config=_schema_config(),
            data_dir=tmp_path / "live",
        )
        await app.start()
        try:
            await app._memory_service.remember_text(
                MemoryScope.for_session(UMO), "当前库里的新记忆"
            )
            legacy = _legacy_package(with_snapshot=True, snapshot=snapshot)
            imported = await app.panel_backup_import(legacy, mode="replace")
            can_replace = imported.get("can_replace_database")
            snapshot_path = imported["database"]["saved_to"]

            # 整库恢复：失败路径（坏快照）应回滚
            bad = Path(snapshot_path).with_name("restored-broken.db")
            bad.write_bytes(b"not a sqlite database")
            failed = await app.panel_backup_replace_database(str(bad))
            after_failure = [item.content for item in await app.memory.list_all(offset=0, limit=20)]

            # 成功路径：用包内快照整库替换
            succeeded = await app.panel_backup_replace_database(snapshot_path)
            after_success = [item.content for item in await app.memory.list_all(offset=0, limit=20)]
            return {
                "imported": imported,
                "can_replace": can_replace,
                "snapshot_path": snapshot_path,
                "failed": failed,
                "succeeded": succeeded,
                "after_failure": after_failure,
                "after_success": after_success,
            }
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    assert data["imported"]["legacy"] is True
    assert data["can_replace"] is True
    assert Path(data["snapshot_path"]).is_file(), "包内快照应已落盘待用"
    assert "整库恢复" in data["imported"]["message"]

    # 坏快照：拒绝并回滚，当前库内容不变
    assert data["failed"]["ok"] is False and "回滚" in data["failed"]["message"]
    assert "当前库里的新记忆" in data["after_failure"]

    # 好快照：整库替换成功，回到备份时刻的内容
    assert data["succeeded"]["ok"] is True
    assert Path(data["succeeded"]["backup"]).is_file(), "原库必须先另存为 pre-restore 备份"
    assert "备份时刻的记忆" in data["after_success"]
    assert "当前库里的新记忆" not in data["after_success"]


def test_legacy_package_without_snapshot_says_unrecoverable(tmp_path: Path) -> None:
    """无快照的 v1 包：明确告知这些数据无法从此备份恢复，不给误导性文案。"""

    async def _run() -> dict:
        app = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config=_schema_config(),
            data_dir=tmp_path,
        )
        await app.start()
        try:
            result = await app.panel_backup_import(_legacy_package(with_snapshot=False))
            return {
                "result": result,
                "replace": await app.panel_backup_replace_database(""),
            }
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    result = data["result"]
    assert result["ok"] is True
    assert result["can_replace_database"] is False
    message = result["message"]
    assert "无数据库快照" in message
    assert "无法从此备份恢复" in message
    # 不得出现任何「还能补导」的暗示
    for hint in ("请用「整库恢复」", "补导", "可以恢复这些"):
        assert hint not in message
    assert data["replace"]["ok"] is False, "没有快照时不允许发起整库恢复"
