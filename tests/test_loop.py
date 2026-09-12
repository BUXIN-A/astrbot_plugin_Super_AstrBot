"""循环控制测试：任务作用域、调度幂等、预算、并发门闸。"""

from __future__ import annotations

import asyncio
import time

from super_astrbot.loop import (
    ABANDONED,
    ConcurrencyGate,
    LLMBudget,
    MemoryStateStore,
    Scheduler,
    TaskScope,
)


def test_task_scope_returns_result_and_stops() -> None:
    async def _run() -> tuple[int | None, object]:
        scope = TaskScope("t")

        async def job() -> int:
            return 42

        value = await scope.run(job)
        scope.cancel()
        skipped = await scope.run(job)
        return value, skipped

    value, skipped = asyncio.run(_run())
    assert value == 42
    assert skipped is ABANDONED


def test_task_scope_token_invalidation() -> None:
    async def _run() -> tuple[int | None, object]:
        scope = TaskScope("t")
        token = scope.token()

        async def job() -> int:
            return 1

        valid = await scope.run(job, token=token)
        scope.bump_generation()
        stale = await scope.run(job, token=token)
        return valid, stale

    valid, stale = asyncio.run(_run())
    assert valid == 1
    assert stale is ABANDONED


def test_task_scope_timeout_abandons() -> None:
    async def _run() -> object:
        scope = TaskScope("t", poll_interval=0.01)

        async def slow() -> str:
            await asyncio.sleep(5)
            return "late"

        return await scope.run(slow, timeout=0.05)

    assert asyncio.run(_run()) is ABANDONED


def test_task_scope_distinguishes_none_result_from_abandoned() -> None:
    """回归：任务正常返回 None 不能被当成「被放弃」。

    v0.2.0 的调度器曾用 ``result is None`` 判断放弃，导致所有正常结束的任务
    都被误报为「超时或令牌失效」。
    """

    async def _run() -> tuple[object, object]:
        scope = TaskScope("t")

        async def returns_none() -> None:
            return None

        normal = await scope.run(returns_none)
        token = scope.token()
        scope.bump_generation()
        abandoned = await scope.run(returns_none, token=token)
        return normal, abandoned

    normal, abandoned = asyncio.run(_run())
    assert normal is None
    assert normal is not ABANDONED
    assert abandoned is ABANDONED


def test_task_scope_cancel_all_converges() -> None:
    async def _run() -> tuple[int, int]:
        scope = TaskScope("t", poll_interval=0.01)

        async def forever() -> None:
            await asyncio.sleep(30)

        scope.spawn(forever(), name="a")
        scope.spawn(forever(), name="b")
        await asyncio.sleep(0.02)
        pending_before = scope.pending_count()
        cancelled = await scope.cancel_all(timeout=1.0)
        return pending_before, cancelled

    pending_before, cancelled = asyncio.run(_run())
    assert pending_before == 2
    assert cancelled == 2


def test_scheduler_daily_runs_once_per_day() -> None:
    async def _run() -> tuple[int, str, list[str]]:
        now = time.time()
        local = time.localtime(now)
        scope = TaskScope("s")
        scheduler = Scheduler(scope, store=MemoryStateStore(), clock=lambda: now, tick=1.0)
        calls: list[str] = []

        async def job() -> None:
            calls.append("ran")

        spec = scheduler.daily_at(local.tm_hour, local.tm_min, job, key="daily-test")
        await scheduler._tick_once(now)  # 首次：应执行
        await scheduler._tick_once(now)  # 再次：当日已执行，应跳过
        return len(calls), spec.last_date, calls

    count, last_date, _ = asyncio.run(_run())
    assert count == 1
    assert last_date != ""


def test_scheduler_interval_advances_next_run() -> None:
    async def _run() -> tuple[int, float]:
        now = 1000.0
        clock = lambda: now  # noqa: E731
        scope = TaskScope("s")
        scheduler = Scheduler(scope, store=MemoryStateStore(), clock=clock, tick=1.0)
        calls: list[int] = []

        async def job() -> None:
            calls.append(1)

        spec = scheduler.every(10.0, job, key="interval-test", run_immediately=True)
        await scheduler._tick_once(now)
        first_next = spec.next_run
        await scheduler._tick_once(now)  # 尚未到点
        return len(calls), first_next

    count, first_next = asyncio.run(_run())
    assert count == 1
    assert first_next == 1010.0


def test_scheduler_job_returning_none_counts_as_executed() -> None:
    """回归：与真实任务一样返回 None 的作业不得被记为「跳过」。

    v0.2.0 的服务器日志出现过误报：
    ``任务 reflection-scan 未完成执行（超时或令牌失效），将按失败处理``。
    """

    async def _run() -> tuple[int, int, int]:
        now = time.time()
        scope = TaskScope("s", poll_interval=0.01)
        scheduler = Scheduler(scope, store=MemoryStateStore(), clock=lambda: now, tick=1.0)
        calls: list[int] = []

        async def job() -> None:
            calls.append(1)

        scheduler.every(10.0, job, key="none-job", run_immediately=True)
        await scheduler._tick_once(now)
        spec = scheduler.get("none-job")
        return len(calls), spec.runs, spec.skipped

    calls, runs, skipped = asyncio.run(_run())
    assert calls == 1
    assert runs == 1
    assert skipped == 0, "正常返回 None 的任务不得被判定为「被放弃」"


