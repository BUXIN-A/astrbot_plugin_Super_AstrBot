"""社交好感度：把用户对 Bot 的交互态度累积成一个可衰减的数值。

设计取舍：

- **规则优先、模型兜底**：绝大多数句子用关键词规则就能判定极性，成本为零；
  只有规则同时命中正向与负向（例如「你可真厉害，就会添乱」）时才请模型裁决，
  避免为每条消息都产生一次模型调用。
- **数值回归基线**：长期不交流时好感度向初始值回落，避免「一次亲密永久亲密」。
- **不做总量再分配**：self_learning 的跨用户再分配算法被公认粗糙，这里只按对象独立累积。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from ..harness.protocols import EventView, LlmGateway
from ..spec.errors import LlmError, safe_detail
from ..spec.scopes import MemoryScope, retrieval_scopes
from ..storage import AffinityRepository
from ..support import PromptOverrides, truncate
from .config import AffinityConfig
from .prompts import affinity_system, build_affinity_prompt, parse_affinity_verdict

MOOD_POSITIVE = "positive"
MOOD_NEGATIVE = "negative"
MOOD_NEUTRAL = "neutral"

SOURCE_RULE = "rule"
SOURCE_LLM = "llm"

# 关键词规则表：命中即视为该交互类型。顺序无关，冲突时交由模型裁决。
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "insult",
        (
            "傻逼",
            "智障",
            "白痴",
            "脑残",
            "废物",
            "去死",
            "滚蛋",
            "闭嘴",
            "神经病",
            "垃圾",
            "sb",
            "fuck",
            "shit",
        ),
    ),
    (
        "conflict",
        ("别说了", "烦死了", "懒得理", "不想理", "无语", "服了", "少管", "别烦"),
    ),
    (
        "criticism",
        ("没用", "不好用", "答非所问", "胡说", "瞎说", "不准确", "差劲", "太笨", "真笨", "弱智"),
    ),
    ("apology", ("对不起", "抱歉", "不好意思", "是我不对", "我错了")),
    (
        "praise",
        (
            "厉害",
            "好棒",
            "太棒",
            "真棒",
            "喜欢你",
            "爱你",
            "牛啊",
            "牛逼",
            "牛批",
            "给力",
            "优秀",
            "聪明",
            "贴心",
            "可爱",
            "太强",
        ),
    ),
    ("thanks", ("谢谢", "感谢", "多谢", "3q", "thx", "thank")),
    ("care", ("注意身体", "早点休息", "别熬夜", "照顾好", "多喝水", "保重", "加油")),
    ("greet", ("你好", "您好", "早上好", "中午好", "晚上好", "早安", "晚安", "嗨", "hi", "hello")),
    ("joke", ("哈哈", "笑死", "233", "hhh", "开玩笑", "逗你", "搞笑", "乐死")),
)

_BASE_DELTA: dict[str, float] = {
    "praise": 0.06,
    "thanks": 0.04,
    "care": 0.04,
    "apology": 0.03,
    "joke": 0.02,
    "greet": 0.01,
    "question": 0.005,
    "criticism": -0.05,
    "conflict": -0.07,
    "insult": -0.12,
    "neutral": 0.0,
}

_POLARITY: dict[str, int] = {
    "praise": 1,
    "thanks": 1,
    "care": 1,
    "apology": 1,
    "joke": 1,
    "greet": 1,
    "question": 1,
    "criticism": -1,
    "conflict": -1,
    "insult": -1,
    "neutral": 0,
}

_QUESTION_MARKS = ("？", "?")
_LLM_CONFIDENCE_FLOOR = 0.5

_GUIDANCE: tuple[tuple[float, float, str], ...] = (
    (0.75, 1.01, "对方与你关系亲近，语气可以更放松自然，允许适度玩笑。"),
    (0.6, 0.75, "对方与你比较熟悉，保持自然随意的聊天语气即可。"),
    (0.4, 0.6, ""),
    (0.25, 0.4, "对方与你还不算熟，保持礼貌友好，不要过度玩笑。"),
    (0.0, 0.25, "对方近期态度明显不善，回复保持克制礼貌，不要开玩笑。"),
)
"""好感度档位表：``(下界, 上界, 语气指引)``。

