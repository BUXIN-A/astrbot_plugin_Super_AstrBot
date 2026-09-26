"""身份归属、作用域迁移与备份导出的测试。

对应线上排查结论（``Super_AstrBot修复方案_2026-09-24``）：

1. 记忆必须记住「谁说的」——否则历史记忆无法归因，作用域也没法从会话切到用户；
2. 平台用户标识是否稳定要**可观测**，不能靠猜（本插件用身份观测表回答它）；
3. 历史作用域处置必须可预览、可回滚（自动备份），且不能谎报「已归属」；
4. 备份要能一次带走配置、数据库与各类数据（zip + manifest 校验）。
"""

from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path

from super_astrbot.app import SuperAstrBotApp
from super_astrbot.backup import BACKUP_TABLES, EXCLUDED_TABLES, decode_rows
from super_astrbot.harness.protocols import EventView
from super_astrbot.memory import (
    IDENTITY_AUTO,
    IDENTITY_SENDER_NAME,
    MemoryIdentity,
    describe_observation,
    normalize_strategy,
    resolve_identity,
)
from super_astrbot.spec.scopes import MemoryScope, ScopeType

from .test_app_integration import FakeContext, FakeStar

UMO = "webchat:FriendMessage:webchat!astrbot!<uuid-1>"
UMO2 = "webchat:FriendMessage:webchat!astrbot!<uuid-2>"


def _view(*, umo: str = UMO, sender_id: str = "u-1", sender_name: str = "谷雨") -> EventView:
    return EventView(
        umo=umo,
        sender_id=sender_id,
        sender_name=sender_name,
        text="今天有点累",
        is_admin=False,
        timestamp=1000.0,
    )


# --------------------------------------------------------------------------- #
# 身份解析：纯逻辑
# --------------------------------------------------------------------------- #


def test_resolve_identity_strategies() -> None:
    # 默认策略：只用平台 ID
    assert resolve_identity(sender_id="u1", sender_name="谷雨").user_key == "u1"
    # 只有昵称时默认策略给 unknown —— 这正是「按会话切碎」的成因，需要显式改策略
    assert resolve_identity(sender_id="", sender_name="谷雨").user_key == "unknown"
    # 昵称策略：ID 不稳定时的退路
    nick = resolve_identity(sender_id="", sender_name="谷雨", strategy=IDENTITY_SENDER_NAME)
    assert nick.user_key == "谷雨" and nick.source == IDENTITY_SENDER_NAME and nick.degraded
    # 自动策略：ID 优先，缺失回退昵称
    auto = resolve_identity(sender_id="u9", sender_name="谷雨", strategy=IDENTITY_AUTO)
    assert auto.user_key == "u9" and not auto.degraded
    auto2 = resolve_identity(sender_id="", sender_name="谷雨", strategy=IDENTITY_AUTO)
    assert auto2.user_key == "谷雨" and auto2.degraded
    # 两个来源都没有时不能退化成空键（否则所有人的记忆会挤进同一个作用域）
    assert resolve_identity(sender_id="", sender_name="", strategy=IDENTITY_AUTO).user_key == "unknown"


def test_normalize_strategy_accepts_aliases() -> None:
    assert normalize_strategy("昵称") == IDENTITY_SENDER_NAME
    assert normalize_strategy("AUTO") == IDENTITY_AUTO
    assert normalize_strategy("乱写") == "sender_id"
    assert normalize_strategy(None) == "sender_id"


def test_describe_observation_flags_unstable_platform_id() -> None:
    # 同一昵称、两个不同 ID、两个不同会话 → 平台 ID 不稳定
    unstable = describe_observation(
        [
            {"umo": UMO, "sender_id": "id-a", "sender_name": "谷雨"},
            {"umo": UMO2, "sender_id": "id-b", "sender_name": "谷雨"},
        ]
    )
    assert unstable["verdict"] == "unstable_id"
    assert "sender_name" in unstable["hint"] or "昵称" in unstable["hint"]

    # 同一 ID 出现在两个会话 → ID 稳定，可以放心用 user 作用域
    stable = describe_observation(
        [
            {"umo": UMO, "sender_id": "id-a", "sender_name": "谷雨"},
            {"umo": UMO2, "sender_id": "id-a", "sender_name": "谷雨"},
        ]
    )
    assert stable["verdict"] == "stable_id"

    assert describe_observation([])["verdict"] == "empty"


# --------------------------------------------------------------------------- #
# 记忆身份写入
# --------------------------------------------------------------------------- #


