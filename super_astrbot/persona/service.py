"""拟人化学习门面：把风格、黑话、好感度三个子能力组装成一条链路。

对外的三个动作：

- ``learn_style``：Bot 回复送达后，把「用户提问 → Bot 回答」配对成样本；
- ``observe_user``：用户消息到达后，累计黑话候选词频（零成本）与好感度；
- ``inject``：请求前把三类学习结果作为**独立注入块**写入本次请求。

审批分流也在此：待审队列里 ``origin`` 为 ``style`` / ``jargon`` 的记录由本域落地，
其余交回反思域处理（``handled=False``）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from ..harness.protocols import EventView, Injector, LlmGateway
from ..loop.state_store import StateStore
from ..spec.scopes import MemoryScope
from ..storage import (
    AffinityRepository,
    JargonRepository,
    ReviewRepository,
    StyleRepository,
)
from .affinity import AffinityService
from .config import PersonaConfig
from .jargon import SOURCE_JARGON, JargonService
from .prompts import render_affinity_block
from .style import SOURCE_STYLE, StyleOutcome, StyleService


@dataclass
class PersonaStats:
    """三类学习结果的汇总。"""

    style: int = 0
    jargon: int = 0
    affinity: int = 0


class PersonaService:
    """拟人化学习的统一入口。"""

    def __init__(
        self,
        *,
        config: PersonaConfig,
        patterns: StyleRepository,
        jargons: JargonRepository,
        affinities: AffinityRepository,
        reviews: ReviewRepository,
        llm: LlmGateway,
        injector: Injector,
        store: StateStore,
        extra_scope: Callable[[EventView], MemoryScope | None] | None = None,
        clock: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._config = config
        self._reviews = reviews
        self._injector = injector
        self._logger = logger
        self._clock = clock or (lambda: 0.0)
        self._extra_scope = extra_scope
        """额外的学习/注入作用域解析（MaiBot 增强的「按发送者个性化」）。"""

        self._style = StyleService(
            config=config.style,
            patterns=patterns,
            reviews=reviews,
            clock=clock,
            logger=logger,
        )
        self._jargon = JargonService(
            config=config.jargon,
            jargons=jargons,
            reviews=reviews,
            llm=llm,
            store=store,
            prompts=config.prompts,
            clock=clock,
            logger=logger,
        )
        self._affinity = AffinityService(
            config=config.affinity,
            affinities=affinities,
            llm=llm,
            prompts=config.prompts,
            clock=clock,
            logger=logger,
        )

    @property
    def config(self) -> PersonaConfig:
        return self._config

    @property
    def style(self) -> StyleService:
        return self._style

    @property
    def jargon(self) -> JargonService:
        return self._jargon

    @property
    def affinity(self) -> AffinityService:
        return self._affinity

    # ------------------------------------------------------------------ #
    # 学习
    # ------------------------------------------------------------------ #

    async def learn_style(
        self, view: EventView, *, user_text: str, reply_text: str, now: float | None = None
    ) -> StyleOutcome:
        return await self._style.learn(
            view,
            user_text=user_text,
            reply_text=reply_text,
            now=now,
            extra_scope=self.extra_scope(view),
        )

    def extra_scope(self, view: EventView) -> MemoryScope | None:
        """额外的个性化作用域；未启用时返回 ``None``（失败也降级为 ``None``）。"""
        if self._extra_scope is None:
            return None
        try:
            return self._extra_scope(view)
        except Exception as exc:  # noqa: BLE001 - 解析失败只降级为不做个性化
            self._debug("个性化作用域解析失败：%s", exc)
            return None

    async def observe_user(self, view: EventView, text: str) -> None:
        """用户消息到达后更新黑话候选与好感度（调用方应放到后台任务）。"""
        if self._config.jargon.enabled:
            try:
                await self._jargon.observe(MemoryScope.for_session(view.umo), text)
            except Exception as exc:  # noqa: BLE001 - 统计失败不影响对话
                self._debug("黑话候选统计失败：%s", exc)
        if self._config.affinity.enabled:
            try:
                outcome = await self._affinity.observe(view, text)
            except Exception as exc:  # noqa: BLE001
                self._debug("好感度更新失败：%s", exc)
                return
            if outcome.updated:
                self._debug("好感度更新（%s）：%s", view.umo, outcome.summary())

    # ------------------------------------------------------------------ #
    # 注入
    # ------------------------------------------------------------------ #

    async def inject(self, request: Any, view: EventView) -> str:
        """把风格示例 / 黑话含义 / 关系语气写成本次请求的临时内容块。"""
        if request is None or not view.text.strip():
            return ""

        scope = MemoryScope.for_session(view.umo)
        blocks: list[str] = []

        if self._config.style.enabled:
            selection = await self._style.select(
                scope, view.text, extra_scope=self.extra_scope(view)
            )
            block = self._style.render_block(selection)
            if block:
                blocks.append(block)

        if self._config.jargon.enabled:
            block = await self._jargon.render_block(scope, view.text)
            if block:
                blocks.append(block)

        if self._config.affinity.enabled:
            guidance = await self._affinity.guidance(scope, view.sender_id)
            block = render_affinity_block(
                guidance, max_chars=self._config.affinity.max_injected_chars
            )
            if block:
                blocks.append(block)

        if not blocks:
            return ""

        result = self._injector.inject(request, blocks)
        if not result.applied:
            return f"未注入：{result.reason}"
        return f"{len(blocks)} 块 / {result.chars} 字符（{result.method}）"

    # ------------------------------------------------------------------ #
    # 审批分流
    # ------------------------------------------------------------------ #

    async def approve(self, review_id: int) -> tuple[bool, str]:
        """审批一条待审记录；非本域来源返回 ``handled=False``。"""
        record = await self._reviews.get(review_id)
        if record is None or str(record.get("status")) != "pending":
            return False, ""
        origin = str(record.get("origin") or "")
        if origin not in {SOURCE_STYLE, SOURCE_JARGON}:
            return False, ""

        payload = _load_payload(record)
        if origin == SOURCE_STYLE:
            message = await self._style.approve(record, payload)
        else:
            message = await self._jargon.approve(record, payload)
        # 落地成功后必须把待审记录置为已处理，否则会反复出现在待审队列里。
        await self._reviews.set_status(review_id, "approved", at=self._clock())
        return True, message

    # ------------------------------------------------------------------ #
    # 维护与查询
    # ------------------------------------------------------------------ #

    async def maintain(
        self, *, now: float | None = None, with_decay: bool = True
    ) -> dict[str, Any]:
        return await self._style.maintain(now=now, with_decay=with_decay)

    async def flush(self) -> None:
        """把内存态的学习进度落盘（插件卸载前调用）。"""
        await self._jargon.flush()

    async def scan_jargon(self, scope: MemoryScope, *, now: float | None = None) -> Any:
        return await self._jargon.scan(scope, now=now)

    async def style_patterns(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 20
    ) -> list[dict[str, Any]]:
        return await self._style.list_page(scope, offset=offset, limit=limit)

    async def all_style_patterns(self, *, offset: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        """跨作用域列表（面板在未指定会话时使用）。"""
        return await self._style.list_all_page(offset=offset, limit=limit)

    async def all_jargons(self, *, offset: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        return await self._jargon.list_all_page(offset=offset, limit=limit)

    async def all_affinity(self, *, offset: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        return await self._affinity.list_all_page(offset=offset, limit=limit)

    async def jargon_entries(self, scope: MemoryScope, *, limit: int = 20) -> list[dict[str, Any]]:
        return await self._jargon.list_active(scope, limit=limit)

    async def affinity_rows(self, scope: MemoryScope, *, limit: int = 20) -> list[dict[str, Any]]:
        return await self._affinity.list_by_scope(scope, limit=limit)

    async def delete_style(self, pattern_id: int) -> bool:
        return await self._style.delete(pattern_id)

    async def delete_jargon(self, jargon_id: int) -> bool:
        return await self._jargon.delete(jargon_id)

    async def stats(self) -> PersonaStats:
        return PersonaStats(
            style=await self._style.count_all(),
            jargon=await self._jargon.count_all(),
            affinity=await self._affinity.count_all(),
        )

    async def session_counts(self, umo: str) -> dict[str, int]:
        scope = MemoryScope.for_session(umo)
        return {
            "style": await self._style.count(scope),
            "jargon": await self._jargon.count(scope),
        }

    async def clear(self, scope: MemoryScope) -> dict[str, int]:
        """清空某作用域的学习结果（供 /sab reset 使用）。"""
        return {
            "style": await self._style.clear(scope),
            "jargon": await self._jargon.clear(scope),
            "affinity": await self._affinity.clear(scope),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "style": self._style.snapshot(),
            "jargon": self._jargon.snapshot(),
            "affinity": self._affinity.snapshot(),
            "user_scope": self._extra_scope is not None,
        }

    def _debug(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.debug(message, *args)


def _load_payload(record: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(record.get("payload") or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def summarize_reviews(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """把待审记录整理成统一的展示结构（命令与面板共用）。"""
    items: list[dict[str, Any]] = []
    for row in rows:
        payload = _load_payload(row)
        origin = str(row.get("origin") or "")
        if origin == SOURCE_STYLE:
            summary = f"风格样本：{payload.get('situation', '')} → {payload.get('expression', '')}"
        elif origin == SOURCE_JARGON:
            summary = f"群内用语「{payload.get('term', '')}」：{payload.get('meaning', '')}"
        else:
            summary = str(payload.get("content") or "")
        items.append(
            {
                "id": row.get("id"),
                "origin": origin,
                "scope": f"{row.get('scope_type')}:{row.get('scope_id')}",
                "created_at": row.get("created_at"),
                "summary": summary,
            }
        )
    return items
