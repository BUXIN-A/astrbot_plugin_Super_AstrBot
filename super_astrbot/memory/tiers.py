"""letta 三级记忆的概念移植（核心 / 归档 / 召回）。

为什么是「概念移植」而非引入服务：letta（原 MemGPT）的三级存储由独立服务
承担；本项目按施行总纲 §0.5 只借用其分层思想，在进程内**派生**分级：

- **core（核心）**：高重要度 / 高频访问 / 稳定偏好 —— 相当于「常驻上下文」；
- **recall（召回）**：普通记忆 —— 需要时由混合召回取用；
- **archive（归档）**：低重要度或已归档 —— 不参与注入，只在面板可查。

分级是**只读派生**：不新增列、不改写记忆，任何时候都能用同一套规则重算，
因此不存在「分级写错后无法回退」的问题。检索侧行为不变（既有权重排序照旧），
本模块只回答「这份记忆处于哪一级、占比如何」。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Sequence

TIER_CORE = "core"
TIER_RECALL = "recall"
TIER_ARCHIVE = "archive"

TIER_ORDER: tuple[str, ...] = (TIER_CORE, TIER_RECALL, TIER_ARCHIVE)

TIER_LABELS: dict[str, str] = {
    TIER_CORE: "核心",
    TIER_RECALL: "召回",
    TIER_ARCHIVE: "归档",
}

TIER_HINTS: dict[str, str] = {
    TIER_CORE: "高重要度 / 高频访问 / 稳定偏好：相当于常驻上下文，注入时优先。",
    TIER_RECALL: "普通记忆：需要时由混合召回取用。",
    TIER_ARCHIVE: "低重要度或已归档：不参与注入，仅面板可查。",
}

CORE_IMPORTANCE = 0.75
"""判定为核心层的重要度门槛。"""

CORE_ACCESS = 3
"""判定为核心层的访问次数门槛（被反复用到说明它重要）。"""

ARCHIVE_IMPORTANCE = 0.35
"""低于该重要度即视为归档层。"""

_STABLE_KINDS = ("preference", "insight")


def classify(memory: Mapping[str, Any]) -> str:
    """按只读规则给一条记忆分级；入参可以是 ORM 行 / dict / MemoryItem。"""
    status = str(_get(memory, "status", "active") or "active")
    if status in {"archived", "forgotten"}:
        return TIER_ARCHIVE
    try:
        importance = float(_get(memory, "importance", 0.5) or 0.0)
    except (TypeError, ValueError):
        importance = 0.5
    try:
        accesses = int(_get(memory, "access_count", 0) or 0)
    except (TypeError, ValueError):
        accesses = 0
    try:
        confidence = float(_get(memory, "confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    kind = str(_get(memory, "kind", "") or "")
    if importance >= CORE_IMPORTANCE or accesses >= CORE_ACCESS:
        return TIER_CORE
    if importance < ARCHIVE_IMPORTANCE:
        return TIER_ARCHIVE
    if kind in _STABLE_KINDS and confidence >= 0.8 and importance >= 0.6:
        return TIER_CORE
    return TIER_RECALL


def _get(memory: Any, key: str, default: Any = None) -> Any:
    if isinstance(memory, Mapping):
        return memory.get(key, default)
    return getattr(memory, key, default)


class TierService:
    """三级占比与样本（面板「记忆后端」页）。"""

    def __init__(
        self,
        *,
        db: Any | None = None,
        clock: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._db = db
        self._clock = clock or time.time
        self._logger = logger

    def enabled(self) -> bool:
        return self._db is not None

    async def overview(self, *, samples: int = 5) -> dict[str, Any]:
        if self._db is None:
            return self._empty()
        rows = await self._db.query(
            "SELECT id, content, kind, status, importance, confidence, access_count,"
            " sender_name, sender_id, created_at, updated_at FROM memories LIMIT 20000"
        )
        buckets: dict[str, list[dict[str, Any]]] = {tier: [] for tier in TIER_ORDER}
        for row in rows:
            tier = classify(
                {
                    "status": row["status"],
                    "importance": row["importance"],
                    "confidence": row["confidence"],
                    "access_count": row["access_count"],
                    "kind": row["kind"],
                }
            )
            buckets[tier].append(
                {
                    "id": int(row["id"]),
                    "content": str(row["content"] or "")[:120],
                    "kind": str(row["kind"] or ""),
                    "status": str(row["status"] or ""),
                    "importance": round(float(row["importance"] or 0.0), 4),
                    "access_count": int(row["access_count"] or 0),
                    "sender_name": str(row["sender_name"] or ""),
                    "sender_id": str(row["sender_id"] or ""),
                }
            )
        total = sum(len(items) for items in buckets.values())
        payload = {
            "total": total,
            "tiers": [
                {
                    "tier": tier,
                    "label": TIER_LABELS[tier],
                    "hint": TIER_HINTS[tier],
                    "count": len(buckets[tier]),
                    "share": round(len(buckets[tier]) / total, 4) if total else 0.0,
                    "samples": sorted(
                        buckets[tier],
                        key=lambda item: (item["importance"], item["access_count"]),
                        reverse=True,
                    )[: max(1, min(20, int(samples)))],
                }
                for tier in TIER_ORDER
            ],
            "thresholds": {
                "core_importance": CORE_IMPORTANCE,
                "core_access": CORE_ACCESS,
                "archive_importance": ARCHIVE_IMPORTANCE,
            },
        }
        return payload

    async def stats(self) -> dict[str, Any]:
        data = await self.overview(samples=1)
        if not data.get("tiers"):
            return self._empty()
        return {
            "total": data.get("total", 0),
            "counts": {item["tier"]: item["count"] for item in data["tiers"]},
            "shares": {item["tier"]: item["share"] for item in data["tiers"]},
        }

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "total": 0,
            "tiers": [
                {
                    "tier": tier,
                    "label": TIER_LABELS[tier],
                    "hint": TIER_HINTS[tier],
                    "count": 0,
                    "share": 0.0,
                    "samples": [],
                }
                for tier in TIER_ORDER
            ],
            "thresholds": {
                "core_importance": CORE_IMPORTANCE,
                "core_access": CORE_ACCESS,
                "archive_importance": ARCHIVE_IMPORTANCE,
            },
        }

    def order_memories(self, items: Sequence[Any]) -> list[Any]:
        """按层级给注入候选排序（核心优先）；同级保持既有顺序（稳定排序）。"""
        weight = {TIER_CORE: 0, TIER_RECALL: 1, TIER_ARCHIVE: 2}
        return sorted(items, key=lambda item: weight.get(classify(item), 1))

    def _debug(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            try:
                self._logger.debug(message, *args)
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "ARCHIVE_IMPORTANCE",
    "CORE_ACCESS",
    "CORE_IMPORTANCE",
    "TIER_ARCHIVE",
    "TIER_CORE",
    "TIER_HINTS",
    "TIER_LABELS",
    "TIER_ORDER",
    "TIER_RECALL",
    "TierService",
    "classify",
]