def test_memory_rows_carry_sender_identity(tmp_path: Path) -> None:
    async def _run() -> dict:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            view = _view()
            await app._memory_service.remember_text(
                MemoryScope.for_session(view.umo),
                "用户说今天有点累",
                identity=MemoryIdentity.from_view(view),
            )
            rows = await app.memory.list_all(offset=0, limit=10)
            item = rows[0]
            payload = {
                "sender_id": item.sender_id,
                "sender_name": item.sender_name,
                "origin_umo": item.origin_umo,
            }
            exported = await app.panel_memory_export()
            return {"item": payload, "exported": exported[0]}
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    assert data["item"]["sender_id"] == "u-1"
    assert data["item"]["sender_name"] == "谷雨"
    assert data["item"]["origin_umo"] == UMO
    # 导出也要带上身份，否则跨环境搬迁会丢掉归属
    assert data["exported"]["sender_id"] == "u-1"
    assert data["exported"]["sender_name"] == "谷雨"


def test_capture_records_identity_observation(tmp_path: Path) -> None:
    """身份观测必须真的写库，并且能判定「同昵称多 ID」。"""

    async def _run() -> dict:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            for umo, sender_id in ((UMO, "id-a"), (UMO2, "id-b")):
                view = _view(umo=umo, sender_id=sender_id)
                scope = app.memory_scope_for(view)
                await app._observe_identity(view, scope)
            report = await app.panel_identity_report()
            return report
        finally:
            await app.shutdown()

    report = asyncio.run(_run())
    assert report["total"] == 2
    assert report["analysis"]["verdict"] == "unstable_id"
    assert report["strategy"] == "sender_id"


def test_scope_for_uses_configured_identity(tmp_path: Path) -> None:
    """``default_scope=user`` + 昵称策略：没有 ID 也能跨会话落到同一个用户作用域。"""

    async def _run() -> dict:
        app = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config={"basic": {"default_scope": "user", "identity_strategy": "auto"}},
            data_dir=tmp_path,
        )
        await app.start()
        try:
            first = app.memory_scope_for(_view(umo=UMO, sender_id="", sender_name="谷雨"))
            second = app.memory_scope_for(_view(umo=UMO2, sender_id="", sender_name="谷雨"))
            return {"first": first.key, "second": second.key}
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    assert data["first"] == data["second"] == "user:谷雨"


def test_command_scope_matches_conversation_scope(tmp_path: Path) -> None:
    """指令与对话链路必须落在同一作用域，否则「记过却搜不到」。

    回归：``CommandService`` 曾自己用 ``sender_id`` 拼作用域，而对话链路按
    ``identity_strategy`` 解析；把策略切成 auto / 昵称后，两边会指向不同的用户键。
    """

    async def _run() -> tuple[str, str]:
        app = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config={"basic": {"default_scope": "user", "identity_strategy": "auto"}},
            data_dir=tmp_path,
        )
        await app.start()
        try:
            from super_astrbot.commands import CommandService

            view = _view(umo=UMO, sender_id="", sender_name="谷雨")
            service = CommandService(app=app, config={"basic": {"admin_only_commands": False}})
            return app.memory_scope_for(view).key, service._scope(view).key
        finally:
            await app.shutdown()

    conversation_scope, command_scope = asyncio.run(_run())
    assert conversation_scope == command_scope == "user:谷雨"


# --------------------------------------------------------------------------- #
# 作用域迁移
# --------------------------------------------------------------------------- #


def test_scope_migration_preview_then_apply(tmp_path: Path) -> None:
    async def _run() -> dict:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            memory = app.memory
            scopes = {
                "attributed": (_view(sender_id="u-1"), "带身份的记忆"),
                "orphan": (_view(sender_id="", sender_name=""), "历史无身份记忆"),
            }
            for view, text in scopes.values():
                scope = MemoryScope.for_session(view.umo)
                await app._memory_service.remember_text(
                    scope, text, identity=MemoryIdentity.from_view(view)
                )

            before = await app.panel_scope_report()
            preview = await app.panel_scope_migrate(
                {"to": "user_else_archive", "from_scope_type": "session", "dry_run": True}
            )
            applied = await app.panel_scope_migrate(
                {"to": "user_else_archive", "from_scope_type": "session", "dry_run": False}
            )
            after = await app.panel_scope_report()
            migrated = await app.memory.list_all(offset=0, limit=10, status="active")
            archived = await app.memory.list_all(offset=0, limit=10, status="archived")
            return {
                "before": before,
                "preview": preview,
                "applied": applied,
                "after": after,
                "migrated": migrated,
                "archived": archived,
            }
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    preview = data["preview"]
    assert preview["dry_run"] is True
    # 预览给出的是计划值：2 条符合条件，其中 1 条可归属、1 条将归档
    assert (preview["matched"], preview["attributed"]) == (2, 1)
    assert (preview["moved"], preview["archived"]) == (1, 1)
    assert data["before"]["types"].get("session", {}).get("active") == 2, "预览不得写库"

    applied = data["applied"]
    assert applied["ok"] is True and applied["dry_run"] is False
    assert applied["matched"] == 2
    assert applied["moved"] == 1 and applied["archived"] == 1
    assert applied["backup"], "落库前必须留下数据库备份"
    assert Path(applied["backup"]).exists()

    assert [item.scope_type for item in data["migrated"]] == ["user"]
    assert data["migrated"][0].scope_id == "u-1"
    assert [item.content for item in data["archived"]] == ["历史无身份记忆"]
    assert data["after"]["types"].get("session", {}).get("active", 0) == 0


