"""群聊语义测试：注意力评分、名单与配额、并发合并、决策落地。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from super_astrbot.group import GroupChatService, GroupConfig, score_message
from super_astrbot.harness import astrbot_compat as compat
from super_astrbot.harness.astrbot_group import (
    apply_group_decision,
    group_gate_open,
    release_group_gate,
    set_group_gate,
    to_group_signals,
)
from super_astrbot.harness.protocols import EventView, GroupDecision, GroupSignals

UMO = "aiocqhttp:GroupMessage:10086"
FILLER = "我们上次聊的那个部署方案" * 8


class FakeClock:
    """可手动推进的时钟。"""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeEvent:
    """落地逻辑所需的最小事件替身。"""

    def __init__(
        self,
        *,
        text: str = "",
        messages: list[Any] | None = None,
        self_id: str = "bot-1",
        at_command: bool = False,
    ) -> None:
        self.message_str = text
        self.is_at_or_wake_command = at_command
        self.is_wake = False
        self.stopped = False
        self._messages = messages or []
        self._self_id = self_id

    def get_self_id(self) -> str:
        return self._self_id

    def get_messages(self) -> list[Any]:
        return self._messages

    def stop_event(self) -> None:
        self.stopped = True


def _view(*, text: str, group: bool = True, sender: str = "u1") -> EventView:
    return EventView(
        umo=UMO,
        is_group=group,
        group_id="10086",
        sender_id=sender,
        sender_name="用户",
        text=text,
    )


def _service(
    *,
    clock: FakeClock | None = None,
    threshold: float = 0.55,
    cooldown: int = 90,
    max_per_hour: int = 6,
    merge_window: float = 0.0,
    **kwargs: Any,
) -> GroupChatService:
    config = GroupConfig(
        enabled=True,
        attention_threshold=threshold,
        cooldown_seconds=cooldown,
        max_per_hour=max_per_hour,
        merge_window_seconds=merge_window,
        **kwargs,
    )
    return GroupChatService(config=config, clock=clock or FakeClock())


# --------------------------------------------------------------------------- #
# 注意力评分
# --------------------------------------------------------------------------- #


def test_score_ignores_meaningless_text() -> None:
    assert score_message("？？？").value == 0.0
    assert score_message("   ").value == 0.0
    assert score_message("🙂🙂").value == 0.0


def test_score_rewards_questions_and_aliases() -> None:
    plain = score_message("今天天气不错")
    question = score_message("这个报错要怎么处理？")
    alias = score_message("小助手帮我看看这个报错")

    assert question.value > plain.value, "疑问句应当加分"
    assert alias.value > plain.value, "称呼 Bot 应当加分"
    assert plain.value < 0.55, "普通闲聊不应越过默认阈值"
    assert question.value >= 0.55, "明确的求助应当越过默认阈值"


def test_score_rewards_topic_continuation() -> None:
    text = "那部署方案什么时候上线"
    unrelated = score_message(text, recent_texts=["今天午饭吃什么"])
    related = score_message(text, recent_texts=[FILLER])
    assert related.value > unrelated.value


def test_score_penalizes_burst_and_extremes() -> None:
    base = score_message("我们继续聊部署方案")
    burst = score_message("我们继续聊部署方案", burst=True)
    assert burst.value < base.value

    tiny = score_message("嗯", burst=False)
    assert "消息过短" in tiny.reasons or tiny.value < base.value

    huge = score_message("部署" * 300)
    assert "消息过长" in huge.reasons


def test_score_is_bounded() -> None:
    score = score_message(
        "小助手，这个部署方案为什么会失败？",
        aliases=("小助手",),
        recent_texts=[FILLER],
    )
    assert 0.0 <= score.value <= 1.0


# --------------------------------------------------------------------------- #
# 配置：钳制与名单
# --------------------------------------------------------------------------- #


def test_group_config_clamps_and_parses_lists() -> None:
    config = GroupConfig.from_mapping(
        {
            "group": {
                "enabled": "true",
                "attention_threshold": 5,
                "cooldown_seconds": -10,
                "merge_window_seconds": 99,
                "bot_aliases": "小助手，bot",
                "whitelist": ["10086"],
            }
        }
    )
    assert config.enabled is True
    assert config.attention_threshold == 1.0
    assert config.cooldown_seconds == 0
    assert config.merge_window_seconds == 10.0
    assert config.bot_aliases == ("小助手", "bot")
    assert config.whitelist == ("10086",)


def test_group_config_whitelist_and_blacklist() -> None:
    open_config = GroupConfig(enabled=True)
    assert open_config.allows(umo=UMO, group_id="10086") is True

    limited = GroupConfig(enabled=True, whitelist=("10086",))
    assert limited.allows(umo=UMO, group_id="10086") is True
    assert limited.allows(umo="aiocqhttp:GroupMessage:999", group_id="999") is False

    blocked = GroupConfig(enabled=True, blacklist=("10086",))
    assert blocked.allows(umo=UMO, group_id="10086") is False


# --------------------------------------------------------------------------- #
# 决策链
# --------------------------------------------------------------------------- #


def test_decide_replies_when_mentioned() -> None:
    service = _service(threshold=0.99)
    decision = asyncio.run(service.decide(_view(text="帮我看看"), GroupSignals(mentioned=True)))
    assert decision.action == "reply"
    assert decision.reason == "被直接提及"


def test_decide_silent_without_text_or_outside_allowlist() -> None:
    service = _service(whitelist=("12345",))
    silent = asyncio.run(service.decide(_view(text="部署方案怎么改"), GroupSignals()))
    assert silent.action == "silent"
    assert silent.reason == "会话不在允许名单内"

    blank = asyncio.run(_service().decide(_view(text="   "), GroupSignals()))
    assert blank.action == "silent"
    assert blank.reason == "无文本内容"


def test_decide_interjects_and_applies_cooldown() -> None:
    clock = FakeClock()
    service = _service(clock=clock, threshold=0.3)
    first = asyncio.run(service.decide(_view(text="这个报错要怎么处理？"), GroupSignals()))
    assert first.action == "interject"

    second = asyncio.run(service.decide(_view(text="还有别的办法吗？"), GroupSignals()))
    assert second.action == "silent"
    assert second.reason == "冷却中"

    clock.advance(91)
    snapshot = service.session_snapshot(UMO)
    assert snapshot["cooldown_remaining"] == 0.0
    assert snapshot["hourly"] == 1


def test_decide_enforces_hourly_quota() -> None:
    clock = FakeClock()
    service = _service(clock=clock, threshold=0.3, cooldown=0, max_per_hour=2)
    for _ in range(2):
        assert (
            asyncio.run(service.decide(_view(text="这个报错要怎么处理？"), GroupSignals())).action
            == "interject"
        )
    blocked = asyncio.run(service.decide(_view(text="这个报错要怎么处理？"), GroupSignals()))
    assert blocked.action == "silent"
    assert blocked.reason == "已达每小时上限"
    assert service.snapshot()["stats"]["interjects"] == 2


def test_decide_silent_when_attention_is_low() -> None:
    service = _service(threshold=0.9)
    decision = asyncio.run(service.decide(_view(text="今天天气不错呢"), GroupSignals()))
    assert decision.action == "silent"
    assert "注意力不足" in decision.reason
    assert service.snapshot()["last"]["action"] == "silent"


# --------------------------------------------------------------------------- #
# 并发合并
# --------------------------------------------------------------------------- #


def test_merge_window_collects_follow_up_messages() -> None:
    async def _run() -> tuple[GroupDecision, GroupDecision]:
        service = _service(threshold=0.4, merge_window=0.4)
        leader = asyncio.create_task(
            service.decide(_view(text="那个部署方案要怎么改？"), GroupSignals())
        )
        await asyncio.sleep(0.05)
        follower = await service.decide(_view(text="到底要怎么处理？"), GroupSignals())
        return await leader, follower

    leader, follower = asyncio.run(_run())
    assert follower.action == "silent"
    assert follower.reason == "已合并到上一条消息"
    assert leader.action == "interject", "合并后的整段文本才参与评分"
    assert leader.merged == 2
    assert "那个部署方案" in leader.text
    assert "到底要怎么处理？" in leader.text


def test_merge_window_isolated_per_session() -> None:
    async def _run() -> tuple[GroupDecision, GroupDecision]:
        service = _service(threshold=0.4, merge_window=0.4)
        other = EventView(
            umo="aiocqhttp:GroupMessage:20000",
            is_group=True,
            group_id="20000",
            sender_id="u2",
            text="另一个群的问题要怎么处理？",
        )
        leader = asyncio.create_task(
            service.decide(_view(text="另一个部署问题要怎么改？"), GroupSignals())
        )
        await asyncio.sleep(0.05)
        independent = await service.decide(other, GroupSignals())
        return await leader, independent

    leader, independent = asyncio.run(_run())
    assert independent.action == "interject", "不同会话不受彼此合并窗口影响"
    assert independent.text == ""
    assert leader.action == "interject"


def test_merge_is_off_by_default() -> None:
    service = _service(threshold=0.3)
    decision = asyncio.run(service.decide(_view(text="这个报错要怎么处理？"), GroupSignals()))
    assert decision.action == "interject"
    assert decision.text == ""
    assert decision.merged == 1


def test_record_bot_reply_feeds_topic_continuation() -> None:
    service = _service(threshold=0.99)
    service.record_bot_reply(UMO, FILLER)
    decision = asyncio.run(service.decide(_view(text=FILLER), GroupSignals()))
    assert decision.action == "silent"
    assert "话题延续" in service.snapshot()["last"]["reason"]


# --------------------------------------------------------------------------- #
# 决策落地（harness）
# --------------------------------------------------------------------------- #


def test_apply_silent_stops_event() -> None:
    event = FakeEvent(text="你好")
    apply_group_decision(event, GroupDecision(action="silent", reason="冷却中"))
    assert event.stopped is True
    assert event.is_at_or_wake_command is False


def test_apply_interject_wakes_event_and_rewrites_text() -> None:
    event = FakeEvent(text="第一条")
    apply_group_decision(event, GroupDecision(action="interject", text="第一条\n第二条", merged=2))
    assert event.stopped is False
    assert event.is_at_or_wake_command is True
    assert event.is_wake is True
    assert event.message_str == "第一条\n第二条"


def test_apply_reply_leaves_event_untouched() -> None:
    event = FakeEvent(text="你好", at_command=True)
    apply_group_decision(event, GroupDecision(action="reply", reason="被直接提及"))
    assert event.stopped is False
    assert event.message_str == "你好"


def test_to_group_signals_detects_at_and_reply(monkeypatch: Any) -> None:
    class FakeAt:
        def __init__(self, qq: str) -> None:
            self.qq = qq

    class FakeReply:
        def __init__(self, sender_id: str) -> None:
            self.sender_id = sender_id

    monkeypatch.setattr(compat, "SYMBOLS", replace(compat.SYMBOLS, At=FakeAt, Reply=FakeReply))

    at_event = FakeEvent(messages=[FakeAt("bot-1")], self_id="bot-1")
    assert to_group_signals(at_event).mentioned is True

    other = FakeEvent(messages=[FakeAt("someone")], self_id="bot-1")
    assert to_group_signals(other).mentioned is False

    replied = FakeEvent(messages=[FakeReply("bot-1")], self_id="bot-1")
    assert to_group_signals(replied).mentioned is True

    woken = FakeEvent(messages=[], self_id="bot-1", at_command=True)
    assert to_group_signals(woken).wake is True


def test_group_gate_release_keeps_other_instances_gate() -> None:
    """插件重载时新旧实例并存：旧实例卸载不得清掉新实例的门控。"""

    def enabled() -> bool:
        return True

    def disabled() -> bool:
        return False

    set_group_gate(enabled)
    assert group_gate_open() is True

    release_group_gate(disabled)  # 不是当前门控 → 保持不动
    assert group_gate_open() is True

    release_group_gate(enabled)  # 是当前门控 → 清除
    assert group_gate_open() is False
