"""融合域测试：群友识别 / 三层人格 / 演化 / 共情 / 世界书 / 时序图谱 / 衰减 / 三级 / 回访队列。

三组覆盖：

1. **纯函数口径**（零 IO）：艾宾浩斯保留率、三级分级、情绪识别、经验分类与增量护栏、
   三层人格校验与合并——这些是「口径」的单一事实来源，必须逐值锁死；
2. **服务行为**（真实 SQLite）：安装表结构后跑一遍写入 / 查询 / 命中 / 注入，
   验证「持久化 + 只读派生 + 证据链」真的可用；
3. **端到端**：启动整个 app，走 ``on_llm_request`` 看注入链路，再逐个调用新增
   面板路由，确认「页面上的每一个按钮都有后端承接」。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from super_astrbot.empathy import cogemp
from super_astrbot.fusion import FusionStatusService
from super_astrbot.harness.protocols import EventView
from super_astrbot.members.config import MembersConfig
from super_astrbot.members.service import MembersService
from super_astrbot.memory.decay import (
    DecayService,
    curve_points,
    effective_strength,
    retention_pct,
)
from super_astrbot.memory.tiers import TierService
from super_astrbot.memory.tiers import classify as classify_tier
from super_astrbot.memory.tkg import TemporalGraphService
from super_astrbot.persona.evolution import (
    MAX_TRAIT_DELTA_PER_EXCHANGE,
    TraitEvolutionService,
    classify_experience,
    compute_deltas,
)
from super_astrbot.persona.forge import ForgeService, default_profile
from super_astrbot.persona.forge.model import BIG_FIVE_AXES, PersonalityProfile
from super_astrbot.persona.worldbook import WorldbookService
from super_astrbot.proactive.queue import CallbackQueueService
from super_astrbot.storage import Database

# web/api 的框架替身由 test_web_api_contract 注入；这里复用同一套，避免重复造桩
from .test_app_integration import FakeContext, FakeStar
from .test_web_api_contract import _data, _use_request, web_api

UMO = "aiocqhttp:GroupMessage:1081062427"


def _event(text: str, *, sender_id: str = "3186805099", sender_name: str = "阿柯", umo: str = UMO) -> EventView:
    return EventView(
        umo=umo,
        platform="aiocqhttp",
        session_id="1081062427",
        is_group=True,
        group_id="1081062427",
        sender_id=sender_id,
        sender_name=sender_name,
        text=text,
        timestamp=time.time(),
    )


def run_async(coro: Any) -> Any:
    return asyncio.run(coro)


class _CollectInjector:
    """记录注入块的替身（与 harness.Injector 协议同形）。"""

    def __init__(self) -> None:
        self.blocks: list[str] = []

    def inject(self, target: Any, blocks: list[str], *, prefer: str = "auto") -> Any:
        self.blocks.extend(blocks)
        return SimpleNamespace(applied=True, reason="", method="extra", chars=len("".join(blocks)), parts=len(blocks), fallback=False)


@pytest.fixture()
def db(tmp_path: Path) -> Any:
    """已建表的真实 SQLite（新表由迁移 6 创建）。"""

    async def _open() -> Database:
        database = Database(tmp_path / "fusion.db")
        await database.connect()
        return database

    database = run_async(_open())
    yield database
    run_async(database.close())


def _schema_config() -> Any:
    """构造带 ``.schema`` 的配置替身：面板「具体设置」依赖 schema 派生白名单。"""
    import json

    schema_path = Path(__file__).resolve().parent.parent / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    def defaults(node: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, field in node.items():
            if isinstance(field, dict) and "type" in field:
                out[key] = defaults(field.get("items") or {}) if field["type"] == "object" else field.get("default")
        return out

    class _Config(dict):
        def __init__(self, data: dict[str, Any], schema_ref: dict[str, Any]) -> None:
            super().__init__(data)
            self.schema = schema_ref

    return _Config(defaults(schema), schema)

# --------------------------------------------------------------------------- #
# 1. 纯函数口径                                                            #
# --------------------------------------------------------------------------- #


def test_ebbinghaus_retention_matches_reference_values() -> None:
    """R=exp(-Δt/S)×120：与 AMBRACE 参考值一致（7 天 / S=7 ⇒ 44.15%）。"""
    assert round(retention_pct(0, 7), 2) == 120.0
    assert round(retention_pct(7, 7), 2) == 44.15
    assert retention_pct(1000, 7) >= 0.0
    # 强化后的记忆遗忘更慢
    assert retention_pct(7, 21) > retention_pct(7, 7)
    # 强度下限保护：S 传 0 也不会除零
    assert retention_pct(1, 0) > 0


def test_effective_strength_derives_from_value_and_confidence() -> None:
    weak = effective_strength(importance=0.1, confidence=0.2, access_count=0)
    strong = effective_strength(importance=0.9, confidence=0.9, access_count=8)
    assert strong > weak
    assert effective_strength(base_strength=1.0) >= 0.5


def test_decay_curve_has_natural_and_reviewed_series() -> None:
    data = curve_points(strength_days=7.0)
    assert data["points"][:4] == [0, 1, 2, 4]
    assert data["natural"][0]["retention"] == 120.0
    # 复习曲线在任意采样点都不低于自然曲线
    for natural, reviewed in zip(data["natural"], data["reviewed"]):
        assert reviewed["retention"] >= natural["retention"]
    assert data["threshold_pct"] == 20.0


def test_tier_classification_rules() -> None:
    assert classify_tier({"importance": 0.9, "status": "active", "access_count": 0}) == "core"
    assert classify_tier({"importance": 0.2, "status": "active"}) == "archive"
    assert classify_tier({"importance": 0.5, "status": "archived"}) == "archive"
    assert classify_tier({"importance": 0.5, "status": "active", "confidence": 0.9, "kind": "preference"}) == "recall"
    assert classify_tier({"importance": 0.65, "status": "active", "confidence": 0.9, "kind": "preference"}) == "core"
    assert classify_tier({"importance": 0.5, "status": "active", "access_count": 5}) == "core"


def test_empathy_identify_understand_and_guidance() -> None:
    hit = cogemp.identify("今天真的太难过了，论文答辩没过")
    assert hit is not None and hit.emotion == "难过" and hit.polarity == "negative"
    assert hit.intensity >= 0.55

    # 否定前缀不反向推断（保守处理）
    assert cogemp.identify("不开心") is None or cogemp.identify("不开心").emotion != "开心"

    plan = cogemp.plan("论文答辩没过，好焦虑，怕来不及")
    assert plan is not None and plan.applied
    assert "学业" in plan.causes
    assert "共情指引" in plan.guidance
    assert "强度" in plan.guidance

    # 强度门槛以下不注入，但保留计划（便于留痕）
    weak = cogemp.plan("还行吧", min_intensity=0.9)
    assert weak is None or (not weak.applied and weak.skipped_reason)

    # 阶段三关闭 ⇒ 不产出指引
    masked = cogemp.plan("好难过", stages=(cogemp.STAGE_IDENTIFY, cogemp.STAGE_UNDERSTAND))
    assert masked is not None and masked.guidance == ""

    # 温度分档
    low = cogemp.build_guidance(hit, ["学业"], temperature=0.1)
    high = cogemp.build_guidance(hit, ["学业"], temperature=0.9)
    assert "克制陪伴" in low and "深度陪伴" in high


def test_experience_classification_and_delta_guards() -> None:
    verdict = classify_experience("项目终于上线了，太爽了哈哈")
    assert verdict.experience in {"success", "humor"}
    assert verdict.confidence >= 0.45

    # 失谐：一轮里正负经验同时高置信 ⇒ 触发自我反思标记
    dissonance = classify_experience("赢了但也失去了朋友，难受")
    assert dissonance.dissonance or dissonance.experience in {"success", "loss"}

    deltas = compute_deltas(verdict)
    assert deltas, "经验应折算为至少一个轴增量"
    for axis, value in deltas.items():
        assert axis in BIG_FIVE_AXES
        assert abs(value) <= MAX_TRAIT_DELTA_PER_EXCHANGE + 1e-9

    # 里程碑上限更宽，但仍有上限
    bigger = compute_deltas(verdict, milestone=True)
    for value in bigger.values():
        assert abs(value) <= 0.10 + 1e-9

    assert classify_experience("", "").experience == "neutral"


def test_forge_model_validation_and_merge() -> None:
    profile = default_profile()
    text = profile.to_profile_text()
    assert "人格内核 · 三层建模" in text
    assert "① 核心特质" in text and "② 表层风格" in text and "③ 当前状态" in text

    # 越界数值被钳制；未知枚举回退默认
    merged = profile.merge(
        {
            "core_traits": {"big_five": {"openness": 5, "agreeableness": -3}, "defense_mechanism": "不存在"},
            "speaking_style": {"sentence_length": "weird"},
            "dynamic_state": {"energy_level": 999, "relationship_map": {"u1": {"intimacy": 30}}},
            "interests": ["猫", "技术"],
        }
    )
    assert merged.core_traits.big_five["openness"] == 1.0
    assert merged.core_traits.big_five["agreeableness"] == 0.0
    assert merged.core_traits.defense_mechanism == "Humor"
    assert merged.speaking_style.sentence_length == "short"
    assert merged.dynamic_state.energy_level == 100
    assert merged.dynamic_state.relationship_map["u1"].intimacy == 30
    assert merged.interests == ["猫", "技术"]

    # 关系映射是增量补丁：不覆盖未提交的对象
    again = merged.merge({"dynamic_state": {"relationship_map": {"u2": {"intimacy": 10}}}})
    assert set(again.dynamic_state.relationship_map) == {"u1", "u2"}

    roundtrip = PersonalityProfile.from_dict(merged.to_dict())
    assert roundtrip.core_traits.big_five == merged.core_traits.big_five
    assert roundtrip.to_dict() == merged.to_dict()


# --------------------------------------------------------------------------- #
# 2. 服务行为（真实 SQLite）                                                #
# --------------------------------------------------------------------------- #


def test_forge_service_persists_and_applies_capped_shift(db: Database) -> None:
    async def _scenario() -> tuple[dict[str, Any], dict[str, float], dict[str, Any]]:
        service = ForgeService(db=db, clock=lambda: 1_000_000.0)
        before = await service.snapshot()
        await service.update({"core_traits": {"mbti": "ENFP-T"}})
        applied = await service.apply_shift(
            {"openness": 0.5, "agreeableness": -0.9, "bogus": 0.4},
            experience="discovery",
            summary="读了一篇长文",
            confidence=0.8,
        )
        reloaded = ForgeService(db=db, clock=lambda: 1_000_000.0)
        after = await reloaded.snapshot()
        return before["profile"], applied, after["profile"]

    before, applied, after = run_async(_scenario())
    assert before["core_traits"]["mbti"] == "INFJ-T"
    # 钳制到 [0,1]：openness 0.72 + 0.5 → 1.0（实际 +0.28）；agreeableness 0.68 - 0.9 → 0.0（实际 -0.68）
    assert applied["openness"] == pytest.approx(0.28, abs=1e-6)
    assert applied["agreeableness"] == pytest.approx(-0.68, abs=1e-6)
    assert "bogus" not in applied
    assert after["core_traits"]["mbti"] == "ENFP-T"
    assert after["core_traits"]["big_five"]["openness"] == 1.0


def test_forge_touch_advances_energy_and_relationship(db: Database) -> None:
    async def _scenario() -> tuple[int, int, float]:
        now = [1_000_000.0]
        service = ForgeService(db=db, clock=lambda: now[0])
        await service.touch(_event("晚上好"))
        first = (await service.snapshot())["profile"]["dynamic_state"]
        # 三天后再互动：能量先向基线回归再恢复
        now[0] += 3 * 86400.0
        await service.touch(_event("在吗"))
        second = (await service.snapshot())["profile"]["dynamic_state"]
        intimacy = second["relationship_map"]["3186805099"]["intimacy"]
        return first["energy_level"], second["energy_level"], intimacy

    first_energy, second_energy, intimacy = run_async(_scenario())
    assert first_energy == 62  # 60 + 2
    assert 0 <= second_energy <= 100
    assert intimacy == pytest.approx(2.4, abs=1e-6)


def test_forge_introspection_is_off_by_default(db: Database) -> None:
    async def _scenario() -> tuple[bool, str]:
        service = ForgeService(db=db, llm=None, introspection=False)
        should, reason = await service.should_introspect(_event("我很难过"))
        monologue = await service.introspect(_event("我很难过"))
        return should, monologue

    should, monologue = run_async(_scenario())
    assert should is False and monologue == ""


def test_evolution_observe_writes_timeline_and_series(db: Database) -> None:
    async def _scenario() -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        forge = ForgeService(db=db, clock=lambda: 1_000_000.0)
        service = TraitEvolutionService(db=db, forge=forge, clock=lambda: 1_000_000.0)
        result = await service.observe(_event("项目终于上线了，太爽了哈哈"), "恭喜！")
        timeline = await service.timeline(limit=10)
        drift = await service.drift_series(days=30)
        radar = await service.radar()
        return result, timeline, drift, radar

    result, timeline, drift, radar = run_async(_scenario())
    assert result is not None and result["deltas"]
    assert timeline and timeline[0]["kind"] in {"shift", "milestone"}
    assert drift["days"], "漂移曲线应至少一天"
    assert len(radar["values"]) == 9 and radar["axes"] == list(radar["labels"])
    assert 0.0 <= radar["values"]["emotional_stability"] <= 1.0


def test_worldbook_match_scope_and_budget(db: Database) -> None:
    async def _scenario() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
        injector = _CollectInjector()
        service = WorldbookService(db=db, injector=injector, max_injected_chars=60)
        await service.add({"triggers": "实验室", "content": "某校 AI 实验室，阿柯所在", "priority": 9})
        await service.add({"triggers": ["猫舍"], "content": "小满家三只布偶猫", "priority": 3})
        await service.add({"triggers": ["私密"], "content": "只有阿柯能看到", "scope_type": "user", "scope_id": "u-x"})
        await service.add({"content": "手动条目不自动注入"})

        hits = await service.match(_event("今天在实验室待了一天"))
        injected = await service.inject(_event("实验室好远"), SimpleNamespace())
        other = await service.match(_event("私密"))
        return hits, other, injected

    hits, other, injected = run_async(_scenario())
    assert [item["triggers"][0] for item in hits] == ["实验室"]
    # 作用域过滤：user 作用域条目对别的发送者不命中
    assert other == []
    assert injected["applied"] is True and injected["ids"]



def test_worldbook_scope_user_matches_owner(db: Database) -> None:
    async def _scenario() -> list[dict[str, Any]]:
        service = WorldbookService(db=db)
        await service.add(
            {"triggers": ["私密"], "content": "只有阿柯能看到", "scope_type": "user", "scope_id": "3186805099"}
        )
        return await service.match(_event("说个私密的事"))

    assert len(run_async(_scenario())) == 1


def test_members_strategy_roster_and_injection(db: Database) -> None:
    async def _scenario() -> tuple[dict[str, Any], dict[str, Any], str]:
        now = 1_000_000.0
        await db.execute(
            "INSERT INTO identity_seen(umo, platform, sender_id, sender_name, scope_type, scope_id,"
            " user_key, first_seen, last_seen, events) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (UMO, "aiocqhttp", "3186805099", "阿柯", "user", "3186805099", "k", now, now, 5),
        )
        await db.execute(
            "INSERT INTO memories(scope_type, scope_id, kind, content, importance, confidence, source,"
            " tags, created_at, updated_at, last_access_at, access_count, status, sender_id, sender_name, origin_umo)"
            " VALUES ('user','3186805099','fact','阿柯在准备考研',0.8,0.9,'capture','[]',?,?,0,0,'active','3186805099','阿柯',?)",
            (now, now, UMO),
        )
        await db.execute(
            "INSERT INTO affinity_state(scope_type, scope_id, target_id, score, mood, interactions,"
            " last_interaction, updated_at) VALUES ('session',?,?,?,?,?,?,?)",
            (UMO, "3186805099", 0.82, "愉快", 12, now, now),
        )
        service = MembersService(config=MembersConfig(), db=db, clock=lambda: now)
        await service.save_strategy(
            "3186805099",
            {
                "stable_name": "阿柯",
                "relation": "挚友/互怼",
                "tone": "毒舌互怼",
                "address_as": "阿柯",
                "topics": ["游戏", "考研"],
                "taboo": ["煽情"],
            },
        )
        roster = await service.roster()
        strategy = await service.strategy("3186805099")
        block = await service.inject_block(_event("今天好累"))
        return roster, strategy, block

    roster, strategy, block = run_async(_scenario())
    assert roster["members"], "应聚合出一位群友"
    member = roster["members"][0]
    assert member["stable_name"] == "阿柯"
    assert member["stability"] == "stable"
    assert member["affinity"] == pytest.approx(0.82)
    assert member["memory_count"] == 1
    assert "阿柯" in (member["memory_summary"] or "")
    assert roster["stability"]["verdict"] == "stable"
    assert strategy is not None and strategy["topics"] == ["游戏", "考研"]
    assert "【群友档案】" in block and "称呼「阿柯」" in block and "底层人格不变" in block

    async def _delete() -> dict[str, Any]:
        service = MembersService(config=MembersConfig(), db=db)
        return await service.delete_strategy("3186805099")

    assert run_async(_delete())["deleted"] == 1


def test_members_distill_prefers_offline_artifact(db: Database, tmp_path: Path) -> None:
    distilly_dir = tmp_path / "distilly"
    distilly_dir.mkdir(parents=True, exist_ok=True)
    (distilly_dir / "u-9.json").write_text(
        '{"stable_name": "小满", "tone": "温柔倾听", "topics": ["猫"], "notes": "离线蒸馏产物"}',
        encoding="utf-8",
    )

    async def _scenario() -> dict[str, Any]:
        service = MembersService(config=MembersConfig(), db=db, data_dir=tmp_path)
        return await service.distill("u-9")

    result = run_async(_scenario())
    assert result["ok"] and result["origin"] == "distilly:offline"


def test_tkg_ingest_snapshot_provenance_and_expand(db: Database) -> None:
    class _Extractor:
        def extract(self, content: str) -> list[str]:
            return [token for token in ("阿柯", "考研", "论文", "猫") if token in content]

    async def _scenario() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        service = TemporalGraphService(db=db, extractor=_Extractor(), clock=lambda: 1_000_000.0)
        await service.ingest_memory(
            memory_id=1, content="阿柯在准备考研", scope_type="user", scope_id="3186805099",
            sender_id="3186805099", created_at=1_000_000.0,
        )
        await service.ingest_memory(
            memory_id=2, content="阿柯的论文和考研冲突", scope_type="user", scope_id="3186805099",
            sender_id="3186805099", created_at=1_000_100.0,
        )
        # 重复入库不应重复计数（重建幂等）
        await service.ingest_memory(
            memory_id=1, content="阿柯在准备考研", scope_type="user", scope_id="3186805099",
            sender_id="3186805099", created_at=1_000_000.0,
        )
        snapshot = await service.snapshot()
        provenance = await service.provenance(memory_ids=[1])
        expand = await service.expand(memory_ids=[1])
        timeline = await service.timeline(limit=5)
        stats = await service.stats()
        return snapshot, provenance, expand, timeline, stats

    snapshot, provenance, expand, timeline, stats = run_async(_scenario())
    assert len(snapshot["nodes"]) == 3
    assert snapshot["edges"], "共现边应被建立"
    assert stats["nodes"] == 3 and stats["senders"] == 1
    assert provenance[1]["entities"], "证据链应包含实体"
    assert provenance[1]["chains"][0]["evidence"].startswith("memory:1")
    assert 2 in expand["extra_ids"], "图上邻居应扩展出另一条记忆"
    assert timeline and timeline[0]["valid_from"] == 1_000_100.0


def test_decay_overview_flags_old_memories(db: Database) -> None:
    now = 2_000_000.0

    async def _scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        # 一条高重要度但很久没被访问；一条刚写入
        await db.execute(
            "INSERT INTO memories(scope_type, scope_id, kind, content, importance, confidence, source, tags,"
            " created_at, updated_at, last_access_at, access_count, status) VALUES"
            " ('global','global','fact','很久以前的旧事',0.3,0.5,'capture','[]',?,?,?,0,'active')",
            (now - 120 * 86400, now - 120 * 86400, now - 120 * 86400),
        )
        await db.execute(
            "INSERT INTO memories(scope_type, scope_id, kind, content, importance, confidence, source, tags,"
            " created_at, updated_at, last_access_at, access_count, status) VALUES"
            " ('global','global','fact','刚刚发生的事',0.9,0.9,'capture','[]',?,?,0,9,'active')",
            (now, now),
        )
        service = DecayService(db=db, clock=lambda: now, base_strength_days=7.0)
        return await service.overview(limit=5), await service.stats()

    overview, stats = run_async(_scenario())
    assert overview["total"] == 2
    assert overview["below_threshold"] >= 1
    assert overview["samples"][0]["retention"] <= overview["samples"][-1]["retention"]
    assert stats["memories"] == 2


def test_decay_write_back_is_gated(db: Database) -> None:
    async def _scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        off = DecayService(db=db, write_back=False)
        on = DecayService(db=db, write_back=True)
        return await off.apply_retention(), await on.apply_retention()

    blocked, allowed = run_async(_scenario())
    assert blocked["ok"] is False and "未开启" in blocked["message"]
    assert allowed["ok"] is True


def test_tiers_overview_counts(db: Database) -> None:
    async def _scenario() -> dict[str, Any]:
        for importance, status, access in ((0.9, "active", 1), (0.5, "active", 0), (0.1, "active", 0)):
            await db.execute(
                "INSERT INTO memories(scope_type, scope_id, kind, content, importance, confidence, source, tags,"
                " created_at, updated_at, last_access_at, access_count, status) VALUES"
                " ('global','global','fact','x',?,0.8,'capture','[]',1,1,0,?,?)",
                (importance, access, status),
            )
        return await TierService(db=db).overview(samples=3)

    data = run_async(_scenario())
    counts = {item["tier"]: item["count"] for item in data["tiers"]}
    assert counts == {"core": 1, "recall": 1, "archive": 1}
    assert data["total"] == 3


def test_callback_queue_lifecycle(db: Database) -> None:
    async def _scenario() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        now = [1_000_000.0]
        service = CallbackQueueService(db=db, clock=lambda: now[0])
        added = await service.add({"umo": UMO, "content": "答辩完问问进展", "due_in_hours": 1})
        assert added["ok"]
        before = await service.due()
        assert before == [], "未到期不应出队"
        now[0] += 3601
        after = await service.due(now=now[0])
        await service.mark(after[0]["id"], "sent")
        listed = await service.list_items(status="sent", limit=10)
        stats = await service.stats()
        return added, after, listed, stats

    added, due_items, listed, stats = run_async(_scenario())
    assert due_items and due_items[0]["content"] == "答辩完问问进展"
    assert listed["items"] and listed["items"][0]["status_label"] == "已发送"
    assert stats["sent"] == 1 and stats["pending"] == 0


def test_fusion_status_aggregates_modules(db: Database) -> None:
    async def _scenario() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        members = MembersService(config=MembersConfig(), db=db)
        queue = CallbackQueueService(db=db)
        decay = DecayService(db=db)
        tiers = TierService(db=db)
        worldbook = WorldbookService(db=db)
        forge = ForgeService(db=db)
        status = FusionStatusService(
            services={
                "members": members,
                "forge": forge,
                "worldbook": worldbook,
                "decay": decay,
                "tiers": tiers,
                "queue": queue,
            },
            capabilities=lambda: {
                "members.enabled": True,
                "forge.enabled": True,
                "worldbook.enabled": True,
                "tiers.enabled": True,
                "latrace.enabled": False,
                "fusion.decay": False,
                "empathy.enabled": False,
                "evolution.enabled": False,
            },
            db=db,
        )
        return await status.backends(), await status.pipeline(), await status.health()

    backends, pipeline, health = run_async(_scenario())
    keys = [item["key"] for item in backends["backends"]]
    assert keys == ["local", "latrace", "tiers", "decay"]
    assert backends["active_backend"] == "local"
    assert len(pipeline["stages"]) == 8
    assert pipeline["stages"][0]["index"] == "①"
    assert {item["status"] for item in health["items"]} <= {"online", "degraded", "off"}
    assert health["summary"]["off"] >= 1  # 未启用的能力应显式为 off，而不是假装在线


# --------------------------------------------------------------------------- #
# 3. 端到端：启动整个应用                                                  #
# --------------------------------------------------------------------------- #


def test_app_startup_assembles_fusion_services(tmp_path: Path) -> None:
    from super_astrbot.app import SuperAstrBotApp

    async def _scenario() -> dict[str, Any]:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            assert app.ready is True
            services = {
                "members": app.members_service,
                "forge": app.forge_service,
                "evolution": app.evolution_service,
                "empathy": app.empathy_service,
                "worldbook": app.worldbook_service,
                "tkg": app.tkg_service,
                "decay": app.decay_service,
                "tiers": app.tiers_service,
                "queue": app.queue_service,
                "fusion": app.fusion_status,
            }
            status = await app.status()
            return {
                "assembled": {key: value is not None for key, value in services.items()},
                "fusion_health": (status.get("fusion") or {}).get("summary") or {},
                "members_stats": status.get("members") or {},
            }
        finally:
            await app.shutdown()

    result = run_async(_scenario())
    assert all(result["assembled"].values()), result["assembled"]
    assert "online" in result["fusion_health"]


def test_llm_request_injects_worldbook_empathy_and_persona(tmp_path: Path) -> None:
    """一条消息走完整注入链：世界书（事实）+ 共情（语气）+ 三层人格（我是谁）。"""
    from super_astrbot.app import SuperAstrBotApp

    async def _scenario() -> tuple[str, bool]:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            await app.worldbook_service.add(
                {"triggers": ["实验室"], "content": "某校 AI 实验室，阿柯所在", "priority": 9}
            )
            request = SimpleNamespace(
                prompt="今天在实验室待到很晚，论文答辩没过，好难过",
                system_prompt="基础提示",
                extra_user_content_parts=[],
            )
            from .test_app_integration import FakeEvent

            await app.on_llm_request(FakeEvent("今天在实验室待到很晚，论文答辩没过，好难过"), request)
            blob = "\n".join(str(part) for part in request.extra_user_content_parts)
            if not blob.strip():
                blob = str(getattr(request, "system_prompt", ""))
            return blob, True
        finally:
            await app.shutdown()

    blob, _ = run_async(_scenario())
    assert "【世界书】" in blob, blob
    assert "【共情指引】" in blob, blob
    assert "【人格内核 · 三层建模】" in blob, blob


def test_new_panel_routes_are_served(tmp_path: Path) -> None:
    """逐个调用新增面板路由：页面上的每个按钮都必须有后端承接。"""
    from super_astrbot.app import SuperAstrBotApp

    async def _scenario() -> dict[str, Any]:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        results: dict[str, Any] = {}
        try:
            _use_request(query={})
            forge = _data(await web_api._forge(app)())
            results["forge"] = bool(forge.get("profile"))

            _use_request(body={"core_traits": {"mbti": "ENFP-T"}}, method="POST")
            updated = _data(await web_api._forge_update(app)())
            results["forge_update"] = updated.get("snapshot", {}).get("profile", {}).get("core_traits", {}).get("mbti")

            _use_request(query={})
            results["evolution"] = "radar" in _data(await web_api._evolution(app)())

            _use_request(query={})
            results["members"] = "members" in _data(await web_api._members(app)())

            _use_request(body={"action": "save", "sender_id": "u-1", "tone": "温柔倾听", "topics": ["猫"]}, method="POST")
            saved = _data(await web_api._member_strategy(app)())
            results["strategy_save"] = saved.get("ok")

            _use_request(query={"sender_id": "u-1"})
            loaded = _data(await web_api._member_strategy(app)())
            results["strategy_get"] = (loaded.get("strategy") or {}).get("tone")

            _use_request(body={"triggers": ["实验室"], "content": "某校 AI 实验室"}, method="POST")
            added = _data(await web_api._worldbook_add(app)())
            entry_id = added.get("id")
            _use_request(query={})
            results["worldbook"] = len(_data(await web_api._worldbook(app)()).get("items") or [])
            _use_request(body={"id": entry_id, "content": "某校 AI 实验室（已更新）"}, method="POST")
            results["worldbook_update"] = _data(await web_api._worldbook_update(app)()).get("ok")
            _use_request(body={"id": entry_id}, method="POST")
            results["worldbook_del"] = _data(await web_api._worldbook_del(app)()).get("ok")

            _use_request(query={})
            results["empathy"] = "config" in _data(await web_api._empathy(app)())
            _use_request(query={})
            results["empathy_log"] = "items" in _data(await web_api._empathy_log(app)())

            _use_request(body={"action": "add", "umo": UMO, "content": "记得问进展", "due_in_hours": 2}, method="POST")
            queued = _data(await web_api._proactive_queue(app)())
            results["queue_add"] = queued.get("ok")
            _use_request(query={})
            results["queue_list"] = ("items" in _data(await web_api._proactive_queue(app)()))
            _use_request(query={})
            results["schedule"] = "config" in _data(await web_api._proactive_schedule(app)())
            _use_request(query={})
            results["queue_log"] = "items" in _data(await web_api._proactive_log(app)())

            _use_request(query={})
            results["backends"] = len(_data(await web_api._memory_backends(app)()).get("backends") or [])
            _use_request(query={})
            results["tiers"] = len(_data(await web_api._memory_tiers(app)()).get("tiers") or [])
            _use_request(query={})
            results["decay"] = "curves" in _data(await web_api._memory_decay(app)())

            _use_request(body={"query": "论文", "umo": UMO}, method="POST")
            recall = _data(await web_api._memory_recall(app)())
            results["recall"] = "items" in recall

            _use_request(body={"limit": 50}, method="POST")
            rebuilt = _data(await web_api._graph_rebuild(app)())
            results["graph_rebuild"] = rebuilt.get("ok")
            _use_request(query={})
            results["graph_timeline"] = "items" in _data(await web_api._graph_timeline(app)())

            _use_request(query={})
            results["pipeline"] = len(_data(await web_api._fusion_pipeline(app)()).get("stages") or [])
            _use_request(query={})
            results["fusion_health"] = len(_data(await web_api._fusion_health(app)()).get("items") or [])
            _use_request(query={})
            results["group_context"] = "enabled" in _data(await web_api._group_context(app)())

            _use_request(query={})
            results["facets"] = "senders" in _data(await web_api._memory_facets(app)())
        finally:
            await app.shutdown()
        return results

    results = run_async(_scenario())
    assert results["forge"] is True
    assert results["forge_update"] == "ENFP-T"
    assert results["evolution"] is True
    assert results["members"] is True
    assert results["strategy_save"] is True
    assert results["strategy_get"] == "温柔倾听"
    assert results["worldbook"] == 1
    assert results["worldbook_update"] is True
    assert results["worldbook_del"] is True
    assert results["empathy"] is True and results["empathy_log"] is True
    assert results["queue_add"] is True and results["queue_list"] is True
    assert results["schedule"] is True and results["queue_log"] is True
    assert results["backends"] == 4
    assert results["tiers"] == 3
    assert results["decay"] is True
    assert results["recall"] is True
    assert results["graph_rebuild"] is True
    assert results["graph_timeline"] is True
    assert results["pipeline"] == 8
    assert results["fusion_health"] >= 9
    assert results["group_context"] is True
    assert results["facets"] is True


def test_legacy_routes_still_served(tmp_path: Path) -> None:
    """兼容别名：老面板 / 脚本调用的旧路由必须继续可用（原项目零丢失）。"""
    from super_astrbot.app import SuperAstrBotApp

    async def _scenario() -> dict[str, Any]:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            _use_request(query={})
            identities = _data(await web_api._identities(app)())
            _use_request(body={"query": "x", "limit": 3})
            search = _data(await web_api._search(app)())
            _use_request(query={})
            memories = _data(await web_api._memories(app)())
            _use_request(query={})
            observed = _data(await web_api._identity_observe(app)())
            return {
                "identities": "observations" in identities or "rows" in identities or isinstance(identities, dict),
                "search": "items" in search,
                "memories": "items" in memories,
                "identity_observe": isinstance(observed, dict),
            }
        finally:
            await app.shutdown()

    result = run_async(_scenario())
    assert all(result.values()), result


def test_shutdown_leaves_no_services(tmp_path: Path) -> None:
    from super_astrbot.app import SuperAstrBotApp

    async def _scenario() -> dict[str, Any]:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        await app.shutdown()
        return {
            "members": app.members_service,
            "forge": app.forge_service,
            "queue": app.queue_service,
            "fusion": app.fusion_status,
            "ready": app.ready,
        }

    result = run_async(_scenario())
    assert all(value is None for key, value in result.items() if key != "ready")
    assert result["ready"] is False

def test_decay_write_back_requires_capability(tmp_path: Path) -> None:
    """写回重要度受「艾宾浩斯衰减接管」能力双重约束：能力关 → 设置开了也不写。"""
    from super_astrbot.app import SuperAstrBotApp

    async def _scenario() -> tuple[bool, bool, bool]:
        app = SuperAstrBotApp(
            star=FakeStar(), context=FakeContext(), config=_schema_config(), data_dir=tmp_path
        )
        await app.start()
        try:
            default_state = app.decay_service.write_back
            await app.set_capability("fusion.decay", True)
            await app.set_feature_setting("fusion.decay_write_back", True)
            enabled = app.decay_service.write_back
            await app.set_capability("fusion.decay", False)
            disabled = app.decay_service.write_back
            return default_state, enabled, disabled
        finally:
            await app.shutdown()

    default_state, enabled, disabled = run_async(_scenario())
    assert default_state is False
    assert enabled is True, "能力开启 + 写回设置开启后应生效"
    assert disabled is False, "能力关闭后写回必须随之失效"