def test_scope_migration_rejects_unknown_target(tmp_path: Path) -> None:
    async def _run() -> dict:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            return await app.panel_scope_migrate({"to": "somewhere", "dry_run": True})
        finally:
            await app.shutdown()

    result = asyncio.run(_run())
    assert result["ok"] is False and "不支持" in result["message"]


# --------------------------------------------------------------------------- #
# 批量审批
# --------------------------------------------------------------------------- #


def test_review_batch_handles_filtered_queue(tmp_path: Path) -> None:
    async def _run() -> dict:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            scope = MemoryScope(ScopeType.GLOBAL, "*")
            repo = app._reviews_repo
            for index in range(3):
                await repo.add(
                    scope_type=scope.scope_type.value,
                    scope_id=scope.scope_id,
                    origin="reflection",
                    payload={"summary": f"待审 {index}", "content": f"内容 {index}"},
                    created_at=1000.0 + index,
                )
            empty = await app.panel_review_batch({"action": "approve", "origin": "不存在"})
            rejected = await app.panel_review_batch({"action": "reject", "origin": "reflection"})
            remaining = await app.pending_reviews_all(limit=50)
            return {"empty": empty, "rejected": rejected, "remaining": remaining}
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    assert data["empty"]["ok"] is False, "筛选下没有待审时应明确报错"
    assert data["rejected"]["ok"] is True and data["rejected"]["handled"] == 3
    assert data["remaining"] == []


# --------------------------------------------------------------------------- #
# 备份导出
# --------------------------------------------------------------------------- #


def test_global_backup_zip_contains_every_table(tmp_path: Path) -> None:
    """全局备份：包内必须覆盖**每一张登记的业务表**，而不是挑几张。"""

    async def _run() -> dict:
        config = {"basic": {"default_scope": "user"}, "memory": {"retrieval_top_k": 7}}
        app = SuperAstrBotApp(
            star=FakeStar(), context=FakeContext(), config=config, data_dir=tmp_path
        )
        await app.start()
        try:
            view = _view()
            await app._memory_service.remember_text(
                MemoryScope.for_session(view.umo),
                "备份前的记忆",
                identity=MemoryIdentity.from_view(view),
            )
            await app.panel_journal_add({"content": "备份前的现实桥记录", "type": "diary"})
            result = await app.panel_backup_build({"notes": "单测备份"})
            listing = await app.panel_backup_list()
            return {"result": result, "listing": listing}
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    result = data["result"]
    archive_path = Path(result["path"])
    assert result["ok"] is True and archive_path.is_file()

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        config_payload = json.loads(archive.read("config/plugin_config.json").decode("utf-8"))
        memories = json.loads(archive.read("tables/memories.json").decode("utf-8"))
        journals = json.loads(archive.read("tables/journals.json").decode("utf-8"))
        db_bytes = archive.read("database/super_astrbot.db")
        readme = archive.read("README.txt").decode("utf-8")

    # 每一张登记表都在包里
    for table in BACKUP_TABLES:
        assert f"tables/{table}.json" in names, f"全局备份缺少表 {table}"
    # 人工可读视图仍保留（供查看与跨插件同步），但不参与恢复
    assert "views/memories.json" in names and manifest["restore"]["source"] == "tables/*.json"
    assert db_bytes[:15] == b"SQLite format 3", "数据库快照必须是可直接打开的 SQLite 文件"
    assert any("备份前的记忆" in item["content"] for item in memories)
    assert journals[0]["content"] == "备份前的现实桥记录"

    # manifest 自证：逐表行数 + 文件大小与 sha256 + 排除项写明原因
    assert manifest["kind"] == "super_astrbot.backup" and manifest["format"] == 2
    assert manifest["notes"] == "单测备份"
    assert manifest["tables"]["memories"] == len(memories) >= 1
    assert manifest["tables"]["journals"] == len(journals) == 1
    assert manifest["rows_total"] == sum(manifest["tables"].values())
    assert manifest["missing_tables"] == []
    for table, reason in manifest["excluded_tables"].items():
        assert reason, f"排除项 {table} 必须写明原因"
    assert "kv_state" in manifest["excluded_tables"]
    for name, meta in manifest["files"].items():
        assert name in names, f"manifest 声明的 {name} 不在包里"
        assert meta["bytes"] > 0 and len(meta["sha256"]) == 64

    # 面板列表把「备份范围」交出来，便于使用前核对
    assert set(data["listing"]["tables"]) == set(BACKUP_TABLES)
    assert data["listing"]["items"][0]["filename"] == result["filename"]
    assert "表" in readme and "manifest.json" in readme


