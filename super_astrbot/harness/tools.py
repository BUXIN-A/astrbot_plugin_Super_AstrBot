"""Agent 函数工具的框架适配层。

分层职责：

- 本模块只负责「把工具接线到 AstrBot」——构造 ``FunctionTool``、注册与注销；
  检索/写入的规则与校验由 ``MemoryToolBackend`` 的实现（``memory`` 域）提供；
- 框架符号一律经 ``astrbot_compat`` 宽容获取，缺失时降级为「不注册工具」，
  绝不让插件加载失败。

为什么用 ``FunctionTool(handler=...)`` 而不是子类化：
AstrBot 的执行器优先调用 ``handler``，其次才是子类的 ``call()``，最后才回退到旧版
``run()``。``handler`` 是这几代执行路径的最小公约数，且无需子类化 pydantic 数据类，
跨版本最稳。

工具名固定为 ``sab_memory_search`` / ``sab_memory_write``：``ToolSet.openai_schema()``
按名称排序生成定义，稳定的名字才不破坏模型侧的前缀缓存。
"""

from __future__ import annotations

from typing import Any, Sequence

from ..spec.errors import safe_detail
from . import astrbot_compat as compat
from .astrbot_event import to_event_view
from .protocols import MemoryToolBackend

MEMORY_SEARCH_TOOL = "sab_memory_search"
MEMORY_WRITE_TOOL = "sab_memory_write"
MEMORY_TOOL_NAMES: tuple[str, ...] = (MEMORY_SEARCH_TOOL, MEMORY_WRITE_TOOL)

MAX_SEARCH_LIMIT = 20
MIN_IMPORTANCE = 0.1
MAX_IMPORTANCE = 1.0
DEFAULT_IMPORTANCE = 0.6

_SEARCH_DESCRIPTION = (
    "在长期记忆中检索与当前话题相关的条目。"
    "当需要回忆用户此前提到过的偏好、事实、约定或经历时调用；"
    "本工具只读取记忆，不会修改任何内容。"
)
_WRITE_DESCRIPTION = (
    "把一条值得长期记住的信息写入记忆，例如用户的稳定偏好、重要事实、约定或计划。"
    "仅在信息明确且长期有效时调用；不要记录寒暄、临时状态或指令性内容。"
)


def _search_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索关键词或一句自然语言描述，例如「用户的作息习惯」。",
            },
            "limit": {
                "type": "integer",
                "description": f"最多返回几条（1~{MAX_SEARCH_LIMIT}），省略则使用插件默认召回条数。",
                "minimum": 1,
                "maximum": MAX_SEARCH_LIMIT,
            },
        },
        "required": ["query"],
    }


def _write_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "一句话陈述，使用第三人称，例如「用户偏好清晨跑步」。",
            },
            "kind": {
                "type": "string",
                "description": "记忆类型：fact（事实）/ insight（洞察）/ preference（偏好）。",
                "enum": ["fact", "insight", "preference"],
            },
            "importance": {
                "type": "number",
                "description": f"重要度 {MIN_IMPORTANCE}~{MAX_IMPORTANCE}，越大越抗遗忘。",
                "minimum": MIN_IMPORTANCE,
                "maximum": MAX_IMPORTANCE,
            },
        },
        "required": ["content"],
    }


def _coerce_limit(raw: Any) -> int | None:
    """把模型传来的 limit 规整为合法范围；无法解析时返回 None（用默认值）。"""
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return max(1, min(MAX_SEARCH_LIMIT, value))


