"""MaiBot 增强服务：个性化表达作用域 + 学习产物统一时间衰减。

为什么要收敛到一处：

1. **个性化表达**：既有风格模仿只按会话（umo）学习，Bot 学不到「某个群友特有的说话
   方式」。本域额外提供按发送者解析作用域的入口，调用方把同一批样本同时落到会话与用户
   两个作用域后，注入时可复用既有的「会话 + 用户 + 全局」并集召回，从而模仿特定群友。

2. **统一衰减**：风格样本与知识图谱都带权重，都需要「每日衰减 → 逐作用域容量淘汰 →
   低权重剪枝」。把这条维护链收敛到一处，半衰期与权重下限只配置一次，避免两域各维护
   一套参数而漂移（AstrNa 的教训：多源参数必然漂移）。

边界：本服务**只做衰减与容量控制**，不参与写入。若各域自己也挂了每日衰减，则须关闭本域
的 ``time_decay`` 开关，否则同一份数据会被衰减两次。
"""

from __future__ import annotations

from typing import Any, Callable

from ..harness.protocols import EventView
from ..spec.scopes import MemoryScope, ScopeType
from ..storage import GraphRepository, StyleRepository
from ..support import half_life_factor
from .config import MaiBotConfig


class MaiBotService:
    """MaiBot 风格扩展学习：作用域解析与学习产物维护。"""

    def __init__(
        self,
        *,
        config: MaiBotConfig,
        styles: StyleRepository,
        graph: GraphRepository | None = None,
        clock: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._config = config
        self._styles = styles
        self._graph = graph
        self._clock = clock or (lambda: 0.0)
        self._logger = logger

    # ------------------------------------------------------------------ #
    # 开关
    # ------------------------------------------------------------------ #

    def enabled(self) -> bool:
        return self._config.enabled

    def decay_enabled(self) -> bool:
        """是否由本域承担统一衰减；用于避免与其他域的衰减任务重复执行。"""
        return self._config.enabled and self._config.time_decay

    # ------------------------------------------------------------------ #
    # 作用域
    # ------------------------------------------------------------------ #

    def user_scope(self, view: EventView) -> MemoryScope | None:
        """给出按发送者维度的表达作用域；不可用时返回 ``None``。

        未启用、未开启用户维度、或发送者为空时都不返回作用域，让调用方自然退化到既有行为。
        """
        if not self.enabled() or not self._config.expression_user_scope:
            return None
        if not view.sender_id:
            return None
        return MemoryScope(ScopeType.USER, view.sender_id)

    # ------------------------------------------------------------------ #
    # 维护
    # ------------------------------------------------------------------ #

    async def maintain(self, *, now: float | None = None) -> dict[str, Any]:
        """执行一次每日维护：衰减 → 逐作用域容量淘汰 → 图谱剪枝。

        调用方按日触发，因此 ``factor`` 是「每天乘以的比例」：半衰期为 ``H`` 天时，
        日系数取 ``0.5 ** (1 / H)``，连续调用 ``H`` 次后权重恰好减半。
        """
        if not self.enabled():
            return {"skipped": 1}

        moment = self._clock() if now is None else now
        factor = half_life_factor(self._config.half_life_days)
        stats: dict[str, Any] = {
            "style_decayed": 0,
            "style_trimmed": 0,
            "graph_decayed": 0,
            "graph_trimmed": 0,
            "graph_pruned": {},
        }

        # 风格与图谱分别兜底：任一侧异常都不影响另一侧已完成的部分（容错优先于原子性），
        # 维护任务若整体抛出会导致后续调度被反复打断。
        try:
            stats["style_decayed"] = await self._styles.apply_decay(
                factor=factor, floor=self._config.weight_floor, at=moment
            )
            for scope_type, scope_id in await self._styles.all_scopes():
                scope = MemoryScope(ScopeType.parse(scope_type), scope_id)
                stats["style_trimmed"] += await self._styles.trim(
                    (scope,), keep=self._config.style_keep, at=moment
                )
        except Exception as exc:
            self._warn("MaiBot 风格衰减失败：%s", exc)

        if self._graph is not None:
            try:
                stats["graph_decayed"] = await self._graph.apply_decay(
                    factor=factor, floor=self._config.weight_floor, at=moment
                )
                for scope_type, scope_id in await self._graph.all_scopes():
                    scope = MemoryScope(ScopeType.parse(scope_type), scope_id)
                    stats["graph_trimmed"] += await self._graph.trim_entities(
                        (scope,), keep=self._config.graph_keep, at=moment
                    )
                stats["graph_pruned"] = await self._graph.prune(
                    min_weight=self._config.prune_min_weight, at=moment
                )
            except Exception as exc:
                self._warn("MaiBot 图谱衰减失败：%s", exc)

        return stats

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #

    async def stats(self) -> dict[str, Any]:
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "user_scope": self._config.expression_user_scope,
            "time_decay": self._config.time_decay,
            "half_life_days": self._config.half_life_days,
        }

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)
