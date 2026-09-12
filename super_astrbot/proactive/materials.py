"""主动消息的素材收集。

素材来自该会话作用域内的长期记忆、周记与对话缓冲——都是「用户自己说过的话」，
因此生成的内容有据可依，且在素材为空时**宁可不发**（而不是硬编一句问候）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..spec.scopes import MemoryScope
from ..support import truncate

_MATERIAL_LINE_CHARS = 120


@dataclass(frozen=True)
class ProactiveMaterial:
    """一次主动消息可用的素材。"""

    memories: tuple[str, ...] = ()
    journals: tuple[str, ...] = ()
    buffers: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.memories or self.journals or self.buffers)

    def render(self, *, max_chars: int = 1200) -> str:
        """渲染成提示词素材；超出预算即从后往前截断。"""
        sections: list[str] = []
        for title, items in (
            ("长期记忆", self.memories),
            ("现实周记", self.journals),
            ("最近对话", self.buffers),
        ):
            if not items:
                continue
            lines = [f"- {truncate(item, _MATERIAL_LINE_CHARS)}" for item in items if item]
            if lines:
                sections.append(f"{title}：\n" + "\n".join(lines))
        return truncate("\n".join(sections), max_chars)


@runtime_checkable
class MaterialSource(Protocol):
    """素材源协议（便于测试注入替身）。"""

    async def collect(self, umo: str, *, memories: int, journals: int) -> ProactiveMaterial: ...


class MemoryMaterialSource:
    """基于记忆服务的素材源。"""

    def __init__(self, *, memory: Any, logger: Any | None = None) -> None:
        self._memory = memory
        self._logger = logger

    async def collect(self, umo: str, *, memories: int, journals: int) -> ProactiveMaterial:
        scope = MemoryScope.for_session(umo)
        return ProactiveMaterial(
            memories=await self._safe_memories(scope, memories),
            journals=await self._safe_journals(scope, journals),
            buffers=await self._safe_buffers(scope),
        )

    async def _safe_memories(self, scope: MemoryScope, limit: int) -> tuple[str, ...]:
        if limit <= 0:
            return ()
        try:
            items = await self._memory.list_memories(scope, limit=limit)
        except Exception as exc:  # noqa: BLE001 - 素材缺失只影响本次生成
            self._warn("读取记忆素材失败：%s", exc)
            return ()
        return tuple(str(getattr(item, "content", "") or "") for item in items)

    async def _safe_journals(self, scope: MemoryScope, limit: int) -> tuple[str, ...]:
        if limit <= 0:
            return ()
        try:
            rows = await self._memory.list_journals(scope, limit=limit)
        except Exception as exc:  # noqa: BLE001
            self._warn("读取周记素材失败：%s", exc)
            return ()
        return tuple(str(row.get("content") or "") for row in rows)

    async def _safe_buffers(self, scope: MemoryScope) -> tuple[str, ...]:
        try:
            items = await self._memory.buffer_material(scope, limit=5)
        except Exception as exc:  # noqa: BLE001
            self._warn("读取对话缓冲失败：%s", exc)
            return ()
        return tuple(str(getattr(item, "content", "") or "") for item in items)

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)
