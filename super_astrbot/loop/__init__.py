"""循环控制层。

对上层提供四件基础设施：

- ``TaskScope``    任务作用域：取消、停止感知、代次令牌（防迟到结果写库）
- ``Scheduler``    幂等、可持久化、可收敛的任务调度
- ``ConcurrencyGate`` 按 key 的读共享/写独占门闸
- ``LLMBudget``    辅助调用预算（每日总量 + 并发上限）

本层不依赖 AstrBot，只依赖 ``spec`` 与抽象 ``StateStore``。
"""

from .budget import LLMBudget
from .concurrency import ConcurrencyGate
from .scheduler import JobSpec, Scheduler
from .state_store import MemoryStateStore, StateStore
from .task_scope import ABANDONED, ScopeToken, TaskScope

__all__ = [
    "TaskScope",
    "ScopeToken",
    "ABANDONED",
    "Scheduler",
    "JobSpec",
    "ConcurrencyGate",
    "LLMBudget",
    "StateStore",
    "MemoryStateStore",
]
