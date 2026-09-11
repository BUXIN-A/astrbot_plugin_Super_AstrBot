"""周记服务：现实记忆的写入、查询与「周度反思原料」输出。

周记在本项目里是**一等公民记忆**：

- 写入时同时落 ``journals`` 表（用于面板展示与周期统计）与 ``memories`` 表
  （``kind=journal``，参与检索并在排序中享有 ``journal.retrieval_boost`` 加权）；
- 因此「用户上周说想早睡」这类现实信息能在日常对话里被自然唤醒，
  这正是 ``docs/bot.md`` 中「搭建现实与虚拟的桥梁」的落点。
"""

from __future__ import annotations

import time
from typing import Any, Sequence

from ..memory import (
    KIND_JOURNAL,
    SOURCE_JOURNAL,
    MemoryService,
)
from ..spec.scopes import MemoryScope, retrieval_scopes
from ..storage import JournalRepository
from ..support import truncate
from .config import JournalConfig

_EMOTION_MIN, _EMOTION_MAX = 1, 5


class JournalService:
    """周记领域门面。"""

    def __init__(
        self,
        *,
        config: JournalConfig,
        journals: JournalRepository,
        memory_service: MemoryService,
        logger: Any | None = None,
    ) -> None:
        self._config = config
        self._journals = journals
        self._memory = memory_service
        self._logger = logger

    @property
    def config(self) -> JournalConfig:
        return self._config

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #

    async def add(
        self,
        scope: MemoryScope,
        content: str,
        *,
        tags: Sequence[str] | None = None,
        emotion: int | None = None,
        event_time: float | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """写入一条周记；同时生成对应的长期记忆。

        Returns:
            写入结果（含 ``journal_id`` / ``memory_id``）；内容为空时返回 ``None``。
        """
        text = (content or "").strip()
        if not text:
            return None

        moment = now if now is not None else time.time()
        normalized_tags = [
            str(item).strip().lstrip("#") for item in (tags or []) if str(item).strip()
        ]
        if not normalized_tags:
            normalized_tags = list(self._config.default_tags)

        emotion_value: int | None = None
        if emotion is not None:
            try:
                emotion_value = max(_EMOTION_MIN, min(_EMOTION_MAX, int(emotion)))
            except (TypeError, ValueError):
                emotion_value = None

        memory_id = await self._memory.remember_text(
            scope,
            text,
            kind=KIND_JOURNAL,
            importance=0.8,
            confidence=0.95,
            source=SOURCE_JOURNAL,
            tags=normalized_tags,
        )
        journal_id = await self._journals.insert(
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            content=text,
            tags=normalized_tags,
            emotion=emotion_value,
            event_time=event_time if event_time is not None else moment,
            memory_id=memory_id,
            created_at=moment,
        )
        self._info("写入周记 #%s（记忆 #%s）", journal_id, memory_id)
        return {
            "journal_id": journal_id,
            "memory_id": memory_id,
            "tags": normalized_tags,
            "emotion": emotion_value,
        }

    async def delete(self, journal_id: int) -> bool:
        """删除周记，并连带遗忘其对应的记忆。"""
        record = await self._journals.get(journal_id)
        if record is None:
            return False
        memory_id = record.get("memory_id")
        if memory_id:
            await self._memory.delete([int(memory_id)])
        return await self._journals.delete(journal_id)

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #

    async def list_recent(
        self, scope: MemoryScope, *, offset: int = 0, limit: int = 20
    ) -> list[dict[str, Any]]:
        return await self._journals.list_page(retrieval_scopes(scope), offset=offset, limit=limit)

    async def count(self, scope: MemoryScope) -> int:
        return await self._journals.count(retrieval_scopes(scope))

    # ------------------------------------------------------------------ #
    # 周度反思原料
    # ------------------------------------------------------------------ #

    async def weekly_material(
        self,
        scope: MemoryScope,
        *,
        days: int = 7,
        now: float | None = None,
        limit: int = 120,
    ) -> str:
        """把最近 ``days`` 天的周记整理成反思用文本；无内容时返回空串。"""
        moment = now if now is not None else time.time()
        start = moment - max(1, days) * 86400.0
        rows = await self._journals.list_between(
            retrieval_scopes(scope), start=start, end=moment + 1.0, limit=limit
        )
        if not rows:
            return ""

        lines: list[str] = []
        for row in rows:
            date = time.strftime("%Y-%m-%d", time.localtime(float(row.get("event_time") or 0)))
            tags = row.get("tags") or "[]"
            emotion = row.get("emotion")
            suffix = f"（情绪 {emotion}/5）" if emotion else ""
            body = truncate(str(row.get("content") or "").replace("\n", " "), 300)
            lines.append(f"- [{date}] 标签={tags}{suffix} {body}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #

    def _info(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.info(message, *args)
