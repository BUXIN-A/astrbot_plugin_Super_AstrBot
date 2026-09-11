"""辅助 LLM 调用的预算守卫。

两道闸门：

1. **每日总量**：``daily_limit > 0`` 时，当日累计调用次数达上限即拒绝（次日自动重置）；
2. **并发上限**：``max_concurrent`` 控制同时进行的辅助调用数，避免抢占主对话资源。

「计数」记录尝试次数且不回退（失败也算一次尝试），避免失败重试把成本打爆；
「并发」在调用结束后释放。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable


class LLMBudget:
    """实现 ``harness.protocols.BudgetGuard``。"""

    def __init__(
        self,
        *,
        daily_limit: int = 0,
        max_concurrent: int = 2,
        logger: Any | None = None,
        clock: Callable[[], float] | None = None,
        day_key: Callable[[], str] | None = None,
    ) -> None:
        self._daily_limit = max(0, int(daily_limit or 0))
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrent or 1)))
        self._lock = asyncio.Lock()
        self._logger = logger
        self._clock = clock or time.time
        self._day_key = day_key or (
            lambda: time.strftime("%Y-%m-%d", time.localtime(self._clock()))
        )
        self._day = self._day_key()
        self._count = 0
        self._by_purpose: dict[str, int] = {}
        self._rejected = 0

    # ------------------------------------------------------------------ #
    # 契约实现
    # ------------------------------------------------------------------ #

    async def try_acquire(self, purpose: str) -> bool:
        await self._rollover()
        async with self._lock:
            if self._daily_limit > 0 and self._count >= self._daily_limit:
                self._rejected += 1
                if self._logger is not None:
                    self._logger.warning(
                        "辅助调用已达每日上限 %d，本次跳过（purpose=%s）",
                        self._daily_limit,
                        purpose,
                    )
                return False
            self._count += 1
            self._by_purpose[purpose] = self._by_purpose.get(purpose, 0) + 1

        await self._semaphore.acquire()
        return True

    def release(self, purpose: str) -> None:
        try:
            self._semaphore.release()
        except ValueError:
            # 释放次数多于获取次数属于编程错误，但不应影响主流程。
            if self._logger is not None:
                self._logger.debug("预算释放次数异常（purpose=%s）", purpose)

    def snapshot(self) -> dict[str, Any]:
        return {
            "day": self._day,
            "used": self._count,
            "daily_limit": self._daily_limit,
            "remaining": (
                max(0, self._daily_limit - self._count) if self._daily_limit > 0 else None
            ),
            "rejected": self._rejected,
            "by_purpose": dict(self._by_purpose),
            "max_concurrent": getattr(self._semaphore, "_value", None),
        }

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    async def _rollover(self) -> None:
        """跨日重置计数。"""
        today = self._day_key()
        if today == self._day:
            return
        async with self._lock:
            if today == self._day:
                return
            self._day = today
            self._count = 0
            self._by_purpose = {}
            self._rejected = 0
            if self._logger is not None:
                self._logger.info("辅助调用预算已跨日重置（%s）", today)
