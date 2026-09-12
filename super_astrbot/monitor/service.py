"""运行监控服务：内存指标落盘 + 面板查询门面。

分层理由：埋点（``metrics``）不碰 I/O，面板查询也不该关心内存桶的生命周期，
因此把「drain 后批量 UPSERT」「按保留期清理」「总览/趋势组装」这三件事收敛到本类。

容错原则：监控是旁路能力，其故障绝不能影响主链路，也不能让面板请求 500。
所有公开方法因此**吞掉异常**，返回空结构并记日志。
"""

from __future__ import annotations

import time
from typing import Any, Sequence

from ..storage import MetricSeriesRepository
from .metrics import CORE_METRICS, RECORDER, MetricRecorder

MAX_RANGE_HOURS = 24 * 30
"""趋势查询的最大回溯窗口（30 天），防止面板传入超大值拖垮查询。"""


class MonitorService:
    """运行监控领域门面。"""

    def __init__(
        self,
        *,
        metrics: MetricSeriesRepository,
        recorder: MetricRecorder | None = None,
        logger: Any | None = None,
        retention_days: int = 30,
    ) -> None:
        self._metrics = metrics
        self._recorder = recorder or RECORDER
        self._logger = logger
        self._retention_days = max(0, int(retention_days))

    # ------------------------------------------------------------------ #
    # 落盘与清理
    # ------------------------------------------------------------------ #

    async def flush(self) -> int:
        """把内存桶批量写入时序表，返回写入行数。

        先 ``drain`` 再写库：即使写库失败也已清空内存，宁可丢一批指标也不重复累加
        （与仓储的 UPSERT 累加语义配合，重复写会导致数值翻倍）。
        """
        try:
            rows = self._recorder.drain()
        except Exception:
            self._warn("运行监控：读取内存指标失败")
            return 0
        if not rows:
            return 0
        try:
            await self._metrics.bump_many(rows)
        except Exception:
            self._warn("运行监控：指标落盘失败，丢弃 %s 行", len(rows))
            return 0
        return len(rows)

    async def purge(self, *, now: float | None = None) -> int:
        """删除早于保留期的时序数据，返回删除行数。"""
        try:
            moment = now if now is not None else time.time()
            before = moment - self._retention_days * 86400
            return await self._metrics.purge_before(before=before)
        except Exception:
            self._warn("运行监控：过期指标清理失败")
            return 0

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #

    async def overview(self, *, now: float | None = None, hours: int = 24) -> dict[str, Any]:
        """面板总览：当前小时即时快照 + 核心指标区间累计 + 已知指标名。"""
        span = max(1, int(hours))
        empty: dict[str, Any] = {"live": {}, "totals": {}, "metrics": [], "window_hours": span}
        try:
            moment = now if now is not None else time.time()
            since = moment - span * 3600
            totals: dict[str, dict[str, float]] = {}
            for metric in CORE_METRICS:
                totals[metric] = await self._metrics.totals(metric, since=since)
            return {
                "live": self._recorder.snapshot(),
                "totals": totals,
                "metrics": await self._metrics.metrics(),
                "window_hours": span,
            }
        except Exception:
            self._warn("运行监控：总览查询失败")
            return empty

    async def trends(
        self,
        *,
        metrics: Sequence[str],
        range_hours: int = 24,
        bucket_seconds: int = 3600,
        now: float | None = None,
    ) -> dict[str, Any]:
        """按指标批量取时间序列，供面板画折线。

        单个指标失败时其余指标仍返回（各自置空列表），避免一个坏指标拖垮整个图表。
        """
        span = max(1, min(int(range_hours), MAX_RANGE_HOURS))
        bucket = max(60, int(bucket_seconds))
        series: dict[str, list[dict[str, Any]]] = {}
        try:
            moment = now if now is not None else time.time()
            since = moment - span * 3600
            for metric in metrics or ():
                name = str(metric or "").strip()
                if not name or name in series:
                    continue
                try:
                    series[name] = await self._metrics.series(
                        name, since=since, bucket_seconds=bucket
                    )
                except Exception:
                    self._warn("运行监控：时序读取失败 %s", name)
                    series[name] = []
        except Exception:
            self._warn("运行监控：时序查询失败")
            return {"bucket_seconds": bucket, "series": {}}
        return {"bucket_seconds": bucket, "series": series}

    async def snapshot(self) -> dict[str, Any]:
        """运行态摘要（待落盘行数与保留期）。"""
        try:
            return {"pending": self._recorder.pending(), "retention_days": self._retention_days}
        except Exception:
            self._warn("运行监控：状态快照失败")
            return {"pending": 0, "retention_days": self._retention_days}

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)
