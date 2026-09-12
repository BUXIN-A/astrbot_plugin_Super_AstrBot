"""风格模仿：从「用户提问 → Bot 回答」邻接对中零成本抽取表达模式。

为什么用邻接对而不是让模型总结风格：
真实对话里「什么样的场景该怎么说话」已经隐含在每一组问答中，直接配对既是零成本，
样本也不会被模型二次加工得失真（借鉴 self_learning 的表达模式学习思路）。

生命周期：写入（或进审查队列）→ 注入命中累加权重 → 每日衰减 → 容量淘汰。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..harness.protocols import EventView
from ..spec.scopes import MemoryScope, ScopeType, retrieval_scopes
from ..storage import ReviewRepository, StyleRepository
from ..support import jaccard, normalize_text, tokenize, truncate
from .config import StyleConfig
from .prompts import render_style_block

SOURCE_STYLE = "style"
"""待审队列里的来源标记；也是审批分流依据。"""

_SKIP_PREFIXES = ("/", "!", "#", ".")
_HIT_WEIGHT_BONUS = 0.05
_MAX_WEIGHT = 5.0


@dataclass(frozen=True)
class StyleSelection:
    """一次注入选择的结果。"""

    examples: tuple[tuple[str, str], ...] = ()
    pattern_ids: tuple[int, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.examples


@dataclass
class StyleOutcome:
    """一次学习的处置结果（日志/状态用）。"""

    stored: bool = False
    pending: bool = False
    reason: str = ""
    pattern_id: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class StyleService:
    """表达模式的学习、选择与维护。"""

    def __init__(
        self,
        *,
        config: StyleConfig,
        patterns: StyleRepository,
        reviews: ReviewRepository,
        clock: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._config = config
        self._patterns = patterns
        self._reviews = reviews
        self._clock = clock or (lambda: 0.0)
        self._logger = logger
        self._stats = {"learned": 0, "pending": 0, "injected": 0, "skipped": 0}

    @property
    def config(self) -> StyleConfig:
        return self._config

    # ------------------------------------------------------------------ #
    # 学习
    # ------------------------------------------------------------------ #

    async def learn(
        self, view: EventView, *, user_text: str, reply_text: str, now: float | None = None
    ) -> StyleOutcome:
        """记录一组「用户提问 → Bot 回答」表达模式。"""
        if not self._config.enabled:
            return self._skip("风格模仿未启用")

        situation = normalize_text(user_text or "")
        expression = normalize_text(reply_text or "")
        problem = self._reject_reason(situation, expression)
        if problem:
            return self._skip(problem)

        if not view.umo:
            return self._skip("会话标识为空")

        moment = self._clock() if now is None else now
        scope = MemoryScope.for_session(view.umo)

        if self._config.approval_required:
            await self._reviews.add(
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
                origin=SOURCE_STYLE,
                payload={
                    "situation": truncate(situation, 200),
                    "expression": truncate(expression, self._config.max_bot_chars),
                },
                created_at=moment,
            )
            self._stats["pending"] += 1
            return StyleOutcome(pending=True, reason="已进入待审队列")

        pattern_id = await self._store(scope, situation, expression, now=moment)
        self._stats["learned"] += 1
        return StyleOutcome(stored=True, pattern_id=pattern_id, reason="已写入")

    async def _store(
        self, scope: MemoryScope, situation: str, expression: str, *, now: float
    ) -> int | None:
        pattern_id = await self._patterns.add(
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            situation=situation,
            expression=expression,
            weight=1.0,
            source="pair",
            created_at=now,
        )
        if pattern_id is not None:
            await self._patterns.trim((scope,), keep=self._config.max_patterns, at=now)
        return pattern_id

    def _reject_reason(self, situation: str, expression: str) -> str:
        """返回拒绝原因；空字符串表示通过。"""
        if len(situation) < self._config.min_user_chars:
            return "用户消息过短"
        if situation.startswith(_SKIP_PREFIXES):
            return "命令类消息不参与学习"
        if len(expression) < self._config.min_bot_chars:
            return "Bot 回复过短"
        if len(expression) > self._config.max_bot_chars:
            return "Bot 回复过长"
        return ""

    # ------------------------------------------------------------------ #
    # 选择与注入
    # ------------------------------------------------------------------ #

    async def select(
        self, scope: MemoryScope, query: str, *, now: float | None = None
    ) -> StyleSelection:
        """按「与当前消息的相似度 × 权重」选出少量 few-shot 示例。"""
        if not self._config.enabled or not query.strip():
            return StyleSelection()

        rows = await self._patterns.list_active(
            retrieval_scopes(scope), limit=max(10, self._config.max_patterns)
        )
        if not rows:
            return StyleSelection()

        query_tokens = tokenize(query)
        if not query_tokens:
            return StyleSelection()

        scored: list[tuple[float, float, dict[str, Any]]] = []
        for row in rows:
            similarity = jaccard(query_tokens, tokenize(str(row.get("situation") or "")))
            if similarity < self._config.min_similarity:
                continue
            weight = float(row.get("weight") or 1.0)
            scored.append((similarity, weight, row))
        if not scored:
            return StyleSelection()

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        chosen = scored[: self._config.top_k]

        moment = self._clock() if now is None else now
        ids = tuple(int(item[2]["id"]) for item in chosen)
        await self._patterns.update_usage(
            ids,
            weights={
                pattern_id: min(
                    _MAX_WEIGHT, float(item[2].get("weight") or 1.0) + _HIT_WEIGHT_BONUS
                )
                for pattern_id, item in zip(ids, chosen)
            },
            at=moment,
        )
        self._stats["injected"] += 1
        examples = tuple(
            (
                truncate(str(item[2].get("situation") or ""), 80),
                truncate(str(item[2].get("expression") or ""), 200),
            )
            for item in chosen
        )
        return StyleSelection(examples=examples, pattern_ids=ids)

    def render_block(self, selection: StyleSelection) -> str:
        if selection.is_empty:
            return ""
        return render_style_block(selection.examples, max_chars=self._config.max_injected_chars)

    # ------------------------------------------------------------------ #
    # 维护与查询
    # ------------------------------------------------------------------ #

    async def maintain(self, *, now: float | None = None) -> dict[str, Any]:
        """每日衰减 + 逐作用域容量淘汰。"""
        if not self._config.enabled:
            return {"decayed": 0, "trimmed": 0}
        moment = self._clock() if now is None else now
        half_life = max(1.0, self._config.half_life_days)
        factor = 0.5 ** (1.0 / half_life)
        archived = await self._patterns.apply_decay(
            factor=factor, floor=self._config.weight_floor, at=moment
        )

        trimmed = 0
        for scope_type, scope_id in await self._patterns.all_scopes():
            scope = MemoryScope(ScopeType.parse(scope_type), scope_id)
            trimmed += await self._patterns.trim(
                (scope,), keep=self._config.max_patterns, at=moment
            )
        if archived or trimmed:
            self._info("风格维护：衰减归档 %s 条，容量淘汰 %s 条", archived, trimmed)
        return {"decayed": archived, "trimmed": trimmed}

    async def approve(self, record: dict[str, Any], payload: dict[str, Any]) -> str:
        """审批通过：把待审样本写入表达模式。"""
        situation = str(payload.get("situation") or "").strip()
        expression = str(payload.get("expression") or "").strip()
        if not situation or not expression:
            return "样本内容为空，已忽略"
        scope = MemoryScope(
            ScopeType.parse(str(record.get("scope_type") or "session")),
            str(record.get("scope_id") or ""),
        )
        pattern_id = await self._store(
            scope,
            truncate(situation, 200),
            truncate(expression, self._config.max_bot_chars),
            now=self._clock(),
        )
        self._stats["learned"] += 1
        if pattern_id is None:
            return "该样本已存在，未重复写入"
        return f"已写入表达模式 #{pattern_id}"

    async def list_page(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 20
    ) -> list[dict[str, Any]]:
        return await self._patterns.list_page(retrieval_scopes(scope), offset=offset, limit=limit)

    async def list_all_page(self, *, offset: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        return await self._patterns.list_all_page(offset=offset, limit=limit)

    async def count(self, scope: MemoryScope) -> int:
        return await self._patterns.count(retrieval_scopes(scope))

    async def count_all(self) -> int:
        return await self._patterns.count_all()

    async def delete(self, pattern_id: int) -> bool:
        return await self._patterns.delete(pattern_id)

    async def clear(self, scope: MemoryScope) -> int:
        return await self._patterns.clear_scopes((scope,))

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "approval_required": self._config.approval_required,
            "max_patterns": self._config.max_patterns,
            "top_k": self._config.top_k,
            "min_similarity": self._config.min_similarity,
            "stats": dict(self._stats),
        }

    def _skip(self, reason: str) -> StyleOutcome:
        self._stats["skipped"] += 1
        return StyleOutcome(reason=reason)

    def _info(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.info(message, *args)
