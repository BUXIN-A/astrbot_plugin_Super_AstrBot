"""检索测试：融合算法、加权、去重、端到端召回与缓冲隔离。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from super_astrbot.memory.models import MemoryItem
from super_astrbot.memory.retriever.base import Candidate, RouteOutcome
from super_astrbot.memory.retriever.fusion import (
    dedupe_by_similarity,
    normalize,
    recency_score,
    rrf_fuse,
)
from super_astrbot.spec.scopes import MemoryScope

from .helpers import build_stack


def test_rrf_favours_multi_route_hits() -> None:
    keyword = RouteOutcome(
        "keyword",
        [Candidate(1, 0, 1.0, "keyword"), Candidate(2, 1, 0.5, "keyword")],
    )
    vector = RouteOutcome(
        "vector",
        [Candidate(2, 0, 1.0, "vector"), Candidate(3, 1, 0.5, "vector")],
    )
    fused = rrf_fuse([keyword, vector], k=60)
    # 2 号被两条路同时命中，应高于只在单路出现的 1、3
    assert fused[2] > fused[1]
    assert fused[2] > fused[3]


def test_normalize_maps_max_to_one() -> None:
    assert normalize({1: 2.0, 2: 1.0}) == {1: 1.0, 2: 0.5}
    assert normalize({}) == {}
    assert normalize({1: 0.0}) == {1: 0.0}


def test_recency_decays_by_half_life() -> None:
    now = 2_000_000_000.0
    fresh = recency_score(now, now, now=now, half_life_days=14.0)
    created = now - 14 * 86400.0
    one_half_life = recency_score(created, created, now=now, half_life_days=14.0)
    two_half_lives = recency_score(
        now - 28 * 86400.0, now - 28 * 86400.0, now=now, half_life_days=14.0
    )
    assert abs(fresh - 1.0) < 1e-9
    assert abs(one_half_life - 0.5) < 1e-6
    assert abs(two_half_lives - 0.25) < 1e-6


def test_recency_prefers_latest_access_over_creation() -> None:
    now = 2_000_000_000.0
    old_created = now - 100 * 86400.0
    # 创建很久但最近被访问过 → 新近度应取「最近访问」为准
    recently_used = recency_score(old_created, now, now=now, half_life_days=14.0)
    never_used = recency_score(old_created, 0.0, now=now, half_life_days=14.0)
    assert recently_used > never_used


def test_dedupe_keeps_higher_scored() -> None:
    first = MemoryItem(id=1, content="用户喜欢在清晨跑步锻炼身体", score=0.9)
    duplicate = MemoryItem(id=2, content="用户喜欢在清晨跑步锻炼身体呀", score=0.8)
    other = MemoryItem(id=3, content="用户偏好美式咖啡不加糖", score=0.7)
    kept = dedupe_by_similarity([first, duplicate, other], threshold=0.6, max_items=5)
    assert [item.id for item in kept] == [1, 3]


def test_recall_end_to_end(tmp_path: Path) -> None:
    async def _run() -> object:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("aiocqhttp:group:1")
        await stack.memory.remember_text(
            scope, "用户上周项目上线遇到严重 bug，熬夜到凌晨三点，非常疲惫", importance=0.9
        )
        await stack.memory.remember_text(scope, "用户喜欢喝美式咖啡", importance=0.3)
        result = await stack.memory.recall(scope, "项目 上线 疲惫")
        await stack.close()
        return result

    result = asyncio.run(_run())
    assert result.items, "应至少召回一条记忆"
    assert "项目" in result.items[0].content
    assert result.items[0].score > 0
    assert "relevance" in result.items[0].score_breakdown
    assert "keyword" in result.route_summary


def test_buffered_episode_is_not_retrievable(tmp_path: Path) -> None:
    async def _run() -> object:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        await stack.memory.buffer_episode(scope, "用户：这是一句只应作为反思原料的话")
        result = await stack.memory.recall(scope, "反思原料")
        await stack.close()
        return result

    result = asyncio.run(_run())
    assert not result.items


def test_vector_route_degrades_without_embedding(tmp_path: Path) -> None:
    async def _run() -> tuple[list[str], bool]:
        stack = await build_stack(tmp_path, vector_available=False)
        routes = stack.memory.route_names
        flag = stack.embedding.available
        await stack.close()
        return routes, flag

    routes, available = asyncio.run(_run())
    assert routes == ["keyword"]
    assert available is False


def test_vector_route_enabled_when_embedding_available(tmp_path: Path) -> None:
    async def _run() -> tuple[list[str], bool, int]:
        stack = await build_stack(tmp_path, vector_available=True)
        scope = MemoryScope.for_session("s1")
        await stack.memory.remember_text(scope, "用户喜欢在周末爬山放松")
        result = await stack.memory.recall(scope, "周末爬山")
        calls = stack.embedding.calls
        routes = stack.memory.route_names
        await stack.close()
        return routes, bool(result.items), calls

    routes, hit, calls = asyncio.run(_run())
    assert routes == ["keyword", "vector"]
    assert hit is True
    assert calls >= 1


def test_global_scope_memories_are_visible_from_session(tmp_path: Path) -> None:
    async def _run() -> object:
        stack = await build_stack(tmp_path)
        global_scope = MemoryScope.global_scope()
        await stack.memory.remember_text(global_scope, "全局记忆：用户的名字是阿澈")
        session_scope = MemoryScope.for_session("s9")
        result = await stack.memory.recall(session_scope, "名字 阿澈")
        await stack.close()
        return result

    result = asyncio.run(_run())
    assert result.items
    assert "阿澈" in result.items[0].content
