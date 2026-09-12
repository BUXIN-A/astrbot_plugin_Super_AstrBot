"""知识图谱：零成本抽取、落地、检索扩展与维护。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from super_astrbot.graph import GraphConfig, GraphService
from super_astrbot.graph.extractor import canonicalize, deterministic_extract, parse_graph_json
from super_astrbot.memory.retriever.graph import GraphRetriever
from super_astrbot.spec.scopes import MemoryScope
from super_astrbot.storage import Database, GraphRepository, MemoryRepository

UMO = "aiocqhttp:GroupMessage:10086"
SCOPE = MemoryScope.for_session(UMO)
NOW = 1_700_000_000.0


def _config(**overrides: object) -> GraphConfig:
    base: dict[str, object] = {"enabled": True, "extractor": "deterministic"}
    base.update(overrides)
    return GraphConfig.from_mapping({"graph": base})


class FakeLlm:
    """返回固定 JSON 的模型替身。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def chat(self, **kwargs: object) -> object:
        from super_astrbot.harness.protocols import LlmResult

        self.calls += 1
        return LlmResult(text=self.text)


async def _insert_memory(memories: MemoryRepository, content: str) -> int:
    return await memories.insert(
        scope_type=SCOPE.scope_type.value,
        scope_id=SCOPE.scope_id,
        kind="fact",
        content=content,
        importance=0.6,
        confidence=0.8,
        source="manual",
        tags=[],
        created_at=NOW,
    )


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #


def test_canonicalize_strips_edges_and_lowercases_ascii() -> None:
    assert canonicalize("  Alice!!!  ") == "alice"
    assert canonicalize("《小明》") == "小明"
    assert canonicalize("   ") == ""
    assert len(canonicalize("x" * 100)) == 32


def test_deterministic_extract_pairs_neighbours() -> None:
    entities, relations = deterministic_extract(
        "alice bob carol", min_chars=2, max_chars=12, limit=8
    )
    assert entities == ["alice", "bob", "carol"]
    assert relations == [("alice", "共现", "bob"), ("bob", "共现", "carol")]


def test_parse_graph_json_accepts_fenced_output_and_filters_bad_rows() -> None:
    long_name = "x" * 40
    text = (
        "```json\n"
        '{"entities": [{"name": "小明", "type": "person"}, '
        f'{{"name": "{long_name}", "type": "person"}}, '
        '{"name": "怪东西", "type": "unknown"}],\n'
        ' "relations": [{"subject": "小明", "predicate": "喜欢", "object": "跑步"}, '
        '{"subject": "小明", "predicate": "", "object": "跑步"}]}\n'
        "```"
    )
    entities, relations = parse_graph_json(text, max_entities=10, max_relations=10)
    assert entities == [{"name": "小明", "type": "person"}, {"name": "怪东西", "type": "concept"}]
    assert relations == [{"subject": "小明", "predicate": "喜欢", "object": "跑步"}]


def test_parse_graph_json_returns_empty_on_broken_output() -> None:
    assert parse_graph_json("完全不是 JSON", max_entities=5, max_relations=5) == ([], [])


# --------------------------------------------------------------------------- #
# 服务
# --------------------------------------------------------------------------- #


def test_index_then_expand_recalls_linked_memory(tmp_path: Path) -> None:
    async def _run() -> None:
        db = Database(tmp_path / "graph.db")
        await db.connect()
        try:
            repo = GraphRepository(db)
            memories = MemoryRepository(db)
            service = GraphService(config=_config(), entities=repo)
            memory_id = await _insert_memory(memories, "alice bob carol")

            outcome = await service.index_memory(
                SCOPE, memory_id=memory_id, content="alice bob carol", now=NOW
            )
            assert outcome.indexed is True
            assert outcome.entities >= 3

            stats = await service.stats()
            assert stats["entities"] >= 3
            assert stats["relations"] >= 2

            expanded = await service.expand([SCOPE], "alice", limit=10)
            assert [item[0] for item in expanded] == [memory_id]

            snapshot = await service.snapshot(scope=SCOPE, limit_nodes=10, limit_edges=10)
            assert snapshot["nodes"]
            assert all(node["label"] for node in snapshot["nodes"])
        finally:
            await db.close()

    asyncio.run(_run())


