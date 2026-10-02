"""写前查重（去重门）测试 —— memory_beyond 去重方案验证用例的移植版。

覆盖三层去重：

- 内容指纹的跨轮次稳定性（精确去重依据）；
- 相似判定的精度边界（近义合并 vs 独立事实）；
- 生命周期写入门的端到端行为：精确重复复用、近义合并更新、
  独立事实照常入库、周记/缓冲不参与合并。
"""

from __future__ import annotations

import asyncio

from super_astrbot.memory.dedup import content_fingerprint, similar_enough
from super_astrbot.memory.models import KIND_FACT, KIND_INSIGHT, KIND_JOURNAL
from super_astrbot.spec.scopes import MemoryScope

from .helpers import build_stack

SCOPE = MemoryScope.for_session("sess-dedup")


# --------------------------------------------------------------------------- #
# 指纹与相似判定（总纲 §4.2 的判定用例）
# --------------------------------------------------------------------------- #


def test_content_fingerprint_is_stable_across_naming() -> None:
    """同一事实换个说法取名，指纹不应变；内容或类型变了才变。"""
    assert content_fingerprint("用户本地文献库搭建尚未完成", "project") == content_fingerprint(
        "用户本地文献库搭建尚未完成", "project"
    )
    assert content_fingerprint("用户喜欢苹果", "fact") != content_fingerprint(
        "用户喜欢橘子", "fact"
    )
    assert content_fingerprint("同一句话", "fact") != content_fingerprint("同一句话", "insight")


def test_similar_enough_accepts_prefix_overlap() -> None:
    """前缀重叠的近义改写应判为同一事实（2-gram 的设计目标）。"""
    assert similar_enough(
        "用户本地文献库搭建尚未完成", "用户本地文献库搭建（未完成），仍在建设中"
    )


def test_similar_enough_rejects_distinct_facts() -> None:
    """精度边界：0.4 会误并的用例在 0.5 阈值下必须保持区分。"""
    assert not similar_enough("用户喜欢苹果", "用户喜欢橘子")
    assert not similar_enough("项目A已完成", "项目B已完成")
    assert not similar_enough("用户是医生", "用户是工程师")


# --------------------------------------------------------------------------- #
# 写入门端到端
# --------------------------------------------------------------------------- #


def test_exact_duplicate_reuses_existing_row(tmp_path) -> None:
    async def _run() -> None:
        stack = await build_stack(tmp_path)
        try:
            first = await stack.memory.remember_text(SCOPE, "用户喜欢在周末爬山放松")
            second = await stack.memory.remember_text(SCOPE, "用户喜欢在周末爬山放松")
            assert first == second
            assert await stack.memories.count([SCOPE], status="active") == 1
        finally:
            await stack.close()

    asyncio.run(_run())


def test_similar_draft_merges_into_existing_row(tmp_path) -> None:
    """近义重复：不新建行，就地把已有记忆的正文更新为新抽取结果。"""

    async def _run() -> None:
        stack = await build_stack(tmp_path)
        try:
            existing_id = await stack.memory.remember_text(SCOPE, "用户本地文献库搭建尚未完成")
            merged_id = await stack.memory.remember_text(
                SCOPE, "用户本地文献库搭建（未完成），仍在建设中"
            )
            assert merged_id == existing_id
            rows = await stack.memories.list_by_status([SCOPE], status="active", limit=10)
            assert len(rows) == 1
            assert rows[0]["content"] == "用户本地文献库搭建（未完成），仍在建设中"
        finally:
            await stack.close()

    asyncio.run(_run())


def test_distinct_facts_are_both_stored(tmp_path) -> None:
    async def _run() -> None:
        stack = await build_stack(tmp_path)
        try:
            await stack.memory.remember_text(SCOPE, "用户喜欢苹果")
            await stack.memory.remember_text(SCOPE, "用户喜欢橘子")
            assert await stack.memories.count([SCOPE], status="active") == 2
        finally:
            await stack.close()

    asyncio.run(_run())


def test_same_content_different_kind_merges(tmp_path) -> None:
    """同正文不同类型不算精确重复，落到相似判定后仍应合并（保持原类型）。"""

    async def _run() -> None:
        stack = await build_stack(tmp_path)
        try:
            fact_id = await stack.memory.remember_text(SCOPE, "用户对花粉过敏", kind=KIND_FACT)
            insight_id = await stack.memory.remember_text(
                SCOPE, "用户对花粉过敏", kind=KIND_INSIGHT
            )
            assert insight_id == fact_id
            rows = await stack.memories.list_by_status([SCOPE], status="active", limit=10)
            assert len(rows) == 1
            assert rows[0]["kind"] == KIND_FACT  # 合并保持原记忆的类型
        finally:
            await stack.close()

    asyncio.run(_run())


def test_journal_kind_is_exempt_from_merge(tmp_path) -> None:
    """周记是时间线记录：内容相同的不同条目各自保留，不合并。"""

    async def _run() -> None:
        stack = await build_stack(tmp_path)
        try:
            await stack.memory.remember_text(
                SCOPE, "今天陪阿澈去了海边", kind=KIND_JOURNAL, source="journal"
            )
            await stack.memory.remember_text(
                SCOPE, "今天陪阿澈去了海边", kind=KIND_JOURNAL, source="journal"
            )
            assert await stack.memories.count([SCOPE], status="active") == 2
        finally:
            await stack.close()

    asyncio.run(_run())


def test_buffered_episodes_are_not_deduped(tmp_path) -> None:
    """对话缓冲是反思原料，不走查重门（由裁剪与过期清理控量）。"""

    async def _run() -> None:
        stack = await build_stack(tmp_path)
        try:
            await stack.memory.buffer_episode(SCOPE, "今天聊到了天气真好")
            await stack.memory.buffer_episode(SCOPE, "今天聊到了天气真好")
            assert await stack.memories.count_by_status([SCOPE], "buffered") == 2
        finally:
            await stack.close()

    asyncio.run(_run())


def test_merge_keeps_search_index_fresh(tmp_path) -> None:
    """合并更新后，按新正文的措辞检索应能命中同一条记忆。"""

    async def _run() -> None:
        stack = await build_stack(tmp_path)
        try:
            await stack.memory.remember_text(SCOPE, "用户本地文献库搭建尚未完成")
            await stack.memory.remember_text(SCOPE, "用户本地文献库搭建（未完成），仍在建设中")
            result = await stack.memory.recall(SCOPE, "文献库仍在建设中")
            assert result.items, "合并后新措辞应可检索"
            assert result.items[0].content == "用户本地文献库搭建（未完成），仍在建设中"
        finally:
            await stack.close()

    asyncio.run(_run())


def test_cross_scope_facts_not_merged(tmp_path) -> None:
    """查重只在记忆自身作用域内比对：全局与会话各自保留。"""

    async def _run() -> None:
        stack = await build_stack(tmp_path)
        try:
            await stack.memory.remember_text(MemoryScope.global_scope(), "用户喜欢喝美式咖啡")
            await stack.memory.remember_text(SCOPE, "用户喜欢喝美式咖啡")
            assert await stack.memories.count([MemoryScope.global_scope()], status="active") == 1
            assert await stack.memories.count([SCOPE], status="active") == 1
        finally:
            await stack.close()

    asyncio.run(_run())
