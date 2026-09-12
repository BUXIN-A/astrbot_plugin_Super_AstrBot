"""记忆领域数据模型与常量。

状态机设计（关键）：

- ``buffered``：对话缓冲。自动采集进来的原始对话**不参与检索**，
  仅作为反思的原料，避免「每句话都变成记忆」把检索淹没；
- ``active``：正式记忆，参与检索与注入；
- ``pending``：等待人工审批（``reflection.approval_required=True``）；
- ``archived`` / ``forgotten``：已归档 / 已遗忘，不参与检索。

类型（``kind``）用于面板筛选与后续策略扩展，不改变检索行为。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# ------------------------------ 状态 ------------------------------ #

STATUS_ACTIVE = "active"
STATUS_BUFFERED = "buffered"
STATUS_PENDING = "pending"
STATUS_ARCHIVED = "archived"
STATUS_FORGOTTEN = "forgotten"

# ------------------------------ 类型 ------------------------------ #

KIND_EPISODE = "episode"
"""对话片段（缓冲态）。"""

KIND_FACT = "fact"
"""事实性记忆。"""

KIND_INSIGHT = "insight"
"""反思产出的洞察。"""

KIND_JOURNAL = "journal"
"""周记条目。"""

KIND_PREFERENCE = "preference"
"""用户偏好。"""

ALL_KINDS = (KIND_EPISODE, KIND_FACT, KIND_INSIGHT, KIND_JOURNAL, KIND_PREFERENCE)

# ------------------------------ 来源 ------------------------------ #

SOURCE_CAPTURE = "capture"
SOURCE_REFLECTION = "reflection"
SOURCE_WEEKLY = "weekly_reflection"
SOURCE_JOURNAL = "journal"
SOURCE_MANUAL = "manual"
SOURCE_AGENT = "agent"
"""由 Agent 函数工具写入。"""
SOURCE_IMPORT = "import"

# 来源 → 面板显示名
SOURCE_LABELS = {
    SOURCE_CAPTURE: "对话采集",
    SOURCE_REFLECTION: "反思",
    SOURCE_WEEKLY: "周度洞察",
    SOURCE_JOURNAL: "周记",
    SOURCE_MANUAL: "手动写入",
    SOURCE_AGENT: "Agent 写入",
    SOURCE_IMPORT: "导入",
}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []


@dataclass
class MemoryItem:
    """一条记忆。"""

    id: int
    scope_type: str = ""
    scope_id: str = ""
    kind: str = KIND_FACT
    content: str = ""
    importance: float = 0.5
    confidence: float = 0.8
    source: str = SOURCE_MANUAL
    tags: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    last_access_at: float = 0.0
    access_count: int = 0
    status: str = STATUS_ACTIVE

    # --- 检索结果附加（不入库） ---
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Any) -> "MemoryItem":
        """从数据库行/字典构造。"""
        # sqlite3.Row.keys() 每次调用都要构造列表，这里只取一次复用。
        keys = set(row.keys()) if hasattr(row, "keys") else None

        def _get(key: str, default: Any = None) -> Any:
            if keys is not None:
                return row[key] if key in keys else default
            return row.get(key, default)

        return cls(
            id=_as_int(_get("id", 0)),
            scope_type=str(_get("scope_type", "") or ""),
            scope_id=str(_get("scope_id", "") or ""),
            kind=str(_get("kind", KIND_FACT) or KIND_FACT),
            content=str(_get("content", "") or ""),
            importance=_as_float(_get("importance", 0.5), 0.5),
            confidence=_as_float(_get("confidence", 0.8), 0.8),
            source=str(_get("source", SOURCE_MANUAL) or SOURCE_MANUAL),
            tags=_as_tags(_get("tags", [])),
            created_at=_as_float(_get("created_at", 0.0)),
            updated_at=_as_float(_get("updated_at", 0.0)),
            last_access_at=_as_float(_get("last_access_at", 0.0)),
            access_count=_as_int(_get("access_count", 0)),
            status=str(_get("status", STATUS_ACTIVE) or STATUS_ACTIVE),
        )


@dataclass
class MemoryDraft:
    """写入记忆的请求体（由业务域构造，交由生命周期落库）。"""

    scope_type: str
    scope_id: str
    content: str
    kind: str = KIND_FACT
    importance: float = 0.5
    confidence: float = 0.8
    source: str = SOURCE_MANUAL
    tags: list[str] = field(default_factory=list)
    status: str = STATUS_ACTIVE
