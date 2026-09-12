"""受控任务调度器。

设计目标（对照分析文档「循环控制」与 proactive_chat 的调度经验）：

- **幂等**：同一 ``key`` 的 job 只注册一次；每日任务「当天只跑一次」；
- **可持久化**：把 ``last_run`` / ``last_date`` 落到 ``StateStore``，重载/重启后不重复、不丢失；
- **启动补偿**：今日应跑而因停机错过的每日任务，启动后立即补跑一次；
- **可收敛**：所有执行都经 ``TaskScope``，插件卸载时统一取消，不留游离任务；
- **可观测**：``snapshot()`` 暴露每个 job 的下次执行时间、运行次数、失败与最后错误。

注意：本调度器**不依赖 AstrBot 的 cron**（AstrBot 内置 cron 的 basic job handler 只存在内存中，
重启后需重新注册，行为不易控制），因此自建轻量 tick 循环，语义完全可控。
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict

from ..spec.errors import safe_detail
from .state_store import MemoryStateStore, StateStore
from .task_scope import ABANDONED, TaskScope


@dataclass
class JobSpec:
    """一个被调度的任务。"""

    key: str
    kind: str  # "interval" | "daily"
    job: Callable[[], Awaitable[Any]]
    interval: float = 0.0
    hour: int = 0
    minute: int = 0
    weekday: int | None = None  # 0=周一 … 6=周日；None 表示每天
    run_immediately: bool = False
    timeout: float | None = None
    enabled: bool = True

    # --- 运行态 ---
    next_run: float = 0.0
    last_run: float = 0.0
    last_date: str = ""
    runs: int = 0
    failures: int = 0
    skipped: int = 0
    last_error: str = ""

    def describe(self, now: float | None = None) -> dict[str, Any]:
        current = now if now is not None else time.time()
        return {
            "key": self.key,
            "kind": self.kind,
            "enabled": self.enabled,
            "interval_seconds": self.interval,
            "hour": self.hour if self.kind == "daily" else None,
            "minute": self.minute if self.kind == "daily" else None,
            "weekday": self.weekday if self.kind == "daily" else None,
            "runs": self.runs,
            "failures": self.failures,
            "skipped": self.skipped,
            "last_run": self.last_run,
            "last_date": self.last_date,
            "next_run": self.next_run,
            "seconds_to_next": max(0.0, self.next_run - current) if self.next_run else None,
            "last_error": self.last_error,
        }


class Scheduler:
    """轻量、可持久化、可收敛的任务调度器。"""

    def __init__(
        self,
        scope: TaskScope,
        *,
        store: StateStore | None = None,
        logger: Any | None = None,
        clock: Callable[[], float] | None = None,
        observer: Callable[[str, bool, float], None] | None = None,
        tick: float = 5.0,
    ) -> None:
        self._scope = scope
        self._store: StateStore = store or MemoryStateStore()
        self._logger = logger
        self._clock = clock or time.time
        self._observer = observer
        """任务结束回调 ``(key, 是否成功, 耗时毫秒)``；用于运行监控埋点（可为 None）。"""
        self._tick = max(1.0, float(tick or 5.0))
        self._jobs: Dict[str, JobSpec] = {}
        self._loop_task: asyncio.Task[Any] | None = None
        self._started = False

    # ------------------------------------------------------------------ #
    # 注册
    # ------------------------------------------------------------------ #

    def every(
        self,
        seconds: float,
        job: Callable[[], Awaitable[Any]],
        *,
        key: str,
        run_immediately: bool = False,
        timeout: float | None = None,
    ) -> JobSpec:
        """注册一个周期任务。同 key 重复注册时后者覆盖前者。"""
        spec = JobSpec(
            key=key,
            kind="interval",
            job=job,
            interval=max(1.0, float(seconds)),
            run_immediately=run_immediately,
            timeout=timeout,
        )
        self._jobs[key] = spec
        return spec

    def daily_at(
        self,
        hour: int,
        minute: int,
        job: Callable[[], Awaitable[Any]],
        *,
        key: str,
        weekday: int | None = None,
        timeout: float | None = None,
    ) -> JobSpec:
        """注册一个每日/每周任务。``weekday`` 为 0(周一)…6(周日)，``None`` 表示每天。"""
        spec = JobSpec(
            key=key,
            kind="daily",
            job=job,
            hour=max(0, min(23, int(hour))),
            minute=max(0, min(59, int(minute))),
            weekday=None if weekday is None else max(0, min(6, int(weekday))),
            timeout=timeout,
        )
        self._jobs[key] = spec
        return spec

    def set_enabled(self, key: str, enabled: bool) -> bool:
        spec = self._jobs.get(key)
        if spec is None:
            return False
        spec.enabled = bool(enabled)
        return True

    def remove(self, key: str) -> bool:
        return self._jobs.pop(key, None) is not None

    def get(self, key: str) -> JobSpec | None:
        return self._jobs.get(key)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        await self._restore()
        self._loop_task = self._scope.spawn(self._loop(), name="scheduler-loop")

    async def shutdown(self) -> None:
        self._started = False
        if self._loop_task is not None and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._loop_task = None

    # ------------------------------------------------------------------ #
    # 主循环
    # ------------------------------------------------------------------ #

    async def _loop(self) -> None:
        while not self._scope.is_stopped():
            try:
                await self._tick_once(self._clock())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 调度循环不得因单次异常退出
                self._warn("调度循环出现异常：%s", safe_detail(exc))
            await asyncio.sleep(self._tick)

    async def _tick_once(self, now: float) -> None:
        for spec in list(self._jobs.values()):
            if not spec.enabled or self._scope.is_stopped():
                continue
            if not self._is_due(spec, now):
                continue
            await self._run_job(spec, now)

    def _is_due(self, spec: JobSpec, now: float) -> bool:
        if spec.kind == "interval":
            return now >= spec.next_run
        return self._is_daily_due(spec, now)

    async def _run_job(self, spec: JobSpec, now: float) -> None:
        error: str | None = None
        abandoned = False
        started = time.time()
        try:
            result = await self._scope.run(spec.job, timeout=spec.timeout)
            # 必须用哨兵判断：任务正常结束时也会返回 None
            abandoned = result is ABANDONED
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 单个 job 失败不影响调度器
            error = safe_detail(exc)

        self._notify(spec.key, ok=error is None and not abandoned, started=started)

        if abandoned:
            spec.skipped += 1
            if self._scope.is_stopped():
                return
            self._warn(
                "任务 %s 被放弃（超时 %s 秒或令牌失效）",
                spec.key,
                f"{spec.timeout:.0f}" if spec.timeout else "未设置",
            )

        spec.runs += 1
        spec.last_run = now
        if error is not None:
            spec.failures += 1
            spec.last_error = error
            self._warn("任务 %s 执行失败：%s", spec.key, error)

        if spec.kind == "interval":
            spec.next_run = now + max(1.0, spec.interval)
        else:
            spec.last_date = self._date_key(now)
            spec.next_run = self._next_daily_ts(spec, now)

        await self._persist(spec)

    # ------------------------------------------------------------------ #
    # 时间计算
    # ------------------------------------------------------------------ #

    def _date_key(self, ts: float) -> str:
        return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

    def _is_daily_due(self, spec: JobSpec, now: float) -> bool:
        local = _dt.datetime.fromtimestamp(now)
        if spec.weekday is not None and local.weekday() != spec.weekday:
            return False
        if spec.last_date == self._date_key(now):
            return False
        scheduled = local.replace(hour=spec.hour, minute=spec.minute, second=0, microsecond=0)
        return local >= scheduled

    def _next_daily_ts(self, spec: JobSpec, now: float) -> float:
        local = _dt.datetime.fromtimestamp(now)
        for offset in range(0, 8):
            day = (local + _dt.timedelta(days=offset)).date()
            if spec.weekday is not None and day.weekday() != spec.weekday:
                continue
            candidate = _dt.datetime.combine(day, _dt.time(spec.hour, spec.minute))
            stamp = candidate.timestamp()
            if stamp > now:
                return stamp
        return now + 86400.0

    # ------------------------------------------------------------------ #
    # 状态持久化
    # ------------------------------------------------------------------ #

    def _state_key(self, key: str) -> str:
        return f"job:{key}"

    async def _restore(self) -> None:
        now = self._clock()
        for spec in self._jobs.values():
            try:
                data = await self._store.get(self._state_key(spec.key), None)
            except Exception as exc:  # noqa: BLE001
                self._warn("读取任务 %s 状态失败：%s", spec.key, safe_detail(exc))
                data = None
            if isinstance(data, dict):
                spec.last_run = float(data.get("last_run", 0) or 0)
                spec.last_date = str(data.get("last_date", "") or "")
                spec.runs = int(data.get("runs", 0) or 0)
                spec.failures = int(data.get("failures", 0) or 0)
                spec.last_error = str(data.get("last_error", "") or "")

            if spec.kind == "interval":
                if spec.run_immediately and spec.last_run <= 0:
                    spec.next_run = 0.0
                elif spec.last_run > 0:
                    spec.next_run = spec.last_run + max(1.0, spec.interval)
                    if spec.next_run < now:
                        spec.next_run = now  # 停机期间错过的，立即补一次
                else:
                    spec.next_run = now + max(1.0, spec.interval)
            else:
                spec.next_run = self._next_daily_ts(spec, now)
                if spec.last_date != self._date_key(now) and self._is_daily_due(spec, now):
                    spec.next_run = now  # 今日已到点但未跑过 → 立即补跑

    async def _persist(self, spec: JobSpec) -> None:
        payload = {
            "last_run": spec.last_run,
            "last_date": spec.last_date,
            "runs": spec.runs,
            "failures": spec.failures,
            "last_error": spec.last_error,
        }
        try:
            await self._store.set(self._state_key(spec.key), payload)
        except Exception as exc:  # noqa: BLE001 - 持久化失败只影响幂等性，不影响功能
            self._warn("持久化任务 %s 状态失败：%s", spec.key, safe_detail(exc))

    # ------------------------------------------------------------------ #
    # 观测
    # ------------------------------------------------------------------ #

    def snapshot(self) -> list[dict[str, Any]]:
        now = self._clock()
        return [spec.describe(now) for spec in self._jobs.values()]

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)

    def _notify(self, key: str, *, ok: bool, started: float) -> None:
        """上报任务结果给观察者；观察者异常必须吞掉，绝不能影响调度。"""
        if self._observer is None:
            return
        try:
            self._observer(key, ok, max(0.0, (time.time() - started) * 1000.0))
        except Exception as exc:  # noqa: BLE001 - 埋点失败不影响调度
            self._warn("任务观察者回调失败：%s", safe_detail(exc))
