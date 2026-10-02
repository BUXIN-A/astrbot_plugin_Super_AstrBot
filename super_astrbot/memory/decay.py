"""艾宾浩斯遗忘曲线（AMBRACE ``memory/decay.py`` 的进程内重写）。

公式（与 AMBRACE 逐字一致）：``R = exp(-Δt / S)``，其中 ``Δt`` 为距上次强化
的天数、``S`` 为记忆强度（天）。每次强化（检索命中 / 主动复习）令 ``S`` 递增，
遗忘变慢——这正是复习间隔递增的艾宾浩斯规律。

本模块与既有 ``support.decay.half_life_factor`` 的关系：
- ``half_life_factor`` 是「每过一天乘一个系数」的**离散**口径，服务于
  检索打分 / 权重维护（既有行为，不动）；
- 本模块是**连续**保留率口径，服务于「记忆后端」页的双曲线展示，
  以及可选的「写回重要度」维护（默认关闭，避免与既有打分口径打架）。

合规：不依赖任何外部服务；``apply_retention`` 用一条 SQL 批量推进，
在调度器的每日维护里调用（受能力开关 ``fusion.decay`` + 写回开关双重约束）。
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, Sequence

DEFAULT_STRENGTH_DAYS = 7.0
"""默认记忆强度（天）：与 AMBRACE ``S_DEFAULT`` 同量级。"""

DECAY_MAX_PCT = 120.0
"""保留率上限（百分比）：AMBRACE 用 ×120 表达「新记忆略高于 100%」。"""

DECAY_THRESHOLD_PCT = 20.0
"""低于该保留率即视为「接近遗忘」，面板标红、可触发冷归档建议。"""

DEFAULT_CURVE_POINTS: tuple[int, ...] = (0, 1, 2, 4, 7, 15, 30, 60)
"""曲线采样点（天）：前密后疏，覆盖艾宾浩斯经典复习间隔。"""


def retention_pct(elapsed_days: float, strength_days: float) -> float:
    """艾宾浩斯保留率百分比（钳制在 ``[0, DECAY_MAX_PCT]``）。"""
    strength = max(0.5, float(strength_days or DEFAULT_STRENGTH_DAYS))
    elapsed = max(0.0, float(elapsed_days))
    value = math.exp(-elapsed / strength) * DECAY_MAX_PCT
    return max(0.0, min(DECAY_MAX_PCT, value))


def effective_strength(
    *,
    base_strength: float = DEFAULT_STRENGTH_DAYS,
    importance: float = 0.5,
    confidence: float = 0.8,
    access_count: int = 0,
) -> float:
    """分层强度：高价值 / 高可靠 / 常访问的记忆遗忘更慢。

    派生规则（可审计的线性组合，无隐藏参数）：
    ``S = base × (0.5 + importance) × (0.6 + 0.4 × confidence) + 0.5 × ln(1 + access)``
    """
    try:
        importance_v = max(0.0, min(1.0, float(importance)))
    except (TypeError, ValueError):
        importance_v = 0.5
    try:
        confidence_v = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence_v = 0.8
    try:
        accesses = max(0, int(access_count))
    except (TypeError, ValueError):
        accesses = 0
    base = max(0.5, float(base_strength or DEFAULT_STRENGTH_DAYS))
    value = base * (0.5 + importance_v) * (0.6 + 0.4 * confidence_v)
    value += 0.5 * math.log1p(accesses)
    return round(min(365.0, value), 4)


def curve_points(
    *,
    strength_days: float = DEFAULT_STRENGTH_DAYS,
    reviewed_strength_days: float | None = None,
    points: Sequence[int] = DEFAULT_CURVE_POINTS,
) -> dict[str, Any]:
    """自然遗忘与「主动复习后」的双曲线数据（面板 SVG 折线图）。"""
    natural = [
        {"day": int(day), "retention": round(retention_pct(day, strength_days), 2)}
        for day in points
    ]
    reviewed_strength = float(reviewed_strength_days or strength_days * 3.0)
    reviewed = [
        {"day": int(day), "retention": round(retention_pct(day, reviewed_strength), 2)}
        for day in points
    ]
    return {
        "points": [int(day) for day in points],
        "natural": natural,
        "reviewed": reviewed,
        "strength_days": round(float(strength_days), 4),
        "reviewed_strength_days": round(reviewed_strength, 4),
        "threshold_pct": DECAY_THRESHOLD_PCT,
        "max_pct": DECAY_MAX_PCT,
    }


class DecayService:
    """保留率展示 + 可选写回。

    为什么写回默认关闭：既有检索打分以 ``memories.importance`` 为权重，
    直接改写会让「检索排序」随维护任务漂移；作为可选维护项，由用户在
    「功能」页显式开启，且写回的是**保留率派生的重要度**，不删除任何记忆。
    """

    def __init__(
        self,
        *,
        db: Any | None = None,
        base_strength_days: float = DEFAULT_STRENGTH_DAYS,
        threshold_pct: float = DECAY_THRESHOLD_PCT,
        write_back: bool = False,
        clock: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._db = db
        self._base_strength = float(base_strength_days or DEFAULT_STRENGTH_DAYS)
        self._threshold = float(threshold_pct or DECAY_THRESHOLD_PCT)
        self._write_back = bool(write_back)
        self._clock = clock or time.time
        self._logger = logger

    def enabled(self) -> bool:
        return self._db is not None

    @property
    def write_back(self) -> bool:
        return self._write_back

    def curves(self) -> dict[str, Any]:
        return curve_points(strength_days=self._base_strength)

    async def overview(self, *, limit: int = 8) -> dict[str, Any]:
        """面板「记忆后端」页数据：曲线 + 分布 + 最接近遗忘的记忆。"""
        payload: dict[str, Any] = {
            "curves": self.curves(),
            "write_back": self._write_back,
            "threshold_pct": self._threshold,
            "strength_days": self._base_strength,
        }
        if self._db is None:
            return payload
        now = self._clock()
        rows = await self._db.query(
            "SELECT id, content, importance, confidence, access_count, created_at,"
            " last_access_at, sender_name, sender_id FROM memories"
            " WHERE status='active' ORDER BY importance ASC LIMIT 2000"
        )
        samples: list[dict[str, Any]] = []
        below = 0
        buckets = {"core": 0, "stable": 0, "fading": 0, "at_risk": 0}
        for row in rows:
            base = float(row["last_access_at"] or row["created_at"] or now)
            elapsed_days = max(0.0, (now - base) / 86400.0)
            strength = effective_strength(
                base_strength=self._base_strength,
                importance=float(row["importance"] or 0.5),
                confidence=float(row["confidence"] or 0.8),
                access_count=int(row["access_count"] or 0),
            )
            pct = retention_pct(elapsed_days, strength)
            if pct < self._threshold:
                below += 1
            if pct >= 80:
                buckets["core"] += 1
            elif pct >= 50:
                buckets["stable"] += 1
            elif pct >= self._threshold:
                buckets["fading"] += 1
            else:
                buckets["at_risk"] += 1
            samples.append(
                {
                    "id": int(row["id"]),
                    "content": str(row["content"] or "")[:80],
                    "retention": round(pct, 2),
                    "strength_days": strength,
                    "elapsed_days": round(elapsed_days, 2),
                    "importance": round(float(row["importance"] or 0.0), 4),
                    "sender_name": str(row["sender_name"] or ""),
                    "sender_id": str(row["sender_id"] or ""),
                }
            )
        samples.sort(key=lambda item: item["retention"])
        total = len(samples)
        payload.update(
            {
                "total": total,
                "below_threshold": below,
                "below_ratio": round(below / total, 4) if total else 0.0,
                "buckets": buckets,
                "samples": samples[: max(1, min(50, int(limit)))],
            }
        )
        return payload

    async def apply_retention(self) -> dict[str, Any]:
        """把保留率写回 ``memories.importance``（仅当写回开关开启时执行）。

        幂等：保留率由「距上次访问 / 创建」的绝对时长计算，重复执行同值；
        只更新 ``active`` 记忆，且不做任何删除 / 归档动作。
        """
        if self._db is None:
            return {"ok": False, "message": "持久层未就绪"}
        if not self._write_back:
            return {"ok": False, "message": "写回未开启（功能页可开）"}
        now = self._clock()
        rows = await self._db.query(
            "SELECT id, importance, confidence, access_count, created_at, last_access_at"
            " FROM memories WHERE status='active' LIMIT 20000"
        )
        updated = 0
        for row in rows:
            base = float(row["last_access_at"] or row["created_at"] or now)
            elapsed_days = max(0.0, (now - base) / 86400.0)
            strength = effective_strength(
                base_strength=self._base_strength,
                importance=float(row["importance"] or 0.5),
                confidence=float(row["confidence"] or 0.8),
                access_count=int(row["access_count"] or 0),
            )
            pct = retention_pct(elapsed_days, strength)
            normalized = max(0.0, min(1.0, pct / DECAY_MAX_PCT))
            await self._db.execute(
                "UPDATE memories SET importance=? WHERE id=?", (round(normalized, 4), int(row["id"]))
            )
            updated += 1
        return {"ok": True, "updated": updated, "threshold_pct": self._threshold}

    async def stats(self) -> dict[str, Any]:
        if self._db is None:
            return {"memories": 0}
        total = await self._db.scalar(
            "SELECT COUNT(*) FROM memories WHERE status='active'", (), 0
        )
        avg_importance = await self._db.scalar(
            "SELECT AVG(importance) FROM memories WHERE status='active'", (), 0.0
        )
        return {
            "memories": int(total or 0),
            "avg_importance": round(float(avg_importance or 0.0), 4),
            "strength_days": self._base_strength,
            "write_back": self._write_back,
        }

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            try:
                self._logger.warning(message, *args)
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "DECAY_MAX_PCT",
    "DECAY_THRESHOLD_PCT",
    "DEFAULT_CURVE_POINTS",
    "DEFAULT_STRENGTH_DAYS",
    "DecayService",
    "curve_points",
    "effective_strength",
    "retention_pct",
]
