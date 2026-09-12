"""Agent 函数工具测试：工具构造、注册/注销、业务后端行为与降级。

测试环境没有 AstrBot，因此工具构造通过注入 ``tool_cls`` 替身完成；
``create_memory_tools`` 在真实环境中拿不到 ``FunctionTool`` 的降级路径单独断言。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from super_astrbot.harness import compat
from super_astrbot.harness.protocols import EventView
from super_astrbot.harness.tools import (
    MEMORY_SEARCH_TOOL,
    MEMORY_WRITE_TOOL,
    create_memory_tools,
    register_tools,
    unregister_tools,
)
from super_astrbot.memory import AgentMemoryBackend
from super_astrbot.memory.agent_tools import (
    DEFAULT_IMPORTANCE,
    NO_RESULT_TEXT,
    NO_SESSION_TEXT,
    WRITE_DENIED_TEXT,
)
from super_astrbot.memory.models import KIND_FACT, SOURCE_AGENT
from super_astrbot.spec.scopes import MemoryScope

from .helpers import build_stack

OPEN_ADMIN_GATE = {"basic": {"admin_only_commands": False}}
STRICT_ADMIN_GATE = {"basic": {"admin_only_commands": True}}


# --------------------------------------------------------------------------- #
# 替身
# --------------------------------------------------------------------------- #


@dataclass
class FakeFunctionTool:
    """``astrbot.core.agent.tool.FunctionTool`` 的最小替身（字段与真实类一致）。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Any = None


class FakeManager:
    def __init__(self) -> None:
        self.func_list: list[Any] = []


class FakeContext:
    """模拟 AstrBot 的 ``Context``：可选用 ``add_llm_tools`` 或只暴露管理器。"""

    def __init__(self, *, with_adder: bool = True) -> None:
        self.provider_manager = SimpleNamespace(llm_tools=FakeManager())
        self._with_adder = with_adder

    @property
    def func_list(self) -> list[Any]:
        return self.provider_manager.llm_tools.func_list

    def add_llm_tools(self, *tools: Any) -> None:
        if not self._with_adder:
            raise AssertionError("本替身未启用 add_llm_tools")
        self.func_list.extend(tools)


class FakeEvent:
    """仅供 ``to_event_view`` 读取的最小事件替身。"""

    def __init__(
        self, *, umo: str = "aiocqhttp:FriendMessage:10086", is_admin: bool = True
    ) -> None:
        self.unified_msg_origin = umo
        self.created_at = 1_700_000_000.0
        self._is_admin = is_admin

    def is_private_chat(self) -> bool:
        return True

    def get_group_id(self) -> str:
        return ""

    def get_platform_name(self) -> str:
        return "aiocqhttp"

    def get_session_id(self) -> str:
        return "10086"

    def get_sender_id(self) -> str:
        return "u-1"

    def get_sender_name(self) -> str:
        return "阿澈"

    def get_message_str(self) -> str:
        return ""

    def is_admin(self) -> bool:
        return self._is_admin

    def is_stopped(self) -> bool:
        return False


@dataclass
class RecordingBackend:
    """记录调用参数的替身后端。"""

    search_calls: list[tuple[str, str, int | None]] = field(default_factory=list)
    write_calls: list[tuple[str, str, str, float]] = field(default_factory=list)

    async def memory_search(
        self, *, view: EventView, query: str = "", limit: int | None = None
    ) -> str:
        self.search_calls.append((view.umo, query, limit))
        return "SEARCH-OK"

    async def memory_write(
        self,
        *,
        view: EventView,
        content: str = "",
        kind: str = "fact",
        importance: float = DEFAULT_IMPORTANCE,
    ) -> str:
        self.write_calls.append((view.umo, content, kind, importance))
        return "WRITE-OK"


class BrokenBackend:
    """任何调用都抛异常的替身，用于验证工具不会把异常抛回框架。"""

    async def memory_search(self, **kwargs: Any) -> str:
        raise RuntimeError("search boom")

    async def memory_write(self, **kwargs: Any) -> str:
        raise RuntimeError("write boom")


def _view(*, umo: str = "s1", sender_id: str = "u-1", is_admin: bool = True) -> EventView:
    return EventView(umo=umo, sender_id=sender_id, is_admin=is_admin, text="x")


