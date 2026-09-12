"""运行监控：内存指标收集与时间序列落盘。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from super_astrbot.monitor import MonitorService
from super_astrbot.monitor.metrics import (
    METRIC_LLM_CALLS,
    METRIC_MEMORY_TOTAL,
    MetricRecorder,
)
from super_astrbot.storage import Database, MetricSeriesRepository

NOW = 1_700_000_000.0


def test_recorder_buckets_and_drains() -> None:
    recorder = MetricRecorder(clock=lambda: NOW)
    recorder.record(METRIC_LLM_CALLS)
    recorder.record(METRIC_LLM_CALLS)
    recorder.observe("llm.latency_ms", 120.0)
    recorder.record(METRIC_MEMORY_TOTAL, gauge=42.0)

    assert recorder.pending() == 3
    live = recorder.snapshot()
    assert live[METRIC_LLM_CALLS]["count"] == 2
    assert live["llm.latency_ms"]["total"] == 120.0
    assert live[METRIC_MEMORY_TOTAL]["last_value"] == 42.0

    rows = recorder.drain()
    assert len(rows) == 3
    assert recorder.pending() == 0
    assert all(row["bucket_ts"] == recorder.bucket_of(NOW) for row in rows)


def test_recorder_drops_oldest_half_when_over_capacity() -> None:
    recorder = MetricRecorder(clock=lambda: NOW, max_pending=4)
    for index in range(10):
        recorder.record("metric.%d" % index)
    assert recorder.pending() == 4


def test_service_flush_series_and_purge(tmp_path: Path) -> None:
    async def _run() -> None:
        db = Database(tmp_path / "metrics.db")
        await db.connect()
        try:
            repo = MetricSeriesRepository(db)
            recorder = MetricRecorder(clock=lambda: NOW)
            service = MonitorService(metrics=repo, recorder=recorder, retention_days=7)

            recorder.record(METRIC_LLM_CALLS, count=3)
            recorder.record("llm.latency_ms", total=300.0)
            assert await service.flush() == 2

            totals = await repo.totals(METRIC_LLM_CALLS, since=NOW - 3600)
            assert totals["count"] == 3.0

            window = await service.overview(now=NOW, hours=24)
            assert window["totals"][METRIC_LLM_CALLS]["count"] == 3.0
            assert METRIC_LLM_CALLS in window["metrics"]

            trends = await service.trends(metrics=[METRIC_LLM_CALLS], range_hours=24, now=NOW)
            points = trends["series"][METRIC_LLM_CALLS]
            assert len(points) == 1
            assert points[0]["count"] == 3

            # 保留期之外的数据应被清理。
            assert await service.purge(now=NOW + 30 * 86400) == 2
            assert (await service.overview(now=NOW, hours=24))["totals"][METRIC_LLM_CALLS][
                "count"
            ] == 0.0
        finally:
            await db.close()

    asyncio.run(_run())


def test_service_degrades_when_repository_fails(tmp_path: Path) -> None:
    class _Broken:
        async def bump_many(self, rows: object) -> None:
            raise RuntimeError("boom")

        async def series(self, *args: object, **kwargs: object) -> list[object]:
            raise RuntimeError("boom")

        async def totals(self, *args: object, **kwargs: object) -> dict[str, float]:
            raise RuntimeError("boom")

        async def metrics(self) -> list[str]:
            raise RuntimeError("boom")

        async def purge_before(self, **kwargs: object) -> int:
            raise RuntimeError("boom")

    async def _run() -> None:
        recorder = MetricRecorder(clock=lambda: NOW)
        recorder.record(METRIC_LLM_CALLS)
        service = MonitorService(metrics=_Broken(), recorder=recorder)  # type: ignore[arg-type]
        assert await service.flush() == 0
        window = await service.overview(now=NOW)
        assert window["totals"] == {}
        trends = await service.trends(metrics=[METRIC_LLM_CALLS], now=NOW)
        assert trends["bucket_seconds"] == 3600
        assert trends["series"] == {METRIC_LLM_CALLS: []}

    asyncio.run(_run())
