"""调度器状态存储协议与内存实现。

调度器需要「当日幂等」能力：同一 job 在同一天只执行一次，且跨插件重载/进程重启
不能重复触发（参考 livingmemory_ext 的 ``is_due`` + 状态文件做法）。
这里把状态读写抽象出来，``storage`` 层提供 SQLite 实现，默认使用内存实现（仅测试/降级）。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StateStore(Protocol):
    """最小状态存储：按 key 读写可 JSON 序列化的值。"""

    async def get(self, key: str, default: Any = None) -> Any: ...

    async def set(self, key: str, value: Any) -> None: ...


class MemoryStateStore:
    """进程内实现。用于测试或存储不可用时的降级（重启后会丢失幂等记录）。"""

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = dict(initial or {})

    async def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    async def set(self, key: str, value: Any) -> None:
        self._data[key] = value