# --------------------------------------------------------------------------- #
# 工具构造
# --------------------------------------------------------------------------- #


def test_create_memory_tools_builds_expected_schemas() -> None:
    tools = create_memory_tools(RecordingBackend(), tool_cls=FakeFunctionTool)
    assert [tool.name for tool in tools] == [MEMORY_SEARCH_TOOL, MEMORY_WRITE_TOOL]

    search, write = tools
    assert search.parameters["required"] == ["query"]
    assert search.parameters["properties"]["query"]["type"] == "string"
    assert search.parameters["properties"]["limit"]["maximum"] == 20

    assert write.parameters["required"] == ["content"]
    assert write.parameters["properties"]["kind"]["enum"] == ["fact", "insight", "preference"]
    assert write.parameters["properties"]["importance"]["minimum"] == 0.1
    assert callable(search.handler) and callable(write.handler)


def test_create_memory_tools_degrades_when_framework_missing() -> None:
    """框架未提供 FunctionTool 时必须返回空列表，而不是抛异常。"""
    if compat.SYMBOLS.FunctionTool is not None:
        pytest.skip("当前环境已安装 AstrBot，无法断言降级路径")
    assert create_memory_tools(RecordingBackend(), tool_cls=None) == []


def test_create_memory_tools_tolerates_broken_tool_class() -> None:
    """工具类构造失败时必须降级为空列表，不能把异常抛到启动流程。"""

    class Broken:
        def __init__(self, **kwargs: Any) -> None:
            raise RuntimeError("boom")

    assert create_memory_tools(RecordingBackend(), tool_cls=Broken) == []


def test_tool_handler_forwards_arguments_to_backend() -> None:
    backend = RecordingBackend()
    search, write = create_memory_tools(backend, tool_cls=FakeFunctionTool)
    event = FakeEvent()

    async def _run() -> tuple[str, str]:
        searched = await search.handler(event, query="爬山", limit="3")
        written = await write.handler(
            event, content="用户偏爱手冲咖啡", kind="preference", importance="0.9"
        )
        return searched, written

    searched, written = asyncio.run(_run())
    assert searched == "SEARCH-OK"
    assert written == "WRITE-OK"
    assert backend.search_calls == [("aiocqhttp:FriendMessage:10086", "爬山", 3)]
    assert backend.write_calls == [
        ("aiocqhttp:FriendMessage:10086", "用户偏爱手冲咖啡", "preference", 0.9)
    ]


def test_tool_handler_normalizes_bad_numeric_arguments() -> None:
    backend = RecordingBackend()
    search, write = create_memory_tools(backend, tool_cls=FakeFunctionTool)
    event = FakeEvent()

    async def _run() -> None:
        await search.handler(event, query="x", limit="not-a-number")
        await write.handler(event, content="x", importance="not-a-number")

    asyncio.run(_run())
    assert backend.search_calls == [("aiocqhttp:FriendMessage:10086", "x", None)]
    assert backend.write_calls[0][3] == DEFAULT_IMPORTANCE


def test_tool_handler_swallows_backend_failure() -> None:
    """工具内部异常必须被吞掉并给出可读文本，否则会打断整轮对话。"""
    search, write = create_memory_tools(BrokenBackend(), tool_cls=FakeFunctionTool)
    event = FakeEvent()

    async def _run() -> tuple[str, str]:
        return (
            await search.handler(event, query="x"),
            await write.handler(event, content="x"),
        )

    searched, written = asyncio.run(_run())
    assert "暂时不可用" in searched
    assert "暂时不可用" in written


# --------------------------------------------------------------------------- #
# 注册与注销
# --------------------------------------------------------------------------- #


def test_register_tools_uses_add_llm_tools_and_dedupes() -> None:
    context = FakeContext(with_adder=True)
    tools = create_memory_tools(RecordingBackend(), tool_cls=FakeFunctionTool)

    assert register_tools(context, tools) == 2
    assert [tool.name for tool in context.func_list] == [MEMORY_SEARCH_TOOL, MEMORY_WRITE_TOOL]

    # 插件重载后重复注册：同名工具必须被替换而不是堆积
    assert register_tools(context, tools) == 2
    assert len(context.func_list) == 2

    assert unregister_tools(context) == 2
    assert context.func_list == []


