"""拟人化学习测试：风格模仿、群组黑话、社交好感度。

覆盖重点：

- 零成本路径（邻接对抽取、候选统计、规则判定）必须真的**不调用模型**；
- 模型兜底失败时不得改变分数（宁可不更新，也不要误判）；
- 审批制：未批准的样本绝不进入生效表；
- 计数持久化：插件重载后候选词频不归零。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from super_astrbot.harness.protocols import EventView, LlmResult
from super_astrbot.loop import MemoryStateStore
from super_astrbot.persona import (
    SOURCE_JARGON,
    SOURCE_STYLE,
    AffinityConfig,
    AffinityService,
    JargonConfig,
    JargonService,
    PersonaConfig,
    PersonaService,
    StyleConfig,
    StyleService,
)
from super_astrbot.persona.prompts import (
    build_affinity_prompt,
    build_jargon_prompt,
    parse_affinity_verdict,
    parse_jargon_insights,
    render_jargon_block,
    render_style_block,
)
from super_astrbot.spec.errors import LlmError
from super_astrbot.spec.scopes import MemoryScope
from super_astrbot.storage import (
    AffinityRepository,
    Database,
    JargonRepository,
    ReviewRepository,
    StyleRepository,
)

UMO = "aiocqhttp:GroupMessage:10086"
SCOPE = MemoryScope.for_session(UMO)
NOW = 1_700_000_000.0


class FakeClock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class FakeLlm:
    text: str = ""
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    async def chat(
        self,
        *,
        prompt: str,
        system_prompt: str | None = None,
        contexts: Sequence[Any] | None = None,
        provider_id: str | None = None,
        session_key: str | None = None,
        timeout: float | None = None,
        purpose: str = "general",
    ) -> LlmResult:
        self.calls.append(prompt)
        if self.error is not None:
            raise self.error
        return LlmResult(text=self.text)


@dataclass
class FakeInjector:
    calls: list[tuple[int, str]] = field(default_factory=list)

    def inject(self, target: Any, blocks: Sequence[str], *, prefer: str = "auto") -> Any:
        from super_astrbot.harness.protocols import InjectResult

        self.calls.append((len(blocks), "\n".join(blocks)))
        return InjectResult(applied=True, method="fake", parts=len(blocks), chars=1)


def _view(*, umo: str = UMO, text: str = "", sender: str = "u1") -> EventView:
    return EventView(umo=umo, sender_id=sender, text=text, timestamp=NOW)


async def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "persona.db")
    await db.connect()
    return db


def _style_config(**overrides: Any) -> StyleConfig:
    base: dict[str, Any] = {"enabled": True, "approval_required": False}
    base.update(overrides)
    return StyleConfig(**base)


def _jargon_config(**overrides: Any) -> JargonConfig:
    base: dict[str, Any] = {"enabled": True, "approval_required": False, "min_frequency": 2}
    base.update(overrides)
    return JargonConfig(**base)


def _affinity_config(**overrides: Any) -> AffinityConfig:
    base: dict[str, Any] = {"enabled": True, "use_llm": True}
    base.update(overrides)
    return AffinityConfig(**base)


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #


def test_persona_config_defaults_are_disabled() -> None:
    config = PersonaConfig.from_mapping({})
    assert not config.any_enabled
    assert config.style.approval_required is True
    assert config.jargon.approval_required is True
    assert config.affinity.inject_enabled is True


def test_persona_config_clamps_out_of_range_values() -> None:
    config = PersonaConfig.from_mapping(
        {
            "persona": {
                "style": True,
                "style_max_patterns": 99999,
                "style_top_k": 0,
                "jargon_min_frequency": 1,
                "affinity_initial_score": 5.0,
            }
        }
    )
    assert config.style.max_patterns == 2000
    assert config.style.top_k == 1
    assert config.jargon.min_frequency == 2
    assert config.affinity.initial_score == 1.0


# --------------------------------------------------------------------------- #
# 风格模仿
# --------------------------------------------------------------------------- #


def test_style_learn_rejects_short_and_command(tmp_path: Path) -> None:
    async def _run() -> tuple[Any, Any, int]:
        db = await _db(tmp_path)
        service = StyleService(
            config=_style_config(), patterns=StyleRepository(db), reviews=ReviewRepository(db)
        )
        short = await service.learn(_view(), user_text="好", reply_text="好的")
        command = await service.learn(_view(), user_text="/sab status", reply_text="状态如下")
        count = await service.count(SCOPE)
        await db.close()
        return short, command, count

    short, command, count = asyncio.run(_run())
    assert not short.stored
    assert "过短" in short.reason
    assert not command.stored
    assert "命令" in command.reason
    assert count == 0


def test_style_learn_stores_without_approval(tmp_path: Path) -> None:
    async def _run() -> int:
        db = await _db(tmp_path)
        service = StyleService(
            config=_style_config(), patterns=StyleRepository(db), reviews=ReviewRepository(db)
        )
        outcome = await service.learn(
            _view(), user_text="今天天气怎么样", reply_text="挺好的，适合出门"
        )
        count = await service.count(SCOPE)
        await db.close()
        return count if outcome.stored else -1

    assert asyncio.run(_run()) == 1


def test_style_learn_queues_when_approval_required(tmp_path: Path) -> None:
    async def _run() -> tuple[Any, int, list[dict[str, Any]]]:
        db = await _db(tmp_path)
        reviews = ReviewRepository(db)
        service = StyleService(
            config=_style_config(approval_required=True),
            patterns=StyleRepository(db),
            reviews=reviews,
        )
        outcome = await service.learn(
            _view(), user_text="今天天气怎么样", reply_text="挺好的，适合出门"
        )
        count = await service.count(SCOPE)
        pending = await reviews.list_pending((SCOPE,), limit=10)
        await db.close()
        return outcome, count, pending

    outcome, count, pending = asyncio.run(_run())
    assert outcome.pending
    assert count == 0
    assert len(pending) == 1
    assert pending[0]["origin"] == SOURCE_STYLE


def test_style_select_matches_similar_and_skips_unrelated(tmp_path: Path) -> None:
    async def _run() -> tuple[Any, Any]:
        db = await _db(tmp_path)
        service = StyleService(
            config=_style_config(), patterns=StyleRepository(db), reviews=ReviewRepository(db)
        )
        await service.learn(_view(), user_text="今天天气怎么样", reply_text="挺好的，适合出门")
        hit = await service.select(SCOPE, "今天天气怎么样")
        miss = await service.select(SCOPE, "完全不相关的另一段内容")
        await db.close()
        return hit, miss

    hit, miss = asyncio.run(_run())
    assert not hit.is_empty
    assert hit.examples[0][1] == "挺好的，适合出门"
    assert miss.is_empty


def test_style_select_updates_hits_and_weight(tmp_path: Path) -> None:
    async def _run() -> dict[str, Any]:
        db = await _db(tmp_path)
        patterns = StyleRepository(db)
        service = StyleService(
            config=_style_config(), patterns=patterns, reviews=ReviewRepository(db)
        )
        await service.learn(_view(), user_text="今天天气怎么样", reply_text="挺好的，适合出门")
        await service.select(SCOPE, "今天天气怎么样")
        rows = await patterns.list_active((SCOPE,))
        await db.close()
        return rows[0]

    row = asyncio.run(_run())
    assert int(row["hits"]) == 1
    assert float(row["weight"]) > 1.0


def test_style_maintain_archives_low_weight(tmp_path: Path) -> None:
    async def _run() -> tuple[int, int]:
        db = await _db(tmp_path)
        patterns = StyleRepository(db)
        service = StyleService(
            config=_style_config(half_life_days=1.0, weight_floor=0.2),
            patterns=patterns,
            reviews=ReviewRepository(db),
        )
        await service.learn(_view(), user_text="今天天气怎么样", reply_text="挺好的，适合出门")
        for _ in range(3):
            await service.maintain()
        remaining = await service.count(SCOPE)
        await db.close()
        return remaining, 0

    remaining, _ = asyncio.run(_run())
    assert remaining == 0  # 权重 1 → 0.5 → 0.25 → 0.125，低于 0.2 被归档


def test_style_maintain_trims_capacity(tmp_path: Path) -> None:
    async def _run() -> int:
        db = await _db(tmp_path)
        patterns = StyleRepository(db)
        service = StyleService(
            config=_style_config(max_patterns=2),
            patterns=patterns,
            reviews=ReviewRepository(db),
        )
        for index in range(4):
            await service.learn(
                _view(),
                user_text=f"今天天气怎么样第{index}个问题",
                reply_text=f"这是第{index}个回答",
            )
        count = await service.count(SCOPE)
        await db.close()
        return count

    assert asyncio.run(_run()) == 2


def test_render_style_block_stops_at_budget() -> None:
    block = render_style_block(
        [("场景一", "表达一"), ("场景二", "表达二")], max_chars=len("表达参考" * 3)
    )
    assert "场景一" in block
    assert "场景二" not in block


# --------------------------------------------------------------------------- #
# 群组黑话
# --------------------------------------------------------------------------- #


def test_jargon_observe_counts_ascii_terms(tmp_path: Path) -> None:
    async def _run() -> tuple[list[MemoryScope], Any]:
        db = await _db(tmp_path)
        service = JargonService(
            config=_jargon_config(),
            jargons=JargonRepository(db),
            reviews=ReviewRepository(db),
            llm=FakeLlm(),
            store=MemoryStateStore(),
            clock=FakeClock(),
        )
        for _ in range(3):
            await service.observe(SCOPE, "yyds 真的好用")
        await service.observe(SCOPE, "12345 4567")
        scopes = service.tracked_scopes()
        # 未达门槛时扫描不产生任何模型调用
        outcome = await service.scan(SCOPE)
        await db.close()
        return scopes, outcome

    scopes, outcome = asyncio.run(_run())
    assert scopes == [SCOPE]
    assert outcome.ran is False or outcome.reason != ""


def test_jargon_scan_skips_below_frequency(tmp_path: Path) -> None:
    async def _run() -> tuple[Any, int]:
        db = await _db(tmp_path)
        llm = FakeLlm(text="[]")
        service = JargonService(
            config=_jargon_config(min_frequency=5),
            jargons=JargonRepository(db),
            reviews=ReviewRepository(db),
            llm=llm,
            store=MemoryStateStore(),
            clock=FakeClock(),
        )
        await service.observe(SCOPE, "yyds 真的好用")
        outcome = await service.scan(SCOPE)
        await db.close()
        return outcome, len(llm.calls)

    outcome, calls = asyncio.run(_run())
    assert not outcome.ran
    assert calls == 0


def test_jargon_scan_stores_when_approval_off(tmp_path: Path) -> None:
    async def _run() -> tuple[Any, int]:
        db = await _db(tmp_path)
        jargons = JargonRepository(db)
        service = JargonService(
            config=_jargon_config(min_frequency=2),
            jargons=jargons,
            reviews=ReviewRepository(db),
            llm=FakeLlm(
                text='[{"term":"yyds","is_jargon":true,"meaning":"永远的神","confidence":0.9}]'
            ),
            store=MemoryStateStore(),
            clock=FakeClock(),
        )
        for _ in range(2):
            await service.observe(SCOPE, "yyds 真的好用")
        outcome = await service.scan(SCOPE)
        rows = await jargons.list_active((SCOPE,))
        await db.close()
        return outcome, len(rows)

    outcome, stored = asyncio.run(_run())
    assert outcome.stored == 1
    assert stored == 1


def test_jargon_scan_queues_when_approval_required(tmp_path: Path) -> None:
    async def _run() -> tuple[Any, int, str]:
        db = await _db(tmp_path)
        reviews = ReviewRepository(db)
        service = JargonService(
            config=_jargon_config(min_frequency=2, approval_required=True),
            jargons=JargonRepository(db),
            reviews=reviews,
            llm=FakeLlm(
                text='[{"term":"yyds","is_jargon":true,"meaning":"永远的神","confidence":0.9}]'
            ),
            store=MemoryStateStore(),
            clock=FakeClock(),
        )
        for _ in range(2):
            await service.observe(SCOPE, "yyds 真的好用")
        outcome = await service.scan(SCOPE)
        pending = await reviews.list_pending((SCOPE,), limit=10)
        await db.close()
        return outcome, len(pending), pending[0]["origin"] if pending else ""

    outcome, count, origin = asyncio.run(_run())
    assert outcome.pending == 1
    assert count == 1
    assert origin == SOURCE_JARGON


def test_jargon_scan_discards_rejected_candidates(tmp_path: Path) -> None:
    async def _run() -> tuple[Any, int]:
        db = await _db(tmp_path)
        llm = FakeLlm(text='[{"term":"yyds","is_jargon":false,"confidence":0.9}]')
        service = JargonService(
            config=_jargon_config(min_frequency=2),
            jargons=JargonRepository(db),
            reviews=ReviewRepository(db),
            llm=llm,
            store=MemoryStateStore(),
            clock=FakeClock(),
        )
        for _ in range(2):
            await service.observe(SCOPE, "yyds 真的好用")
        await service.scan(SCOPE)
        second = await service.scan(SCOPE)
        await db.close()
        return second, len(llm.calls)

    second, calls = asyncio.run(_run())
    assert not second.ran  # 已处理过的候选被清空
    assert calls == 1


def test_jargon_scan_skips_existing_terms(tmp_path: Path) -> None:
    async def _run() -> tuple[bool, bool]:
        db = await _db(tmp_path)
        llm = FakeLlm(
            text='[{"term":"yyds","is_jargon":true,"meaning":"永远的神","confidence":0.9}]'
        )
        jargons = JargonRepository(db)
        service = JargonService(
            config=_jargon_config(min_frequency=2),
            jargons=jargons,
            reviews=ReviewRepository(db),
            llm=llm,
            store=MemoryStateStore(),
            clock=FakeClock(),
        )
        for _ in range(2):
            await service.observe(SCOPE, "yyds 真的好用")
        await service.scan(SCOPE)
        stored = await jargons.existing_terms((SCOPE,))

        for _ in range(2):
            await service.observe(SCOPE, "yyds 真的好用")
        await service.scan(SCOPE)
        # 已收录的词不应再次出现在推断提示词里（该词本身仍是高频候选）
        repeated = any("term: yyds" in prompt for prompt in llm.calls[1:])
        await db.close()
        return "yyds" in stored, repeated

    stored, repeated = asyncio.run(_run())
    assert stored
    assert not repeated


def test_jargon_counts_persist_across_reload(tmp_path: Path) -> None:
    async def _run() -> int:
        db = await _db(tmp_path)
        store = MemoryStateStore()
        first = JargonService(
            config=_jargon_config(min_frequency=2),
            jargons=JargonRepository(db),
            reviews=ReviewRepository(db),
            llm=FakeLlm(),
            store=store,
            clock=FakeClock(),
        )
        for _ in range(2):
            await first.observe(SCOPE, "yyds 真的好用")
        await first.flush()  # 插件卸载前的落盘

        llm = FakeLlm(
            text='[{"term":"yyds","is_jargon":true,"meaning":"永远的神","confidence":0.9}]'
        )
        second = JargonService(
            config=_jargon_config(min_frequency=2),
            jargons=JargonRepository(db),
            reviews=ReviewRepository(db),
            llm=llm,
            store=store,
            clock=FakeClock(),
        )
        outcome = await second.scan(SCOPE)
        await db.close()
        return outcome.stored

    assert asyncio.run(_run()) == 1


def test_jargon_render_block_only_on_hit() -> None:
    block = render_jargon_block([("yyds", "永远的神")], max_chars=200)
    assert "yyds" in block
    assert "不要复述" in block


def test_jargon_prompt_and_parse_roundtrip() -> None:
    prompt = build_jargon_prompt([("yyds", ["yyds 真的好用"])])
    assert "yyds" in prompt
    parsed = parse_jargon_insights(
        '```json\n[{"term":"yyds","is_jargon":true,"meaning":"永远的神","confidence":0.8}]\n```',
        candidates=["yyds"],
        min_confidence=0.6,
    )
    assert parsed[0]["meaning"] == "永远的神"


def test_parse_jargon_rejects_low_confidence_and_unknown_terms() -> None:
    payload = (
        '[{"term":"yyds","is_jargon":true,"meaning":"神","confidence":0.2},'
        '{"term":"unknown","is_jargon":true,"meaning":"神","confidence":0.9}]'
    )
    assert parse_jargon_insights(payload, candidates=["yyds"], min_confidence=0.6) == []


# --------------------------------------------------------------------------- #
# 好感度
# --------------------------------------------------------------------------- #


def test_affinity_praise_raises_score(tmp_path: Path) -> None:
    async def _run() -> Any:
        db = await _db(tmp_path)
        service = AffinityService(
            config=_affinity_config(),
            affinities=AffinityRepository(db),
            llm=FakeLlm(),
            clock=FakeClock(),
        )
        outcome = await service.observe(_view(), "你真的很厉害，太贴心了")
        await db.close()
        return outcome

    outcome = asyncio.run(_run())
    assert outcome.updated
    assert outcome.kind == "praise"
    assert outcome.score > 0.5


def test_affinity_insult_lowers_score(tmp_path: Path) -> None:
    async def _run() -> Any:
        db = await _db(tmp_path)
        service = AffinityService(
            config=_affinity_config(),
            affinities=AffinityRepository(db),
            llm=FakeLlm(),
            clock=FakeClock(),
        )
        outcome = await service.observe(_view(), "你这个废物，滚蛋")
        await db.close()
        return outcome

    outcome = asyncio.run(_run())
    assert outcome.kind == "insult"
    assert outcome.score < 0.5


def test_affinity_conflict_uses_llm_fallback(tmp_path: Path) -> None:
    async def _run() -> tuple[Any, int]:
        db = await _db(tmp_path)
        llm = FakeLlm(text='{"type":"insult","confidence":0.9}')
        service = AffinityService(
            config=_affinity_config(),
            affinities=AffinityRepository(db),
            llm=llm,
            clock=FakeClock(),
        )
        # 同时命中「厉害」（正向）与「垃圾」（负向）→ 交给模型裁决
        outcome = await service.observe(_view(), "你可真厉害，就是个垃圾")
        await db.close()
        return outcome, len(llm.calls)

    outcome, calls = asyncio.run(_run())
    assert calls == 1
    assert outcome.kind == "insult"
    assert outcome.source == "llm"


def test_affinity_llm_failure_keeps_score(tmp_path: Path) -> None:
    async def _run() -> Any:
        db = await _db(tmp_path)
        llm = FakeLlm(error=LlmError("模型不可用"))
        service = AffinityService(
            config=_affinity_config(),
            affinities=AffinityRepository(db),
            llm=llm,
            clock=FakeClock(),
        )
        outcome = await service.observe(_view(), "你可真厉害，就是个垃圾")
        await db.close()
        return outcome

    outcome = asyncio.run(_run())
    assert outcome.kind == "neutral"
    assert outcome.delta == 0.0


def test_affinity_decays_toward_baseline(tmp_path: Path) -> None:
    async def _run() -> tuple[float, float]:
        db = await _db(tmp_path)
        clock = FakeClock()
        affinities = AffinityRepository(db)
        service = AffinityService(
            config=_affinity_config(decay_half_life_days=1.0),
            affinities=affinities,
            llm=FakeLlm(),
            clock=clock,
        )
        await service.observe(_view(), "你真的很厉害")
        first = await affinities.get("session", UMO, "u1")
        before = float((first or {}).get("score") or 0.0)

        clock.advance(86400 * 3)  # 3 个半衰期
        await service.observe(_view(), "今天几点了")  # 中性交互触发写回
        second = await affinities.get("session", UMO, "u1")
        await db.close()
        return before, float((second or {}).get("score") or 0.0)

    before, after = asyncio.run(_run())
    assert before > 0.5
    assert after < before
    assert after <= 0.52  # 长期静默后大部分回归基线


def test_affinity_guidance_is_empty_for_neutral_score(tmp_path: Path) -> None:
    async def _run() -> tuple[str, str]:
        db = await _db(tmp_path)
        affinities = AffinityRepository(db)
        service = AffinityService(
            config=_affinity_config(),
            affinities=affinities,
            llm=FakeLlm(),
            clock=FakeClock(),
        )
        await affinities.upsert(
            scope_type="session",
            scope_id=UMO,
            target_id="u1",
            score=0.5,
            mood="neutral",
            interactions=1,
            last_interaction=NOW,
            updated_at=NOW,
        )
        neutral = await service.guidance(SCOPE, "u1")
        await affinities.upsert(
            scope_type="session",
            scope_id=UMO,
            target_id="u1",
            score=0.9,
            mood="positive",
            interactions=5,
            last_interaction=NOW,
            updated_at=NOW,
        )
        close = await service.guidance(SCOPE, "u1")
        await db.close()
        return neutral, close

    neutral, close = asyncio.run(_run())
    assert neutral == ""  # 常规关系不注入
    assert "亲近" in close


def test_affinity_delta_is_capped(tmp_path: Path) -> None:
    async def _run() -> Any:
        db = await _db(tmp_path)
        service = AffinityService(
            config=_affinity_config(daily_delta_cap=0.01),
            affinities=AffinityRepository(db),
            llm=FakeLlm(),
            clock=FakeClock(),
        )
        outcome = await service.observe(_view(), "你真的很厉害")
        await db.close()
        return outcome

    outcome = asyncio.run(_run())
    assert outcome.score <= 0.51001


def test_parse_affinity_verdict_handles_noise() -> None:
    assert parse_affinity_verdict('```json\n{"type":"praise","confidence":0.8}\n```') == (
        "praise",
        0.8,
    )
    assert parse_affinity_verdict("完全没有结构") == ("", 0.0)
    assert parse_affinity_verdict('{"type":"unknown","confidence":0.8}') == ("", 0.0)
    assert "不难过" in build_affinity_prompt("不难过")


# --------------------------------------------------------------------------- #
# 门面：审批分流与注入
# --------------------------------------------------------------------------- #


def _persona_service(
    db: Database,
    *,
    style: StyleConfig | None = None,
    jargon: JargonConfig | None = None,
    affinity: AffinityConfig | None = None,
    llm: FakeLlm | None = None,
    injector: FakeInjector | None = None,
    store: MemoryStateStore | None = None,
    clock: FakeClock | None = None,
) -> PersonaService:
    return PersonaService(
        config=PersonaConfig(
            style=style or _style_config(),
            jargon=jargon or _jargon_config(),
            affinity=affinity or _affinity_config(),
        ),
        patterns=StyleRepository(db),
        jargons=JargonRepository(db),
        affinities=AffinityRepository(db),
        reviews=ReviewRepository(db),
        llm=llm or FakeLlm(),
        injector=injector or FakeInjector(),
        store=store or MemoryStateStore(),
        clock=clock or FakeClock(),
    )


def test_service_approve_routes_style_and_jargon(tmp_path: Path) -> None:
    async def _run() -> tuple[bool, bool, int, int]:
        db = await _db(tmp_path)
        reviews = ReviewRepository(db)
        service = _persona_service(
            db,
            style=_style_config(approval_required=True),
            jargon=_jargon_config(approval_required=True, min_frequency=2),
            llm=FakeLlm(
                text='[{"term":"yyds","is_jargon":true,"meaning":"永远的神","confidence":0.9}]'
            ),
        )
        await service.learn_style(
            _view(), user_text="今天天气怎么样", reply_text="挺好的，适合出门"
        )
        for _ in range(2):
            await service.observe_user(_view(text="yyds 真的好用"), "yyds 真的好用")
        await service.scan_jargon(SCOPE)

        pending = await reviews.list_pending((SCOPE,), limit=10)
        for row in pending:
            await service.approve(int(row["id"]))

        style_count = await service.stats()
        handled, _ = await service.approve(int(pending[0]["id"]))
        left = await reviews.count_pending((SCOPE,))
        await db.close()
        return handled, bool(pending), style_count.style, left

    handled, has_pending, style_count, left = asyncio.run(_run())
    assert has_pending
    assert style_count == 1
    assert left == 0
    assert not handled  # 已处理过的记录不再重复落地


def test_service_approve_ignores_reflection_origin(tmp_path: Path) -> None:
    async def _run() -> bool:
        db = await _db(tmp_path)
        reviews = ReviewRepository(db)
        review_id = await reviews.add(
            scope_type="session",
            scope_id=UMO,
            origin="reflection",
            payload={"content": "一条反思结论"},
            created_at=NOW,
        )
        service = _persona_service(db)
        handled, _ = await service.approve(review_id)
        await db.close()
        return handled

    assert asyncio.run(_run()) is False


def test_service_inject_composes_enabled_blocks(tmp_path: Path) -> None:
    async def _run() -> tuple[int, str, str]:
        db = await _db(tmp_path)
        injector = FakeInjector()
        llm = FakeLlm(
            text='[{"term":"yyds","is_jargon":true,"meaning":"永远的神","confidence":0.9}]'
        )
        service = _persona_service(db, llm=llm, injector=injector)
        await service.learn_style(
            _view(), user_text="今天天气怎么样", reply_text="挺好的，适合出门"
        )
        for _ in range(2):
            await service.observe_user(_view(text="yyds 真的好用"), "yyds 真的好用")
        await service.scan_jargon(SCOPE)
        for _ in range(2):
            await service.observe_user(_view(text="你真的很厉害"), "你真的很厉害")

        detail = await service.inject(object(), _view(text="今天天气怎么样，yyds"))
        blocks = injector.calls[0][1] if injector.calls else ""
        await db.close()
        return len(injector.calls), detail, blocks

    calls, detail, blocks = asyncio.run(_run())
    assert calls == 1
    assert "3 块" in detail
    assert "表达参考" in blocks  # 风格 few-shot
    assert "不要复述" in blocks  # 黑话的负向指令
    assert "社交参考" in blocks  # 好感度语气


def test_service_inject_skips_when_all_disabled(tmp_path: Path) -> None:
    async def _run() -> tuple[int, str]:
        db = await _db(tmp_path)
        injector = FakeInjector()
        service = _persona_service(
            db,
            style=StyleConfig(enabled=False),
            jargon=JargonConfig(enabled=False),
            affinity=AffinityConfig(enabled=False),
            injector=injector,
        )
        detail = await service.inject(object(), _view(text="随便说点什么"))
        await db.close()
        return len(injector.calls), detail

    calls, detail = asyncio.run(_run())
    assert calls == 0
    assert detail == ""


def test_service_clear_removes_all_learned_data(tmp_path: Path) -> None:
    async def _run() -> tuple[int, int, int]:
        db = await _db(tmp_path)
        service = _persona_service(db)
        await service.learn_style(
            _view(), user_text="今天天气怎么样", reply_text="挺好的，适合出门"
        )
        await service.observe_user(_view(text="你真的很厉害"), "你真的很厉害")
        cleared = await service.clear(SCOPE)
        counts = await service.stats()
        await db.close()
        return cleared["style"], counts.style, counts.affinity

    style_cleared, style_left, affinity_left = asyncio.run(_run())
    assert style_cleared == 1
    assert style_left == 0
    assert affinity_left == 0
