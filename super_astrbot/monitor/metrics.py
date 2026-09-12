"""运行监控：内存态指标收集器。

设计动机：埋点散落在 LLM 调用、检索、注入、调度等各处，若每次事件都直接写库，
会产生大量小事务与写放大；而面板又希望能实时看到「当前正在发生什么」。
因此这里分成两层：

- **内存层**（本模块）：埋点只做纯内存累加（按小时桶聚合），零 I/O、零 await；
- **落盘层**（``service``）：由定时任务把内存桶 ``drain`` 出来批量 UPSERT。

并发说明：埋点的「读改写」全程不出现 ``await``，因此即使同一事件循环内多个协程
交错调用，单个 ``record`` 也是原子的（不存在让出点），无需加锁。

内存上限：长时间运行下，若落盘任务迟迟不执行，内存桶会持续膨胀。这里用
``max_pending`` 做兜底——超过上限时按桶时间丢弃最旧的一半，宁可损失早期指标
也不让进程内存无界增长。
"""

from __future__ import annotations

import time
from typing import Any, Callable

from ..spec.scopes import MemoryScope

# --------------------------------------------------------------------------- #
# 指标名常量
#
# 统一收敛为常量，避免埋点处手写字符串拼错；命名沿用「域.动作」的点分风格，
# 便于面板按前缀分组展示。
# --------------------------------------------------------------------------- #

METRIC_LLM_CALLS = "llm.calls"
"""辅助 LLM 调用次数（count）。"""

METRIC_LLM_ERRORS = "llm.errors"
"""辅助 LLM 调用失败次数（count）。"""

METRIC_LLM_LATENCY_MS = "llm.latency_ms"
"""辅助 LLM 耗时累计（total 为毫秒和，count 为次数，可算均值）。"""

METRIC_LLM_TOKENS = "llm.tokens"
"""辅助 LLM token 累计（total）。"""

METRIC_LLM_BUDGET_BLOCKED = "llm.budget_blocked"
"""预算拒绝次数（count）。"""

METRIC_RETRIEVAL_CALLS = "retrieval.calls"
"""记忆检索次数（count）。"""

METRIC_RETRIEVAL_LATENCY_MS = "retrieval.latency_ms"
"""记忆检索耗时累计（total 为毫秒和）。"""

METRIC_RETRIEVAL_HITS = "retrieval.hits"
"""记忆检索命中条数累计（total）。"""

METRIC_RERANK_CALLS = "rerank.calls"
"""重排序模型调用次数（count）。"""

METRIC_RERANK_FAILURES = "rerank.failures"
"""重排序调用失败/超时次数（count）；失败后走回退策略。"""

METRIC_RERANK_LATENCY_MS = "rerank.latency_ms"
"""重排序耗时累计（total 为毫秒和）。"""

METRIC_RERANK_CANDIDATES = "rerank.candidates"
"""送入重排序的候选条数累计（total）。"""

METRIC_INJECT_BLOCKS = "inject.blocks"
"""注入块数累计（total）。"""

METRIC_INJECT_CHARS = "inject.chars"
"""注入字符数累计（total）。"""

METRIC_MEMORY_WRITES = "memory.writes"
"""记忆写入次数（count）。"""

METRIC_MEMORY_TOTAL = "memory.total"
"""当前记忆总量（gauge，落在 last_value）。"""

METRIC_REVIEW_PENDING = "review.pending"
"""待审队列长度（gauge，落在 last_value）。"""

METRIC_REVIEW_AUTO_APPROVED = "review.auto_approved"
"""自动审核通过次数（count）。"""

METRIC_REVIEW_AUTO_REJECTED = "review.auto_rejected"
"""自动审核拒绝次数（count）。"""

METRIC_PERSONA_LEARNED = "persona.learned"
"""拟人化表达模式学习条数（count）。"""

METRIC_PERSONA_INJECTED = "persona.injected"
"""拟人化表达注入次数（count）。"""

METRIC_GRAPH_INDEXED = "graph.indexed"
"""知识图谱索引写入次数（count）。"""

METRIC_GRAPH_ENTITIES = "graph.entities"
"""图谱实体总量（gauge，落在 last_value）。"""

METRIC_PROACTIVE_SENT = "proactive.sent"
"""主动消息发送次数（count）。"""

METRIC_PROACTIVE_SKIPPED = "proactive.skipped"
"""主动消息因限流/静默被跳过次数（count）。"""

METRIC_GROUP_INTERJECT = "group.interject"
"""群聊主动插话次数（count）。"""

METRIC_SCHEDULER_RUNS = "scheduler.runs"
"""调度任务执行次数（count）。"""

METRIC_SCHEDULER_FAILURES = "scheduler.failures"
"""调度任务失败次数（count）。"""

METRIC_SCHEDULER_DURATION_MS = "scheduler.duration_ms"
"""调度任务耗时累计（total 为毫秒和）。"""