def test_register_tools_falls_back_to_tool_manager() -> None:
    context = FakeContext(with_adder=False)
    tools = create_memory_tools(RecordingBackend(), tool_cls=FakeFunctionTool)

    assert register_tools(context, tools) == 2
    assert len(context.func_list) == 2
    assert unregister_tools(context) == 2


def test_register_tools_degrades_without_manager() -> None:
    assert (
        register_tools(object(), [FakeFunctionTool(name="x", description="", parameters={})]) == 0
    )
    assert unregister_tools(object()) == 0


# --------------------------------------------------------------------------- #
# 业务后端
# --------------------------------------------------------------------------- #


def test_agent_search_returns_formatted_memories_and_touches_access(tmp_path: Path) -> None:
    async def _run() -> tuple[str, str, str, int]:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        await stack.memory.remember_text(scope, "用户喜欢在周末爬山放松", importance=0.9)

        backend = AgentMemoryBackend(service=stack.memory, config=OPEN_ADMIN_GATE)
        hit = await backend.memory_search(view=_view(), query="爬山")
        miss = await backend.memory_search(view=_view(), query="量子力学")
        blank = await backend.memory_search(view=_view(), query="   ")
        access = (await stack.memory.list_memories(scope))[0].access_count
        await stack.close()
        return hit, miss, blank, access

    hit, miss, blank, access = asyncio.run(_run())
    assert "爬山" in hit
    assert miss == NO_RESULT_TEXT
    assert blank == "请提供检索关键词。"
    assert access == 1, "被工具命中的记忆应累计访问次数"


def test_agent_search_rejects_missing_session(tmp_path: Path) -> None:
    async def _run() -> str:
        stack = await build_stack(tmp_path)
        backend = AgentMemoryBackend(service=stack.memory, config=OPEN_ADMIN_GATE)
        result = await backend.memory_search(view=_view(umo=""), query="爬山")
        await stack.close()
        return result

    assert asyncio.run(_run()) == NO_SESSION_TEXT


def test_agent_write_enforces_admin_gate_and_normalizes(tmp_path: Path) -> None:
    async def _run() -> tuple[str, str, str, str, str, float]:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        strict = AgentMemoryBackend(service=stack.memory, config=STRICT_ADMIN_GATE)
        opened = AgentMemoryBackend(service=stack.memory, config=OPEN_ADMIN_GATE)

        denied = await strict.memory_write(view=_view(is_admin=False), content="用户偏好拿铁")
        allowed = await strict.memory_write(view=_view(is_admin=True), content="用户偏好拿铁")
        opened_ok = await opened.memory_write(view=_view(is_admin=False), content="用户偏好手冲")

        # 非法 kind → fact，非法 importance → 默认值
        await opened.memory_write(
            view=_view(is_admin=False), content="用户每周三打球", kind="bogus", importance="abc"
        )
        items = await stack.memory.list_memories(scope)
        fallback = next(item for item in items if item.content == "用户每周三打球")
        await stack.close()
        return (
            denied,
            allowed,
            opened_ok,
            fallback.kind,
            fallback.source,
            fallback.importance,
        )

    denied, allowed, opened_ok, kind, source, importance = asyncio.run(_run())
    assert denied == WRITE_DENIED_TEXT
    assert "已写入长期记忆" in allowed
    assert "已写入长期记忆" in opened_ok
    assert kind == KIND_FACT
    assert source == SOURCE_AGENT
    assert abs(importance - DEFAULT_IMPORTANCE) < 1e-6


def test_agent_write_validates_content(tmp_path: Path) -> None:
    async def _run() -> tuple[str, str, str]:
        stack = await build_stack(tmp_path)
        backend = AgentMemoryBackend(service=stack.memory, config=OPEN_ADMIN_GATE)
        await stack.memory.remember_text(MemoryScope.for_session("s1"), "已有记忆", importance=0.5)
        blank = await backend.memory_write(view=_view(), content="   ")
        too_long = await backend.memory_write(view=_view(), content="很" * 500)
        no_session = await backend.memory_write(view=_view(umo=""), content="用户偏好拿铁")
        await stack.close()
        return blank, too_long, no_session

    blank, too_long, no_session = asyncio.run(_run())
    assert blank == "记忆内容为空，未写入。"
    assert "过长" in too_long
    assert no_session == NO_SESSION_TEXT