def test_backup_respects_include_switches(tmp_path: Path) -> None:
    async def _run() -> dict:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            result = await app.panel_backup_build(
                {"include_database": False, "include_data": False, "include_config": True}
            )
            with zipfile.ZipFile(Path(result["path"])) as archive:
                return {"names": archive.namelist(), "manifest": json.loads(
                    archive.read("manifest.json").decode("utf-8")
                )}
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    assert "database/super_astrbot.db" not in data["names"]
    assert not any(name.startswith("tables/") for name in data["names"])
    assert "config/plugin_config.json" in data["names"]
    assert data["manifest"]["include"] == {"config": True, "database": False, "data": False}


def test_backup_prunes_old_archives(tmp_path: Path) -> None:
    """只保留最近若干份，避免备份把磁盘写满。"""

    async def _run() -> list[str]:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            app._backup_service._keep = 2  # noqa: SLF001 测试内直接调保留份数
            for _ in range(3):
                await app.panel_backup_build({})
                await asyncio.sleep(1.05)  # 文件名带秒级时间戳，错开一秒
            listing = await app.panel_backup_list()
            return [item["filename"] for item in listing["items"]]
        finally:
            await app.shutdown()

    names = asyncio.run(_run())
    assert len(names) == 2, names


class SchemaConfig(dict):
    """模拟 AstrBotConfig：带 ``schema`` 与 ``check_config_integrity``。

    配置导入是按 Schema 归一后合并的（``config_import``），因此要真正验证
    「备份包里的配置能被灌回去」，就必须提供一个带 Schema 的配置对象——
    纯 dict 会让导入侧判定「没有可识别的配置项」而拒绝（这是既有语义，不是缺陷）。
    """

    def __init__(self, data: dict, schema: dict) -> None:
        super().__init__(data)
        self.schema = schema

    def check_config_integrity(self, defaults: dict, incoming: dict) -> None:
        for key in list(incoming):
            if key not in defaults:
                incoming.pop(key)
        for key, value in defaults.items():
            incoming.setdefault(key, value)


def _schema_config(**overrides: object) -> SchemaConfig:
    import json

    schema = json.loads(
        (Path(__file__).resolve().parent.parent / "_conf_schema.json").read_text(encoding="utf-8")
    )
    data: dict = {}
    for key, value in overrides.items():
        data[key] = value
    return SchemaConfig(data, schema)


