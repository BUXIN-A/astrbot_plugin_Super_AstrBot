"""主动交互服务：双轨调度、竞态保护与免打扰。

两条轨道共用同一套守卫，只在「触发时机」上不同：

- **计划轨（daily）**：每天 ``daily_time`` 由调度器触发一次；
- **空闲轨（idle）**：每 ``idle_check_minutes`` 检查一次，会话静默达到 ``idle_minutes`` 才触发。

竞态保护：
1. 同一会话同一时刻只允许一次发送（``_inflight``）；
2. 生成前后各复核一次「静默条件」——生成期间用户开始说话就放弃本次发送；
3. 当日配额先写入 KV 再发送（``proactive:sent:<umo>:<date>``），跨重载幂等、不重复打扰；
4. 全局并发与每日调用预算由 ``LLMBudget`` 负责，本服务不做二次限流。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from ..harness.protocols import Host, LlmGateway
from ..loop.state_store import StateStore
from ..spec.capabilities import as_int
from ..spec.errors import LlmError, safe_detail
from ..support import normalize_text, truncate
from .config import ProactiveConfig
from .materials import MaterialSource
from .prompts import build_proactive_prompt, proactive_system

_DAILY_COUNT_PREFIX = "proactive:sent:"
_LAST_PREFIX = "proactive:last:"
_PAUSED_PREFIX = "proactive:paused:"

_QUOTE_CHARS = "「」『』\"'“”‘’"
_FORBIDDEN_PREFIXES = ("主动", "（主动", "(主动", "系统", "【", "[")

TRACK_DAILY = "daily"
TRACK_IDLE = "idle"


@dataclass
class Attempt:
    """一次主动发送的尝试结果。"""

    umo: str
    kind: str
    sent: bool = False
    reason: str = ""
    text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "umo": self.umo,
            "kind": self.kind,
            "sent": self.sent,
            "reason": self.reason,
        }


class ProactiveService:
    """主动消息调度器。"""

    def __init__(
        self,
        *,
        config: ProactiveConfig,
        host: Host,
        llm: LlmGateway,
        materials: MaterialSource,
        store: StateStore | None = None,
        logger: Any | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        self._host = host
        self._llm = llm
        self._materials = materials
        self._store = store
        self._logger = logger
        self._clock = clock or time.time
        self._inflight: set[str] = set()
        self._activity: dict[str, float] = {}
        self._started_at = self._clock()
        self._paused: set[str] = set()
        self._paused_loaded = False
        self._stats: dict[str, int] = {"sent": 0, "skipped": 0, "failed": 0}
        self._last: dict[str, Any] = {}

    @property
    def config(self) -> ProactiveConfig:
        return self._config

    def note_activity(self, umo: str, *, at: float | None = None) -> None:
        """记录会话活动（收到消息或 Bot 回复），用于判断是否「正在聊天」。"""
        if not umo:
            return
        self._activity[umo] = at if at is not None else self._clock()

    # ------------------------------------------------------------------ #
    # 调度入口
    # ------------------------------------------------------------------ #

    async def run_track(self, kind: str) -> list[Attempt]:
        """执行一条轨道：逐个目标会话尝试发送。"""
        if not self._config.enabled:
            return []
        if kind == TRACK_DAILY and not self._config.daily_enabled:
            return []
        if kind == TRACK_IDLE and not self._config.idle_enabled:
            return []

        attempts: list[Attempt] = []
        for umo in self._config.targets:
            try:
                attempts.append(await self.send_one(umo, kind=kind))
            except Exception as exc:  # noqa: BLE001 - 单个会话失败不影响其它会话
                self._stats["failed"] += 1
                self._warn("主动消息发送失败（%s）：%s", umo, safe_detail(exc))
                attempts.append(Attempt(umo=umo, kind=kind, reason=f"异常：{safe_detail(exc)}"))
        return attempts

    async def send_one(self, umo: str, *, kind: str) -> Attempt:
        """对单个会话尝试发起一次主动消息。"""
        if not self._config.enabled:
            return Attempt(umo=umo, kind=kind, reason="未启用")
        if umo in self._inflight:
            return self._skip(umo, kind, "已有一次发送在进行中")

        self._inflight.add(umo)
        try:
            return await self._attempt(umo, kind)
        finally:
            self._inflight.discard(umo)

    # ------------------------------------------------------------------ #
    # 观测
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict[str, Any]:
        config = self._config
        return {
            "enabled": bool(config.enabled),
            "targets": len(config.targets),
            "daily_enabled": bool(config.daily_enabled),
            "daily_time": f"{config.daily_hour:02d}:{config.daily_minute:02d}",
            "idle_enabled": bool(config.idle_enabled),
            "idle_minutes": config.idle_minutes,
            "daily_max": config.daily_max,
            "quiet_hours": f"{config.quiet_start:02d}:00-{config.quiet_end:02d}:00",
            "stats": dict(self._stats),
            "last": dict(self._last),
        }

    async def session_snapshot(self, umo: str) -> dict[str, Any]:
        """当前会话的暂停状态、今日已发条数与静默时长。"""
        now = self._clock()
        return {
            "umo": umo,
            "paused": await self.is_paused(umo),
            "sent_today": await self._daily_count(umo, now),
            "daily_max": self._config.daily_max,
            "idle_minutes": round((now - self._activity.get(umo, self._started_at)) / 60.0, 1),
        }

    async def is_paused(self, umo: str) -> bool:
        """该会话是否被手动暂停（免打扰）。"""
        if not self._paused_loaded:
            await self._load_paused()
        return umo in self._paused

    async def set_paused(self, umo: str, paused: bool) -> bool:
        """设置/取消会话的主动消息暂停；返回设置后的状态。"""
        if not umo:
            return False
        if not self._paused_loaded:
            await self._load_paused()
        if paused:
            self._paused.add(umo)
        else:
            self._paused.discard(umo)
        await self._kv_set(_PAUSED_PREFIX + umo, bool(paused))
        return paused

    # ------------------------------------------------------------------ #
    # 内部：守卫 → 生成 → 复核 → 发送
    # ------------------------------------------------------------------ #

    async def _attempt(self, umo: str, kind: str) -> Attempt:
        now = self._clock()
        reason = await self._blocked_reason(umo, kind, now)
        if reason:
            return self._skip(umo, kind, reason)

        material = await self._materials.collect(
            umo,
            memories=self._config.material_memories,
            journals=self._config.material_journals,
        )
        if material.is_empty:
            return self._skip(umo, kind, "无可用素材")

        prompt = build_proactive_prompt(
            material.render(),
            kind=kind,
            target="",
            hour=time.localtime(now).tm_hour,
            max_chars=self._config.max_chars,
            last_text=await self._last_text(umo),
            overrides=self._config.prompts,
        )
        try:
            result = await self._llm.chat(
                prompt=prompt,
                system_prompt=proactive_system(self._config.prompts),
                provider_id=self._config.provider_id or None,
                session_key=umo,
                purpose="proactive",
            )
        except LlmError as exc:
            self._stats["failed"] += 1
            return Attempt(umo=umo, kind=kind, reason=f"生成失败：{safe_detail(exc)}")

        text = _clean_text(result.text, max_chars=self._config.max_chars)
        if not text:
            return self._skip(umo, kind, "生成内容为空")

        # 竞态复核：生成期间用户若已开始说话，本轮放弃，避免硬插话。
        if self._clock() - self._activity.get(umo, self._started_at) < self._guard_seconds:
            return self._skip(umo, kind, "生成期间会话已活跃")

        # 先占配额再发送：发送失败也不补发（避免重复打扰）。
        await self._bump_daily(umo, now)

        sent = False
        try:
            sent = bool(await self._host.send_message(umo, text))
        except Exception as exc:  # noqa: BLE001 - 主动发送失败只记录
            sent = False
            self._warn("主动发送异常（%s）：%s", umo, safe_detail(exc))

        if not sent:
            self._stats["failed"] += 1
            self._last = {"umo": umo, "kind": kind, "sent": False, "reason": "发送失败", "at": now}
            return Attempt(umo=umo, kind=kind, reason="发送失败", text=text)

        self._stats["sent"] += 1
        self._last = {
            "umo": umo,
            "kind": kind,
            "sent": True,
            "reason": "已发送",
            "text": truncate(text, 60),
            "at": now,
        }
        await self._remember_last(umo, text, kind)
        self._info("主动消息已发送（%s/%s）：%s", kind, umo, truncate(text, 40))
        return Attempt(umo=umo, kind=kind, sent=True, reason="已发送", text=text)

    async def _blocked_reason(self, umo: str, kind: str, now: float) -> str:
        """返回阻止本次发送的原因；空字符串表示可以发送。"""
        if not umo:
            return "目标会话为空"
        local_hour = time.localtime(now).tm_hour
        if self._config.in_quiet_hours(local_hour):
            return "免打扰时段"
        if await self.is_paused(umo):
            return "已被手动暂停"
        if await self._daily_count(umo, now) >= self._config.daily_max:
            return "今日已达上限"

        idle = now - self._activity.get(umo, self._started_at)
        if kind == TRACK_IDLE:
            if not self._config.idle_enabled:
                return "空闲轨未启用"
            if idle < self._config.idle_minutes * 60:
                return "会话未达到静默时长"
        elif idle < self._guard_seconds:
            return "会话正在活跃"
        return ""

    @property
    def _guard_seconds(self) -> float:
        return float(self._config.busy_guard_minutes) * 60.0

    # ------------------------------------------------------------------ #
    # 内部：状态存取
    # ------------------------------------------------------------------ #

    async def _load_paused(self) -> None:
        self._paused_loaded = True
        if self._store is None:
            return
        for umo in self._config.targets:
            try:
                value = await self._store.get(_PAUSED_PREFIX + umo, False)
            except Exception as exc:  # noqa: BLE001 - 读失败按未暂停处理
                self._warn("读取暂停状态失败：%s", safe_detail(exc))
                continue
            if value:
                self._paused.add(umo)

    async def _daily_count(self, umo: str, now: float) -> int:
        if self._store is None:
            return 0
        try:
            value = await self._store.get(self._count_key(umo, now), 0)
        except Exception as exc:  # noqa: BLE001
            self._warn("读取当日发送计数失败：%s", safe_detail(exc))
            return 0
        return as_int(value, 0)

    async def _bump_daily(self, umo: str, now: float) -> None:
        if self._store is None:
            return
        await self._kv_set(self._count_key(umo, now), await self._daily_count(umo, now) + 1)

    async def _last_text(self, umo: str) -> str:
        if self._store is None:
            return ""
        try:
            value = await self._store.get(_LAST_PREFIX + umo, None)
        except Exception as exc:  # noqa: BLE001
            self._warn("读取上次主动内容失败：%s", safe_detail(exc))
            return ""
        return str(value.get("text") or "") if isinstance(value, dict) else ""

    async def _remember_last(self, umo: str, text: str, kind: str) -> None:
        await self._kv_set(
            _LAST_PREFIX + umo,
            {"text": text, "kind": kind, "at": self._clock()},
        )

    async def _kv_set(self, key: str, value: Any) -> None:
        if self._store is None:
            return
        try:
            await self._store.set(key, value)
        except Exception as exc:  # noqa: BLE001 - 写失败只影响幂等性
            self._warn("写入主动交互状态失败：%s", safe_detail(exc))

    @staticmethod
    def _count_key(umo: str, now: float) -> str:
        return f"{_DAILY_COUNT_PREFIX}{umo}:{time.strftime('%Y-%m-%d', time.localtime(now))}"

    def _skip(self, umo: str, kind: str, reason: str) -> Attempt:
        self._stats["skipped"] += 1
        self._debug("主动消息跳过（%s/%s）：%s", kind, umo, reason)
        return Attempt(umo=umo, kind=kind, reason=reason)

    def _info(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.info(message, *args)

    def _debug(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.debug(message, *args)

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)


def _clean_text(text: str, *, max_chars: int) -> str:
    """清洗模型输出：去包裹符号与自称，压平空白并限长。"""
    cleaned = normalize_text(text or "")
    if not cleaned:
        return ""
    cleaned = cleaned.strip(_QUOTE_CHARS)
    for prefix in _FORBIDDEN_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].lstrip("：:， ,）)")
            break
    cleaned = cleaned.strip(_QUOTE_CHARS)
    return truncate(cleaned, max_chars).strip()
