"""自我学习（反思）测试：结构解析、触发判定、闭环与审批模式。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from super_astrbot.harness.protocols import LlmResult
from super_astrbot.learning import ReflectionConfig, ReflectionService
from super_astrbot.learning.prompts import (
    build_reflection_prompt,
    build_weekly_prompt,
    parse_insights,
)
from super_astrbot.spec.errors import LlmError
from super_astrbot.spec.scopes import MemoryScope, ScopeType
from super_astrbot.storage import ReflectionRepository, ReviewRepository

from .helpers import build_stack


def test_reflection_prompt_explains_first_person_bot_lines() -> None:
    """记忆里 Bot 的发言以「我：」记录，提示词必须说清「我」是谁。

    前缀从「助手：」改成「我：」后，「我」不再自解释：若不说明，模型可能把
    Bot 自己的话当成用户说的，产出「用户说……（其实是 Bot 说的）」这类错误记忆。
    """
    prompt = build_reflection_prompt("[09-25 00:57] 我：……嗯", max_facts=3)
    assert "「我」" in prompt
    assert "用户(昵称)" in prompt


def test_weekly_prompt_stays_free_of_transcript_convention() -> None:
    """周度洞察读的是用户自己写的周记，不能被「『我』是 Bot」的约定污染。"""
    prompt = build_weekly_prompt("今天我和朋友去爬山", max_facts=3)
    assert "「我」" not in prompt


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


VALID_JSON = (
    '[{"content":"用户偏好清晨跑步","kind":"preference","importance":0.8,"tags":["运动"]},'
    '{"content":"用户近期在做记忆插件","kind":"insight","importance":0.7,"tags":[]}]'
)


def test_parse_insights_accepts_json_and_fences() -> None:
    direct = parse_insights(VALID_JSON, max_facts=5)
    assert len(direct) == 2
    assert direct[0]["kind"] == "preference"

    fenced = parse_insights(f"```json\n{VALID_JSON}\n```", max_facts=5)
    assert len(fenced) == 2


def test_parse_insights_rejects_invalid_entries() -> None:
    payload = (
        '[{"content":"ok 足够长的内容","kind":"fact","importance":0.5},'
        '{"content":"短","kind":"fact"},'  # 内容过短
        '{"content":"非法类型应被归一化","kind":"unknown","importance":9},'
        '"不是对象"]'
    )
    parsed = parse_insights(payload, max_facts=5)
    assert len(parsed) == 2
    assert parsed[1]["kind"] == "fact"
    assert parsed[1]["importance"] <= 1.0


def test_parse_insights_caps_and_handles_garbage() -> None:
    many = (
        "["
        + ",".join(f'{{"content":"第{i}条足够长的记忆内容","kind":"fact"}}' for i in range(10))
        + "]"
    )
    assert len(parse_insights(many, max_facts=3)) == 3
    assert parse_insights("模型胡说八道，没有 JSON", max_facts=3) == []


async def _make_service(stack: object, llm: FakeLlm, config: ReflectionConfig) -> ReflectionService:
    return ReflectionService(
        config=config,
        memory_service=stack.memory,  # type: ignore[attr-defined]
        journals=stack.journal,  # type: ignore[attr-defined]
        reflections=ReflectionRepository(stack.db),  # type: ignore[attr-defined]
        reviews=ReviewRepository(stack.db),  # type: ignore[attr-defined]
        llm=llm,
    )


def test_should_run_respects_buffer_threshold(tmp_path: Path) -> None:
    async def _run() -> tuple[bool, str, bool, str]:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        config = ReflectionConfig(
            min_messages=3, trigger_rounds=3, interval_minutes=60, cooldown_minutes=0, mode="rounds"
        )
        service = await _make_service(stack, FakeLlm(), config)

        too_few, reason_few = await service.should_run(scope)
        for index in range(3):
            await stack.memory.buffer_episode(scope, f"用户：第 {index} 条消息")
        enough, reason_enough = await service.should_run(scope)
        await stack.close()
        return too_few, reason_few, enough, reason_enough

    too_few, reason_few, enough, reason_enough = asyncio.run(_run())
    assert too_few is False
    assert "不足" in reason_few
    assert enough is True
    assert "达标" in reason_enough


def test_should_run_ignores_trigger_rounds_for_single_scope(tmp_path: Path) -> None:
    """回归：「单作用域阈值」曾被 trigger_rounds 抬高到 30，导致反思永不触发。

    只要本作用域攒够 ``min_messages`` 就该达标，与 ``trigger_rounds`` 无关。
    """

    async def _run() -> tuple[bool, str]:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        config = ReflectionConfig(
            min_messages=3,
            trigger_rounds=99,  # 远高于 min_messages：旧实现会因此判不达标
            interval_minutes=600,
            cooldown_minutes=0,
            mode="rounds",
        )
        service = await _make_service(stack, FakeLlm(), config)
        for index in range(3):
            await stack.memory.buffer_episode(scope, f"用户：第 {index} 条消息")
        should, reason = await service.should_run(scope)
        await stack.close()
        return should, reason

    should, reason = asyncio.run(_run())
    assert should is True, f"单作用域达标不应被 trigger_rounds 抬高：{reason}"
    assert "累计内容达标" in reason


def test_should_run_aggregates_buffer_across_scopes(tmp_path: Path) -> None:
    """缓冲被会话切碎时，全库总量达标也要触发（聚合兜底）。"""

    async def _run() -> tuple[bool, str, int, int]:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        other = MemoryScope.for_session("s2")
        config = ReflectionConfig(
            min_messages=5,
            trigger_rounds=4,
            interval_minutes=600,
            cooldown_minutes=0,
            mode="rounds",
        )
        service = await _make_service(stack, FakeLlm(), config)
        # 两个会话各 2 条：单作用域都不够（2/5），但总量 4 条已达到 trigger_rounds
        for index in range(2):
            await stack.memory.buffer_episode(scope, f"用户：A 第 {index} 条")
            await stack.memory.buffer_episode(other, f"用户：B 第 {index} 条")
        total = await stack.memory.count_buffer_total()
        per_scope = await stack.memory.count_buffer(scope)
        should, reason = await service.should_run(scope)
        await stack.close()
        return should, reason, total, per_scope

    should, reason, total, per_scope = asyncio.run(_run())
    assert per_scope == 2, "单作用域只有 2 条"
    assert total == 4, "聚合计数应包含其它作用域"
    assert should is True
    assert "聚合达标" in reason


def test_should_run_explains_skip_with_both_counts(tmp_path: Path) -> None:
    """两条判据都不达标时，理由要能同时说清单作用域与全库的数量。"""

    async def _run() -> str:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        config = ReflectionConfig(min_messages=9, trigger_rounds=30, mode="rounds")
        service = await _make_service(stack, FakeLlm(), config)
        await stack.memory.buffer_episode(scope, "用户：只有一条")
        _, reason = await service.should_run(scope)
        await stack.close()
        return reason

    reason = asyncio.run(_run())
    assert "本作用域 1/9" in reason
    assert "全库 1/30" in reason


def test_reflection_defaults_favor_triggering() -> None:
    """默认值必须「容易触发」：``both`` + 12 轮，且代码默认与配置页默认一致。

    这组默认值曾经是 ``rounds`` + 30：单作用域阈值实际等于 30，而缓冲按会话分片，
    于是反思长期不触发、长期记忆冻结。默认值改动必须同时落在两处，否则重置配置即复发。
    """
    import json

    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "_conf_schema.json").read_text(encoding="utf-8"))
    items = schema["reflection"]["items"]

    default_config = ReflectionConfig()
    assert default_config.mode == "both"
    assert default_config.trigger_rounds == 12
    assert ReflectionConfig.from_mapping({}).mode == "both"
    assert ReflectionConfig.from_mapping({}).trigger_rounds == 12
    assert items["mode"]["default"] == default_config.mode
    assert items["trigger_rounds"]["default"] == default_config.trigger_rounds


def test_reflect_writes_insight_and_consumes_buffer(tmp_path: Path) -> None:
    async def _run() -> tuple[object, int, object]:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        for index in range(3):
            await stack.memory.buffer_episode(scope, f"用户：我喜欢清晨跑步 {index}")
        llm = FakeLlm(VALID_JSON)
        config = ReflectionConfig(
            min_messages=2, trigger_rounds=2, interval_minutes=1, cooldown_minutes=0, mode="rounds"
        )
        service = await _make_service(stack, llm, config)
        outcome = await service.reflect(scope)
        remaining = await stack.memory.count_buffer(scope)
        recalled = await stack.memory.recall(scope, "清晨 跑步")
        await stack.close()
        return outcome, remaining, recalled

    outcome, remaining, recalled = asyncio.run(_run())
    assert outcome.ran is True
    assert outcome.produced == 2
    assert outcome.error == ""
    assert remaining == 0
    assert recalled.items, "反思写入的洞察应可被检索到"
    assert any("跑步" in item.content for item in recalled.items)


def test_reflect_llm_failure_keeps_buffer(tmp_path: Path) -> None:
    async def _run() -> tuple[object, int]:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        for index in range(3):
            await stack.memory.buffer_episode(scope, f"用户：消息 {index}")
        config = ReflectionConfig(
            min_messages=2, trigger_rounds=2, mode="rounds", cooldown_minutes=0
        )
        service = await _make_service(stack, FakeLlm(fail=True), config)
        outcome = await service.reflect(scope)
        remaining = await stack.memory.count_buffer(scope)
        await stack.close()
        return outcome, remaining

    outcome, remaining = asyncio.run(_run())
    assert outcome.ran is True
    assert outcome.error
    assert outcome.produced == 0
    assert remaining == 3, "模型失败时缓冲不应被消费，以便下次重试"


def test_approval_mode_queues_then_approves(tmp_path: Path) -> None:
    async def _run() -> tuple[int, int, int, int | None, object]:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        for index in range(3):
            await stack.memory.buffer_episode(scope, f"用户：消息 {index}")
        config = ReflectionConfig(
            min_messages=2,
            trigger_rounds=2,
            mode="rounds",
            cooldown_minutes=0,
            approval_required=True,
        )
        service = await _make_service(stack, FakeLlm(VALID_JSON), config)
        outcome = await service.reflect(scope)
        pending = await service.pending_reviews(scope, limit=5)

        # 显式挑选目标记录，避免依赖队列顺序（队列顺序已按 created_at, id 确定，但断言不应耦合）
        target = next(
            (row for row in pending if "清晨跑步" in str(_payload_content(row))),
            None,
        )
        memory_id = await service.approve(target["id"]) if target else None
        recalled = await stack.memory.recall(scope, "清晨 跑步")
        await stack.close()
        return outcome.produced, outcome.pending, len(pending), memory_id, recalled

    produced, pending_count, listed, memory_id, recalled = asyncio.run(_run())
    assert produced == 0
    assert pending_count == 2
    assert listed == 2
    assert memory_id is not None
    assert recalled.items
    assert any("跑步" in item.content for item in recalled.items)


def _payload_content(row: dict) -> str:
    import json

    try:
        return str(json.loads(row.get("payload") or "{}").get("content") or "")
    except (TypeError, ValueError):
        return ""


def test_pending_review_order_is_deterministic(tmp_path: Path) -> None:
    """同一时间戳创建的多条待审记录，必须按 id 稳定排序。"""

    async def _run() -> list[int]:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        for index in range(3):
            await stack.memory.buffer_episode(scope, f"用户：消息 {index}")
        config = ReflectionConfig(
            min_messages=2,
            trigger_rounds=2,
            mode="rounds",
            cooldown_minutes=0,
            approval_required=True,
        )
        service = await _make_service(stack, FakeLlm(VALID_JSON), config)
        await service.reflect(scope)
        first = [row["id"] for row in await service.pending_reviews(scope, limit=5)]
        second = [row["id"] for row in await service.pending_reviews(scope, limit=5)]
        await stack.close()
        return first if first == second else []

    ids = asyncio.run(_run())
    assert ids == sorted(ids)
    assert len(ids) == 2


def test_reject_marks_review(tmp_path: Path) -> None:
    async def _run() -> tuple[bool, int]:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        for index in range(3):
            await stack.memory.buffer_episode(scope, f"用户：消息 {index}")
        config = ReflectionConfig(
            min_messages=2,
            trigger_rounds=2,
            mode="rounds",
            cooldown_minutes=0,
            approval_required=True,
        )
        service = await _make_service(stack, FakeLlm(VALID_JSON), config)
        await service.reflect(scope)
        pending = await service.pending_reviews(scope, limit=5)
        ok = await service.reject(pending[0]["id"])
        left = await service.pending_count(scope)
        await stack.close()
        return ok, left

    ok, left = asyncio.run(_run())
    assert ok is True
    assert left == 1


def test_weekly_reflect_uses_journals(tmp_path: Path) -> None:
    async def _run() -> tuple[object, int]:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        await stack.journal.add(scope, "这周开始跑步，心情好多了", tags=["运动"], emotion=4)
        await stack.journal.add(scope, "工作压力大，但周末去爬山放松了")
        llm = FakeLlm(
            '[{"content":"用户倾向通过运动缓解压力","kind":"insight","importance":0.8,"tags":["情绪"]}]'
        )
        config = ReflectionConfig(
            min_messages=2, trigger_rounds=2, mode="rounds", cooldown_minutes=0
        )
        service = await _make_service(stack, llm, config)
        outcome = await service.weekly_reflect(scope)
        recalled = await stack.memory.recall(scope, "运动 缓解 压力")
        await stack.close()
        return outcome, len(recalled.items)

    outcome, hits = asyncio.run(_run())
    assert outcome.ran is True
    assert outcome.produced == 1
    assert hits >= 1


def test_weekly_reflect_without_journals(tmp_path: Path) -> None:
    async def _run() -> object:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        config = ReflectionConfig()
        service = await _make_service(stack, FakeLlm(VALID_JSON), config)
        outcome = await service.weekly_reflect(scope)
        await stack.close()
        return outcome

    outcome = asyncio.run(_run())
    assert outcome.ran is False
    assert "没有周记" in outcome.reason


def test_scope_type_roundtrip_in_reflection_rows(tmp_path: Path) -> None:
    async def _run() -> str:
        stack = await build_stack(tmp_path)
        scope = MemoryScope(ScopeType.USER, "u-1")
        for index in range(3):
            await stack.memory.buffer_episode(scope, f"用户：消息 {index}")
        config = ReflectionConfig(
            min_messages=2, trigger_rounds=2, mode="rounds", cooldown_minutes=0
        )
        service = await _make_service(stack, FakeLlm(VALID_JSON), config)
        await service.reflect(scope)
        rows = await ReflectionRepository(stack.db).list_recent((scope,), limit=5)
        await stack.close()
        return str(rows[0]["scope_type"]) if rows else ""

    assert asyncio.run(_run()) == "user"
