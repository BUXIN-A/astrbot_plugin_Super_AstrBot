"""记忆产出链路升级的自检用例。

覆盖三项改造：

- JSON 解析加固（``extract_json_entries``）：``<think>`` 块 / 尾随逗号 / 中文引号 / JSONL；
- episode（叙事型）记忆通道：白名单放行 + 长正文不被 400 字截断；
- 缓冲消费与整合层：0 产出不丢原料；整合产物落库、原始条目归档。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from super_astrbot.harness.protocols import LlmResult
from super_astrbot.learning import ReflectionConfig, ReflectionService
from super_astrbot.learning.consolidation import ConsolidationConfig, ConsolidationService
from super_astrbot.learning.prompts import parse_insights
from super_astrbot.memory import STATUS_ARCHIVED, STATUS_BUFFERED
from super_astrbot.spec.errors import LlmError
from super_astrbot.spec.scopes import MemoryScope
from super_astrbot.storage import ReflectionRepository, ReviewRepository
from super_astrbot.support import extract_json_entries

from .helpers import build_stack

DAY = 86400.0


# --------------------------------------------------------------------- #
# extract_json_entries：畸形输出容错
# --------------------------------------------------------------------- #


def test_extract_json_entries_handles_think_block() -> None:
    text = '<think>先想一下</think>[{"content": "甲", "kind": "fact"}]'
    assert len(extract_json_entries(text)) == 1


def test_extract_json_entries_handles_trailing_comma_and_cn_quotes() -> None:
    text = '[{"content": “乙”, "kind": "fact"},]'
    assert extract_json_entries(text)[0]["content"] == "乙"


def test_extract_json_entries_handles_jsonl() -> None:
    text = '{"content": "甲", "kind": "fact"}\n{"content": "乙", "kind": "fact"}'
    assert len(extract_json_entries(text)) == 2


def test_extract_json_entries_handles_single_object_with_array_field() -> None:
    """回归：单个对象内含数组字段（如 tags）时，数组探测会命中内层数组，
    不能因此短路返回空——整合/审核要求的「单个 JSON 对象」就是这种形状。"""
    text = '{"content":"合并后的摘要","kind":"insight","importance":0.6,"tags":["近况"]}'
    entries = extract_json_entries(text)
    assert len(entries) == 1
    assert entries[0]["content"] == "合并后的摘要"


def test_extract_json_entries_returns_empty_on_garbage() -> None:
    assert extract_json_entries("模型胡说八道，没有任何 JSON") == []
    assert extract_json_entries("") == []


# --------------------------------------------------------------------- #
# parse_insights：episode 通道
# --------------------------------------------------------------------- #


def test_parse_insights_accepts_episode_and_keeps_long_content() -> None:
    long_body = "这段时间和 XX 的几次私聊，我心里一直记着。" * 40  # 远超 400 字
    text = f'[{{"content": "{long_body}", "kind": "episode", "importance": 0.8}}]'
    out = parse_insights(text, max_facts=5)
    assert len(out) == 1
    assert out[0]["kind"] == "episode"
    assert len(out[0]["content"]) > 400  # 未被截断到 400
    assert len(out[0]["content"]) <= 2000


def test_parse_insights_still_caps_plain_fact_at_400() -> None:
    long_body = "普通事实" * 300
    text = f'[{{"content": "{long_body}", "kind": "fact"}}]'
    out = parse_insights(text, max_facts=5)
    assert out[0]["kind"] == "fact"
    assert len(out[0]["content"]) <= 400


def test_parse_insights_unknown_kind_falls_back_to_fact() -> None:
    text = '[{"content": "某条足够长的内容", "kind": "不存在的类型"}]'
    assert parse_insights(text, max_facts=5)[0]["kind"] == "fact"


# --------------------------------------------------------------------- #
# 反思闭环：0 产出不丢原料 / 留痕 / 有产出正常消费
# --------------------------------------------------------------------- #


class FakeLlm:
    """可控的 LLM 替身。"""

    def __init__(self, text: str = "[]", *, fail: bool = False) -> None:
        self._text = text
        self._fail = fail
        self.calls = 0

    def list_providers(self) -> list[object]:
        return []

    async def chat(self, **kwargs: object) -> LlmResult:
        self.calls += 1
        if self._fail:
            raise LlmError("模拟模型不可用")
        return LlmResult(text=self._text)


class RecordingLogger:
    """捕获 warning 日志的替身。"""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    def debug(self, *a: object, **k: object) -> None: ...
    def info(self, *a: object, **k: object) -> None: ...

    def warning(self, *a: object, **k: object) -> None:
        self.warnings.append(" ".join(str(arg) for arg in a))

    def error(self, *a: object, **k: object) -> None: ...


async def _make_service(
    stack: object, llm: FakeLlm, config: ReflectionConfig, logger: object | None = None
) -> ReflectionService:
    return ReflectionService(
        config=config,
        memory_service=stack.memory,  # type: ignore[attr-defined]
        journals=stack.journal,  # type: ignore[attr-defined]
        reflections=ReflectionRepository(stack.db),  # type: ignore[attr-defined]
        reviews=ReviewRepository(stack.db),  # type: ignore[attr-defined]
        llm=llm,
        logger=logger,  # type: ignore[arg-type]
    )


def test_reflection_keeps_buffer_when_nothing_parsed(tmp_path: Path) -> None:
    """0 产出时缓冲必须保持 buffered：原料被静默归档就无法重试了。"""

    async def _run() -> tuple[int, list[str]]:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        config = ReflectionConfig(
            min_messages=3,
            trigger_rounds=3,
            interval_minutes=60,
            cooldown_minutes=0,
            mode="rounds",
        )
        logger = RecordingLogger()
        service = await _make_service(stack, FakeLlm("模型输出的纯文本，没有 JSON"), config, logger)
        for index in range(3):
            await stack.memory.buffer_episode(scope, f"用户：第 {index} 条消息")

        await service.reflect(scope, reason="test")
        buffered = await stack.memories.list_by_status([scope], status=STATUS_BUFFERED, limit=50)
        await stack.close()
        return len(buffered), logger.warnings

    buffered_left, warnings = asyncio.run(_run())
    assert buffered_left == 3  # 缓冲未被归档
    assert any("反思产出无法解析" in warning for warning in warnings)


def test_reflection_consumes_buffer_when_produced(tmp_path: Path) -> None:
    """正常产出时缓冲照常消费（归档），且 episode 经 ensure_kind_valid 原样落库。"""

    async def _run() -> tuple[int, int, list[str]]:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        config = ReflectionConfig(
            min_messages=3,
            trigger_rounds=3,
            interval_minutes=60,
            cooldown_minutes=0,
            mode="rounds",
        )
        payload = (
            '[{"content":"这段时间和 XX 的几次私聊，我心里一直记着。","kind":"episode",'
            '"importance":0.8,"tags":["私聊"]}]'
        )
        service = await _make_service(stack, FakeLlm(payload), config)
        for index in range(3):
            await stack.memory.buffer_episode(scope, f"用户：第 {index} 条消息")

        outcome = await service.reflect(scope, reason="test")
        buffered = await stack.memories.list_by_status([scope], status=STATUS_BUFFERED, limit=50)
        active = await stack.memories.list_by_status([scope], status="active", limit=50)
        kinds = [str(row["kind"]) for row in active if row["source"] == "reflection"]
        await stack.close()
        return outcome.produced, len(buffered), kinds

    produced, buffered_left, kinds = asyncio.run(_run())
    assert produced == 1
    assert buffered_left == 0  # 有产出 → 缓冲消费
    assert kinds == ["episode"]


# --------------------------------------------------------------------- #
# 整合层
# --------------------------------------------------------------------- #

# 三条彼此差异明显（2-gram Jaccard 远低于 0.5）的种子记忆，避免被写前查重合并。
_SEED_CONTENTS = (
    "用户周二晚上说正在准备跳槽面试，目标是后端开发岗",
    "用户周三提到家里的猫生病了，带去宠物医院挂水",
    "用户周四说想开始学吉他，让我推荐入门教程",
)


def _consolidation_config(**overrides: object) -> ConsolidationConfig:
    base: dict[str, object] = {
        "enabled": True,
        "min_age_days": 7.0,
        "max_importance": 0.5,
        "min_memories_per_group": 3,
        "max_groups_per_run": 5,
    }
    base.update(overrides)
    return ConsolidationConfig(**base)  # type: ignore[arg-type]


async def _seed_old_memories(stack: object, count: int = 3) -> None:
    scope = MemoryScope.for_session("s1")
    moment = time.time()
    for index in range(count):
        await stack.memory.remember_text(  # type: ignore[attr-defined]
            scope,
            _SEED_CONTENTS[index],
            kind="fact",
            importance=0.3,
            source="reflection",
            created_at=moment - 10 * DAY,
        )


def test_consolidation_disabled_is_noop(tmp_path: Path) -> None:
    """默认关闭时零副作用：run_once 直接跳过。"""

    async def _run() -> dict[str, int]:
        stack = await build_stack(tmp_path)
        service = ConsolidationService(
            config=ConsolidationConfig(enabled=False), memory_service=stack.memory, llm=FakeLlm()
        )
        stats = await service.run_once(force=True)
        await stack.close()
        return stats

    assert asyncio.run(_run()) == {"skipped": 1}


def test_consolidation_merges_and_archives(tmp_path: Path) -> None:
    """开启后：产出 1 条 source=consolidation 的记忆，原 ≥3 条变 archived。"""

    async def _run() -> tuple[dict[str, int], int, int]:
        stack = await build_stack(tmp_path)
        await _seed_old_memories(stack)
        scope = MemoryScope.for_session("s1")

        llm = FakeLlm(
            '{"content":"用户最近聊了跳槽面试、猫生病和想学吉他，几件事前后相隔两天。",'
            '"kind":"insight","importance":0.6,"tags":["近况"]}'
        )
        service = ConsolidationService(
            config=_consolidation_config(), memory_service=stack.memory, llm=llm
        )
        stats = await service.run_once(force=True)
        active = await stack.memories.list_by_status([scope], status="active", limit=50)
        archived = await stack.memories.list_by_status([scope], status=STATUS_ARCHIVED, limit=50)
        consolidated = [row for row in active if row["source"] == "consolidation"]
        await stack.close()
        return (
            stats,
            len(consolidated),
            sum(1 for row in archived if row["source"] == "reflection"),
        )

    stats, consolidated_count, archived_count = asyncio.run(_run())
    assert stats["groups"] == 1
    assert consolidated_count == 1
    assert archived_count == 3


def test_consolidation_failure_never_raises(tmp_path: Path) -> None:
    """LLM 配错/失败：返回 failed>0 的统计而不是抛异常，原始记忆不受影响。"""

    async def _run() -> tuple[dict[str, int], int]:
        stack = await build_stack(tmp_path)
        await _seed_old_memories(stack)
        scope = MemoryScope.for_session("s1")

        service = ConsolidationService(
            config=_consolidation_config(),
            memory_service=stack.memory,
            llm=FakeLlm(fail=True),
        )
        stats = await service.run_once(force=True)
        active = await stack.memories.list_by_status([scope], status="active", limit=50)
        await stack.close()
        return stats, len(active)

    stats, active_left = asyncio.run(_run())
    assert stats["failed"] >= 1
    assert active_left == 3  # 原始记忆原封不动


def test_consolidation_keeps_product_when_dedup_hits_member(tmp_path: Path) -> None:
    """写前查重命中组内条目时，不能把承载整合产物的记忆一起归档。

    摘要与某条原文相同会触发「包含判定」的近义去重：写前查重会直接复用
    （并改写）那条记忆。旧实现随后把整组归档，整合产物随之丢失。
    """

    async def _run() -> tuple[int, int]:
        stack = await build_stack(tmp_path)
        await _seed_old_memories(stack)
        scope = MemoryScope.for_session("s1")

        # 摘要完全等于第 2 条的正文 → 必然触发包含判定的写前查重
        llm = FakeLlm('{"content":"用户周三提到家里的猫生病了","kind":"insight"}')
        service = ConsolidationService(
            config=_consolidation_config(), memory_service=stack.memory, llm=llm
        )
        stats = await service.run_once(force=True)
        active = await stack.memories.list_by_status([scope], status="active", limit=50)
        await stack.close()
        assert stats["groups"] == 1
        assert len(active) == 1  # 整合产物仍在，未被连带归档
        assert "猫生病" in str(active[0]["content"])
        return len(active), stats["removed"]

    active_left, removed = asyncio.run(_run())
    assert active_left == 1
    assert removed == 2  # 另外两条原始记忆被归档


# --------------------------------------------------------------------- #
# 二期：整合切块 / 「全部」排除缓冲 / 叙事段落化注入
# --------------------------------------------------------------------- #


def test_consolidation_chunks_large_scope_into_windows(tmp_path: Path) -> None:
    """作用域内候选很多时按 max_group_size 切窗：多次调用、各窗独立整合归档。"""

    async def _run() -> tuple[int, int, int, int]:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        import time as _time

        moment = _time.time()
        distinct = (
            "用户在周一点名要复盘上周的群活动安排",
            "用户周三半夜发了一堆猫的照片",
            "用户周四提到想买新键盘，问我推荐",
            "用户周五说被老板批评了心情很差",
            "用户周六约了朋友去爬山放松",
        )
        for index in range(5):
            await stack.memory.remember_text(
                scope,
                distinct[index],
                kind="fact",
                importance=0.3,
                source="reflection",
                created_at=moment - 10 * DAY - index * 60.0,
            )

        window_summaries = (
            "复盘群活动的请求被提上了日程，用户想整理上周的安排。",
            "半夜晒猫的照片让群里热闹了一阵，氛围轻松。",
            "买键盘的咨询、被批评的坏心情和周六的爬山计划，构成了这几天的日常。",
        )

        class CountingLlm(FakeLlm):
            def __init__(self) -> None:
                super().__init__()
                self.counter = 0

            async def chat(self, **kwargs: object) -> LlmResult:
                self.counter += 1
                text = window_summaries[self.counter - 1]
                return LlmResult(text='{"content":"' + text + '","kind":"insight"}')

        llm = CountingLlm()
        service = ConsolidationService(
            config=_consolidation_config(max_group_size=2, max_groups_per_run=10),
            memory_service=stack.memory,
            llm=llm,
        )
        stats = await service.run_once(force=True)
        active = await stack.memories.list_by_status([scope], status="active", limit=50)
        archived = await stack.memories.list_by_status([scope], status=STATUS_ARCHIVED, limit=50)
        consolidated = [row for row in active if row["source"] == "consolidation"]
        await stack.close()
        return llm.counter, stats["groups"], len(consolidated), sum(1 for _ in archived)

    calls, groups, consolidated, archived = asyncio.run(_run())
    assert calls == 3, "5 条按 2 条一窗应切 3 窗（2+2+1）"
    assert groups == 3
    assert consolidated == 3
    assert archived == 5


def test_consolidation_max_group_size_config_defaults(tmp_path: Path) -> None:
    config = ConsolidationConfig.from_mapping({"consolidation": {"enabled": True}})
    assert config.max_group_size == 25
    custom = ConsolidationConfig.from_mapping({"consolidation": {"max_group_size": 10}})
    assert custom.max_group_size == 10


def test_memory_body_renders_episode_as_paragraph_block() -> None:
    """叙事记忆注入为段落块（保留换行、带日期头），短记忆仍是一行一条。"""
    from super_astrbot.memory import MemoryItem, build_memory_body

    episode = MemoryItem(
        id=1,
        kind="episode",
        content="这几天群里基本是零中二鸟在主聊。2026-08-29 他一边收玉米一边啰嗦。\n我就安静地陪在群里。",
        source="reflection",
        created_at=1_700_000_000.0,
    )
    fact = MemoryItem(
        id=2, kind="fact", content="用户偏好清晨跑步", source="manual", created_at=0.0
    )
    body = build_memory_body([episode, fact], max_chars=2000)
    assert "【" in body and "｜反思】" in body, "叙事块应有日期头"
    assert "\n我就安静地陪在群里。" in body, "叙事正文的换行应保留"
    assert "- (" in body and "用户偏好清晨跑步" in body, "短记忆仍是一行一条"
    assert "(1、" not in body