def test_scheduler_marks_skipped_only_when_abandoned() -> None:
    async def _run() -> tuple[int, int]:
        now = time.time()
        scope = TaskScope("s", poll_interval=0.01)
        scheduler = Scheduler(scope, store=MemoryStateStore(), clock=lambda: now, tick=1.0)

        async def slow() -> None:
            await asyncio.sleep(5)

        scheduler.every(10.0, slow, key="slow-job", run_immediately=True, timeout=0.05)
        await scheduler._tick_once(now)
        spec = scheduler.get("slow-job")
        return spec.runs, spec.skipped

    runs, skipped = asyncio.run(_run())
    assert skipped == 1, "真正被放弃（超时）的任务才计入跳过"
    assert runs == 1


def test_scheduler_persists_state_across_instances() -> None:
    async def _run() -> tuple[int, int, int, str]:
        now = time.time()
        local = time.localtime(now)
        store = MemoryStateStore()

        async def job() -> None: ...

        first = Scheduler(TaskScope("s1"), store=store, clock=lambda: now, tick=1.0)
        first.daily_at(local.tm_hour, local.tm_min, job, key="persisted")
        await first._tick_once(now)
        first_runs = first.get("persisted").runs

        # 模拟插件重载：新的调度器 + 同一状态存储
        second = Scheduler(TaskScope("s2"), store=store, clock=lambda: now, tick=1.0)
        second.daily_at(local.tm_hour, local.tm_min, job, key="persisted")
        await second._restore()
        restored_runs = second.get("persisted").runs
        restored_date = second.get("persisted").last_date
        await second._tick_once(now)  # 当日已执行过，不应重复执行
        after_tick_runs = second.get("persisted").runs
        return first_runs, restored_runs, after_tick_runs, restored_date

    first_runs, restored_runs, after_tick_runs, restored_date = asyncio.run(_run())
    assert first_runs == 1
    # 运行统计会随状态恢复（累计值），因此恢复后仍为 1
    assert restored_runs == 1
    assert restored_date != ""
    # 关键断言：重载后当日不重复执行（若重复执行，计数会变成 2）
    assert after_tick_runs == 1


def test_budget_daily_limit_and_concurrency() -> None:
    async def _run() -> tuple[bool, bool, bool, int]:
        budget = LLMBudget(daily_limit=2, max_concurrent=1)
        first = await budget.try_acquire("reflection")
        budget.release("reflection")
        second = await budget.try_acquire("reflection")
        budget.release("reflection")
        third = await budget.try_acquire("reflection")
        snapshot_rejected = budget.snapshot()["rejected"]
        return first, second, third, snapshot_rejected

    first, second, third, rejected = asyncio.run(_run())
    assert (first, second) == (True, True)
    assert third is False
    assert rejected == 1


def test_budget_without_limit_always_allows() -> None:
    async def _run() -> bool:
        budget = LLMBudget(daily_limit=0, max_concurrent=2)
        results: list[bool] = []
        # 逐次获取并释放，避免并发信号量被占满（并发上限单独由 test_budget_* 覆盖）
        for _ in range(5):
            acquired = await budget.try_acquire("x")
            results.append(acquired)
            if acquired:
                budget.release("x")
        return all(results)

    assert asyncio.run(_run()) is True


def test_budget_blocks_when_concurrency_exhausted() -> None:
    async def _run() -> tuple[bool, str]:
        budget = LLMBudget(daily_limit=0, max_concurrent=1)
        first = await budget.try_acquire("x")
        # 第二个调用应阻塞在信号量上，因此加超时断言其确实被挂起
        try:
            await asyncio.wait_for(budget.try_acquire("x"), timeout=0.1)
            second = "未阻塞"
        except asyncio.TimeoutError:
            second = "已阻塞"
        budget.release("x")
        return first, second

    first, second = asyncio.run(_run())
    assert first is True
    assert second == "已阻塞"


def test_concurrency_gate_writer_excludes_readers() -> None:
    async def _run() -> list[str]:
        gate = ConcurrencyGate()
        order: list[str] = []

        async def reader() -> None:
            async with gate.read("k"):
                order.append("r-start")
                await asyncio.sleep(0.05)
                order.append("r-end")

        async def writer() -> None:
            await asyncio.sleep(0.01)
            async with gate.write("k"):
                order.append("w")

        await asyncio.gather(reader(), writer())
        return order

    order = asyncio.run(_run())
    assert order == ["r-start", "r-end", "w"]


def test_concurrency_gate_prune_idle() -> None:
    async def _run() -> tuple[int, int]:
        gate = ConcurrencyGate()
        async with gate.read("a"):
            inside = len(gate.stats())
        return inside, gate.prune_idle()

    inside, pruned = asyncio.run(_run())
    assert inside == 1
    assert pruned == 1
