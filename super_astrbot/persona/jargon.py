"""群组黑话：先用统计筛出候选词，再让模型推断含义。

为什么必须两级：把整个群的词流直接丢给模型既慢又贵，而且绝大多数是普通词汇；
先用「词频 + 长度」这类零成本信号把候选压到个位数，模型只处理真正可疑的词
（借鉴 self_learning 的统计预筛思路，但不引入 jieba 之外的依赖）。

计数与例句以「作用域」为键持久化到状态存储：插件重载后不会从头开始累积，
但也不写入业务表（避免把未确认的猜测混进已学习的词条里）。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from ..harness.protocols import LlmGateway
from ..loop.state_store import StateStore
from ..spec.errors import LlmError, safe_detail
from ..spec.scopes import MemoryScope, ScopeType, retrieval_scopes
from ..storage import JargonRepository, ReviewRepository
from ..support import PromptOverrides, normalize_text, tokenize, truncate
from .config import JargonConfig
from .prompts import build_jargon_prompt, jargon_system, parse_jargon_insights, render_jargon_block

SOURCE_JARGON = "jargon"
"""待审队列里的来源标记；也是审批分流依据。"""

_COUNT_KEY_PREFIX = "persona:jargon:counts:"
_MAX_TERMS_PER_SCOPE = 2000
_MAX_SAMPLES = 3
_SAMPLE_CHARS = 60
_PENDING_SCAN_LIMIT = 200


@dataclass
class JargonOutcome:
    """一次黑话扫描的结果。"""

    ran: bool
    reason: str = ""
    candidates: int = 0
    stored: int = 0
    pending: int = 0
    error: str = ""

    def summary(self) -> str:
        if not self.ran:
            return f"未执行：{self.reason}"
        if self.error:
            return f"执行失败：{self.error}"
        return f"推断 {self.candidates} 个候选，收录 {self.stored} 条，待审 {self.pending} 条"


class JargonService:
    """黑话候选统计、模型推断与理解注入。"""

    def __init__(
        self,
        *,
        config: JargonConfig,
        jargons: JargonRepository,
        reviews: ReviewRepository,
        llm: LlmGateway,
        store: StateStore,
        prompts: PromptOverrides | None = None,
        clock: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._config = config
        self._jargons = jargons
        self._reviews = reviews
        self._llm = llm
        self._store = store
        self._prompts = prompts
        self._clock = clock or (lambda: 0.0)
        self._logger = logger
        self._counts: dict[str, dict[str, int]] = {}
        self._samples: dict[str, dict[str, list[str]]] = {}
        self._stats = {"observed": 0, "scans": 0, "stored": 0, "pending": 0, "errors": 0}

    @property
    def config(self) -> JargonConfig:
        return self._config

    # ------------------------------------------------------------------ #
    # 候选统计（零成本）
    # ------------------------------------------------------------------ #

    async def observe(self, scope: MemoryScope, text: str) -> None:
        """把一条用户消息纳入候选统计（纯内存累加，不写库）。"""
        if not self._config.enabled:
            return
        cleaned = normalize_text(text or "")
        if not cleaned:
            return

        await self._load(scope)
        counts = self._counts.setdefault(scope.key, {})
        samples = self._samples.setdefault(scope.key, {})
        for term in tokenize(cleaned, max_tokens=64):
            if not self._accept(term):
                continue
            counts[term] = counts.get(term, 0) + 1
            bucket = samples.setdefault(term, [])
            if len(bucket) < _MAX_SAMPLES:
                bucket.append(truncate(cleaned, _SAMPLE_CHARS))
        self._trim_capacity(counts, samples)
        self._stats["observed"] += 1

    async def scan(self, scope: MemoryScope, *, now: float | None = None) -> JargonOutcome:
        """挑候选 → 模型推断 → 入待审队列或直接收录。"""
        if not self._config.enabled:
            return JargonOutcome(ran=False, reason="黑话学习未启用")

        await self._load(scope)
        await self._flush(scope)
        counts = self._counts.get(scope.key) or {}
        if not counts:
            return JargonOutcome(ran=False, reason="暂无可统计的对话内容")

        scopes = retrieval_scopes(scope)
        existing = await self._jargons.existing_terms(scopes)
        pending = await self._pending_terms(scopes)

        candidates = [
            (term, count)
            for term, count in counts.items()
            if count >= self._config.min_frequency
            and self._accept(term)
            and term not in existing
            and term not in pending
        ]
        if not candidates:
            return JargonOutcome(ran=False, reason="没有达到频次门槛的新词")

        candidates.sort(key=lambda item: item[1], reverse=True)
        chosen = candidates[: self._config.candidate_limit]
        samples = self._samples.get(scope.key) or {}
        prompt = build_jargon_prompt(
            [(term, samples.get(term, [])) for term, _ in chosen], overrides=self._prompts
        )

        try:
            result = await self._llm.chat(
                prompt=prompt,
                system_prompt=jargon_system(self._prompts),
                provider_id=self._config.provider_id or None,
                session_key=scope.scope_id,
                timeout=self._config.timeout_seconds,
                purpose="jargon",
            )
        except asyncio.CancelledError:
            raise
        except LlmError as exc:
            self._stats["errors"] += 1
            return JargonOutcome(
                ran=True, reason="推断失败", candidates=len(chosen), error=safe_detail(exc)
            )
        except Exception as exc:  # noqa: BLE001 - 单次扫描失败不影响其它任务
            self._stats["errors"] += 1
            return JargonOutcome(
                ran=True, reason="推断异常", candidates=len(chosen), error=safe_detail(exc)
            )

        insights = parse_jargon_insights(
            result.text,
            candidates=[term for term, _ in chosen],
            min_confidence=self._config.min_confidence,
        )
        moment = self._clock() if now is None else now
        stored = 0
        queued = 0
        for insight in insights:
            term = str(insight["term"])
            evidence = samples.get(term, [])
            if self._config.approval_required:
                await self._reviews.add(
                    scope_type=scope.scope_type.value,
                    scope_id=scope.scope_id,
                    origin=SOURCE_JARGON,
                    payload={
                        "term": term,
                        "meaning": insight["meaning"],
                        "confidence": insight["confidence"],
                        "samples": list(evidence),
                    },
                    created_at=moment,
                )
                queued += 1
                continue
            await self._jargons.upsert(
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
                term=term,
                meaning=insight["meaning"],
                confidence=float(insight["confidence"]),
                samples=evidence,
                created_at=moment,
                last_seen_at=moment,
            )
            stored += 1

        # 已处理的候选无论判定结果如何都清空计数，避免反复推断同一个通用词。
        for term, _ in chosen:
            counts.pop(term, None)
            self._samples.get(scope.key, {}).pop(term, None)
        await self._flush(scope)

        self._stats["scans"] += 1
        self._stats["stored"] += stored
        self._stats["pending"] += queued
        return JargonOutcome(
            ran=True,
            reason="扫描完成",
            candidates=len(chosen),
            stored=stored,
            pending=queued,
        )

    async def render_block(self, scope: MemoryScope, text: str) -> str:
        """按当前消息中出现的已收录词条生成「理解用」注入块。"""
        if not self._config.enabled or not text.strip():
            return ""
        rows = await self._jargons.list_active(
            retrieval_scopes(scope), limit=max(20, self._config.max_jargons)
        )
        hits: list[tuple[str, str]] = []
        for row in rows:
            term = str(row.get("term") or "")
            if term and term in text:
                hits.append((term, str(row.get("meaning") or "")))
            if len(hits) >= self._config.inject_max:
                break
        if not hits:
            return ""
        return render_jargon_block(hits, max_chars=self._config.max_injected_chars)

    # ------------------------------------------------------------------ #
    # 审批与查询
    # ------------------------------------------------------------------ #

    async def approve(self, record: dict[str, Any], payload: dict[str, Any]) -> str:
        term = str(payload.get("term") or "").strip()
        meaning = str(payload.get("meaning") or "").strip()
        if not term or not meaning:
            return "词条内容为空，已忽略"
        moment = self._clock()
        await self._jargons.upsert(
            scope_type=str(record.get("scope_type") or "session"),
            scope_id=str(record.get("scope_id") or ""),
            term=term,
            meaning=meaning,
            confidence=float(payload.get("confidence") or self._config.min_confidence),
            samples=[str(item) for item in (payload.get("samples") or [])],
            created_at=moment,
            last_seen_at=moment,
        )
        self._stats["stored"] += 1
        return f"已收录群内用语「{term}」：{meaning}"

    async def list_active(
        self, scope: MemoryScope, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        return await self._jargons.list_active(
            retrieval_scopes(scope), limit=limit or self._config.max_jargons
        )

    async def list_all_page(self, *, offset: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        return await self._jargons.list_all_page(offset=offset, limit=limit)

    async def count(self, scope: MemoryScope) -> int:
        return await self._jargons.count(retrieval_scopes(scope))

    async def count_all(self) -> int:
        return await self._jargons.count_all()

    async def delete(self, jargon_id: int) -> bool:
        return await self._jargons.delete(jargon_id)

    async def clear(self, scope: MemoryScope) -> int:
        removed = await self._jargons.clear_scopes((scope,))
        self._counts.pop(scope.key, None)
        self._samples.pop(scope.key, None)
        await self._store.set(_COUNT_KEY_PREFIX + scope.key, {})
        return removed

    async def flush(self) -> None:
        """把内存中的候选词计数落盘（插件卸载前调用，避免重载丢失累积）。"""
        for key in list(self._counts):
            scope_type, _, scope_id = key.partition(":")
            try:
                await self._flush(MemoryScope(ScopeType.parse(scope_type), scope_id))
            except Exception as exc:  # noqa: BLE001 - 落盘失败不应阻断卸载流程
                self._debug("黑话计数落盘失败（%s）：%s", key, safe_detail(exc))

    def tracked_scopes(self) -> list[MemoryScope]:
        """当前正在累积候选词的作用域（供调度任务遍历）。"""
        scopes: list[MemoryScope] = []
        for key in list(self._counts):
            scope_type, _, scope_id = key.partition(":")
            scopes.append(MemoryScope(ScopeType.parse(scope_type), scope_id))
        return scopes

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "approval_required": self._config.approval_required,
            "min_frequency": self._config.min_frequency,
            "max_jargons": self._config.max_jargons,
            "scan_interval_minutes": self._config.scan_interval_minutes,
            "tracked_scopes": len(self._counts),
            "stats": dict(self._stats),
        }

    # ------------------------------------------------------------------ #
    # 计数持久化
    # ------------------------------------------------------------------ #

    async def _load(self, scope: MemoryScope) -> None:
        key = scope.key
        if key in self._counts:
            return
        raw = await self._store.get(_COUNT_KEY_PREFIX + key, {})
        counts: dict[str, int] = {}
        samples: dict[str, list[str]] = {}
        if isinstance(raw, dict):
            for term, value in raw.items():
                if isinstance(value, dict):
                    count = int(value.get("c") or 0)
                    bucket = [str(item) for item in (value.get("s") or [])]
                else:
                    count, bucket = int(value or 0), []
                if count > 0:
                    counts[str(term)] = count
                    samples[str(term)] = bucket[:_MAX_SAMPLES]
        # 并发首次加载时可能有其它协程已经写入并开始累加，不能直接覆盖。
        self._counts.setdefault(key, counts)
        self._samples.setdefault(key, samples)

    async def _flush(self, scope: MemoryScope) -> None:
        counts = self._counts.get(scope.key)
        if counts is None:
            return
        samples = self._samples.get(scope.key) or {}
        payload = {
            term: {"c": count, "s": list(samples.get(term, []))} for term, count in counts.items()
        }
        await self._store.set(_COUNT_KEY_PREFIX + scope.key, payload)

    def _accept(self, term: str) -> bool:
        if not term or term.isdigit():
            return False
        length = len(term)
        return self._config.min_chars <= length <= self._config.max_chars

    def _trim_capacity(self, counts: dict[str, int], samples: dict[str, list[str]]) -> None:
        """控制单作用域内存占用：超出上限时只保留高频词。"""
        if len(counts) <= _MAX_TERMS_PER_SCOPE:
            return
        keep = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:_MAX_TERMS_PER_SCOPE]
        kept = dict(keep)
        counts.clear()
        counts.update(kept)
        for term in list(samples):
            if term not in kept:
                samples.pop(term, None)

    async def _pending_terms(self, scopes: Sequence[MemoryScope]) -> set[str]:
        """待审队列里已有的候选词，避免同一个词反复入队。"""
        try:
            rows = await self._reviews.list_pending(scopes, limit=_PENDING_SCAN_LIMIT)
        except Exception as exc:  # noqa: BLE001 - 读取失败只影响去重，不应中断扫描
            self._debug("读取待审队列失败：%s", safe_detail(exc))
            return set()

        terms: set[str] = set()
        for row in rows:
            if str(row.get("origin") or "") != SOURCE_JARGON:
                continue
            payload = _load_payload(row)
            term = str(payload.get("term") or "").strip()
            if term:
                terms.add(term)
        return terms

    def _debug(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.debug(message, *args)


def _load_payload(record: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(record.get("payload") or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


__all__ = ["JargonService", "JargonOutcome", "SOURCE_JARGON"]
