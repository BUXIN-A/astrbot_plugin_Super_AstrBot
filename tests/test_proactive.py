"""主动交互测试：配置、双轨守卫、竞态保护与免打扰。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from super_astrbot.harness.protocols import LlmResult
from super_astrbot.loop import MemoryStateStore
from super_astrbot.proactive import (
    TRACK_DAILY,
    TRACK_IDLE,
    MemoryMaterialSource,
    ProactiveConfig,
    ProactiveMaterial,
    ProactiveService,
    parse_daily_time,
)
from super_astrbot.proactive.service import _clean_text
from super_astrbot.spec.errors import LlmError

UMO = "aiocqhttp:FriendMessage:10086"


class FakeClock:
    """可手动推进的时钟；默认取「本地当天 12:00」，避免受时区与免打扰时段影响。"""

    def __init__(self, now: float | None = None) -> None:
        self.now = _local_noon() if now is None else now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _local_noon() -> float:
    local = time.localtime()
    return float(time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 12, 0, 0, 0, 0, -1)))


@dataclass
class FakeHost:
    ok: bool = True
    sent: list[tuple[str, str]] = field(default_factory=list)

    async def send_message(self, umo: str, text: str) -> bool:
        if self.ok:
            self.sent.append((umo, text))
        return self.ok


@dataclass
class FakeLlm:
    text: str = "早上好呀，今天有空吗？"
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    on_chat: Any = None

    async def chat(self, **kwargs: Any) -> LlmResult:
        self.calls.append(kwargs)
        if self.on_chat is not None:
            self.on_chat()
        if self.error is not None:
            raise self.error
        return LlmResult(text=self.text)


@dataclass
class FakeMaterials:
    material: ProactiveMaterial = ProactiveMaterial(memories=("用户喜欢在周末爬山",))
    calls: int = 0

    async def collect(self, umo: str, *, memories: int, journals: int) -> ProactiveMaterial:
        self.calls += 1
        return self.material


def _service(
    *,
    config: ProactiveConfig,
    host: FakeHost | None = None,
    llm: FakeLlm | None = None,
    materials: FakeMaterials | None = None,
    store: MemoryStateStore | None = None,
    clock: FakeClock | None = None,
) -> tuple[ProactiveService, FakeHost, FakeLlm, FakeMaterials, MemoryStateStore]:
    host = host or FakeHost()
    llm = llm or FakeLlm()
    materials = materials or FakeMaterials()
    store = store or MemoryStateStore()
    service = ProactiveService(
        config=config,
        host=host,
        llm=llm,
        materials=materials,
        store=store,
        clock=clock or FakeClock(),
    )
    return service, host, llm, materials, store


def _config(**kwargs: Any) -> ProactiveConfig:
    base: dict[str, Any] = {
        "enabled": True,
        "targets": (UMO,),
        "daily_enabled": True,
        "daily_hour": 10,
        "daily_minute": 0,
        "idle_enabled": False,
        "busy_guard_minutes": 10,
        "daily_max": 1,
        "quiet_start": 0,
        "quiet_end": 0,
        "max_chars": 80,
    }
    base.update(kwargs)
    return ProactiveConfig(**base)


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #


def test_parse_daily_time_falls_back_on_invalid_input() -> None:
    assert parse_daily_time("7:05") == (7, 5)
    assert parse_daily_time("23:59") == (23, 59)
    assert parse_daily_time("25:00") == (10, 0)
    assert parse_daily_time("乱七八糟") == (10, 0)
    assert parse_daily_time(None) == (10, 0)


def test_config_clamps_and_parses_targets() -> None:
    config = ProactiveConfig.from_mapping(
        {
            "proactive": {
                "enabled": "yes",
                "targets": ["a:b:c", "d:e:f"],
                "idle_minutes": 1,
                "quiet_start": 30,
                "daily_time": "22:30",
            }
        }
    )
    assert config.enabled is True
    assert config.targets == ("a:b:c", "d:e:f")
    assert config.idle_minutes == 5
    assert config.quiet_start == 23
    assert (config.daily_hour, config.daily_minute) == (22, 30)


def test_quiet_hours_support_crossing_midnight() -> None:
    config = _config(quiet_start=23, quiet_end=8)
    assert config.in_quiet_hours(23) is True
    assert config.in_quiet_hours(3) is True
    assert config.in_quiet_hours(8) is False
    assert config.in_quiet_hours(12) is False

    disabled = _config(quiet_start=8, quiet_end=8)
    assert disabled.in_quiet_hours(8) is False


def test_material_render_and_empty() -> None:
    empty = ProactiveMaterial()
    assert empty.is_empty is True
    assert empty.render() == ""

    material = ProactiveMaterial(memories=("喜欢爬山",), journals=("上周去了黄山",))
    rendered = material.render()
    assert "长期记忆" in rendered
    assert "现实周记" in rendered
    assert "喜欢爬山" in rendered


def test_material_labels_speakers_in_recent_dialogue() -> None:
    """对话缓冲里 Bot 的发言以「我：」记录，素材标题必须说明「我」是谁。"""
    material = ProactiveMaterial(buffers=("用户(谷雨)：最近在爬山", "我：那挺好的"))

    rendered = material.render()

    assert "最近对话" in rendered
    assert "「我」" in rendered
    assert "用户(昵称)" in rendered
    assert "用户(谷雨)：最近在爬山" in rendered


# --------------------------------------------------------------------------- #
# 双轨守卫
# --------------------------------------------------------------------------- #


def test_daily_track_sends_message_and_records_quota() -> None:
    clock = FakeClock()
    service, host, llm, _, store = _service(config=_config(), clock=clock)
    service.note_activity(UMO, at=clock.now - 11 * 60)

    attempt = asyncio.run(service.send_one(UMO, kind=TRACK_DAILY))
    assert attempt.sent is True
    assert host.sent and host.sent[0][0] == UMO
    assert llm.calls[0]["purpose"] == "proactive"
    assert service.snapshot()["stats"]["sent"] == 1

    key = ProactiveService._count_key(UMO, clock.now)
    assert asyncio.run(store.get(key)) == 1


def test_daily_max_blocks_second_send() -> None:
    clock = FakeClock()
    service, host, _, _, _ = _service(config=_config(), clock=clock)
    service.note_activity(UMO, at=clock.now - 600)

    assert asyncio.run(service.send_one(UMO, kind=TRACK_DAILY)).sent is True
    clock.advance(60)
    second = asyncio.run(service.send_one(UMO, kind=TRACK_DAILY))
    assert second.sent is False
    assert second.reason == "今日已达上限"
    assert len(host.sent) == 1


def test_busy_guard_skips_when_recently_active() -> None:
    clock = FakeClock()
    service, _, _, _, _ = _service(config=_config(), clock=clock)
    service.note_activity(UMO)

    attempt = asyncio.run(service.send_one(UMO, kind=TRACK_DAILY))
    assert attempt.sent is False
    assert attempt.reason == "会话正在活跃"

    clock.advance(11 * 60)
    assert asyncio.run(service.send_one(UMO, kind=TRACK_DAILY)).sent is True


def test_idle_track_requires_longer_silence() -> None:
    clock = FakeClock()
    config = _config(idle_enabled=True, idle_minutes=30, busy_guard_minutes=5)
    service, _, _, _, _ = _service(config=config, clock=clock)
    service.note_activity(UMO)
    clock.advance(10 * 60)

    attempt = asyncio.run(service.send_one(UMO, kind=TRACK_IDLE))
    assert attempt.sent is False
    assert attempt.reason == "会话未达到静默时长"

    clock.advance(25 * 60)
    assert asyncio.run(service.send_one(UMO, kind=TRACK_IDLE)).sent is True


def test_track_disabled_is_not_run() -> None:
    service, host, _, materials, _ = _service(config=_config(idle_enabled=False))
    assert asyncio.run(service.run_track(TRACK_IDLE)) == []
    assert host.sent == []
    assert materials.calls == 0


def test_run_track_returns_empty_when_capability_off() -> None:
    service, _, _, _, _ = _service(config=_config(enabled=False))
    assert asyncio.run(service.run_track(TRACK_DAILY)) == []


# --------------------------------------------------------------------------- #
# 免打扰与暂停
# --------------------------------------------------------------------------- #


def test_quiet_hours_block_sending() -> None:
    clock = FakeClock()
    hour = time.localtime(clock.now).tm_hour
    service, host, _, _, _ = _service(
        config=_config(quiet_start=hour, quiet_end=(hour + 1) % 24), clock=clock
    )
    service.note_activity(UMO, at=clock.now - 3600)

    attempt = asyncio.run(service.send_one(UMO, kind=TRACK_DAILY))
    assert attempt.sent is False
    assert attempt.reason == "免打扰时段"
    assert host.sent == []


def test_pause_blocks_and_persists() -> None:
    clock = FakeClock()
    store = MemoryStateStore()
    service, host, _, _, _ = _service(config=_config(), store=store, clock=clock)
    service.note_activity(UMO, at=clock.now - 3600)

    asyncio.run(service.set_paused(UMO, True))
    blocked = asyncio.run(service.send_one(UMO, kind=TRACK_DAILY))
    assert blocked.sent is False
    assert blocked.reason == "已被手动暂停"
    assert asyncio.run(store.get("proactive:paused:" + UMO)) is True

    # 新的服务实例应能从 KV 恢复暂停状态（跨重载一致）
    resumed_service, _, _, _, _ = _service(config=_config(), store=store, clock=clock)
    assert asyncio.run(resumed_service.is_paused(UMO)) is True

    asyncio.run(service.set_paused(UMO, False))
    clock.advance(60)
    service.note_activity(UMO, at=clock.now - 3600)
    assert asyncio.run(service.send_one(UMO, kind=TRACK_DAILY)).sent is True
    assert host.sent


# --------------------------------------------------------------------------- #
# 竞态保护与降级
# --------------------------------------------------------------------------- #


def test_activity_during_generation_cancels_send() -> None:
    clock = FakeClock()
    llm = FakeLlm()
    service = ProactiveService(
        config=_config(),
        host=FakeHost(),
        llm=llm,
        materials=FakeMaterials(),
        store=MemoryStateStore(),
        clock=clock,
    )
    # 模拟「生成期间用户开始说话」
    llm.on_chat = lambda: service.note_activity(UMO)
    service.note_activity(UMO, at=clock.now - 3600)

    attempt = asyncio.run(service.send_one(UMO, kind=TRACK_DAILY))
    assert attempt.sent is False
    assert attempt.reason == "生成期间会话已活跃"
    assert service.snapshot()["stats"]["sent"] == 0


def test_empty_material_skips_generation() -> None:
    clock = FakeClock()
    service, host, llm, _, _ = _service(
        config=_config(), materials=FakeMaterials(material=ProactiveMaterial()), clock=clock
    )
    service.note_activity(UMO, at=clock.now - 3600)

    attempt = asyncio.run(service.send_one(UMO, kind=TRACK_DAILY))
    assert attempt.sent is False
    assert attempt.reason == "无可用素材"
    assert llm.calls == []
    assert host.sent == []


def test_llm_failure_degrades_without_sending() -> None:
    clock = FakeClock()
    service, host, _, _, _ = _service(
        config=_config(), llm=FakeLlm(error=LlmError("模型不可用")), clock=clock
    )
    service.note_activity(UMO, at=clock.now - 3600)

    attempt = asyncio.run(service.send_one(UMO, kind=TRACK_DAILY))
    assert attempt.sent is False
    assert "生成失败" in attempt.reason
    assert host.sent == []
    assert service.snapshot()["stats"]["failed"] == 1


def test_send_failure_does_not_retry() -> None:
    clock = FakeClock()
    service, _, _, _, store = _service(config=_config(), host=FakeHost(ok=False), clock=clock)
    service.note_activity(UMO, at=clock.now - 3600)

    assert asyncio.run(service.send_one(UMO, kind=TRACK_DAILY)).sent is False
    assert service.snapshot()["stats"]["failed"] == 1
    # 配额已占用：即使失败也不补发，避免重复打扰
    clock.advance(60)
    service.note_activity(UMO, at=clock.now - 3600)
    assert asyncio.run(service.send_one(UMO, kind=TRACK_DAILY)).reason == "今日已达上限"


def test_missing_activity_falls_back_to_start_time() -> None:
    """没有活动记录时按「服务启动时刻」计算静默，避免重启后立刻打扰。"""
    clock = FakeClock()
    service, _, _, _, _ = _service(config=_config(), clock=clock)
    assert asyncio.run(service.send_one(UMO, kind=TRACK_DAILY)).sent is False

    clock.advance(11 * 60)
    assert asyncio.run(service.send_one(UMO, kind=TRACK_DAILY)).sent is True


def test_clean_text_strips_wrapping_and_self_reference() -> None:
    assert _clean_text("「早上好呀」", max_chars=20) == "早上好呀"
    assert _clean_text("主动：今天过得怎么样", max_chars=20) == "今天过得怎么样"
    assert _clean_text("  多行\n内容  ", max_chars=20) == "多行 内容"
    assert len(_clean_text("啊" * 100, max_chars=10)) <= 10


def test_session_snapshot_reports_pause_and_quota() -> None:
    clock = FakeClock()
    service, _, _, _, _ = _service(config=_config(), clock=clock)
    service.note_activity(UMO, at=clock.now - 120)
    snapshot = asyncio.run(service.session_snapshot(UMO))
    assert snapshot["paused"] is False
    assert snapshot["sent_today"] == 0
    assert snapshot["daily_max"] == 1
    assert snapshot["idle_minutes"] == 2.0


def test_material_source_reads_memory_journals_and_buffer() -> None:
    class Item:
        def __init__(self, content: str) -> None:
            self.content = content

    class FakeMemory:
        async def list_memories(self, scope: Any, *, limit: int) -> list[Item]:
            return [Item("记忆一")]

        async def list_journals(self, scope: Any, *, limit: int) -> list[dict[str, Any]]:
            return [{"content": "周记一"}]

        async def buffer_material(self, scope: Any, *, limit: int) -> list[Item]:
            return [Item("对话一")]

    source = MemoryMaterialSource(memory=FakeMemory())
    material = asyncio.run(source.collect(UMO, memories=5, journals=2))
    assert material.memories == ("记忆一",)
    assert material.journals == ("周记一",)
    assert material.buffers == ("对话一",)