async def _seed_all_tables(app: SuperAstrBotApp) -> None:
    """往每张登记表里塞一行，用于验证「全局备份 = 一条都不丢」。"""
    db = app._db
    assert db is not None
    now = 1_700_000_000.0
    view = _view()
    scope = MemoryScope.for_session(view.umo)
    await app._memory_service.remember_text(
        scope, "全表备份的记忆", identity=MemoryIdentity.from_view(view)
    )
    await app.journal.add(scope, "全表备份的现实桥记录", entry_type="diary")

    memory_id = (await app.memory.list_all(offset=0, limit=1))[0].id
    await db.execute(
        "INSERT OR REPLACE INTO memory_vectors(memory_id, fingerprint, dim, vector, updated_at)"
        " VALUES (?,?,?,?,?)",
        (memory_id, "fp-test", 4, bytes([0, 1, 2, 3]), now),
    )
    await db.execute(
        "INSERT OR REPLACE INTO memory_links(src_id, dst_id, relation, weight, created_at)"
        " VALUES (?,?,?,?,?)",
        (memory_id, memory_id, "related", 0.5, now),
    )
    # 用 execute 拿自增 id（scalar 面向查询，INSERT 拿不到值）
    cursor = await db.execute(
        "INSERT INTO graph_entities(scope_type, scope_id, name, canonical_name, entity_type,"
        " weight, confidence, evidence, source, created_at, updated_at, status)"
        " VALUES ('session', ?, '谷雨', '谷雨', 'person', 1.0, 0.8, 1, 'test', ?, ?, 'active')",
        (view.umo, now, now),
    )
    entity_id = int(cursor.lastrowid)
    await db.execute(
        "INSERT OR REPLACE INTO memory_entities(memory_id, entity_id, weight, created_at)"
        " VALUES (?,?,?,?)",
        (memory_id, entity_id, 1.0, now),
    )
    await db.execute(
        "INSERT OR REPLACE INTO graph_relations(scope_type, scope_id, src_entity_id,"
        " dst_entity_id, relation, weight, confidence, evidence, source, created_at, updated_at,"
        " status) VALUES ('session', ?, ?, ?, 'knows', 0.7, 0.6, 1, 'test', ?, ?, 'active')",
        (view.umo, entity_id, entity_id, now, now),
    )
    await db.execute(
        "INSERT OR REPLACE INTO style_patterns(scope_type, scope_id, situation, expression,"
        " weight, hits, source, created_at, updated_at, status)"
        " VALUES ('session', ?, '被问好', '嗯，我在', 1.2, 3, 'test', ?, ?, 'active')",
        (view.umo, now, now),
    )
    await db.execute(
        "INSERT OR REPLACE INTO jargons(scope_type, scope_id, term, meaning, confidence,"
        " evidence, samples, created_at, updated_at, last_seen_at, status)"
        " VALUES ('session', ?, '咕', '打招呼', 0.9, 2, '[]', ?, ?, ?, 'active')",
        (view.umo, now, now, now),
    )
    await db.execute(
        "INSERT OR REPLACE INTO affinity_state(scope_type, scope_id, target_id, score, mood,"
        " interactions, last_interaction, updated_at)"
        " VALUES ('session', ?, 'u1', 0.72, 'warm', 9, ?, ?)",
        (view.umo, now, now),
    )
    await app._reviews_repo.add(
        scope_type="session",
        scope_id=view.umo,
        origin="reflection",
        payload={"summary": "待审的一条"},
        created_at=now,
    )
    await app._reflections_repo.start(scope_type="session", scope_id=view.umo, started_at=now)
    await app._identities_repo.observe(
        umo=view.umo, platform="webchat", sender_id="u-1", sender_name="谷雨", now=now
    )
    await db.execute(
        "INSERT OR REPLACE INTO metric_series(bucket_ts, metric, scope_type, scope_id, count,"
        " total, last_value) VALUES (?, 'llm.calls', 'session', ?, 2, 1.5, 0.8)",
        (int(now), view.umo),
    )


def _table_rows(counts: dict) -> dict:
    return {name: len(rows) for name, rows in counts.items() if isinstance(rows, list)}


def test_global_backup_restores_every_table(tmp_path: Path) -> None:
    """全局恢复：逐表回灌，字段与时间一字不差，且不动备份之后的新数据。"""

    async def _run() -> dict:
        # 源实例带一份有内容的配置，才能顺带验证「配置随包恢复」
        source = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config=_schema_config(basic={"default_scope": "user"}, memory={"retrieval_top_k": 6}),
            data_dir=tmp_path / "src",
        )
        await source.start()
        try:
            await _seed_all_tables(source)
            backup = await source.panel_backup_build({})
            archive = Path(backup["path"])
            with zipfile.ZipFile(archive) as zf:
                # 与恢复侧同一套解码：包里的 BLOB 是 {"$b64": ...} 包装
                before = {
                    name.split("/")[1][:-5]: decode_rows(json.loads(zf.read(name).decode("utf-8")))
                    for name in zf.namelist()
                    if name.startswith("tables/")
                }
            raw = archive.read_bytes()
        finally:
            await source.shutdown()

        # 目标实例：恢复一次 → 写入「备份之后才出现」的新数据 → 再恢复一次
        target = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config=_schema_config(),
            data_dir=tmp_path / "dst",
        )
        await target.start()
        try:
            first = await target.panel_backup_import(raw)
            await target._memory_service.remember_text(
                MemoryScope.for_session(UMO), "备份之后新增的记忆"
            )
            second = await target.panel_backup_import(raw)
            after = {table: await target._db.dump_table(table) for table in BACKUP_TABLES}
            contents = [item.content for item in await target.memory.list_all(offset=0, limit=20)]
            return {
                "first": first,
                "second": second,
                "before": before,
                "after": after,
                "contents": contents,
            }
        finally:
            await target.shutdown()

    data = asyncio.run(_run())
    first = data["first"]
    assert first["ok"] is True
    assert first["planned_rows"] == sum(_table_rows(data["before"]).values())
    assert Path(first["backup"]).is_file(), "写库前必须留下可回滚的数据库快照"
    assert "关键词索引重建" in first["reindex"]
    assert first["config"]["ok"] is True

    # 逐表核对：备份里的每一行都必须一字不差地出现在恢复后的库里
    before = data["before"]
    after = data["after"]
    for table in BACKUP_TABLES:
        if table != "memories":  # 这张表刻意在两次恢复之间新增过一行
            assert len(after[table]) == len(before[table]), f"{table} 行数不一致"
        for row in before[table]:
            assert row in after[table], f"{table} 的行未按原值恢复：{row}"
        if before[table]:
            assert set(after[table][0]) == set(before[table][0]), f"{table} 字段集不一致"
    assert len(after["memories"]) == len(before["memories"]) + 1

    # 抽查几类「以前会丢」的数据
    assert after["style_patterns"][0]["expression"] == "嗯，我在"
    assert after["affinity_state"][0]["score"] == 0.72
    assert after["graph_entities"][0]["canonical_name"] == "谷雨"
    assert after["pending_reviews"][0]["payload"] == before["pending_reviews"][0]["payload"]
    assert after["identity_seen"][0]["sender_name"] == "谷雨"
    assert after["memory_vectors"][0]["vector"] == before["memory_vectors"][0]["vector"]
    assert after["reflection_logs"][0]["status"] == "running"

    # 第二次恢复不能吃掉备份之后新增的数据（逐表合并、备份优先）
    assert "备份之后新增的记忆" in data["contents"]
    assert data["second"]["ok"] is True


