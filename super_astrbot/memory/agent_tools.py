"""Agent 函数工具的业务实现。

把「模型调用工具」翻译成记忆域操作：解析作用域 → 校验参数与权限 →
调用 ``MemoryService`` → 返回模型可读文本。

放在 ``memory`` 域而不是 ``app.py``：写入门槛、内容长度、类型白名单都是业务规则，
不应混进只做装配的应用容器。

写入门槛与 ``/sab remember`` 保持一致（``basic.admin_only_commands`` 开启时仅管理员），
避免普通成员通过模型间接绕过管理命令的限制写入正式记忆。
"""

from __future__ import annotations

from typing import Any, Mapping

from ..harness.protocols import EventView
from ..spec.capabilities import as_bool, get_path
from ..spec.errors import safe_detail
from ..spec.scopes import MemoryScope
from ..support import truncate
from .models import KIND_FACT, KIND_INSIGHT, KIND_PREFERENCE, SOURCE_AGENT
from .service import MemoryService

MAX_CONTENT_CHARS = 400
MIN_IMPORTANCE = 0.1
MAX_IMPORTANCE = 1.0
DEFAULT_IMPORTANCE = 0.6

_ALLOWED_KINDS = (KIND_FACT, KIND_INSIGHT, KIND_PREFERENCE)

NO_RESULT_TEXT = "没有检索到相关记忆。"
NO_SESSION_TEXT = "无法确定当前会话，暂不处理记忆操作。"
WRITE_DENIED_TEXT = "当前配置下仅管理员可通过工具写入记忆。"
_EMPTY_QUERY_TEXT = "请提供检索关键词。"
_EMPTY_CONTENT_TEXT = "记忆内容为空，未写入。"


def _clamp_importance(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_IMPORTANCE
    return max(MIN_IMPORTANCE, min(MAX_IMPORTANCE, value))


class AgentMemoryBackend:
    """``MemoryToolBackend`` 的默认实现。"""

    def __init__(
        self,
        *,
        service: MemoryService,
        config: Mapping[str, Any] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._service = service
        self._config = config or {}
        self._logger = logger

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #

    async def memory_search(
        self, *, view: EventView, query: str = "", limit: int | None = None
    ) -> str:
        scope = self._scope(view)
        if scope is None:
            return NO_SESSION_TEXT
        text = (query or "").strip()
        if not text:
            return _EMPTY_QUERY_TEXT

        try:
            result = await self._service.recall(scope, text, limit=limit)
        except Exception as exc:  # noqa: BLE001 - 工具失败不得打断对话
            self._warn("Agent 记忆检索失败：%s", safe_detail(exc))
            return "记忆检索暂时不可用，请直接根据现有信息回答。"
        if not result.items:
            return NO_RESULT_TEXT

        # 工具返回的内容等同于「被模型使用」，与注入路径一致地累计访问次数，
        # 否则高频使用的记忆会因为 access_count 长期为 0 而被衰减归档。
        await self._service.touch(result.items)
        return self._service.format_results(result)

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #

    async def memory_write(
        self,
        *,
        view: EventView,
        content: str = "",
        kind: str = KIND_FACT,
        importance: float = DEFAULT_IMPORTANCE,
    ) -> str:
        scope = self._scope(view)
        if scope is None:
            return NO_SESSION_TEXT
        text = (content or "").strip()
        if not text:
            return _EMPTY_CONTENT_TEXT
        if len(text) > MAX_CONTENT_CHARS:
            return (
                f"记忆内容过长（{len(text)} 字，上限 {MAX_CONTENT_CHARS} 字），"
                "请压缩成一句话后重试。"
            )
        if not self._can_write(view):
            return WRITE_DENIED_TEXT

        normalized = str(kind or "").strip().lower()
        if normalized not in _ALLOWED_KINDS:
            normalized = KIND_FACT

        try:
            memory_id = await self._service.remember_text(
                scope,
                text,
                kind=normalized,
                importance=_clamp_importance(importance),
                confidence=0.8,
                source=SOURCE_AGENT,
            )
        except Exception as exc:  # noqa: BLE001
            self._warn("Agent 记忆写入失败：%s", safe_detail(exc))
            return "记忆写入失败，请直接回答用户。"
        return f"已写入长期记忆（#{memory_id}）：{truncate(text, 120)}"

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    def _scope(self, view: EventView) -> MemoryScope | None:
        umo = (view.umo or "").strip()
        if not umo:
            return None
        return MemoryScope.from_event(
            self._service.config.default_scope,
            umo=umo,
            user_id=(view.sender_id or "unknown"),
        )

    def _can_write(self, view: EventView) -> bool:
        if not as_bool(get_path(self._config, "basic.admin_only_commands", True), True):
            return True
        return bool(view.is_admin)

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)
