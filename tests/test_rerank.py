"""重排序（Rerank）：网关解析、回退策略、熔断与混合检索接入。

覆盖的核心命题是「重排序永远不能让召回变差或变慢到不可用」：
模型不可用/超时/返回空/候选不足时都必须落到确定的回退路径，
且绝不向外抛异常。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from super_astrbot.harness.astrbot_rerank import AstrBotRerankGateway, parse_hits
from super_astrbot.harness.protocols import RerankHit
from super_astrbot.memory import (
    MemoryItem,
    Reranker,
    RerankSettings,
    lexical_scores,
    normalize_scores,
)
from super_astrbot.memory.retriever import (
    Candidate,
    HybridRetriever,
    RetrievalConfig,
)
from super_astrbot.spec.scopes import MemoryScope
from super_astrbot.storage import Database, MemoryRepository

NOW = 1_700_000_000.0
SCOPE = MemoryScope.for_session("aiocqhttp:FriendMessage:10086")


# --------------------------------------------------------------------------- #
# 替身
# --------------------------------------------------------------------------- #


class FakeRerankGateway:
    """可控重排序网关替身（鸭子类型，与协议一致）。"""

    def __init__(
        self,
        *,
        available: bool = True,
        hits: object = None,
        error: Exception | None = None,
        delay: float = 0.0,
        model: str = "fake-rerank-1",
    ) -> None:
        self._available = available
        self._hits = hits
        self._error = error
        self._delay = delay
        self._model = model
        self.calls: list[tuple[str, list[str]]] = []
        self.refreshed = 0

    @property
    def available(self) -> bool:
        return self._available

    def model(self) -> str:
        return self._model

    def refresh(self) -> None:
        self.refreshed += 1

    def list_providers(self) -> list[object]:
        return []

    async def rerank(self, query: str, documents, *, top_n=None) -> list[RerankHit]:
        self.calls.append((query, list(documents)))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return list(self._hits or [])


class StaticRoute:
    """固定返回给定记忆 ID 顺序的检索路替身。"""

    name = "static"

    def __init__(self, ids: list[int]) -> None:
        self._ids = list(ids)

    async def search(self, scopes, query, *, limit):
        return [
            Candidate(memory_id=memory_id, rank=index, relevance=1.0, route=self.name)
            for index, memory_id in enumerate(self._ids)
        ]


def _item(memory_id: int, content: str) -> MemoryItem:
    return MemoryItem(
        id=memory_id,
        content=content,
        importance=0.5,
        created_at=NOW,
        last_access_at=NOW,
    )


def _settings(**overrides: object) -> RerankSettings:
    base = {"enabled": True, "candidates": 10, "min_candidates": 2, "weight": 1.0, "timeout": 1.0}
    base.update(overrides)
    return RerankSettings(**base)  # type: ignore[arg-type]


def _apply(reranker: Reranker, query: str, items: list[MemoryItem]):
    return asyncio.run(reranker.apply(query, items))


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #


def test_normalize_scores_min_max_and_degenerate() -> None:
    assert normalize_scores({}) == {}
    assert normalize_scores({1: 5.0}) == {1: 1.0}
    # 全等分数视为并列：统一给 1.0，保持调用方原有顺序
    assert normalize_scores({1: 3.0, 2: 3.0}) == {1: 1.0, 2: 1.0}
    assert normalize_scores({1: 0.0, 2: 10.0}) == {1: 0.0, 2: 1.0}
    # 负分同样可用（部分重排序接口返回 logits）
    assert normalize_scores({1: -2.0, 2: 2.0}) == {1: 0.0, 2: 1.0}


def test_lexical_scores_rank_by_query_coverage() -> None:
    items = [
        _item(1, "用户喜欢在周末爬山放松"),
        _item(2, "用户偏好清晨跑步"),
        _item(3, "今天天气不错"),
    ]
    scores = lexical_scores("周末爬山", items)
    assert scores[1] == 1.0
    assert scores[2] < scores[1]
    assert scores[3] < scores[1]


def test_lexical_scores_empty_query_returns_empty() -> None:
    assert lexical_scores("   ", [_item(1, "内容")]) == {}


# --------------------------------------------------------------------------- #
# Reranker：正路与各条回退路径
# --------------------------------------------------------------------------- #


def test_disabled_returns_off_without_calling() -> None:
    gateway = FakeRerankGateway(hits=[RerankHit(index=0, score=1.0)])
    reranker = Reranker(config=_settings(enabled=False), gateway=gateway)
    outcome = _apply(reranker, "天气", [_item(1, "天气")])
    assert outcome.source == "off"
    assert outcome.scores == {}
    assert gateway.calls == []


def test_provider_scores_are_normalized_and_sorted() -> None:
    gateway = FakeRerankGateway(hits=[RerankHit(index=0, score=1.0), RerankHit(index=1, score=5.0)])
    reranker = Reranker(config=_settings(), gateway=gateway)
    outcome = _apply(reranker, "天气", [_item(1, "无关"), _item(2, "相关")])
    assert outcome.source == "provider"
    assert outcome.scores[2] == 1.0
    assert outcome.scores[1] == 0.0
    assert gateway.calls and gateway.calls[0][0] == "天气"


def test_unrated_candidates_get_lowest_score() -> None:
    # 只回评部分候选时，未评分项不得靠着原顺序挤进前列。
    gateway = FakeRerankGateway(hits=[RerankHit(index=2, score=9.0)])
    reranker = Reranker(config=_settings(), gateway=gateway)
    outcome = _apply(reranker, "天气", [_item(1, "a"), _item(2, "b"), _item(3, "c")])
    assert outcome.source == "provider"
    assert outcome.scores[3] == 1.0
    assert outcome.scores[1] == outcome.scores[2] == 0.0


def test_fallback_to_lexical_when_provider_unavailable() -> None:
    gateway = FakeRerankGateway(available=False)
    reranker = Reranker(config=_settings(), gateway=gateway)
    items = [_item(1, "周末爬山"), _item(2, "随便聊聊")]
    outcome = _apply(reranker, "周末爬山", items)
    assert outcome.source == "lexical"
    assert "不可用" in outcome.note
    assert gateway.calls == [], "模型不可用时不应发起调用"


def test_fallback_when_call_raises_and_observer_reports_failure() -> None:
    gateway = FakeRerankGateway(error=RuntimeError("boom"))
    events: list[tuple] = []
    reranker = Reranker(
        config=_settings(),
        gateway=gateway,
        observer=lambda *args: events.append(args),
    )
    items = [_item(1, "周末爬山"), _item(2, "随便聊聊")]
    outcome = _apply(reranker, "周末爬山", items)
    assert outcome.source == "lexical"
    assert "调用失败" in outcome.note
    assert events and events[-1][1] is False


def test_fallback_on_timeout() -> None:
    gateway = FakeRerankGateway(hits=[RerankHit(index=0, score=1.0)], delay=0.5)
    reranker = Reranker(config=_settings(timeout=0.1), gateway=gateway)
    outcome = _apply(reranker, "天气", [_item(1, "天气"), _item(2, "别的")])
    assert outcome.source == "lexical"
    assert "超时" in outcome.note


def test_fallback_on_empty_result() -> None:
    gateway = FakeRerankGateway(hits=[])
    reranker = Reranker(config=_settings(), gateway=gateway)
    outcome = _apply(reranker, "天气", [_item(1, "天气"), _item(2, "别的")])
    assert outcome.source == "lexical"
    assert "空" in outcome.note


def test_skips_model_below_min_candidates() -> None:
    gateway = FakeRerankGateway(hits=[RerankHit(index=0, score=1.0)])
    reranker = Reranker(config=_settings(min_candidates=5), gateway=gateway)
    outcome = _apply(reranker, "天气", [_item(1, "天气"), _item(2, "别的")])
    assert gateway.calls == [], "候选不足时应省下这次模型调用"
    assert outcome.source == "lexical"
    assert "候选不足" in outcome.note


def test_fallback_mode_none_returns_off() -> None:
    gateway = FakeRerankGateway(available=False)
    reranker = Reranker(config=_settings(fallback="none"), gateway=gateway)
    outcome = _apply(reranker, "天气", [_item(1, "天气"), _item(2, "别的")])
    assert outcome.source == "off"
    assert outcome.scores == {}


def test_circuit_breaker_opens_then_recovers(tmp_path: Path) -> None:
    gateway = FakeRerankGateway(error=RuntimeError("boom"))
    clock = SimpleNamespace(now=1000.0)
    reranker = Reranker(
        config=_settings(),
        gateway=gateway,
        clock=lambda: clock.now,
    )
    items = [_item(1, "天气"), _item(2, "别的内容")]

    for _ in range(3):
        assert _apply(reranker, "天气", items).source == "lexical"
    calls_after_failures = len(gateway.calls)

    # 熔断期内不再调用模型，直接回退
    outcome = _apply(reranker, "天气", items)
    assert len(gateway.calls) == calls_after_failures
    assert "熔断" in outcome.note or "连续失败" in outcome.note

    # 冷却结束后恢复尝试
    clock.now += 10_000.0
    _apply(reranker, "天气", items)
    assert len(gateway.calls) > calls_after_failures


# --------------------------------------------------------------------------- #
# AstrBot 网关：解析与容错
# --------------------------------------------------------------------------- #


class _FakeProvider:
    def __init__(self, provider_id: str, hits: list[object], model: str = "bge-rerank") -> None:
        self._meta = SimpleNamespace(id=provider_id, type="rerank", model=model)
        self._hits = hits
        self.calls: list[tuple] = []

    def meta(self):
        return self._meta

    def get_model(self) -> str:
        return self._meta.model

    async def rerank(self, query, documents, top_n=None):
        self.calls.append((query, list(documents), top_n))
        return list(self._hits)


class _FakeContext:
    def __init__(self, providers: list[object], configs: list[dict] | None = None) -> None:
        self._providers = providers
        self.provider_manager = SimpleNamespace(providers_config=list(configs or []))
        self.lookups = 0

    def get_all_rerank_providers(self) -> list[object]:
        self.lookups += 1
        return list(self._providers)


class _FakeHost:
    def log(self):
        return SimpleNamespace(
            debug=lambda *a, **k: None,
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            error=lambda *a, **k: None,
        )


def test_parse_hits_accepts_objects_dicts_and_drops_bad_rows() -> None:
    class _Row:
        def __init__(self, index: int, score: float) -> None:
            self.index = index
            self.relevance_score = score

    parsed = parse_hits(
        [
            _Row(1, 0.2),
            {"index": 0, "relevance_score": 0.9},
            {"index": 5, "score": 0.1},  # 越界
            {"index": 2, "relevance_score": "bad"},  # 分数非法
            "garbage",
        ],
        3,
    )
    assert [(hit.index, hit.score) for hit in parsed] == [(0, 0.9), (1, 0.2)]


def test_parse_hits_tolerates_non_list() -> None:
    assert parse_hits(None, 3) == []
    assert parse_hits("nope", 3) == []


def test_gateway_resolves_preferred_provider_and_truncates() -> None:
    hits = [SimpleNamespace(index=0, relevance_score=0.3)]
    first = _FakeProvider("rerank-a", [])
    second = _FakeProvider("rerank-b", hits)
    context = _FakeContext([first, second])
    gateway = AstrBotRerankGateway(context, _FakeHost(), provider_id="rerank-b")

    assert gateway.available is True
    assert gateway.model() == "bge-rerank"
    result = asyncio.run(gateway.rerank("查询" * 400, ["文档"]))
    assert [hit.index for hit in result] == [0]
    # 查询会被截断，避免超出重排序接口的长度上限
    assert len(second.calls[0][0]) <= 512
    assert first.calls == []


def test_gateway_failure_invalidates_cache() -> None:
    class _BoomProvider(_FakeProvider):
        async def rerank(self, query, documents, top_n=None):
            raise RuntimeError("connection closed")

    provider = _BoomProvider("rerank-a", [])
    context = _FakeContext([provider])
    gateway = AstrBotRerankGateway(context, _FakeHost())
    assert asyncio.run(gateway.rerank("q", ["d"])) == []

    # 失败后缓存被清空：下一次调用会重新解析（Provider 实例可能已被框架重建）
    before = context.lookups
    assert asyncio.run(gateway.rerank("q", ["d"])) == []
    assert context.lookups > before


def test_gateway_lists_configured_but_unloaded_providers() -> None:
    configs = [
        {"id": "rerank-x", "provider_type": "rerank", "model": "m1"},
        {"id": "embed-y", "provider_type": "embedding"},
    ]
    gateway = AstrBotRerankGateway(_FakeContext([], configs), _FakeHost())
    ids = [info.id for info in gateway.list_providers()]
    assert ids == ["rerank-x"]


def test_gateway_without_framework_support_is_unavailable() -> None:
    gateway = AstrBotRerankGateway(SimpleNamespace(), _FakeHost())
    assert gateway.available is False
    assert asyncio.run(gateway.rerank("q", ["d"])) == []


# --------------------------------------------------------------------------- #
# 混合检索接入
# --------------------------------------------------------------------------- #


def test_hybrid_rerank_reorders_and_exposes_breakdown(tmp_path: Path) -> None:
    async def _run() -> tuple[list[int], int, str, dict, str]:
        db = Database(tmp_path / "rerank.db")
        await db.connect()
        try:
            memories = MemoryRepository(db)
            first = await memories.insert(
                scope_type=SCOPE.scope_type.value,
                scope_id=SCOPE.scope_id,
                kind="fact",
                content="用户喜欢在周末爬山放松",
                importance=0.5,
                confidence=0.8,
                source="manual",
                tags=[],
                created_at=NOW,
            )
            second = await memories.insert(
                scope_type=SCOPE.scope_type.value,
                scope_id=SCOPE.scope_id,
                kind="fact",
                content="用户偏好清晨跑步",
                importance=0.5,
                confidence=0.8,
                source="manual",
                tags=[],
                created_at=NOW,
            )
            third = await memories.insert(
                scope_type=SCOPE.scope_type.value,
                scope_id=SCOPE.scope_id,
                kind="fact",
                content="用户最近在准备爬山装备",
                importance=0.5,
                confidence=0.8,
                source="manual",
                tags=[],
                created_at=NOW,
            )

            # 关键词顺序：first > second > third；重排序把 third 提到第一。
            gateway = FakeRerankGateway(
                hits=[
                    RerankHit(index=2, score=0.9),
                    RerankHit(index=0, score=0.4),
                    RerankHit(index=1, score=0.1),
                ]
            )
            config = RetrievalConfig(
                top_k=3,
                min_score=0.0,
                dedup_similarity=1.0,
                rerank=_settings(weight=1.0),
            )
            retriever = HybridRetriever(
                routes=[StaticRoute([first, second, third])],
                memories=memories,
                config=config,
                reranker=Reranker(config=config.rerank, gateway=gateway),
            )
            result = await retriever.search([SCOPE], "爬山", limit=3)
            return (
                [item.id for item in result.items],
                third,
                result.rerank_source,
                result.items[0].score_breakdown,
                result.rerank_summary,
            )
        finally:
            await db.close()

    ids, expected_first, source, breakdown, summary = asyncio.run(_run())
    assert source == "provider"
    assert ids[0] == expected_first, "重排序后应把模型判为最相关的记忆排到第一"
    assert breakdown["rerank"] == 1.0
    assert breakdown["rerank_source"] == "provider"
    assert "模型" in summary


def test_hybrid_rerank_falls_back_without_provider(tmp_path: Path) -> None:
    async def _run() -> tuple[str, str]:
        db = Database(tmp_path / "rerank-fallback.db")
        await db.connect()
        try:
            memories = MemoryRepository(db)
            memory_id = await memories.insert(
                scope_type=SCOPE.scope_type.value,
                scope_id=SCOPE.scope_id,
                kind="fact",
                content="用户喜欢在周末爬山放松",
                importance=0.5,
                confidence=0.8,
                source="manual",
                tags=[],
                created_at=NOW,
            )
            config = RetrievalConfig(
                top_k=3,
                min_score=0.0,
                dedup_similarity=1.0,
                rerank=_settings(),
            )
            retriever = HybridRetriever(
                routes=[StaticRoute([memory_id])],
                memories=memories,
                config=config,
                reranker=Reranker(config=config.rerank, gateway=FakeRerankGateway(available=False)),
            )
            result = await retriever.search([SCOPE], "爬山", limit=3)
            return result.rerank_source, result.rerank_summary
        finally:
            await db.close()

    source, summary = asyncio.run(_run())
    assert source in {"off", "lexical"}
    assert "重排序" in summary


def test_hybrid_rerank_disabled_is_silent(tmp_path: Path) -> None:
    async def _run() -> tuple[str, str, str]:
        db = Database(tmp_path / "rerank-off.db")
        await db.connect()
        try:
            memories = MemoryRepository(db)
            memory_id = await memories.insert(
                scope_type=SCOPE.scope_type.value,
                scope_id=SCOPE.scope_id,
                kind="fact",
                content="用户喜欢在周末爬山放松",
                importance=0.5,
                confidence=0.8,
                source="manual",
                tags=[],
                created_at=NOW,
            )
            config = RetrievalConfig(top_k=3, min_score=0.0, dedup_similarity=1.0)
            retriever = HybridRetriever(
                routes=[StaticRoute([memory_id])],
                memories=memories,
                config=config,
            )
            result = await retriever.search([SCOPE], "爬山", limit=3)
            return (
                result.rerank_source,
                result.rerank_summary,
                result.items[0].score_breakdown["rerank_source"],
            )
        finally:
            await db.close()

    source, summary, breakdown_source = asyncio.run(_run())
    assert source == "off"
    assert summary == "", "未启用重排序时不应在输出里刷屏"
    assert breakdown_source == "off"