def test_backup_import_roundtrip_into_fresh_instance(tmp_path: Path) -> None:
    """备份包导入：换一个干净实例，配置与数据都能灌回去。"""

    async def _run() -> dict:
        config = _schema_config(basic={"default_scope": "user"}, memory={"retrieval_top_k": 9})
        source = SuperAstrBotApp(
            star=FakeStar(), context=FakeContext(), config=config, data_dir=tmp_path / "src"
        )
        await source.start()
        try:
            view = _view()
            await source._memory_service.remember_text(
                MemoryScope.for_session(view.umo),
                "迁移前的记忆",
                identity=MemoryIdentity.from_view(view),
            )
            await source.panel_journal_add({"content": "迁移前的日记", "type": "diary"})
            backup = await source.panel_backup_build({})
            raw = Path(backup["path"]).read_bytes()
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
            result = await target.panel_backup_import(raw)
            memories = await target.memory.list_all(offset=0, limit=10)
            journals = await target.memory.list_all_journals(offset=0, limit=10)
            bad = await target.panel_backup_import(b"not a zip at all")
            restored = next((item for item in memories if item.content == "迁移前的记忆"), None)
            return {
                "result": result,
                "memories": [item.content for item in memories],
                "sender": restored.sender_name if restored else "",
                "origin_umo": restored.origin_umo if restored else "",
                "journals": [row["content"] for row in journals],
                "bad": bad,
            }
        finally:
            await target.shutdown()

    data = asyncio.run(_run())
    result = data["result"]
    assert result["ok"] is True
    assert result["manifest"]["kind"] == "super_astrbot.backup"
    assert result["config"]["ok"] is True, "配置应走既有导入管线热应用"
    # 逐表回灌不会重复：现实桥记录与它联动的记忆各自保留原始 id
    assert result["tables"]["memories"] == 2
    assert result["tables"]["journals"] == 1
    assert "迁移前的记忆" in data["memories"] and "迁移前的日记" in data["memories"]
    assert data["sender"] == "谷雨", "导入应保留发送者身份"
    assert data["origin_umo"].startswith("webchat:"), "导入应保留来源会话"
    # 数据库快照不覆盖运行中的库，而是另存待手动恢复
    assert Path(result["database"]["saved_to"]).is_file()
    assert "未覆盖运行中的库" in result["database"]["message"]
    # 不是 zip 的内容要被拒绝，且给出可读原因
    assert data["bad"]["ok"] is False and "zip" in data["bad"]["message"]



def test_backup_import_rejects_non_backup_zip(tmp_path: Path) -> None:
    """普通 zip（不含 manifest/data/db）应被识别为「不是备份包」。"""

    async def _run() -> dict:
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("hello.txt", "hi")
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            return await app.panel_backup_import(buffer.getvalue())
        finally:
            await app.shutdown()

    result = asyncio.run(_run())
    assert result["ok"] is False
    assert "super_astrbot" in result["message"]


