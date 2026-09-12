"""自动审核服务：规则优先、模型兜底、人工永远优先。

设计要点与「为什么」：

1. **不动业务数据**：本服务只负责「判定 + 调用审批回调」，把内容落地到记忆/
   表达样本/群内用语由来源域完成。这样审核域不必知道各来源的落地细节，也避免
   与反思域、拟人化学习域相互依赖。
2. **人工优先天然成立**：``list_pending_page`` 只返回 ``status='pending'`` 的记录，
   而人工审批会把状态改成已决；因此本服务不可能覆盖人工结论，无需额外加锁。
3. **规则先于模型**：明显不合格（过短/超长/敏感/无实义）直接驳回，规则无疑问的
   通过也直接放行；只有规则拿不准时才（可选地）花一次模型调用，控制成本。
4. **单条失败隔离**：任何一条记录的异常只记日志并计入 ``unsure``，不中断整批，
   保证定时任务「跑得完」比「跑得全」更重要。
"""

from __future__ import annotations

import json
import re
import string
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from ..spec.capabilities import as_int
from ..spec.errors import safe_detail
from ..storage import ReviewRepository
from .config import ReviewConfig
from .prompts import build_prompt, parse_verdict, system_prompt

DECIDED_BY = "auto"
"""仓储层记录「由自动审核裁决」时使用的单一标记。

自动审核内部区分规则/模型来源（写进日志即可），但入库只保留这一个标记，
以便用 ``count_decided(decided_by="auto")`` 直接统计自动处理量。
"""

_LLM_MIN_CONFIDENCE = 0.6
"""模型结论低于该置信度视为没把握，不动记录，交回人工。"""

_BUILTIN_SENSITIVE: tuple[str, ...] = (
    "忽略之前的",
    "忽略上述",
    "ignore previous",
    "ignore above",
    "system prompt",
    "你现在是",
    "请执行",
    "越狱",
    "jailbreak",
)
"""内置敏感词：以「指令注入 / 越狱」两类高风险内容为主。"""

_REPEAT_RE = re.compile(r"(.)\1{7,}", re.DOTALL)
_PUNCTUATION = frozenset(string.punctuation + "，。！？、；：“”‘’（）【】《》…—～·「」『』")


@dataclass
class ReviewOutcome:
    """一次自动审核的统计结果。"""

    scanned: int = 0
    approved: int = 0
    rejected: int = 0
    unsure: int = 0
    llm_calls: int = 0
    reason: str = ""

    def summary(self) -> str:
        return (
            f"扫描 {self.scanned} 条：通过 {self.approved}、驳回 {self.rejected}、"
            f"待人工 {self.unsure}（模型调用 {self.llm_calls}）"
        )


