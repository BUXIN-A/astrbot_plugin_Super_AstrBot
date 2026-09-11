"""作用域与注入测试（含无 AstrBot 环境下的降级路径）。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from super_astrbot.harness.astrbot_llm import (
    MEMORY_BLOCK_END,
    MEMORY_BLOCK_START,
    AstrBotInjector,
)
from super_astrbot.memory import MemoryConfig
from super_astrbot.spec.scopes import (
    GLOBAL_SCOPE_ID,
    MemoryScope,
    ScopeType,
    retrieval_scopes,
)

from .helpers import build_stack


class _NullHost:
    class _Logger:
        def debug(self, *args: object, **kwargs: object) -> None: ...

    def log(self) -> object:
        return self._Logger()


def test_global_scope_id_is_normalized() -> None:
    scope = MemoryScope(ScopeType.GLOBAL, "任何值")
    assert scope.scope_id == GLOBAL_SCOPE_ID
    assert scope.key == "global:*"


def test_retrieval_scopes_include_global() -> None:
    scopes = retrieval_scopes(MemoryScope.for_session("a"))
    assert [item.scope_type for item in scopes] == [ScopeType.SESSION, ScopeType.GLOBAL]

    global_only = retrieval_scopes(MemoryScope.global_scope())
    assert len(global_only) == 1


def test_scope_parse_falls_back_to_default() -> None:
    assert ScopeType.parse("USER") is ScopeType.USER
    assert ScopeType.parse("不认识") is ScopeType.SESSION
    assert ScopeType.parse(None, ScopeType.GLOBAL) is ScopeType.GLOBAL


def test_injector_writes_and_clears_without_accumulation() -> None:
    injector = AstrBotInjector(_NullHost())
    request = SimpleNamespace(extra_user_content_parts=[], system_prompt="基础提示")

    first = injector.inject(request, ["- 记忆A"])
    assert first.applied is True
    assert MEMORY_BLOCK_START in request.system_prompt

    # 第二次注入应先清理上一次，绝不能累积
    second = injector.inject(request, ["- 记忆B"])
    assert second.applied is True
    assert request.system_prompt.count(MEMORY_BLOCK_START) == 1
    assert "记忆A" not in request.system_prompt
    assert "记忆B" in request.system_prompt

    removed = injector.clear(request)
    assert removed >= 1
    assert MEMORY_BLOCK_START not in request.system_prompt
    assert request.system_prompt.strip() == "基础提示"


def test_injector_ignores_empty_blocks() -> None:
    injector = AstrBotInjector(_NullHost())
    request = SimpleNamespace(extra_user_content_parts=[], system_prompt="")
    result = injector.inject(request, ["", "   "])
    assert result.applied is False
    assert request.system_prompt == ""


def test_injector_clears_legacy_parts() -> None:
    injector = AstrBotInjector(_NullHost())
    stale = SimpleNamespace(text=f"{MEMORY_BLOCK_START}\n旧内容\n{MEMORY_BLOCK_END}")
    fresh = SimpleNamespace(text="普通内容")
    request = SimpleNamespace(extra_user_content_parts=[stale, fresh], system_prompt="")

    removed = injector.clear(request)
    assert removed == 1
    assert len(request.extra_user_content_parts) == 1


def test_service_inject_calls_injector_when_hits(tmp_path: Path) -> None:
    async def _run() -> tuple[bool, list[tuple[int, str]]]:
        stack = await build_stack(tmp_path)
        scope = MemoryScope.for_session("s1")
        await stack.memory.remember_text(scope, "用户喜欢在周末爬山放松")
        result = await stack.memory.recall(scope, "爬山")
        inject_result = await stack.memory.inject(object(), result)
        calls = list(stack.injector.calls)
        await stack.close()
        return inject_result.applied, calls

    applied, calls = asyncio.run(_run())
    assert applied is True
    assert calls, "应至少调用一次注入器"


def test_service_inject_respects_disabled_method(tmp_path: Path) -> None:
    async def _run() -> tuple[bool, str, list[tuple[int, str]]]:
        config = MemoryConfig(injection_method="disabled")
        stack = await build_stack(tmp_path, config=config)
        scope = MemoryScope.for_session("s1")
        await stack.memory.remember_text(scope, "用户喜欢在周末爬山放松")
        result = await stack.memory.recall(scope, "爬山")
        inject_result = await stack.memory.inject(object(), result)
        calls = list(stack.injector.calls)
        await stack.close()
        return inject_result.applied, inject_result.reason, calls

    applied, reason, calls = asyncio.run(_run())
    assert applied is False
    assert "仅检索不注入" in reason
    assert calls == []