CORE_METRICS: tuple[str, ...] = (
    METRIC_LLM_CALLS,
    METRIC_LLM_ERRORS,
    METRIC_RETRIEVAL_CALLS,
    METRIC_RERANK_CALLS,
    METRIC_RERANK_FAILURES,
    METRIC_INJECT_CHARS,
    METRIC_SCHEDULER_RUNS,
    METRIC_SCHEDULER_FAILURES,
    METRIC_MEMORY_WRITES,
)
"""面板总览默认展示的核心指标。"""


class MetricRecorder:
    """内存态小时桶指标收集器。

    条目键为 ``(bucket_ts, metric, scope_type, scope_id)``，与 ``metric_series``
    表的主键一一对应，``drain`` 产出的行可直接交给仓储 ``bump_many``。
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        bucket_seconds: int = 3600,
        max_pending: int = 5000,
    ) -> None:
        self._clock = clock or time.time
        self._bucket_seconds = max(1, int(bucket_seconds))
        self._max_pending = max(1, int(max_pending))
        self._pending: dict[tuple[int, str, str, str], dict[str, float]] = {}

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #

    def record(
        self,
        metric: str,
        *,
        count: int = 1,
        total: float = 0.0,
        gauge: float | None = None,
        scope: MemoryScope | None = None,
    ) -> None:
        """累加一次指标。

        ``gauge`` 非空时覆盖 ``last_value``（取最新值），用于「当前总量」这类
        瞬时量；其余指标保持区间累加语义。
        """
        if not metric:
            return
        key = (self.bucket_of(self._clock()), metric, *self._scope_key(scope))
        entry = self._pending.get(key)
        if entry is None:
            entry = {"count": 0.0, "total": 0.0, "last_value": 0.0}
            self._pending[key] = entry
            self._trim_if_needed()
        entry["count"] += int(count)
        entry["total"] += float(total)
        if gauge is not None:
            entry["last_value"] = float(gauge)

    def observe(self, metric: str, value: float, *, scope: MemoryScope | None = None) -> None:
        """记录一次观测：``count=1``、``total=value``（如单次耗时、命中条数）。"""
        self.record(metric, count=1, total=float(value), scope=scope)

    # ------------------------------------------------------------------ #
    # 落盘
    # ------------------------------------------------------------------ #

    def drain(self) -> list[dict[str, Any]]:
        """取出并清空待落盘行；返回结构可直接交给 ``MetricSeriesRepository.bump_many``。"""
        rows = [
            {
                "bucket_ts": bucket,
                "metric": metric,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "count": int(entry["count"]),
                "total": float(entry["total"]),
                "last_value": float(entry["last_value"]),
            }
            for (bucket, metric, scope_type, scope_id), entry in self._pending.items()
        ]
        self._pending.clear()
        return rows

    def pending(self) -> int:
        """待落盘行数。"""
        return len(self._pending)

    # ------------------------------------------------------------------ #
    # 读快照
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict[str, Any]:
        """当前小时桶的即时快照（跨作用域合并），供面板实时展示，不落库。"""
        current = self.bucket_of(self._clock())
        result: dict[str, dict[str, float]] = {}
        for (bucket, metric, _scope_type, _scope_id), entry in self._pending.items():
            if bucket != current:
                continue
            aggregate = result.get(metric)
            if aggregate is None:
                aggregate = {"count": 0.0, "total": 0.0, "last_value": 0.0}
                result[metric] = aggregate
            aggregate["count"] += entry["count"]
            aggregate["total"] += entry["total"]
            aggregate["last_value"] = entry["last_value"]
        return result

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #

    def bucket_of(self, moment: float) -> int:
        """把时刻对齐到小时桶起始秒。"""
        seconds = self._bucket_seconds
        return int(moment // seconds * seconds)

    @staticmethod
    def _scope_key(scope: MemoryScope | None) -> tuple[str, str]:
        """缺省作用域用空串表示，与仓储默认参数保持一致。"""
        if scope is None:
            return "", ""
        return scope.scope_type.value, scope.scope_id

    def _trim_if_needed(self) -> None:
        """超过上限时丢弃最旧的一半桶。"""
        if len(self._pending) <= self._max_pending:
            return
        order = sorted(self._pending, key=lambda item: (item[0], item[1], item[2], item[3]))
        for key in order[: len(order) // 2]:
            self._pending.pop(key, None)


RECORDER = MetricRecorder()
"""模块级单例；埋点处直接使用下面的便捷函数即可。"""


def record(
    metric: str,
    *,
    count: int = 1,
    total: float = 0.0,
    gauge: float | None = None,
    scope: MemoryScope | None = None,
) -> None:
    """便捷埋点：转发到全局 ``RECORDER``。"""
    RECORDER.record(metric, count=count, total=total, gauge=gauge, scope=scope)


def observe(metric: str, value: float, *, scope: MemoryScope | None = None) -> None:
    """便捷观测：转发到全局 ``RECORDER``。"""
    RECORDER.observe(metric, value, scope=scope)