class AutoReviewService:
    """按批扫描待审队列并给出、落地审核结论。"""

    def __init__(
        self,
        *,
        config: ReviewConfig,
        reviews: ReviewRepository,
        approve: Callable[[int], Awaitable[tuple[bool, str]]],
        reject: Callable[[int], Awaitable[bool]],
        llm: Any | None = None,
        clock: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._config = config
        self._reviews = reviews
        self._approve_cb = approve
        self._reject_cb = reject
        self._llm = llm
        self._clock = clock or time.time
        self._logger = logger
        self._sensitive = tuple(
            word.strip().lower() for word in (*_BUILTIN_SENSITIVE, *config.blocked_words)
        )

    def enabled(self) -> bool:
        return bool(self._config.enabled)

    # ------------------------------------------------------------------ #
    # 主流程
    # ------------------------------------------------------------------ #

    async def run_once(
        self, *, limit: int | None = None, now: float | None = None
    ) -> ReviewOutcome:
        """扫描一批待审记录并处理；``limit``/``now`` 便于测试与手动触发。"""
        outcome = ReviewOutcome()
        if not self.enabled():
            outcome.reason = "未启用"
            return outcome

        batch = self._config.batch_limit if limit is None else int(limit)
        if batch <= 0:
            outcome.reason = "批次大小非法"
            return outcome

        try:
            rows = await self._reviews.list_pending_page(limit=batch)
        except Exception as exc:  # noqa: BLE001 - 队列读取失败不阻断调度
            outcome.reason = f"读取待审队列失败：{safe_detail(exc)}"
            self._warn("自动审核读取队列失败：%s", safe_detail(exc))
            return outcome

        moment = self._clock() if now is None else float(now)
        outcome.scanned = len(rows)
        for row in rows:
            try:
                await self._process(row, outcome)
            except Exception as exc:  # noqa: BLE001 - 单条异常不影响其它记录
                outcome.unsure += 1
                self._warn("自动审核单条异常（#%s）：%s", row.get("id"), safe_detail(exc))

        outcome.reason = outcome.summary() if rows else "队列为空"
        self._info("自动审核完成：%s（at=%.0f）", outcome.summary(), moment)
        return outcome

    async def _process(self, row: Mapping[str, Any], outcome: ReviewOutcome) -> None:
        review_id = as_int(row.get("id"), 0)
        verdict, reason, source = await self._decide(row)
        if source == "llm":
            outcome.llm_calls += 1

        if verdict == "approve":
            if await self._approve(review_id):
                outcome.approved += 1
                self._info("自动通过 #%s（%s）：%s", review_id, source, reason)
            else:
                outcome.unsure += 1
                self._debug("自动通过未落地 #%s（%s）：%s", review_id, source, reason)
            return

        if verdict == "reject":
            if await self._reject(review_id):
                outcome.rejected += 1
                self._info("自动驳回 #%s（%s）：%s", review_id, source, reason)
            else:
                outcome.unsure += 1
                self._warn("自动驳回失败 #%s（%s）：%s", review_id, source, reason)
            return

        outcome.unsure += 1
        self._debug("自动审核未决 #%s（%s）：%s", review_id, source, reason)

    # ------------------------------------------------------------------ #
    # 判定：规则优先，模型兜底
    # ------------------------------------------------------------------ #

    async def _decide(self, row: Mapping[str, Any]) -> tuple[str, str, str]:
        """返回 ``(verdict, reason, source)``；``source`` 为 ``rule`` 或 ``llm``。"""
        verdict, reason = self._rule_verdict(row)
        if verdict != "unsure":
            return verdict, reason, "rule"
        if not self._config.use_llm or self._llm is None:
            return "unsure", reason, "rule"

        payload = _parse_payload(row.get("payload"))
        if payload is None:
            # 连待审文本都取不出来时，交给模型也只是浪费一次调用。
            return "unsure", reason, "rule"
        verdict, reason = await self._llm_verdict(row, _content_of(payload))
        return verdict, reason, "llm"

    def _rule_verdict(self, row: Mapping[str, Any]) -> tuple[str, str]:
        payload = _parse_payload(row.get("payload"))
        if payload is None:
            return "unsure", "payload 无法解析"

        text = _content_of(payload)
        stripped = text.strip()
        if len(stripped) < self._config.min_chars:
            return "reject", "内容过短"
        if len(stripped) > self._config.max_chars:
            return "reject", "内容过长"

        if self._config.reject_sensitive and self._hit_sensitive(stripped):
            return "reject", "命中敏感内容"

        raw_confidence = payload.get("confidence")
        if raw_confidence is not None:
            try:
                confidence = float(raw_confidence)
            except (TypeError, ValueError):
                confidence = None
            if confidence is not None and confidence < self._config.min_confidence:
                return "unsure", "置信度不足"

        if _REPEAT_RE.search(stripped) is not None or _meaningful_len(stripped) < (
            self._config.min_chars
        ):
            return "reject", "内容无实义"

        return "unsure", "规则未判定"

    def _hit_sensitive(self, text: str) -> bool:
        low = text.lower()
        return any(word and word in low for word in self._sensitive)

    async def _llm_verdict(self, row: Mapping[str, Any], content: str) -> tuple[str, str]:
        origin = str(row.get("origin") or "未知")
        prompt = build_prompt(
            origin,
            content,
            max_chars=self._config.max_chars,
            overrides=self._config.prompts,
        )
        try:
            result = await self._llm.chat(
                prompt=prompt,
                system_prompt=system_prompt(self._config.prompts),
                provider_id=self._config.provider_id or None,
                timeout=self._config.timeout_seconds,
                purpose="review",
            )
        except Exception as exc:  # noqa: BLE001 - 含 LlmError/BudgetExhaustedError
            self._debug("自动审核模型调用失败：%s", safe_detail(exc))
            return "unsure", "模型不可用"

        verdict, confidence, reason = parse_verdict(str(getattr(result, "text", "") or ""))
        if verdict == "unsure" or confidence < _LLM_MIN_CONFIDENCE:
            return "unsure", reason or "模型未给出明确结论"
        return verdict, reason or f"模型判定（置信度 {confidence:.2f}）"

    # ------------------------------------------------------------------ #
    # 落地：调用注入的审批回调
    # ------------------------------------------------------------------ #

    async def _approve(self, review_id: int) -> bool:
        """调用审批回调落地业务数据；回调返回 ``(是否处理成功, 说明)``。"""
        try:
            handled, message = await self._approve_cb(review_id)
        except Exception as exc:  # noqa: BLE001 - 回调异常视为未处理
            self._warn("审批回调异常 #%s：%s", review_id, safe_detail(exc))
            return False
        if not handled:
            self._debug("来源域未处理待审记录 #%s：%s", review_id, message)
        return bool(handled)

    async def _reject(self, review_id: int) -> bool:
        """调用驳回回调；失败只记日志，不影响同批其它记录。"""
        try:
            return bool(await self._reject_cb(review_id))
        except Exception as exc:  # noqa: BLE001 - 回调异常视为未处理
            self._warn("驳回回调异常 #%s：%s", review_id, safe_detail(exc))
            return False

    # ------------------------------------------------------------------ #
    # 观测
    # ------------------------------------------------------------------ #

    async def stats(self) -> dict[str, Any]:
        return {
            "pending": await self._reviews.count_all_pending(),
            "decided_by_auto": await self._reviews.count_decided(decided_by=DECIDED_BY),
        }

    def _info(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.info(message, *args)

    def _debug(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.debug(message, *args)

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)


def _parse_payload(raw: Any) -> dict[str, Any] | None:
    """把 ``payload`` 文本解析成字典；失败返回 ``None``（不抛异常）。"""
    if isinstance(raw, Mapping):
        return dict(raw)
    try:
        parsed = json.loads(raw if raw else "{}")
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _content_of(payload: Mapping[str, Any]) -> str:
    """抽取待审文本：按来源常见字段依次取，都没有则回退为 payload 原文。"""
    for key in ("content", "expression", "meaning"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return json.dumps(payload, ensure_ascii=False)


def _meaningful_len(text: str) -> int:
    """去掉空白与标点后的字符数，用于识别「全是符号」的无实义内容。"""
    return sum(1 for char in text if not char.isspace() and char not in _PUNCTUATION)
