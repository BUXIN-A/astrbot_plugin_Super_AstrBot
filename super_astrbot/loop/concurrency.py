"""按 key 的读写门闸。

场景：同一会话里可能有「读记忆」与「写记忆」并发发生（例如反思任务在写、用户消息在读）。
读操作可共享、写操作需独占，这样既保证一致性，又不至于把并发完全串死。

设计参考 AstrNa 的 ``GroupConcurrencyGate``（读写者条件变量），但抽象为通用 key，
不绑定具体业务。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict


class _Gate:
    """单个 key 的门闸状态。"""

    __slots__ = ("condition", "readers", "writer")

    def __init__(self) -> None:
        self.condition = asyncio.Condition()
        self.readers = 0
        self.writer = False


class ConcurrencyGate:
    """读共享、写独占的门闸集合。"""

    _MAX_IDLE_GATES = 256
    """空闲门闸超过该数量时先清理，避免长期运行下字典按会话数无限增长。"""

    def __init__(self, *, logger: Any | None = None) -> None:
        self._gates: Dict[str, _Gate] = {}
        self._logger = logger

    def _gate(self, key: str) -> _Gate:
        gate = self._gates.get(key)
        if gate is None:
            if len(self._gates) >= self._MAX_IDLE_GATES:
                self.prune_idle()
            gate = _Gate()
            self._gates[key] = gate
        return gate

    # ------------------------------------------------------------------ #
    # 读
    # ------------------------------------------------------------------ #

    @asynccontextmanager
    async def read(self, key: str) -> AsyncIterator[None]:
        """进入读共享区；有写者持有时等待。"""
        gate = self._gate(key)
        async with gate.condition:
            while gate.writer:
                await gate.condition.wait()
            gate.readers += 1
        try:
            yield
        finally:
            async with gate.condition:
                gate.readers -= 1
                if gate.readers <= 0:
                    gate.readers = 0
                    gate.condition.notify_all()

    # ------------------------------------------------------------------ #
    # 写
    # ------------------------------------------------------------------ #

    @asynccontextmanager
    async def write(self, key: str) -> AsyncIterator[None]:
        """进入写独占区；有读或写持有时等待。"""
        gate = self._gate(key)
        async with gate.condition:
            while gate.writer or gate.readers > 0:
                await gate.condition.wait()
            gate.writer = True
        try:
            yield
        finally:
            async with gate.condition:
                gate.writer = False
                gate.condition.notify_all()

    # ------------------------------------------------------------------ #
    # 观测
    # ------------------------------------------------------------------ #

    def stats(self) -> dict[str, dict[str, int]]:
        """当前门闸占用情况，供面板/排障使用。"""
        return {
            key: {"readers": gate.readers, "writer": int(gate.writer)}
            for key, gate in self._gates.items()
            if gate.readers or gate.writer
        }

    def prune_idle(self) -> int:
        """清理完全空闲的门闸，避免长跑后字典膨胀。"""
        idle = [key for key, gate in self._gates.items() if gate.readers == 0 and not gate.writer]
        for key in idle:
            self._gates.pop(key, None)
        return len(idle)
