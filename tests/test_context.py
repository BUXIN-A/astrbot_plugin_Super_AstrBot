"""上下文治理测试：token 估算、占位压缩、摘要水位线与缓存、降级与不变式。

测试环境没有 AstrBot，因此用最小替身模拟 ``ProviderRequest.contexts``（普通 ``list[dict]``）
与 ``LlmGateway``；这正是本模块刻意保持的依赖边界。
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
from typing import Any

from super_astrbot.context import ContextConfig, ContextGovernor
from super_astrbot.context.governor import (
    IMAGE_PLACEHOLDER,
    SUMMARY_HEADER,
    TOOL_PLACEHOLDER,
    _message_digest,
)
from super_astrbot.harness.protocols import LlmResult
from super_astrbot.loop import MemoryStateStore
from super_astrbot.spec.errors import BudgetExhaustedError, LlmError
from super_astrbot.support import (
    MESSAGE_OVERHEAD,
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_tokens,
)

SESSION = "aiocqhttp:FriendMessage:10086"
CACHE_KEY = "ctx-summary:" + SESSION
FILLER = "内容填充" * 60


# --------------------------------------------------------------------------- #
# 替身与构造
# --------------------------------------------------------------------------- #


@dataclass
class FakeRequest:
    contexts: list[dict[str, Any]] = field(default_factory=list)


class ReadOnlyRequest:
    """``contexts`` 只读，模拟不可写回的请求对象。"""

    def __init__(self, contexts: list[dict[str, Any]]) -> None:
        self._contexts = contexts

    @property
    def contexts(self) -> list[dict[str, Any]]:
        return self._contexts


@dataclass
class FakeLlm:
    text: str = "摘要内容"
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def chat(self, **kwargs: Any) -> LlmResult:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return LlmResult(text=self.text)


def _pair_history(count: int, *, prefix: str = "第") -> list[dict[str, Any]]:
    """构造 ``count`` 轮「用户 + 助手」的普通对话（无工具、无图片）。"""
    messages: list[dict[str, Any]] = []
    for index in range(count):
        messages.append({"role": "user", "content": f"{prefix}{index}轮用户：{FILLER}"})
        messages.append({"role": "assistant", "content": f"{prefix}{index}轮助手：{FILLER}"})
    return messages


def _tool_heavy_history(tool_count: int, *, recent: int = 4) -> list[dict[str, Any]]:
    """构造大量「工具结果 + 图片」的历史，用于验证零成本占位压缩。"""
    messages: list[dict[str, Any]] = []
    for index in range(tool_count):
        messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": f"c{index}"}]})
        messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": "结" * 2000})
    messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "这张图里有什么"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        }
    )
    for index in range(recent):
        messages.append({"role": "user", "content": f"最近第 {index} 条"})
    return messages


def _photo(messages: list[dict[str, Any]]) -> list[Any]:
    return copy.deepcopy(messages)


def _governor(
    *,
    llm: FakeLlm,
    max_tokens: int,
    store: MemoryStateStore | None = None,
    keep_recent: int = 4,
    min_messages: int = 4,
) -> ContextGovernor:
    return ContextGovernor(
        config=ContextConfig(
            enabled=True,
            max_tokens=max_tokens,
            keep_recent=keep_recent,
            min_messages=min_messages,
        ),
        llm=llm,
        store=store,
    )


def _triggering(messages: list[dict[str, Any]]) -> int:
    """取一个必然触发治理的阈值（比当前估算小 1）。"""
    return estimate_messages_tokens(messages) - 1


# --------------------------------------------------------------------------- #
# token 估算
# --------------------------------------------------------------------------- #


def test_estimate_tokens_heuristics() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 40) == 10, "ASCII 约 4 字符 1 token"
    assert estimate_tokens("中" * 10) == 10, "CJK 逐字计 1 token"
    assert estimate_tokens("ab 中") == 2, "2 个 ASCII → 1 token，1 个 CJK → 1 token"
    assert estimate_tokens("中" * 100) > estimate_tokens("中" * 10)


def test_estimate_message_tokens_counts_tool_calls_and_images() -> None:
    assert estimate_message_tokens({"role": "user", "content": "abc"}) == MESSAGE_OVERHEAD + 1
    plain = estimate_message_tokens({"role": "assistant", "content": None})
    with_calls = estimate_message_tokens(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"function": {"name": "search", "arguments": '{"q":"x"}'}}],
        }
    )
    assert with_calls > plain
    assert (
        estimate_message_tokens(
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}
        )
        >= 500
    )


def test_context_config_clamps_values() -> None:
    config = ContextConfig.from_mapping(
        {"context": {"enabled": "true", "keep_recent": 1, "max_tokens": 10}}
    )
    assert config.enabled is True
    assert config.keep_recent == 2, "最少保留 2 条，保证模型能看到最近一轮问答"
    assert config.max_tokens == 1000, "低边界钳制"
    assert config.protected_messages == 2


# --------------------------------------------------------------------------- #
# 决策与占位压缩
# --------------------------------------------------------------------------- #


def test_governor_skips_when_disabled() -> None:
    async def _run() -> tuple[Any, list[dict[str, Any]]]:
        messages = _pair_history(12)
        before = _photo(messages)
        config = ContextConfig(enabled=False, max_tokens=1, keep_recent=4, min_messages=4)
        result = await ContextGovernor(config=config, llm=FakeLlm()).govern(
            FakeRequest(contexts=messages), session_key=SESSION
        )
        return result, before

    result, before = asyncio.run(_run())
    assert result.applied is False
    assert result.reason == "未启用"


def test_governor_skips_below_threshold() -> None:
    async def _run() -> Any:
        messages = _pair_history(6)
        governor = _governor(llm=FakeLlm(), max_tokens=estimate_messages_tokens(messages) + 100)
        return await governor.govern(FakeRequest(contexts=messages), session_key=SESSION)

    result = asyncio.run(_run())
    assert result.applied is False
    assert result.reason == "未超过阈值"


def test_governor_shrinks_tools_and_images_without_llm() -> None:
    async def _run() -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]], list[Any], FakeLlm]:
        messages = _tool_heavy_history(10)
        before = _photo(messages)
        llm = FakeLlm()
        governor = _governor(llm=llm, max_tokens=_triggering(messages))
        request = FakeRequest(contexts=messages)
        result = await governor.govern(request, session_key=SESSION)
        return result, request.contexts, messages, before, llm

    result, contexts, messages, before, llm = asyncio.run(_run())
    assert result.applied is True
    assert result.reason == "仅占位压缩"
    assert result.placeholders >= 10
    assert result.final_tokens < result.original_tokens
    assert llm.calls == [], "占位压缩已达标时不得调用模型"
    assert messages == before, "传入的消息字典不得被原地修改（可能与 AstrBot 历史共享引用）"
    assert any(TOOL_PLACEHOLDER in str(m.get("content")) for m in contexts)
    assert any(IMAGE_PLACEHOLDER in str(m.get("content")) for m in contexts)


# --------------------------------------------------------------------------- #
# 摘要水位线与缓存
# --------------------------------------------------------------------------- #


def test_governor_summarizes_head_and_caches() -> None:
    async def _run() -> tuple[Any, list[dict[str, Any]], Any, FakeLlm]:
        messages = _pair_history(12)
        store = MemoryStateStore()
        llm = FakeLlm(text="摘要A")
        governor = _governor(llm=llm, store=store, max_tokens=_triggering(messages))
        request = FakeRequest(contexts=messages)
        result = await governor.govern(request, session_key=SESSION)
        return result, request.contexts, await store.get(CACHE_KEY), llm

    result, contexts, cached, llm = asyncio.run(_run())
    assert result.applied is True
    assert result.summarized == 20, "12 轮共 24 条，保留最近 4 条 → 摘要覆盖 20 条"
    assert len(llm.calls) == 1
    assert contexts[0]["role"] == "system"
    assert contexts[0]["content"].startswith(SUMMARY_HEADER)
    assert "摘要A" in contexts[0]["content"]
    assert len(contexts) == 5, "摘要 + 最近 4 条原文"
    assert cached["covered"] == 20
    assert cached["anchor"] == _message_digest(_pair_history(12)[19])
    assert cached["summary"] == "摘要A"


def test_governor_extends_cached_summary_incrementally() -> None:
    async def _run() -> tuple[int, str, Any]:
        first = _pair_history(12)
        second = first + _pair_history(4, prefix="续")
        store = MemoryStateStore()
        llm = FakeLlm(text="摘要A")

        await _governor(llm=llm, store=store, max_tokens=_triggering(first)).govern(
            FakeRequest(contexts=first), session_key=SESSION
        )
        await _governor(llm=llm, store=store, max_tokens=_triggering(second)).govern(
            FakeRequest(contexts=second), session_key=SESSION
        )
        return len(llm.calls), llm.calls[-1]["prompt"], await store.get(CACHE_KEY)

    calls, second_prompt, cached = asyncio.run(_run())
    assert calls == 2, "第二次应只做增量续写"
    assert "摘要A" in second_prompt, "续写提示词必须带上旧摘要"
    assert cached["covered"] == 28
    assert cached["summary"] == "摘要A"


def test_governor_keeps_recent_messages_verbatim() -> None:
    async def _run() -> list[dict[str, Any]]:
        messages = _pair_history(12)
        governor = _governor(llm=FakeLlm(text="摘要B"), max_tokens=_triggering(messages))
        request = FakeRequest(contexts=messages)
        await governor.govern(request, session_key=SESSION)
        return request.contexts

    contexts = asyncio.run(_run())
    assert contexts[1:] == _pair_history(12)[20:], "最近保留区必须逐字不变"


# --------------------------------------------------------------------------- #
# 降级与不变式
# --------------------------------------------------------------------------- #


def test_governor_tolerates_summary_failure() -> None:
    async def _run() -> tuple[Any, list[dict[str, Any]]]:
        messages = _pair_history(12)
        before = _photo(messages)
        llm = FakeLlm(error=LlmError("模型不可用"))
        governor = _governor(llm=llm, max_tokens=_triggering(messages))
        result = await governor.govern(FakeRequest(contexts=messages), session_key=SESSION)
        assert messages == before
        return result, before

    result, before = asyncio.run(_run())
    assert result.applied is False, "没有任何改动时不应声称已治理"
    assert "摘要调用失败" in result.error


def test_governor_tolerates_budget_exhaustion() -> None:
    async def _run() -> Any:
        messages = _pair_history(12)
        llm = FakeLlm(error=BudgetExhaustedError("预算用尽"))
        governor = _governor(llm=llm, max_tokens=_triggering(messages))
        return await governor.govern(FakeRequest(contexts=messages), session_key=SESSION)

    result = asyncio.run(_run())
    assert result.applied is False
    assert "预算" in result.error


def test_governor_still_applies_placeholders_when_summary_fails() -> None:
    async def _run() -> tuple[Any, list[dict[str, Any]]]:
        # 头部塞一条不可占位压缩的长文本：占位后仍超阈值 → 必须尝试摘要
        messages = _tool_heavy_history(10, recent=6)
        messages.insert(2, {"role": "assistant", "content": "长" * 2000})
        llm = FakeLlm(error=LlmError("模型不可用"))
        governor = _governor(llm=llm, max_tokens=1000, keep_recent=6)
        request = FakeRequest(contexts=messages)
        result = await governor.govern(request, session_key=SESSION)
        return result, request.contexts

    result, contexts = asyncio.run(_run())
    assert result.applied is True, "摘要失败也必须保留占位压缩成果"
    assert result.placeholders >= 10
    assert any(TOOL_PLACEHOLDER in str(m.get("content")) for m in contexts)
    assert "摘要调用失败" in result.error


def test_governor_aligns_tool_pairing_at_split() -> None:
    async def _run() -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = _pair_history(10)
        messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": "x"}]})
        messages.append({"role": "tool", "tool_call_id": "x", "content": FILLER})
        messages.append({"role": "user", "content": "最近一条"})
        governor = _governor(
            llm=FakeLlm(text="摘要C"), max_tokens=_triggering(messages), keep_recent=2
        )
        request = FakeRequest(contexts=messages)
        await governor.govern(request, session_key=SESSION)
        return request.contexts

    contexts = asyncio.run(_run())
    assert contexts[0]["role"] == "system"
    assert contexts[1]["role"] != "tool", "保留区不得以 tool 消息开头（会切断 tool_calls 配对）"


def test_governor_preserves_leading_system_message() -> None:
    async def _run() -> list[dict[str, Any]]:
        system = {"role": "system", "content": "你是一个助手"}
        messages = [system, *_pair_history(12)]
        governor = _governor(llm=FakeLlm(text="摘要S"), max_tokens=_triggering(messages))
        request = FakeRequest(contexts=messages)
        await governor.govern(request, session_key=SESSION)
        return request.contexts

    contexts = asyncio.run(_run())
    assert contexts[0] == {"role": "system", "content": "你是一个助手"}, (
        "系统提示词必须原样保留且仍在首位"
    )
    assert contexts[1]["role"] == "system", "摘要应插在系统提示词之后"
    assert contexts[1]["content"].startswith(SUMMARY_HEADER)


def test_governor_skips_summary_when_checkpoint_present() -> None:
    async def _run() -> tuple[Any, FakeLlm]:
        messages = _pair_history(12)
        messages.insert(2, {"role": "_checkpoint", "content": {"id": "cp-1"}})
        llm = FakeLlm(text="摘要D")
        governor = _governor(llm=llm, max_tokens=_triggering(messages))
        result = await governor.govern(FakeRequest(contexts=messages), session_key=SESSION)
        return result, llm

    result, llm = asyncio.run(_run())
    assert llm.calls == [], "含框架 checkpoint 段时必须放弃摘要"
    assert result.applied is False
    assert "checkpoint" in result.reason


def test_governor_gives_up_when_request_not_writable() -> None:
    async def _run() -> tuple[Any, list[dict[str, Any]]]:
        messages = _tool_heavy_history(10)
        before = _photo(messages)
        governor = _governor(llm=FakeLlm(), max_tokens=1000)
        result = await governor.govern(ReadOnlyRequest(messages), session_key=SESSION)
        assert messages == before
        return result, before

    result, before = asyncio.run(_run())
    assert result.applied is False
    assert "不可写" in result.reason