居中的一档（0.4–0.6，即常规关系）刻意留空：普通关系不需要额外指引，
注入过多「关系说明」反而是噪声。
"""


@dataclass
class AffinityOutcome:
    """一次好感度更新的结果。"""

    updated: bool
    reason: str = ""
    kind: str = "neutral"
    delta: float = 0.0
    score: float = 0.0
    source: str = SOURCE_RULE

    def summary(self) -> str:
        if not self.updated:
            return f"未更新：{self.reason}"
        sign = "+" if self.delta >= 0 else ""
        return f"{self.kind}（{sign}{self.delta:.3f} → {self.score:.3f}，{self.source}）"


class AffinityService:
    """好感度的判定、累积与语气指引。"""

    def __init__(
        self,
        *,
        config: AffinityConfig,
        affinities: AffinityRepository,
        llm: LlmGateway,
        prompts: PromptOverrides | None = None,
        clock: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._config = config
        self._affinities = affinities
        self._llm = llm
        self._prompts = prompts
        self._clock = clock or (lambda: 0.0)
        self._logger = logger
        self._stats = {"updated": 0, "llm_calls": 0, "skipped": 0}

    @property
    def config(self) -> AffinityConfig:
        return self._config

    # ------------------------------------------------------------------ #
    # 交互判定
    # ------------------------------------------------------------------ #

    async def observe(
        self, view: EventView, text: str, *, now: float | None = None
    ) -> AffinityOutcome:
        """判定一条用户消息的态度并更新好感度。"""
        if not self._config.enabled:
            return self._skip("好感度未启用")
        content = (text or "").strip()
        if not content or not view.umo or not view.sender_id:
            return self._skip("缺少会话或发送者信息")

        kind, source, _ = await self._classify(content)
        moment = self._clock() if now is None else now
        scope = MemoryScope.for_session(view.umo)

        current = await self._affinities.get(scope.scope_type.value, scope.scope_id, view.sender_id)
        base_score = float(
            (current or {}).get("score", self._config.initial_score) or self._config.initial_score
        )
        interactions = int((current or {}).get("interactions") or 0)
        last_at = float((current or {}).get("last_interaction") or 0.0)

        decayed = self._decay(base_score, last_at=last_at, now=moment)
        delta = self._clamp_delta(_BASE_DELTA.get(kind, 0.0))
        score = self._clamp_score(decayed + delta)

        await self._affinities.upsert(
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            target_id=view.sender_id,
            score=score,
            mood=self._mood_of(kind),
            interactions=interactions + 1,
            last_interaction=moment,
            updated_at=moment,
        )
        self._stats["updated"] += 1
        return AffinityOutcome(
            updated=True,
            reason="已更新",
            kind=kind,
            delta=round(score - base_score, 4),
            score=round(score, 4),
            source=source,
        )

    async def _classify(self, text: str) -> tuple[str, str, bool]:
        """返回 ``(交互类型, 判定来源, 是否规则冲突)``。"""
        hits: list[str] = []
        lowered = text.lower()
        for kind, keywords in _RULES:
            if any(keyword in lowered for keyword in keywords):
                hits.append(kind)

        if not hits:
            if text.rstrip().endswith(_QUESTION_MARKS):
                return "question", SOURCE_RULE, False
            return "neutral", SOURCE_RULE, False

        polarities = {_POLARITY.get(kind, 0) for kind in hits}
        conflicted = 1 in polarities and -1 in polarities
        if not conflicted:
            # 命中多个同极性类型时取影响最大的那个
            best = max(hits, key=lambda kind: abs(_BASE_DELTA.get(kind, 0.0)))
            return best, SOURCE_RULE, False

        verdict = await self._ask_llm(text)
        if verdict:
            return verdict, SOURCE_LLM, False
        # 模型不可用或未表态时保持分数不变，宁可不动也不要误判。
        return "neutral", SOURCE_RULE, True

    async def _ask_llm(self, text: str) -> str:
        if not self._config.use_llm:
            return ""
        try:
            result = await self._llm.chat(
                prompt=build_affinity_prompt(text, overrides=self._prompts),
                system_prompt=affinity_system(self._prompts),
                provider_id=self._config.provider_id or None,
                timeout=self._config.timeout_seconds,
                purpose="affinity",
            )
        except asyncio.CancelledError:
            raise
        except LlmError as exc:
            self._debug("好感度判定失败：%s", safe_detail(exc))
            return ""
        except Exception as exc:  # noqa: BLE001 - 兜底失败不影响对话
            self._debug("好感度判定异常：%s", safe_detail(exc))
            return ""
        self._stats["llm_calls"] += 1
        kind, confidence = parse_affinity_verdict(result.text)
        if not kind or confidence < _LLM_CONFIDENCE_FLOOR:
            return ""
        return kind

    # ------------------------------------------------------------------ #
    # 注入
    # ------------------------------------------------------------------ #

    async def guidance(self, scope: MemoryScope, target_id: str) -> str:
        """按当前好感度给出语气指引；中性档位不注入。"""
        if not self._config.enabled or not self._config.inject_enabled or not target_id:
            return ""
        row = await self._affinities.get(scope.scope_type.value, scope.scope_id, target_id)
        if row is None:
            return ""
        score = self._decay(
            float(row.get("score") or 0.0), last_at=float(row.get("last_interaction") or 0.0)
        )
        text = self._guidance_for(score)
        return truncate(text, self._config.max_injected_chars)

    @staticmethod
    def _guidance_for(score: float) -> str:
        for low, high, text in _GUIDANCE:
            if low <= score < high:
                return text
        return ""

    async def list_by_scope(self, scope: MemoryScope, *, limit: int = 50) -> list[dict[str, Any]]:
        return await self._affinities.list_by_scope(
            scope.scope_type.value, scope.scope_id, limit=limit
        )

    async def list_all_page(self, *, offset: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        return await self._affinities.list_all_page(offset=offset, limit=limit)

    async def count_all(self) -> int:
        return await self._affinities.count_all()

    async def clear(self, scope: MemoryScope) -> int:
        return await self._affinities.clear_scopes(retrieval_scopes(scope))

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "use_llm": self._config.use_llm,
            "initial_score": self._config.initial_score,
            "half_life_days": self._config.decay_half_life_days,
            "inject_enabled": self._config.inject_enabled,
            "stats": dict(self._stats),
        }

    # ------------------------------------------------------------------ #
    # 数值
    # ------------------------------------------------------------------ #

    def _decay(self, score: float, *, last_at: float, now: float | None = None) -> float:
        """向初始值回归；没有上次交互时间时视为不衰减。"""
        if last_at <= 0:
            return self._clamp_score(score)
        moment = self._clock() if now is None else now
        elapsed_days = max(0.0, (moment - last_at) / 86400.0)
        if elapsed_days <= 0:
            return self._clamp_score(score)
        half_life = max(1.0, self._config.decay_half_life_days)
        factor = 0.5 ** (elapsed_days / half_life)
        baseline = self._config.initial_score
        return self._clamp_score(baseline + (score - baseline) * factor)

    def _clamp_score(self, score: float) -> float:
        low, high = self._config.min_score, self._config.max_score
        if low > high:
            low, high = high, low
        return round(min(high, max(low, score)), 4)

    def _clamp_delta(self, delta: float) -> float:
        cap = max(0.0, self._config.daily_delta_cap)
        return max(-cap, min(cap, delta))

    @staticmethod
    def _mood_of(kind: str) -> str:
        polarity = _POLARITY.get(kind, 0)
        if polarity > 0:
            return MOOD_POSITIVE
        if polarity < 0:
            return MOOD_NEGATIVE
        return MOOD_NEUTRAL

    def _skip(self, reason: str) -> AffinityOutcome:
        self._stats["skipped"] += 1
        return AffinityOutcome(updated=False, reason=reason)

    def _debug(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.debug(message, *args)