def test_import_preserves_original_timestamps(tmp_path: Path) -> None:
    """备份导入必须回填原始时间线（回归：导入后创建时间变成导入时刻）。

    时间列不只是展示——衰减（``max(created_at, last_access_at)``）与「最近更新」排序
    都读它；盖上导入时间戳会让整批历史数据的时序与衰减判断一起失真。
    """
    old_moment = 1_700_000_000.0  # 2023-11-15 前后，明显早于当前
    old_access = old_moment + 3600.0

    async def _run() -> dict:
        source = SuperAstrBotApp(
            star=FakeStar(), context=FakeContext(), config=_schema_config(), data_dir=tmp_path / "src"
        )
        await source.start()
        try:
            await source._memory_service.remember_text(
                MemoryScope.for_session(UMO),
                "去年的旧记忆",
                created_at=old_moment,
                updated_at=old_moment,
                last_access_at=old_access,
                access_count=7,
            )
            await source.journal.add(
                MemoryScope.for_session(UMO),
                "去年的旧日记",
                entry_type="diary",
                event_time=old_moment,
                created_at=old_moment,
            )
            backup = await source.panel_backup_build({})
            raw = Path(backup["path"]).read_bytes()
        finally:
            await source.shutdown()

        target = SuperAstrBotApp(
            star=FakeStar(), context=FakeContext(), config=_schema_config(), data_dir=tmp_path / "dst"
        )
        await target.start()
        try:
            result = await target.panel_backup_import(raw)
            memories = await target.memory.list_all(offset=0, limit=10)
            journals = await target.memory.list_all_journals(offset=0, limit=10)
            plain = await target.panel_memory_import([{"content": "手写 JSON，没有时间字段"}])
            fresh = await target.memory.list_all(offset=0, limit=10, keyword="手写 JSON")
            return {
                "result": result,
                "memories": memories,
                "journals": journals,
                "plain": plain,
                "fresh": fresh[0] if fresh else None,
            }
        finally:
            await target.shutdown()

    data = asyncio.run(_run())
    assert data["result"]["ok"] is True

    restored = next(item for item in data["memories"] if item.content == "去年的旧记忆")
    assert restored.created_at == old_moment, "记忆创建时间应按原值回填"
    assert restored.updated_at == old_moment
    assert restored.last_access_at == old_access, "访问时间要保留（否则衰减判断失真）"
    assert restored.access_count == 7

    row = next(item for item in data["journals"] if item["content"] == "去年的旧日记")
    assert float(row["created_at"]) == old_moment, "现实桥记录的创建时间应按原值回填"
    assert float(row["event_time"]) == old_moment

    # 联动记忆（现实桥 ↔ 记忆）也共享同一条时间线
    linked = next(item for item in data["memories"] if item.content == "去年的旧日记")
    assert linked.created_at == old_moment

    # 手写 JSON 没有时间字段时，仍按写入时刻算（不写 1970 年）
    assert data["plain"]["imported"] == 1
    assert data["fresh"] is not None and data["fresh"].created_at > old_moment + 86400 * 300


def test_import_timestamps_survive_through_json_view(tmp_path: Path) -> None:
    """导出视图必须带上 ``created_at``，否则导入侧无从回填（上下游一起验）。"""

    async def _run() -> dict:
        app = SuperAstrBotApp(
            star=FakeStar(), context=FakeContext(), config=_schema_config(), data_dir=tmp_path
        )
        await app.start()
        try:
            await app._memory_service.remember_text(
                MemoryScope.for_session(UMO), "带时间的记忆", created_at=1_700_000_000.0
            )
            await app.journal.add(
                MemoryScope.for_session(UMO), "带时间的日记", created_at=1_700_000_000.0
            )
            memories = await app.panel_memory_export()
            journals = await app.panel_journal_export()
            return {"memories": memories, "journals": journals}
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    assert data["memories"][0]["created_at"] == 1_700_000_000.0
    assert data["journals"][0]["created_at"] == 1_700_000_000.0


def test_backup_tables_cover_every_schema_table(tmp_path: Path) -> None:
    """新增表必须同步登记：要么进 ``BACKUP_TABLES``，要么在 ``EXCLUDED_TABLES`` 写明原因。

    这是「备份别悄悄漏数据」的机制保障——上一次数据丢失的根源就是按类挑着导出，
    漏了哪张表没人知道。
    """

    async def _run() -> set[str]:
        from super_astrbot.storage import Database

        db = Database(tmp_path / "schema.db")
        await db.connect()
        try:
            return set(await db.table_names())
        finally:
            await db.close()

    actual = asyncio.run(_run())
    covered = set(BACKUP_TABLES)
    excluded = set(EXCLUDED_TABLES)
    assert not (covered & excluded), f"同一张表不能既备份又排除：{sorted(covered & excluded)}"
    uncovered = actual - covered - excluded
    assert not uncovered, (
        f"这些表既不在备份清单、也没登记为排除项，备份会漏数据：{sorted(uncovered)}"
    )
    missing = covered - actual
    assert not missing, f"备份清单里的表在库里不存在（迁移改名了？）：{sorted(missing)}"


def test_backup_binary_columns_round_trip() -> None:
    """BLOB 列（``memory_vectors.vector``）用 ``{"$b64": ...}`` 包装，能原样往返。"""
    from super_astrbot.backup import decode_rows, encode_rows

    rows = [
        {"memory_id": 1, "vector": bytes([0, 1, 2, 255]), "note": "普通字段不受影响"},
        {"memory_id": 2, "vector": None, "note": {"$b64": "看起来像但字段不合法"}},
    ]
    encoded = encode_rows(rows)
    assert encoded[0]["vector"] == {"$b64": "AAEC/w=="}
    assert encoded[0]["note"] == "普通字段不受影响"
    # 解不开的包装原样保留，避免误伤业务里恰好长这样的对象
    assert encoded[1]["note"] == {"$b64": "看起来像但字段不合法"}
    assert decode_rows(encoded) == [
        {"memory_id": 1, "vector": bytes([0, 1, 2, 255]), "note": "普通字段不受影响"},
        {"memory_id": 2, "vector": None, "note": {"$b64": "看起来像但字段不合法"}},
    ]


