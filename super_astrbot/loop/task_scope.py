"""任务作用域：把「可取消、可停止感知、防迟到结果」收敛成一个基础设施。

为什么需要它（对照分析文档「循环控制机制」）：

- 辅助 LLM 调用（反思、摘要）可能耗时数十秒，用户执行 ``/stop`` 后不应继续等待；
- 任务在等待期间可能被取消/超时，旧任务的结果若写库会污染新状态 → 用**代次令牌**丢弃；
- 后台任务必须可枚举、可在插件卸载时全部收敛，避免游离任务（self_learning 的经验）。

用法::

    scope = TaskScope("reflection", logger=log)
    token = scope.token()
    result = await scope.run(lambda: llm.chat(...), timeout=30, token=token)
    if result is not ABANDONED and scope.is_current(token):
        ...  # 安全地使用结果
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from ..spec.errors import safe_detail


@dataclass(frozen=True)
class ScopeToken:
    """代次令牌：作用域标识 + 代次号。代次变化后旧令牌即失效。"""

    scope: str
    generation: int


class _Abandoned:
    """哨兵：表示任务被放弃（停止 / 超时 / 令牌失效）。

    必须与「任务正常返回 ``None``」区分开——否则所有正常结束的任务都会被
    误判为超时（调度器会误报 WARN 并计入跳过）。
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "ABANDONED"

    def __bool__(self) -> bool:
        return False


ABANDONED = _Abandoned()
"""``TaskScope.run`` 在放弃执行时返回该哨兵。"""


class TaskScope:
    """一个可取消的任务作用域。"""

    def __init__(
        self,
        name: str,
        *,
        poll_interval: float = 0.05,
        logger: Any | None = None,
    ) -> None:
        self._name = name
        self._poll = max(0.01, float(poll_interval or 0.05))
        self._logger = logger
        self._generation = 0
        self._cancelled = False
        self._tasks: set[asyncio.Task[Any]] = set()
        self._stop_checkers: list[Callable[[], bool]] = []

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #

    @property
    def name(self) -> str:
        return self._name

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def token(self) -> ScopeToken:
        return ScopeToken(scope=self._name, generation=self._generation)

    def is_current(self, token: ScopeToken | None) -> bool:
        """令牌是否仍然有效（作用域未取消且代次未变）。"""
        if token is None:
            return True
        if self._cancelled:
            return False
        return token.scope == self._name and token.generation == self._generation

    def bump_generation(self) -> ScopeToken:
        """推进代次，使所有旧令牌失效（例如会话被重置时调用）。"""
        self._generation += 1
        return self.token()

    def register_stop_checker(self, checker: Callable[[], bool]) -> None:
        """注册一个外部停止信号（例如 ``lambda: event.is_stopped()``）。"""
        self._stop_checkers.append(checker)

    def is_stopped(self) -> bool:
        if self._cancelled:
            return True
        for checker in self._stop_checkers:
            try:
                if checker():
                    return True
            except Exception as exc:  # noqa: BLE001 - 停止检查不应成为新的故障点
                self._log_debug("停止检查器异常：%s", safe_detail(exc))
        return False

    def cancel(self) -> None:
        """标记取消；不会立即中断正在等待的 ``run``（由 ``run`` 轮询感知）。"""
        self._cancelled = True

    # ------------------------------------------------------------------ #
    # 任务管理
    # ------------------------------------------------------------------ #

    def spawn(self, coro: Awaitable[Any], *, name: str = "") -> asyncio.Task[Any]:
        """创建并跟踪一个后台任务。

        任务完成时自动从集合移除；异常只记录不抛出（fire-and-forget 语义）。
        """
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        label = name or getattr(coro, "__qualname__", "task")

        def _done(finished: asyncio.Task[Any]) -> None:
            self._tasks.discard(finished)
            if finished.cancelled():
                return
            error = finished.exception()
            if error is not None:
                self._log_warning(
                    "[%s] 后台任务 %s 异常：%s", self._name, label, safe_detail(error)
                )

        task.add_done_callback(_done)
        return task

    def pending_count(self) -> int:
        return len(self._tasks)

    async def cancel_all(self, *, timeout: float = 5.0) -> int:
        """取消并等待全部任务收敛，返回取消数量。

        超时后放弃等待（仅告警），保证插件卸载不被卡死。
        """
        tasks = [task for task in self._tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait(tasks, timeout=timeout)
            remaining = [task for task in tasks if not task.done()]
            if remaining:
                self._log_warning(
                    "[%s] 仍有 %d 个任务未在 %.1fs 内收敛", self._name, len(remaining), timeout
                )
        self._cancelled = True
        return len(tasks)

    # ------------------------------------------------------------------ #
    # 停止感知执行
    # ------------------------------------------------------------------ #

    async def run(
        self,
        factory: Callable[[], Awaitable[Any]],
        *,
        timeout: float | None = None,
        token: ScopeToken | None = None,
    ) -> Any:
        """在停止感知下执行一个协程。

        Args:
            factory: 协程工厂（延迟创建，避免在取消后仍创建协程对象）。
            timeout: 超时秒数；超时按「放弃」处理。
            token: 代次令牌；非当前代次直接放弃。

        Returns:
            协程结果；被停止、超时或令牌失效时返回 ``ABANDONED`` 哨兵
            （注意：任务本身正常返回 ``None`` 时也会原样返回 ``None``，
            调用方必须用 ``is ABANDONED`` 而不是 ``is None`` 判断放弃）。
            协程自身抛出的异常会原样向上传递（交由调用方决定降级策略）。
        """
        if self.is_stopped():
            return ABANDONED
        if not self.is_current(token):
            self._log_debug("令牌已失效，跳过执行：%s", token)
            return ABANDONED

        loop = asyncio.get_running_loop()
        task: asyncio.Task[Any] = asyncio.ensure_future(factory())
        deadline = None if timeout is None else loop.time() + max(0.1, float(timeout))

        try:
            while True:
                if self.is_stopped():
                    await self._abandon(task)
                    return ABANDONED
                remaining = None if deadline is None else deadline - loop.time()
                if remaining is not None and remaining <= 0:
                    await self._abandon(task)
                    self._log_debug("执行超时（%.1fs），已放弃", timeout or 0)
                    return ABANDONED
                wait_slice = self._poll if remaining is None else min(self._poll, remaining)
                done, _ = await asyncio.wait({task}, timeout=wait_slice)
                if done:
                    return task.result()
        except asyncio.CancelledError:
            await self._abandon(task)
            raise

    async def _abandon(self, task: asyncio.Task[Any]) -> None:
        """取消并静默回收一个不再需要的任务。"""
        if task.done():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                task.exception()
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #

    def _log_debug(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.debug("[TaskScope:%s] " + message, self._name, *args)

    def _log_warning(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)
