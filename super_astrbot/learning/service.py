"""反思式自我学习：把对话缓冲与周记提炼为长期记忆。

闭环：
``对话缓冲( buffered )`` → 反思模型 → 结构化洞察 → ``待审`` 或 ``正式记忆( active )``
→ 缓冲归档 → 写 ``reflection_logs`` 留痕。

可靠性约定：

- 反思**永远不影响正常对话**：任何异常都被吞掉并记录，只返回结构化结果；
- 触发条件可预测：缓冲条数 / 时间间隔 / 冷却三重约束；
- 产出可追溯：每条写入的记忆都能通过 ``reflection_logs.id`` 对应到本次运行。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..harness.protocols import LlmGateway
from ..journal import JournalService
from ..memory import (
    KIND_INSIGHT,
    SOURCE_REFLECTION,
    SOURCE_WEEKLY,
    MemoryDraft,
    MemoryService,
)
from ..spec.errors import LlmError, safe_detail
from ..spec.scopes import MemoryScope, retrieval_scopes
from ..storage import ReflectionRepository, ReviewRepository
from ..support import parse_payload, truncate
from .config import MODE_BOTH, MODE_INTERVAL, MODE_ROUNDS, ReflectionConfig
from .prompts import (
    REFLECTION_SYSTEM,
    build_reflection_prompt,
    build_weekly_prompt,
    ensure_kind_valid,
    parse_insights,
    reflection_system,
    weekly_system,
)

_TRANSCRIPT_LINE_MAX = 200
_TRANSCRIPT_TOTAL_MAX = 6000


@dataclass
class ReflectionOutcome:
    """一次反思的结果快照。"""

    ran: bool
    reason: str = ""
    produced: int = 0
    pending: int = 0
    log_id: int = 0
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        if not self.ran:
            return f"未执行：{self.reason}"
        if self.error:
            return f"执行失败：{self.error}"
        return f"执行完成：写入 {self.produced} 条，待审 {self.pending} 条"


class ReflectionService:
    """反思服务。"""

    def __init__(
        self,
        *,
        config: ReflectionConfig,
        memory_service: MemoryService,
        journals: JournalService,
        reflections: ReflectionRepository,
        reviews: ReviewRepository,
        llm: LlmGateway,
        logger: Any | None = None,
    ) -> None:
        self._config = config
        self._memory = memory_service
        self._journals = journals
        self._reflections = reflections
        self._reviews = reviews
        self._llm = llm
        self._logger = logger

    @property
    def config(self) -> ReflectionConfig:
        return self._config

    # ------------------------------------------------------------------ #
    # 触发判定
    # ------------------------------------------------------------------ #

    async def should_run(self, scope: MemoryScope, *, now: float | None = None) -> tuple[bool, str]:
        """判断当前作用域是否满足反思条件。"""
        if not self._config.enabled:
            return False, "反思未启用"

        moment = now if now is not None else time.time()
        scopes = retrieval_scopes(scope)
        buffered = await self._memory.count_buffer(scope)
        needed = max(2, self._config.min_messages)
        if buffered < needed:
            return False, f"待反思内容不足（{buffered}/{needed}）"

        last_finished = await self._reflections.last_finished_at(scopes)
        cooldown = max(0, self._config.cooldown_minutes) * 60.0
        if last_finished > 0 and moment - last_finished < cooldown:
            remain = int(cooldown - (moment - last_finished))
            return False, f"冷却中（剩余 {remain}s）"

        mode = self._config.mode
        rounds_reached = buffered >= max(needed, self._config.trigger_rounds)
        interval_ready = (
            last_finished <= 0
            or (moment - last_finished) >= max(1, self._config.interval_minutes) * 60.0
        )

        if mode == MODE_ROUNDS:
            return (True, "累计内容达标") if rounds_reached else (False, "累计内容未达标")
        if mode == MODE_INTERVAL:
            return (True, "时间间隔达标") if interval_ready else (False, "时间间隔未达标")
        if mode == MODE_BOTH:
            if rounds_reached or interval_ready:
                return True, "轮数或间隔达标"
            return False, "轮数与间隔均未达标"
        return False, "触发模式非法"

    # ------------------------------------------------------------------ #
    # 反思执行
    # ------------------------------------------------------------------ #

    async def reflect(
        self, scope: MemoryScope, *, now: float | None = None, reason: str = "auto"
    ) -> ReflectionOutcome:
        """基于对话缓冲执行一次反思。"""
        if not self._config.enabled:
            return ReflectionOutcome(ran=False, reason="反思未启用")

        moment = now if now is not None else time.time()
        limit = max(
            self._config.min_messages,
            self._config.trigger_rounds,
            self._config.max_facts * 4,
            20,
        )
        material = await self._memory.buffer_material(scope, limit=limit)
        if len(material) < 2:
            return ReflectionOutcome(ran=False, reason="对话缓冲不足")

        log_id = await self._reflections.start(
            scope_type=scope.scope_type.value, scope_id=scope.scope_id, started_at=moment
        )
        transcript = self._render_transcript(material)
        prompt = build_reflection_prompt(
            transcript, max_facts=self._config.max_facts, overrides=self._config.prompts
        )

        text, error = await self._invoke(prompt, system=reflection_system(self._config.prompts))
        if error:
            await self._reflections.finish(
                log_id,
                finished_at=time.time(),
                status="error",
                produced=0,
                error=error,
            )
            return ReflectionOutcome(ran=True, reason=reason, log_id=log_id, error=error)

        insights = parse_insights(text or "", max_facts=self._config.max_facts)
        return await self._store(
            scope,
            insights,
            log_id=log_id,
            material_ids=[item.id for item in material],
            source=SOURCE_REFLECTION,
            reason=reason,
            now=moment,
        )

    async def weekly_reflect(
        self, scope: MemoryScope, *, now: float | None = None, days: int = 7
    ) -> ReflectionOutcome:
        """基于本周周记执行一次「周度洞察」。"""
        if not self._config.enabled:
            return ReflectionOutcome(ran=False, reason="反思未启用")

        moment = now if now is not None else time.time()
        material = await self._journals.weekly_material(scope, days=days, now=moment)
        if not material.strip():
            return ReflectionOutcome(ran=False, reason="本周没有周记")

        log_id = await self._reflections.start(
            scope_type=scope.scope_type.value, scope_id=scope.scope_id, started_at=moment
        )
        prompt = build_weekly_prompt(
            material, max_facts=self._config.max_facts, overrides=self._config.prompts
        )
        text, error = await self._invoke(prompt, system=weekly_system(self._config.prompts))
        if error:
            await self._reflections.finish(
                log_id, finished_at=time.time(), status="error", produced=0, error=error
            )
            return ReflectionOutcome(ran=True, reason="weekly", log_id=log_id, error=error)

        insights = parse_insights(text or "", max_facts=self._config.max_facts)
        return await self._store(
            scope,
            insights,
            log_id=log_id,
            material_ids=[],
            source=SOURCE_WEEKLY,
            reason="weekly",
            now=moment,
            default_importance=0.75,
        )

    async def _invoke(self, prompt: str, *, system: str = "") -> tuple[str | None, str]:
        """调用反思模型；返回 ``(文本, 错误)``，二者必有其一为空。"""
        try:
            result = await self._llm.chat(
                prompt=prompt,
                system_prompt=system or REFLECTION_SYSTEM,
                provider_id=self._config.provider_id or None,
                timeout=self._config.timeout_seconds,
                purpose="reflection",
            )
            return result.text, ""
        except LlmError as exc:
            return None, safe_detail(exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 反思失败不得外溢
            return None, safe_detail(exc)

    async def _store(
        self,
        scope: MemoryScope,
        insights: Sequence[dict[str, Any]],
        *,
        log_id: int,
        material_ids: Sequence[int],
        source: str,
        reason: str,
        now: float,
        default_importance: float = 0.65,
    ) -> ReflectionOutcome:
        produced = 0
        pending = 0

        for insight in insights:
            payload = {
                "content": insight["content"],
                "kind": ensure_kind_valid(insight.get("kind", KIND_INSIGHT)),
                "importance": float(insight.get("importance") or default_importance),
                "tags": list(insight.get("tags") or []),
                "source": source,
                "log_id": log_id,
            }
            if self._config.approval_required:
                await self._reviews.add(
                    scope_type=scope.scope_type.value,
                    scope_id=scope.scope_id,
                    origin=source,
                    payload=payload,
                    created_at=now,
                )
                pending += 1
                continue

            await self._memory.remember(
                MemoryDraft(
                    scope_type=scope.scope_type.value,
                    scope_id=scope.scope_id,
                    content=payload["content"],
                    kind=payload["kind"],
                    importance=payload["importance"],
                    confidence=0.75,
                    source=source,
                    tags=payload["tags"],
                )
            )
            produced += 1

        # 无论是否审批，缓冲都视为已消费，避免下次重复反思。
        if material_ids:
            await self._memory.consume_buffer(list(material_ids), now=now)

        await self._reflections.finish(
            log_id,
            finished_at=time.time(),
            status="ok",
            produced=produced + pending,
            detail=f"reason={reason};pending={pending}",
        )
        self._info("反思完成（%s）：写入 %s 条，待审 %s 条", reason, produced, pending)
        return ReflectionOutcome(
            ran=True,
            reason=reason,
            produced=produced,
            pending=pending,
            log_id=log_id,
        )

    def _render_transcript(self, material: Sequence[Any]) -> str:
        lines: list[str] = []
        used = 0
        for item in material:
            date = time.strftime("%m-%d %H:%M", time.localtime(item.created_at or 0))
            body = truncate(str(item.content or "").replace("\n", " "), _TRANSCRIPT_LINE_MAX)
            line = f"[{date}] {body}"
            if used + len(line) > _TRANSCRIPT_TOTAL_MAX:
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 审批
    # ------------------------------------------------------------------ #

    async def pending_reviews(self, scope: MemoryScope, *, limit: int = 10) -> list[dict[str, Any]]:
        return await self._reviews.list_pending(retrieval_scopes(scope), limit=limit)

    async def pending_count(self, scope: MemoryScope) -> int:
        return await self._reviews.count_pending(retrieval_scopes(scope))

    async def approve(self, review_id: int, *, now: float | None = None) -> int | None:
        """批准一条待审记忆，返回写入的记忆 ID。"""
        record = await self._reviews.get(review_id)
        if record is None or str(record.get("status")) != "pending":
            return None
        payload = parse_payload(record.get("payload")) or {}
        content = str(payload.get("content") or "").strip()
        if not content:
            await self._reviews.set_status(
                review_id, "rejected", at=now if now is not None else time.time()
            )
            return None

        memory_id = await self._memory.remember(
            MemoryDraft(
                scope_type=str(record.get("scope_type") or ""),
                scope_id=str(record.get("scope_id") or ""),
                content=content,
                kind=ensure_kind_valid(str(payload.get("kind") or KIND_INSIGHT)),
                importance=float(payload.get("importance") or 0.65),
                confidence=0.8,
                source=str(payload.get("source") or SOURCE_REFLECTION),
                tags=list(payload.get("tags") or []),
            )
        )
        moment = now if now is not None else time.time()
        await self._reviews.set_status(review_id, "approved", at=moment)
        return memory_id

    async def reject(self, review_id: int, *, now: float | None = None) -> bool:
        moment = now if now is not None else time.time()
        return await self._reviews.set_status(review_id, "rejected", at=moment)

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #

    def _info(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.info(message, *args)
