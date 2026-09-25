"""现实桥服务：现实记忆的写入、查询与「周度反思原料」输出。

「现实桥」（原「周记」）在本项目里是**一等公民记忆**，承载三类现实书写：
周记（``weekly``）/ 日记（``diary``）/ 随笔（``essay``）。

- 写入时同时落 ``journals`` 表（用于面板展示、类型筛选与周期统计）与 ``memories`` 表
  （``kind=journal``，参与检索并在排序中享有 ``journal.retrieval_boost`` 加权）；
- 三类文本共用同一条链路，只在 ``entry_type`` 上区分，因此「用户上周说想早睡」这类
  现实信息能在日常对话里被自然唤醒——这正是 ``docs/bot.md`` 中「搭建现实与虚拟的桥梁」
  的落点；
- 标题遵循「用户填写优先，留空用当天日期时间」，保证列表与导出里不存在无名条目。
"""

from __future__ import annotations

import json
import time
from typing import Any, Mapping, Sequence

from ..memory import (
    KIND_JOURNAL,
    SOURCE_JOURNAL,
    MemoryIdentity,
    MemoryService,
)
from ..spec.entry_types import (
    default_title,
    entry_type_label,
    normalize_entry_type,
    resolve_title,
)
from ..spec.scopes import MemoryScope, retrieval_scopes
from ..storage import JournalRepository
from ..support import truncate
from .config import JournalConfig

_EMOTION_MIN, _EMOTION_MAX = 1, 5


def _normalize_tags(tags: Sequence[str] | None) -> list[str]:
    return [str(item).strip().lstrip("#") for item in (tags or []) if str(item).strip()]


def _normalize_emotion(emotion: Any) -> int | None:
    if emotion is None:
        return None
    try:
        return max(_EMOTION_MIN, min(_EMOTION_MAX, int(emotion)))
    except (TypeError, ValueError):
        return None


def _decode_tags(raw: Any) -> list[str]:
    """``journals.tags`` 是 JSON 字符串；导出时还原成数组，避免二次转义。"""
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw]
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return [part.strip() for part in text.replace("，", ",").split(",") if part.strip()]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def display_title(row: Mapping[str, Any]) -> str:
    """展示用标题：库里为空（v4 迁移前的历史行）时按条目时间补默认标题。

    迁移只做增量（不 UPDATE 历史行），因此老记录的 ``title`` 是空串；这里在读取侧补
    默认标题，既不破坏「只增不改」的迁移约束，也不让面板与导出出现空白标题。
    """
    title = str(row.get("title") or "").strip()
    if title:
        return title
    moment = row.get("event_time") or row.get("created_at")
    return default_title(float(moment) if moment else None)


def to_export_item(row: Mapping[str, Any]) -> dict[str, Any]:
    """把一条记录整理成稳定的导出结构（字段顺序即面板表头顺序）。

    ``scope`` / ``event_time`` / ``memory_id`` 用于导入往返；``type`` 是类型代码，
    导入时同时兼容 ``type`` 与 ``entry_type`` 两个键。
    """
    return {
        "id": row.get("id"),
        "title": display_title(row),
        "content": str(row.get("content") or ""),
        "type": normalize_entry_type(row.get("entry_type")),
        "tags": _decode_tags(row.get("tags")),
        "emotion": row.get("emotion"),
        "created_at": row.get("created_at"),
        "event_time": row.get("event_time"),
        "memory_id": row.get("memory_id"),
        "scope": f"{row.get('scope_type')}:{row.get('scope_id')}",
        "scope_type": row.get("scope_type"),
        "scope_id": row.get("scope_id"),
    }