def _coerce_importance(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_IMPORTANCE
    return max(MIN_IMPORTANCE, min(MAX_IMPORTANCE, value))


def _build_memory_tools(
    backend: MemoryToolBackend,
    *,
    tool_cls: Any,
    logger: Any | None,
) -> list[Any]:
    async def _search(event: Any, **kwargs: Any) -> str:
        try:
            return await backend.memory_search(
                view=to_event_view(event),
                query=str(kwargs.get("query") or ""),
                limit=_coerce_limit(kwargs.get("limit")),
            )
        except Exception as exc:  # noqa: BLE001 - 工具失败不得打断对话
            _log_debug(logger, "记忆检索工具执行失败：%s", safe_detail(exc))
            return "记忆检索暂时不可用，请直接根据现有信息回答。"

    async def _write(event: Any, **kwargs: Any) -> str:
        try:
            return await backend.memory_write(
                view=to_event_view(event),
                content=str(kwargs.get("content") or ""),
                kind=str(kwargs.get("kind") or "fact"),
                importance=_coerce_importance(kwargs.get("importance")),
            )
        except Exception as exc:  # noqa: BLE001
            _log_warning(logger, "记忆写入工具执行失败：%s", safe_detail(exc))
            return "记忆写入暂时不可用，请直接回答用户。"

    return [
        tool_cls(
            name=MEMORY_SEARCH_TOOL,
            description=_SEARCH_DESCRIPTION,
            parameters=_search_parameters(),
            handler=_search,
        ),
        tool_cls(
            name=MEMORY_WRITE_TOOL,
            description=_WRITE_DESCRIPTION,
            parameters=_write_parameters(),
            handler=_write,
        ),
    ]


def create_memory_tools(
    backend: MemoryToolBackend,
    *,
    tool_cls: Any | None = None,
    logger: Any | None = None,
) -> list[Any]:
    """构造记忆工具对象；框架不支持函数工具时返回空列表。"""
    resolved = tool_cls if tool_cls is not None else compat.SYMBOLS.FunctionTool
    if resolved is None:
        _log_debug(logger, "当前 AstrBot 未提供 FunctionTool，跳过记忆工具注册")
        return []
    try:
        return _build_memory_tools(backend, tool_cls=resolved, logger=logger)
    except Exception as exc:  # noqa: BLE001 - 构造失败按「不支持」处理
        _log_warning(logger, "构造记忆工具失败，已跳过：%s", safe_detail(exc))
        return []


def register_tools(
    context: Any,
    tools: Sequence[Any],
    *,
    logger: Any | None = None,
) -> int:
    """把工具注册进 AstrBot，返回实际注册数量（失败返回 0）。

    优先使用官方 ``context.add_llm_tools(*tools)``；该 API 不存在时退化为直接写入
    ``FunctionToolManager.func_list``。两条路径都会先按名字清理同名旧工具，
    保证插件重载后不会残留重复项。
    """
    if not tools:
        return 0
    _prune_by_name(context, {str(getattr(tool, "name", "")) for tool in tools})

    adder = getattr(context, "add_llm_tools", None)
    if callable(adder):
        try:
            adder(*tools)
            return len(tools)
        except Exception as exc:  # noqa: BLE001 - 回退到工具管理器路径
            _log_debug(logger, "add_llm_tools 不可用，改用工具管理器注册：%s", safe_detail(exc))

    manager = _tool_manager(context)
    func_list = getattr(manager, "func_list", None) if manager is not None else None
    if not isinstance(func_list, list):
        _log_warning(logger, "未获取到函数工具管理器，记忆工具未注册")
        return 0
    func_list.extend(tools)
    return len(tools)


def unregister_tools(context: Any, *, logger: Any | None = None) -> int:
    """注销本插件注册的记忆工具（插件卸载/重载时调用）。"""
    removed = _prune_by_name(context, set(MEMORY_TOOL_NAMES))
    if removed:
        _log_debug(logger, "已注销 %s 个记忆工具", removed)
    return removed


def register_memory_tools(
    context: Any,
    backend: MemoryToolBackend,
    *,
    tool_cls: Any | None = None,
    logger: Any | None = None,
) -> int:
    """构造并注册记忆工具；返回注册数量（0 表示框架不支持或注册失败）。"""
    tools = create_memory_tools(backend, tool_cls=tool_cls, logger=logger)
    return register_tools(context, tools, logger=logger)


def _tool_manager(context: Any) -> Any | None:
    """获取函数工具管理器；不同版本分别经 ``Context`` 或 ``provider_manager``。"""
    if context is None:
        return None
    try:
        getter = getattr(context, "get_llm_tool_manager", None)
        if callable(getter):
            return getter()
        return getattr(getattr(context, "provider_manager", None), "llm_tools", None)
    except Exception as exc:  # noqa: BLE001 - 探测必须宽容
        _log_debug(None, "获取函数工具管理器失败：%s", safe_detail(exc))
        return None


def _prune_by_name(context: Any, names: set[str]) -> int:
    """从工具管理器移除指定名字的工具，返回移除数量。"""
    if not names:
        return 0
    manager = _tool_manager(context)
    func_list = getattr(manager, "func_list", None) if manager is not None else None
    if not isinstance(func_list, list):
        return 0
    before = len(func_list)
    func_list[:] = [tool for tool in func_list if str(getattr(tool, "name", "")) not in names]
    return before - len(func_list)


def _log_debug(logger: Any | None, message: str, *args: Any) -> None:
    if logger is not None:
        logger.debug(message, *args)


def _log_warning(logger: Any | None, message: str, *args: Any) -> None:
    if logger is not None:
        logger.warning(message, *args)
