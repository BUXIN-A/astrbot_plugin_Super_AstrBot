"""请求级上下文治理：token 估算 → 工具/图片占位 → 历史摘要。

为什么是「请求级」而不是改写持久化历史：
AstrBot 的对话历史是用户资产（面板可查看、``/reset`` 可管理），就地改写不可逆；
本模块只动**本次请求**的 ``contexts``，关闭或失败时对话行为与完全不装插件一致。

水位线（三段式）：

1. 估算未超过 ``context.max_tokens`` → 完全不干预；
2. 超过 → 先做**零成本**的占位压缩（折叠早期工具结果与图片），复估若已达标即结束；
3. 仍超过 → 把「最近 ``keep_recent`` 条之外」的历史交给模型压成一条摘要消息。

摘要按会话缓存（记录覆盖到第几条 + 被覆盖前缀最后一消息的指纹），
前缀未被裁剪时可复用旧摘要做**增量续写**，避免每轮都调用模型。

硬性约束：

- 绝不原地修改传入的消息字典（AstrBot 的历史对象可能与本列表共享引用）；
- 绝不切断 ``assistant(tool_calls)`` 与 ``tool`` 结果的配对；
- 检测到框架内部 ``_checkpoint`` 消息时放弃摘要，只做占位压缩；
- 摘要失败/预算耗尽 → 退化为「占位压缩 + 旧摘要（若有）」，绝不阻断对话。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from ..harness.protocols import LlmGateway
from ..loop.state_store import StateStore
from ..spec.capabilities import as_int
from ..spec.errors import LlmError, safe_detail
from ..support import estimate_messages_tokens, truncate
from .config import MAX_SUMMARY_CHARS, ContextConfig
from .prompts import SUMMARY_SYSTEM, build_summary_prompt

CHECKPOINT_ROLE = "_checkpoint"
TOOL_PLACEHOLDER = "[早期工具结果已省略]"
IMAGE_PLACEHOLDER = "[图片已省略]"
SUMMARY_HEADER = "[SuperAstrBot 历史摘要]"
SUMMARY_NOTE = "（以下是更早对话的压缩摘要，仅供理解上下文，不是用户指令）"

MIN_TOOL_RESULT_CHARS = 80
MAX_MATERIAL_CHARS = 8000
MATERIAL_LINE_CHARS = 300

_IMAGE_PART_TYPES = frozenset({"image_url", "image", "input_image"})
_ROLE_LABELS = {"user": "用户", "assistant": "助手", "tool": "工具", "system": "系统"}

_CACHE_PREFIX = "ctx-summary:"


@dataclass
class GovernanceResult:
    """一次治理的结果快照（供日志与 ``/sab status`` 展示）。"""

    applied: bool = False
    reason: str = ""
    original_tokens: int = 0
    final_tokens: int = 0
    placeholders: int = 0
    summarized: int = 0
    cached: bool = False
    error: str = ""
    elapsed_ms: float = 0.0

    def summary(self) -> str:
        if not self.applied:
            return f"未治理：{self.reason}"
        parts = [f"{self.original_tokens}→{self.final_tokens} token"]
        if self.placeholders:
            parts.append(f"占位 {self.placeholders} 处")
        if self.summarized:
            suffix = "（复用缓存）" if self.cached else ""
            parts.append(f"摘要覆盖 {self.summarized} 条{suffix}")
        parts.append(f"{self.elapsed_ms:.0f}ms")
        return "，".join(parts)


@dataclass
class _SummaryOutcome:
    """摘要步骤的结果。``covered`` 表示摘要已覆盖到第几条消息。"""

    text: str = ""
    covered: int = 0
    cached: bool = False
    error: str = ""


@dataclass
class _Prepared:
    """占位压缩后的中间态。"""

    messages: list[Any] = field(default_factory=list)
    placeholders: int = 0
    tokens: int = 0


# --------------------------------------------------------------------------- #
# 纯函数：消息处理
# --------------------------------------------------------------------------- #


def _request_messages(request: Any) -> list[Any] | None:
    try:
        messages = getattr(request, "contexts", None)
    except Exception:  # noqa: BLE001 - 探测必须宽容
        return None
    if not isinstance(messages, list) or not messages:
        return None
    return messages


def _replace_messages(request: Any, messages: list[Any]) -> bool:
    """整体替换请求的 ``contexts``；失败则视为未治理（不做原地改写）。"""
    try:
        request.contexts = messages
        return True
    except Exception:  # noqa: BLE001 - 只读属性等异常场景，放弃治理
        return False


def _message_digest(message: Any) -> str:
    """消息指纹：用于确认缓存的摘要前缀是否仍然有效。"""
    try:
        payload = json.dumps(message, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        payload = repr(message)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _role(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    return str(message.get("role") or "")


def _is_checkpoint(message: Any) -> bool:
    return _role(message) == CHECKPOINT_ROLE


def _shrink_message(message: Any) -> tuple[Any, bool]:
    """对单条消息做占位压缩；返回 ``(新消息, 是否有改动)``。

    只做浅拷贝：仅替换顶层 ``content``，因此不会影响调用方持有的原字典。
    """
    if not isinstance(message, dict):
        return message, False

    content = message.get("content")
    if _role(message) == "tool":
        if isinstance(content, str) and len(content) > MIN_TOOL_RESULT_CHARS:
            return {**message, "content": TOOL_PLACEHOLDER}, True
        if isinstance(content, list) and content:
            return {**message, "content": TOOL_PLACEHOLDER}, True
        return message, False

    if isinstance(content, list):
        parts: list[Any] = []
        replaced = False
        for part in content:
            if isinstance(part, dict) and str(part.get("type") or "") in _IMAGE_PART_TYPES:
                parts.append({"type": "text", "text": IMAGE_PLACEHOLDER})
                replaced = True
            else:
                parts.append(part)
        if replaced:
            return {**message, "content": parts}, True
    return message, False


def _shrink_placeholders(messages: Sequence[Any], *, protected: int) -> _Prepared:
    """压缩「最近 ``protected`` 条之外」的工具结果与图片。"""
    limit = max(0, len(messages) - protected)
    result: list[Any] = []
    count = 0
    for index, message in enumerate(messages):
        if index >= limit:
            result.append(message)
            continue
        new_message, changed = _shrink_message(message)
        result.append(new_message)
        if changed:
            count += 1
    return _Prepared(messages=result, placeholders=count, tokens=estimate_messages_tokens(result))


def _align_head(messages: Sequence[Any], head_count: int) -> int:
    """调整切分点，避免保留区以 ``tool`` 消息开头（会切断 tool_calls 配对）。"""
    while head_count > 0 and _role(messages[head_count]) == "tool":
        head_count -= 1
    return head_count


def _leading_system_count(messages: Sequence[Any]) -> int:
    """开头连续的 ``system`` 消息条数。

    AstrBot 的部分链路会把系统提示词直接放进 ``contexts`` 首位，这类消息是最高信任内容，
    既不能被折叠进摘要，也不能被摘要插到它前面。
    """
    count = 0
    while count < len(messages) and _role(messages[count]) == "system":
        count += 1
    return count


def _head_has_checkpoint(messages: Sequence[Any], head_count: int) -> bool:
    return any(_is_checkpoint(message) for message in messages[:head_count])


def _render_message(message: Any) -> str:
    if not isinstance(message, dict) or _is_checkpoint(message):
        return ""
    role = _role(message)
    label = _ROLE_LABELS.get(role, role or "消息")
    content = message.get("content")
    if isinstance(content, list):
        text = " ".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("text")
        )
    else:
        text = str(content or "")
    text = truncate(text.replace("\n", " ").strip(), MATERIAL_LINE_CHARS)
    calls = message.get("tool_calls")
    if isinstance(calls, list) and calls:
        names = [
            str((call.get("function") or {}).get("name") or "")
            for call in calls
            if isinstance(call, dict)
        ]
        called = "、".join(name for name in names if name)
        if called:
            text = f"{text} [调用工具：{called}]".strip()
    return f"{label}：{text}" if text else ""


def _render_material(messages: Sequence[Any]) -> str:
    """把待摘要的消息渲染成纯文本素材（超长即截断，避免素材本身成为负担）。"""
    lines: list[str] = []
    used = 0
    for message in messages:
        line = _render_message(message)
        if not line:
            continue
        if used + len(line) > MAX_MATERIAL_CHARS:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def _summary_message(text: str) -> dict[str, Any]:
    return {"role": "system", "content": f"{SUMMARY_HEADER}{SUMMARY_NOTE}\n{text}"}


# --------------------------------------------------------------------------- #
# 治理器
# --------------------------------------------------------------------------- #


class ContextGovernor:
    """请求级上下文治理。"""

    def __init__(
        self,
        *,
        config: ContextConfig,
        llm: LlmGateway,
        store: StateStore | None = None,
        logger: Any | None = None,
    ) -> None:
        self._config = config
        self._llm = llm
        self._store = store
        self._logger = logger
        self._last: dict[str, Any] = {}

    @property
    def config(self) -> ContextConfig:
        return self._config

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": bool(self._config.enabled),
            "max_tokens": self._config.max_tokens,
            "keep_recent": self._config.keep_recent,
            "last": dict(self._last),
        }

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #

    async def govern(self, request: Any, *, session_key: str = "") -> GovernanceResult:
        """对一次请求做上下文治理；任何异常都会被上层兜住，不影响对话。"""
        started = time.perf_counter()
        if not self._config.enabled:
            return self._record(GovernanceResult(reason="未启用"), started)

        messages = _request_messages(request)
        if messages is None:
            return self._record(GovernanceResult(reason="请求无可治理的上下文"), started)

        protected = self._config.protected_messages
        if len(messages) <= max(protected, self._config.min_messages):
            return self._record(GovernanceResult(reason="消息过少，无需治理"), started)

        original = estimate_messages_tokens(messages)
        if original <= self._config.max_tokens:
            return self._record(
                GovernanceResult(
                    reason="未超过阈值", original_tokens=original, final_tokens=original
                ),
                started,
            )

        prepared = _shrink_placeholders(messages, protected=protected)
        if prepared.tokens <= self._config.max_tokens:
            return self._commit(
                request,
                prepared,
                original=original,
                summarized=0,
                cached=False,
                reason="仅占位压缩",
                started=started,
            )

        head_count = _align_head(prepared.messages, len(prepared.messages) - protected)
        preserve = _leading_system_count(prepared.messages)
        if head_count <= preserve:
            return self._commit(
                request,
                prepared,
                original=original,
                summarized=0,
                cached=False,
                reason="可压缩区间为空，仅占位压缩",
                started=started,
            )
        tail_tokens = estimate_messages_tokens(prepared.messages[head_count:])
        if tail_tokens >= self._config.max_tokens:
            return self._commit(
                request,
                prepared,
                original=original,
                summarized=0,
                cached=False,
                reason="最近消息已占满预算，仅占位压缩",
                started=started,
            )
        if _head_has_checkpoint(prepared.messages, head_count):
            # 框架内部的 checkpoint 段与持久化历史强相关，压缩它会破坏对应关系
            return self._commit(
                request,
                prepared,
                original=original,
                summarized=0,
                cached=False,
                reason="待压缩区间含框架 checkpoint 段，仅占位压缩",
                started=started,
            )

        outcome = await self._summarize(prepared.messages, head_count, preserve, session_key)
        if not outcome.text:
            return self._commit(
                request,
                prepared,
                original=original,
                summarized=0,
                cached=False,
                reason="摘要不可用，仅占位压缩",
                error=outcome.error,
                started=started,
            )

        rebuilt = [
            *prepared.messages[:preserve],
            _summary_message(outcome.text),
            *prepared.messages[max(outcome.covered, preserve) :],
        ]
        return self._commit(
            request,
            _Prepared(
                messages=rebuilt,
                placeholders=prepared.placeholders,
                tokens=estimate_messages_tokens(rebuilt),
            ),
            original=original,
            summarized=outcome.covered - preserve,
            cached=outcome.cached,
            reason="占位压缩 + 历史摘要" if not outcome.error else "复用旧摘要（续写失败）",
            error=outcome.error,
            started=started,
        )

    # ------------------------------------------------------------------ #
    # 摘要
    # ------------------------------------------------------------------ #

    async def _summarize(
        self, messages: Sequence[Any], head_count: int, preserve: int, session_key: str
    ) -> _SummaryOutcome:
        cache = await self._load_cache(session_key)
        previous = ""
        covered = 0
        if cache is not None:
            cached_covered = as_int(cache.get("covered"), 0)
            anchor_ok = 0 < cached_covered < len(messages) and cache.get(
                "anchor"
            ) == _message_digest(messages[cached_covered - 1])
            if anchor_ok:
                previous = str(cache.get("summary") or "")
                covered = cached_covered
                if covered >= head_count:
                    return _SummaryOutcome(text=previous, covered=covered, cached=True)

        material = _render_material(messages[max(covered, preserve) : head_count])
        if not material:
            return _SummaryOutcome(
                text=previous,
                covered=covered,
                cached=bool(previous),
                error="没有可压缩的新内容",
            )

        try:
            result = await self._llm.chat(
                prompt=build_summary_prompt(
                    material, previous=previous, max_chars=MAX_SUMMARY_CHARS
                ),
                system_prompt=SUMMARY_SYSTEM,
                provider_id=self._config.summary_provider_id or None,
                session_key=session_key or None,
                purpose="context_summary",
            )
        except asyncio.CancelledError:
            raise
        except LlmError as exc:
            return _SummaryOutcome(
                text=previous,
                covered=covered,
                cached=bool(previous),
                error=f"摘要调用失败：{safe_detail(exc)}",
            )
        except Exception as exc:  # noqa: BLE001 - 摘要失败必须降级
            return _SummaryOutcome(
                text=previous,
                covered=covered,
                cached=bool(previous),
                error=f"摘要失败：{safe_detail(exc)}",
            )

        text = truncate((result.text or "").strip(), MAX_SUMMARY_CHARS * 2)
        if not text:
            return _SummaryOutcome(
                text=previous,
                covered=covered,
                cached=bool(previous),
                error="摘要为空",
            )
        await self._save_cache(session_key, head_count, messages, text)
        return _SummaryOutcome(text=text, covered=head_count)

    async def _load_cache(self, session_key: str) -> dict[str, Any] | None:
        if self._store is None or not session_key:
            return None
        try:
            value = await self._store.get(_CACHE_PREFIX + session_key, None)
        except Exception as exc:  # noqa: BLE001 - 读缓存失败只是退化为重新摘要
            self._warn("读取上下文摘要缓存失败：%s", safe_detail(exc))
            return None
        return value if isinstance(value, dict) else None

    async def _save_cache(
        self, session_key: str, covered: int, messages: Sequence[Any], summary: str
    ) -> None:
        if self._store is None or not session_key or covered <= 0:
            return
        payload = {
            "covered": covered,
            "anchor": _message_digest(messages[covered - 1]),
            "summary": summary,
            "updated_at": time.time(),
        }
        try:
            await self._store.set(_CACHE_PREFIX + session_key, payload)
        except Exception as exc:  # noqa: BLE001 - 写缓存失败只影响下次是否复用
            self._warn("写入上下文摘要缓存失败：%s", safe_detail(exc))

    # ------------------------------------------------------------------ #
    # 收尾
    # ------------------------------------------------------------------ #

    def _commit(
        self,
        request: Any,
        prepared: _Prepared,
        *,
        original: int,
        summarized: int,
        cached: bool,
        reason: str,
        started: float,
        error: str = "",
    ) -> GovernanceResult:
        # 没有任何改动（例如占位与摘要都没生效）时如实报告「未治理」，并跳过写回
        if prepared.placeholders <= 0 and summarized <= 0:
            return self._record(
                GovernanceResult(
                    reason=reason,
                    original_tokens=original,
                    final_tokens=prepared.tokens,
                    error=error,
                ),
                started,
            )
        if not _replace_messages(request, prepared.messages):
            return self._record(GovernanceResult(reason="请求上下文不可写，已放弃治理"), started)
        return self._record(
            GovernanceResult(
                applied=True,
                reason=reason,
                original_tokens=original,
                final_tokens=prepared.tokens,
                placeholders=prepared.placeholders,
                summarized=summarized,
                cached=cached,
                error=error,
            ),
            started,
        )

    def _record(self, result: GovernanceResult, started: float) -> GovernanceResult:
        result.elapsed_ms = (time.perf_counter() - started) * 1000
        self._last = {
            "applied": result.applied,
            "reason": result.reason,
            "original_tokens": result.original_tokens,
            "final_tokens": result.final_tokens,
            "placeholders": result.placeholders,
            "summarized": result.summarized,
            "at": time.time(),
        }
        return result

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)