class JournalService:
    """现实桥领域门面。"""

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
        title: str | None = None,
        entry_type: str | None = None,
        tags: Sequence[str] | None = None,
        emotion: int | None = None,
        event_time: float | None = None,
        now: float | None = None,
        identity: MemoryIdentity | None = None,
        created_at: float | None = None,
    ) -> dict[str, Any] | None:
        """写入一条现实记录（周记 / 日记 / 随笔）；同时生成对应的长期记忆。

        标题与类型：``title`` 留空（或全空白）时取**条目时间**所在的那一天，格式
        ``YYYYMMDDHH:MM``；``entry_type`` 未识别时回退周记。
        ``identity`` 记录说话者，写入同源的长期记忆，供后续按用户归属。

        Returns:
            写入结果（含 ``journal_id`` / ``memory_id`` / ``title`` / ``entry_type``）；
            内容为空时返回 ``None``。
        """
        text = (content or "").strip()
        if not text:
            return None

        # ``created_at`` 非空表示「导入历史记录」：记录行与联动记忆都要用它，
        # 否则恢复出来的条目会显示成导入时刻，创建时间列全部失真。
        moment = float(created_at) if created_at else (now if now is not None else time.time())
        entry_moment = event_time if event_time is not None else moment
        type_value = normalize_entry_type(entry_type)
        title_value = resolve_title(title, fallback_moment=entry_moment)
        normalized_tags = _normalize_tags(tags) or list(self._config.default_tags)
        emotion_value = _normalize_emotion(emotion)

        memory_id = await self._memory.remember_text(
            scope,
            text,
            kind=KIND_JOURNAL,
            importance=0.8,
            confidence=0.95,
            source=SOURCE_JOURNAL,
            tags=normalized_tags,
            identity=identity,
            created_at=moment,
        )
        journal_id = await self._journals.insert(
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            content=text,
            tags=normalized_tags,
            emotion=emotion_value,
            event_time=entry_moment,
            memory_id=memory_id,
            created_at=moment,
            title=title_value,
            entry_type=type_value,
        )
        self._info(
            "写入%s #%s（记忆 #%s，标题 %s）",
            entry_type_label(type_value),
            journal_id,
            memory_id,
            title_value,
        )
        return {
            "journal_id": journal_id,
            "memory_id": memory_id,
            "title": title_value,
            "entry_type": type_value,
            "tags": normalized_tags,
            "emotion": emotion_value,
        }

    async def delete(self, journal_id: int) -> bool:
        """删除记录，并连带遗忘其对应的记忆。"""
        record = await self._journals.get(journal_id)
        if record is None:
            return False
        memory_id = record.get("memory_id")
        if memory_id:
            await self._memory.delete([int(memory_id)])
        return await self._journals.delete(journal_id)

    async def update(
        self,
        journal_id: int,
        *,
        content: str,
        title: str | None = None,
        entry_type: str | None = None,
        tags: Sequence[str] | None = None,
        emotion: int | None = None,
    ) -> bool:
        """编辑记录：同步更新记录行与其对应记忆的正文。

        ``title`` / ``entry_type`` 为 ``None`` 表示保持原值；``title`` 传空串（用户
        清空了标题框）时按该条目的时间重新生成默认标题，不让标题变成空白。
        """
        text = (content or "").strip()
        if not text:
            return False
        record = await self._journals.get(journal_id)
        if record is None:
            return False

        normalized_tags = _normalize_tags(tags) or None
        emotion_value = _normalize_emotion(emotion)
        if title is None:
            title_value = None
        else:
            fallback = record.get("event_time") or record.get("created_at") or time.time()
            title_value = resolve_title(title, fallback_moment=float(fallback))
        type_value = None if entry_type is None else normalize_entry_type(entry_type)

        ok = await self._journals.update(
            journal_id,
            content=text,
            tags=normalized_tags,
            emotion=emotion_value,
            title=title_value,
            entry_type=type_value,
        )
        if not ok:
            return False
        memory_id = record.get("memory_id")
        if memory_id:
            await self._memory.update_content(int(memory_id), text)
        return True

    async def export_items(
        self,
        *,
        ids: Sequence[int] | None = None,
        entry_type: str = "",
        keyword: str = "",
    ) -> list[dict[str, Any]]:
        """导出记录（跨作用域，面板导出 JSON 用）。

        ``ids`` 非空表示「导出勾选的这几条」；否则按 ``entry_type`` / ``keyword``
        筛选导出（即面板上的「全选当前筛选」与「导出全部」）。
        """
        rows = await self._journals.export_all(
            ids=list(ids) if ids else None,
            entry_type=entry_type,
            keyword=keyword,
        )
        return [to_export_item(row) for row in rows]

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #

    async def list_recent(
        self,
        scope: MemoryScope,
        *,
        offset: int = 0,
        limit: int = 20,
        entry_type: str = "",
    ) -> list[dict[str, Any]]:
        return await self._journals.list_page(
            retrieval_scopes(scope), offset=offset, limit=limit, entry_type=entry_type
        )

    async def count(self, scope: MemoryScope) -> int:
        return await self._journals.count(retrieval_scopes(scope))

    async def count_by_type(self) -> dict[str, int]:
        """各类型条目数（面板筛选下拉显示计数用）。"""
        return await self._journals.count_by_type()

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
        """把最近 ``days`` 天的现实记录整理成反思用文本；无内容时返回空串。"""
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
            title = str(row.get("title") or "").strip()
            label = entry_type_label(row.get("entry_type"))
            tags = row.get("tags") or "[]"
            emotion = row.get("emotion")
            suffix = f"（情绪 {emotion}/5）" if emotion else ""
            head = f"[{date}] {label}"
            if title:
                head += f"「{title}」"
            body = truncate(str(row.get("content") or "").replace("\n", " "), 300)
            lines.append(f"- {head} 标签={tags}{suffix} {body}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #

    def _info(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.info(message, *args)