def test_index_is_idempotent_for_same_memory(tmp_path: Path) -> None:
    async def _run() -> None:
        db = Database(tmp_path / "graph.db")
        await db.connect()
        try:
            repo = GraphRepository(db)
            memories = MemoryRepository(db)
            service = GraphService(config=_config(), entities=repo)
            memory_id = await _insert_memory(memories, "alice bob")

            await service.index_memory(SCOPE, memory_id=memory_id, content="alice bob", now=NOW)
            first = (await service.stats())["entities"]
            # 重复索引先解绑再重建，实体总量不应翻倍。
            await service.index_memory(SCOPE, memory_id=memory_id, content="alice bob", now=NOW)
            assert (await service.stats())["entities"] == first
        finally:
            await db.close()

    asyncio.run(_run())


def test_disabled_graph_does_nothing(tmp_path: Path) -> None:
    async def _run() -> None:
        db = Database(tmp_path / "graph.db")
        await db.connect()
        try:
            service = GraphService(config=_config(enabled=False), entities=GraphRepository(db))
            outcome = await service.index_memory(SCOPE, memory_id=1, content="alice bob", now=NOW)
            assert outcome.indexed is False
            assert await service.expand([SCOPE], "alice", limit=5) == []
        finally:
            await db.close()

    asyncio.run(_run())


def test_llm_extractor_falls_back_to_deterministic_on_failure(tmp_path: Path) -> None:
    async def _run() -> None:
        db = Database(tmp_path / "graph.db")
        await db.connect()
        try:
            repo = GraphRepository(db)
            memories = MemoryRepository(db)
            service = GraphService(
                config=_config(extractor="both"),
                entities=repo,
                llm=FakeLlm("并非 JSON"),
            )
            memory_id = await _insert_memory(memories, "alice bob")
            outcome = await service.index_memory(
                SCOPE, memory_id=memory_id, content="alice bob", now=NOW
            )
            # 模型产出不可解析 → 退回零成本结果，而不是写空或抛异常。
            assert outcome.indexed is True
            assert outcome.entities >= 2
        finally:
            await db.close()

    asyncio.run(_run())


def test_maintain_prunes_orphan_entities(tmp_path: Path) -> None:
    async def _run() -> None:
        db = Database(tmp_path / "graph.db")
        await db.connect()
        try:
            repo = GraphRepository(db)
            memories = MemoryRepository(db)
            service = GraphService(config=_config(), entities=repo)
            # 单实体、无关系：一旦记忆关联被解除，它就是纯孤立节点。
            memory_id = await _insert_memory(memories, "alice")
            await service.index_memory(SCOPE, memory_id=memory_id, content="alice", now=NOW)
            assert (await service.stats())["entities"] == 1

            await repo.clear_memories([memory_id])
            result = await service.maintain(now=NOW)
            assert result["pruned"]["entities_pruned"] >= 1
            assert (await service.stats())["entities"] == 0
        finally:
            await db.close()

    asyncio.run(_run())


# --------------------------------------------------------------------------- #
# 检索路
# --------------------------------------------------------------------------- #


def test_graph_retriever_maps_weights_to_candidates() -> None:
    class _Service:
        async def expand(
            self, scopes: object, query: str, *, limit: int
        ) -> list[tuple[int, float]]:
            return [(11, 1.0), (22, 0.5)]

    candidates = asyncio.run(
        GraphRetriever(_Service()).search([SCOPE], "alice", limit=10)  # type: ignore[arg-type]
    )
    assert [item.memory_id for item in candidates] == [11, 22]
    assert candidates[0].route == "graph"
    assert candidates[0].relevance == 1.0


def test_graph_retriever_degrades_on_error() -> None:
    class _Broken:
        async def expand(
            self, scopes: object, query: str, *, limit: int
        ) -> list[tuple[int, float]]:
            raise RuntimeError("boom")

    assert (
        asyncio.run(
            GraphRetriever(_Broken()).search([SCOPE], "alice", limit=10)  # type: ignore[arg-type]
        )
        == []
    )