def test_import_warns_when_backup_schema_is_newer(tmp_path: Path) -> None:
    """备份来自更高 schema 版本时要显式告警——本版本读不懂的列会被丢弃，不能装作恢复成功。"""

    async def _run() -> dict:
        import io

        app = SuperAstrBotApp(
            star=FakeStar(), context=FakeContext(), config=_schema_config(), data_dir=tmp_path
        )
        await app.start()
        try:
            # 包内要有数据，否则会被「没有可恢复内容」提前拒绝
            await app._memory_service.remember_text(MemoryScope.for_session(UMO), "占位记忆")
            backup = await app.panel_backup_build({})
            raw = Path(backup["path"]).read_bytes()
            # 伪造一份「来自未来版本」的包：把 manifest 的 schema_version 调高
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                entries = {name: zf.read(name) for name in zf.namelist()}
            manifest = json.loads(entries["manifest.json"].decode("utf-8"))
            manifest["schema_version"] = 99
            entries["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for name, data in entries.items():
                    zf.writestr(name, data)
            result = await app.panel_backup_import(buffer.getvalue())
            return {"result": result}
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    result = data["result"]
    assert result["ok"] is True
    assert any("schema 版本" in warning for warning in result["warnings"]), result["warnings"]


def test_import_accepts_legacy_format_one_package(tmp_path: Path) -> None:
    """旧格式（format 1）包仍可导入：按 data/*.json 恢复，并说明其余数据要用库快照。

    用户手里可能已经存着上一版导出的包，格式升级不能让他们「旧备份打不开」。
    """

    async def _run() -> dict:
        import io

        app = SuperAstrBotApp(
            star=FakeStar(), context=FakeContext(), config=_schema_config(), data_dir=tmp_path
        )
        await app.start()
        try:
            # 造一个旧格式包：config + data/{memories,journals}.json + manifest(format=1)
            memories = [
                {
                    "id": 1,
                    "title": "",
                    "content": "旧包里的记忆",
                    "type": "fact",
                    "tags": ["旧包"],
                    "emotion": None,
                    "created_at": 1_700_000_000.0,
                    "event_time": 1_700_000_000.0,
                    "scope": "session:legacy-umo",
                    "scope_type": "session",
                    "scope_id": "legacy-umo",
                }
            ]
            journals = [
                {
                    "id": 1,
                    "title": "旧包日记",
                    "content": "旧包里的现实桥记录",
                    "type": "diary",
                    "tags": [],
                    "emotion": 3,
                    "created_at": 1_700_000_000.0,
                    "event_time": 1_700_000_000.0,
                    "scope": "session:legacy-umo",
                }
            ]
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("README.txt", "旧格式备份")
                zf.writestr(
                    "manifest.json",
                    json.dumps({"kind": "super_astrbot.backup", "format": 1}, ensure_ascii=False),
                )
                zf.writestr("config/plugin_config.json", json.dumps({"basic": {}}, ensure_ascii=False))
                zf.writestr("data/memories.json", json.dumps(memories, ensure_ascii=False))
                zf.writestr("data/journals.json", json.dumps(journals, ensure_ascii=False))

            result = await app.panel_backup_import(buffer.getvalue())
            found = await app.memory.list_all(offset=0, limit=10)
            journals_after = await app.memory.list_all_journals(offset=0, limit=10)
            return {"result": result, "memories": found, "journals": journals_after}
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    result = data["result"]
    assert result["ok"] is True
    assert result.get("legacy") is True
    assert "旧格式" in result["message"]
    assert result["tables"]["memories"]["imported"] == 1
    assert result["tables"]["journals"]["imported"] == 1
    contents = [item.content for item in data["memories"]]
    assert "旧包里的记忆" in contents
    # 旧格式走的是按类导入管线：现实桥记录会连带生成它对应的记忆（与当时行为一致）
    assert "旧包里的现实桥记录" in contents
    restored = next(item for item in data["memories"] if item.content == "旧包里的记忆")
    assert restored.created_at == 1_700_000_000.0, "旧包里的时间也要回填"
    assert data["journals"][0]["content"] == "旧包里的现实桥记录"
    assert "数据库快照" in result["message"], "必须说明其余数据要用库快照恢复"
