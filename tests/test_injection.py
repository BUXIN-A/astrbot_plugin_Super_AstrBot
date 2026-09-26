"""作用域与注入测试（含无 AstrBot 环境下的降级路径）。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    """宿主替身：实现 ``LoggerLike`` 的最小接口，并把日志留档供断言。"""

    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []
        self._logger = self._Logger(self.records)

    class _Logger:
        def __init__(self, records: list[tuple[str, str]]) -> None:
            self._records = records

        def _write(self, level: str, msg: Any, *args: Any, **kwargs: Any) -> None:
            self._records.append((level, str(msg) % args if args else str(msg)))

        def debug(self, msg: Any, *args: Any, **kwargs: Any) -> None:
            self._write("debug", msg, *args)

        def info(self, msg: Any, *args: Any, **kwargs: Any) -> None:
            self._write("info", msg, *args)

        def warning(self, msg: Any, *args: Any, **kwargs: Any) -> None:
            self._write("warning", msg, *args)

        def error(self, msg: Any, *args: Any, **kwargs: Any) -> None:
            self._write("error", msg, *args)

    def log(self) -> object:
        return self._logger


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


class _FakePart:
    """``TextPart`` 替身：只需 ``text`` 与 ``mark_as_temp()``。"""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.temp = False

    def mark_as_temp(self) -> "_FakePart":
        self.temp = True
        return self


class _IgnoringParts(list):
    """append 被吞掉的列表替身：模拟「宿主给得出列表，但根本不消费它」。"""

    def append(self, item: Any) -> None:
        return None


def _with_fake_text_part(monkeypatch: Any) -> None:
    from super_astrbot.harness import astrbot_compat as compat
    from super_astrbot.harness import astrbot_llm

    monkeypatch.setattr(
        astrbot_llm.compat, "SYMBOLS", replace(compat.SYMBOLS, TextPart=_FakePart)
    )


def test_injector_prefers_temp_parts_when_host_supports_them(monkeypatch: Any) -> None:
    _with_fake_text_part(monkeypatch)
    host = _NullHost()
    injector = AstrBotInjector(host)
    request = SimpleNamespace(extra_user_content_parts=[], system_prompt="基础提示")

    result = injector.inject(request, ["- 记忆A"])

    assert result.applied is True
    assert result.method == "extra_user_content"
    assert result.fallback is False
    assert request.extra_user_content_parts[0].temp is True, "应标记为临时内容块"
    assert MEMORY_BLOCK_START not in request.system_prompt, "走临时内容块时不动系统提示词"
    assert any(level == "info" for level, _ in host.records), "注入方式应在后台可见"


def test_injector_verifies_parts_and_falls_back_when_ignored(monkeypatch: Any) -> None:
    """append 成功但宿主不消费 → 必须识别为未生效、回退并告警（回归：曾静默失效）。"""
    _with_fake_text_part(monkeypatch)
    host = _NullHost()
    injector = AstrBotInjector(host)
    request = SimpleNamespace(extra_user_content_parts=_IgnoringParts(), system_prompt="")

    result = injector.inject(request, ["- 记忆A"])

    assert result.applied is True
    assert result.method == "system_prompt"
    assert result.fallback is True
    assert MEMORY_BLOCK_START in request.system_prompt
    warnings = [message for level, message in host.records if level == "warning"]
    assert any("宿主不支持临时内容块注入" in msg for msg in warnings)
    assert any("回退 system_prompt" in msg for msg in warnings)


def test_injector_reports_failure_in_strict_mode() -> None:
    """配置要求只走临时内容块、宿主又不支持时：明确失败，而不是假装注入成功。"""
    injector = AstrBotInjector(_NullHost())
    request = SimpleNamespace(extra_user_content_parts=[], system_prompt="")

    result = injector.inject(request, ["- 记忆A"], prefer="extra_user_content")

    assert result.applied is False
    assert result.fallback is True
    assert "宿主不支持临时内容块注入" in result.reason
    assert request.system_prompt == ""


def test_injector_fills_none_system_prompt() -> None:
    """系统提示词为空（未配置人格）时也要能注入，而不是判定「不支持」。"""
    injector = AstrBotInjector(_NullHost())
    request = SimpleNamespace(extra_user_content_parts=[], system_prompt=None)

    result = injector.inject(request, ["- 记忆A"], prefer="system_prompt")

    assert result.applied is True
    assert result.method == "system_prompt"
    assert result.fallback is False, "配置本就要求系统提示词，不是降级"
    assert str(request.system_prompt).startswith(MEMORY_BLOCK_START)


def test_injector_reports_when_request_has_no_place_to_inject() -> None:
    injector = AstrBotInjector(_NullHost())
    request = SimpleNamespace()

    result = injector.inject(request, ["- 记忆A"])

    assert result.applied is False
    assert "系统提示词" in result.reason


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
