"""检索路抽象。

每条检索路（关键词 / 向量）只需实现 ``search`` 并返回**按相关性降序**的
``Candidate`` 列表；融合层只依赖「排名」，因此各路打分口径互不影响。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable

from ...spec.scopes import MemoryScope


@dataclass
class Candidate:
    """单条命中。"""

    memory_id: int
    rank: int = 0
    """0 基排名，越小越相关。"""
    relevance: float = 0.0
    """路内归一化相关性（0~1），仅用于展示与单路打分。"""
    route: str = ""


@dataclass
class RouteOutcome:
    """一条检索路的执行结果（含耗时与错误，便于面板排障）。"""

    route: str
    candidates: list[Candidate] = field(default_factory=list)
    elapsed_ms: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def hit_count(self) -> int:
        return len(self.candidates)


@runtime_checkable
class Retriever(Protocol):
    """检索路协议。"""

    name: str

    async def search(
        self,
        scopes: Sequence[MemoryScope],
        query: str,
        *,
        limit: int,
    ) -> list[Candidate]:
        """返回按相关性降序的候选。实现内部**不要**抛异常，失败返回空列表。"""


def build_candidates(ids_ordered: Sequence[int], *, route: str) -> list[Candidate]:
    """把「按相关性降序的 ID 列表」转成候选，并做线性衰减归一化。"""
    total = len(ids_ordered)
    if total == 0:
        return []
    return [
        Candidate(
            memory_id=int(memory_id),
            rank=index,
            relevance=1.0 if total == 1 else 1.0 - index / total,
            route=route,
        )
        for index, memory_id in enumerate(ids_ordered)
    ]
