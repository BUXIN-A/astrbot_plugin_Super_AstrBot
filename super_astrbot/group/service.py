"""群聊语义服务：读空气决策、冷却与配额、并发合并。

决策链（自上而下短路，前者优先级更高）：

1. 能力关闭 → 不参与（门控 filter 已保证，这里只做兜底）；
2. 被直接提及（@ / 引用 / 框架唤醒标记）→ 沿用框架既有链路正常回复，**不干预**；
3. 已有合并窗口 → 把本条并入后静默，避免同一波消息被回答多次；
4. 无文本 / 名单外 / 冷却中 / 已达配额 → 静默（这几步零成本，先做）；
5. 开启合并窗口时，先收集同一会话短时间内的后续消息，**对合并后的整段文本评分**
   （用户常把一句话拆成几条发，逐条评分会让它们全部落空）；
6. 注意力得分低于阈值 → 静默；否则插话。

并发安全：合并窗口的「认领」发生在任何 ``await`` 之前，因此在单线程事件循环里只有
第一条消息能成为 leader；从「检查配额」到「登记已回复」同样没有 ``await``，
不会出现同一会话两条并发插话。
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque

from ..harness.protocols import EventView, GroupDecision, GroupSignals
from ..support import truncate
from .attention import score_message
from .config import GroupConfig

_HOUR = 3600.0
_RECENT_BOT_TEXTS = 3
_MERGE_POLL_SECONDS = 0.2
_BURST_WINDOW_SECONDS = 20.0
_BURST_COUNT = 3
_RECENT_MESSAGES = 6


@dataclass
class _SessionState:
    """单个群会话的运行态（仅内存，重启后重置）。"""

    last_reply_at: float = 0.0
    reply_times: Deque[float] = field(default_factory=deque)
    recent_bot_texts: Deque[str] = field(default_factory=lambda: deque(maxlen=_RECENT_BOT_TEXTS))
    recent_messages: Deque[tuple[float, str]] = field(
        default_factory=lambda: deque(maxlen=_RECENT_MESSAGES)
    )


@dataclass
class _PendingMerge:
    """进行中的合并窗口。"""

    deadline: float
    texts: list[str] = field(default_factory=list)


class GroupChatService:
    """群聊「读空气」决策器。"""

    def __init__(
        self,
        *,
        config: GroupConfig,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        self._clock = clock or time.time
        self._sessions: dict[str, _SessionState] = {}
        self._pending: dict[str, _PendingMerge] = {}
        self._stats: dict[str, int] = {"interjects": 0, "silent": 0, "merged": 0}
        self._last: dict[str, Any] = {}

    @property
    def config(self) -> GroupConfig:
        return self._config

    def can_activate(self) -> bool:
        """能力是否生效（供 harness 门控使用）。"""
        return bool(self._config.enabled)

    # ------------------------------------------------------------------ #
    # 决策
    # ------------------------------------------------------------------ #

    async def decide(self, view: EventView, signals: GroupSignals) -> GroupDecision:
        """给出本次群消息的处理决策。"""
        if not self._config.enabled:
            return GroupDecision(action="reply", reason="未启用")
        if signals.wake or signals.mentioned:
            return GroupDecision(action="reply", reason="被直接提及")
        if not view.is_group:
            return GroupDecision(action="reply", reason="非群聊")

        now = self._clock()
        state = self._sessions.setdefault(view.umo, _SessionState())
        burst = self._observe(state, view, now)

        # 已有合并窗口：并入后静默（同一波消息只回答一次）。
        pending = self._pending.get(view.umo)
        if pending is not None:
            self._offer(pending, view.text)
            return self._silent("已合并到上一条消息", umo=view.umo, now=now)

        # 零成本前置检查：为注定静默的消息白等一个合并窗口没有意义。
        if not view.text.strip():
            return self._silent("无文本内容", umo=view.umo, now=now)
        if not self._config.allows(umo=view.umo, group_id=view.group_id):
            return self._silent("会话不在允许名单内", umo=view.umo, now=now)
        if now - state.last_reply_at < self._config.cooldown_seconds:
            return self._silent("冷却中", umo=view.umo, now=now)
        if self._hourly_count(state, now) >= self._config.max_per_hour:
            return self._silent("已达每小时上限", umo=view.umo, now=now)

        text, merged = await self._merge_window(view, now)
        score = score_message(
            text or view.text,
            aliases=self._config.bot_aliases,
            recent_texts=state.recent_bot_texts,
            burst=burst,
        )
        if score.value < self._config.attention_threshold:
            return self._silent(
                f"注意力不足（{score.describe()}）",
                umo=view.umo,
                now=now,
                attention=score.value,
            )

        # 登记已回复（同步、无 await）→ 同会话后续消息要么被窗口合并、要么撞上冷却。
        self._mark_replied(state, now)
        self._stats["interjects"] += 1
        self._stats["merged"] += max(0, merged - 1)
        self._last = {
            "action": "interject",
            "umo": view.umo,
            "attention": round(score.value, 3),
            "reason": score.describe(),
            "merged": merged,
            "at": now,
        }
        return GroupDecision(
            action="interject",
            reason=f"注意力 {score.describe()}",
            attention=score.value,
            text=text,
            merged=merged,
        )

    def record_bot_reply(self, umo: str, text: str) -> None:
        """记录 Bot 在本群的发言（用于话题延续度）。"""
        if not text:
            return
        state = self._sessions.setdefault(umo, _SessionState())
        state.recent_bot_texts.append(text)

    # ------------------------------------------------------------------ #
    # 观测
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": bool(self._config.enabled),
            "attention_threshold": self._config.attention_threshold,
            "cooldown_seconds": self._config.cooldown_seconds,
            "max_per_hour": self._config.max_per_hour,
            "merge_window_seconds": self._config.merge_window_seconds,
            "sessions": len(self._sessions),
            "stats": dict(self._stats),
            "last": dict(self._last),
        }

    def session_snapshot(self, umo: str) -> dict[str, Any]:
        """当前会话的冷却与配额余量。"""
        state = self._sessions.get(umo)
        if state is None:
            hourly = 0
            remaining = 0.0
        else:
            now = self._clock()
            hourly = self._hourly_count(state, now)
            remaining = max(0.0, self._config.cooldown_seconds - (now - state.last_reply_at))
        return {
            "umo": umo,
            "hourly": hourly,
            "hourly_limit": self._config.max_per_hour,
            "cooldown_remaining": round(remaining, 1),
        }

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    async def _merge_window(self, view: EventView, now: float) -> tuple[str, int]:
        """认领并等待合并窗口，收集同会话的后续消息。

        返回 ``(合并后的文本, 合并条数)``：只有一条消息时返回 ``("", 1)``，
        表示无需改写事件文本。窗口的认领在任何 ``await`` 之前完成，因此
        并发到达的消息只会有一条成为 leader，其余在入口处被合并。
        """
        window = self._config.merge_window_seconds
        if window <= 0 or not view.text.strip():
            return "", 1

        pending = _PendingMerge(deadline=now + window, texts=[view.text.strip()])
        self._pending[view.umo] = pending
        try:
            # 轮询次数按窗口长度封顶：既保证按时结束，也不依赖时钟一定单调递增。
            for _ in range(max(1, math.ceil(window / _MERGE_POLL_SECONDS))):
                if len(pending.texts) >= self._config.merge_max_messages:
                    break
                remain = pending.deadline - self._clock()
                if remain <= 0:
                    break
                await asyncio.sleep(min(remain, _MERGE_POLL_SECONDS))
        finally:
            # 必须无条件摘除：任务被取消（插件卸载/超时）时不能留下悬挂窗口
            if self._pending.get(view.umo) is pending:
                self._pending.pop(view.umo, None)

        if len(pending.texts) <= 1:
            return "", 1
        joined = truncate("\n".join(pending.texts), self._config.merge_max_chars)
        return joined, len(pending.texts)

    def _offer(self, pending: _PendingMerge, text: str) -> None:
        """把后续消息并入合并窗口（超出条数上限即丢弃）。"""
        stripped = text.strip()
        if not stripped or len(pending.texts) >= self._config.merge_max_messages:
            return
        pending.texts.append(stripped)

    def _observe(self, state: _SessionState, view: EventView, now: float) -> bool:
        """记录本条消息并判断是否属于「连续刷屏」。"""
        state.recent_messages.append((now, view.sender_id))
        same_sender = [
            moment
            for moment, sender in state.recent_messages
            if sender and sender == view.sender_id and now - moment <= _BURST_WINDOW_SECONDS
        ]
        return len(same_sender) >= _BURST_COUNT

    def _hourly_count(self, state: _SessionState, now: float) -> int:
        while state.reply_times and now - state.reply_times[0] > _HOUR:
            state.reply_times.popleft()
        return len(state.reply_times)

    def _mark_replied(self, state: _SessionState, now: float) -> None:
        state.last_reply_at = now
        state.reply_times.append(now)

    def _silent(
        self,
        reason: str,
        *,
        umo: str = "",
        now: float = 0.0,
        attention: float = 0.0,
    ) -> GroupDecision:
        self._stats["silent"] += 1
        self._last = {
            "action": "silent",
            "umo": umo,
            "attention": round(attention, 3),
            "reason": reason,
            "at": now,
        }
        return GroupDecision(action="silent", reason=reason, attention=attention)
