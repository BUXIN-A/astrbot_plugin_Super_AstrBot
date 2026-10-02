"""人格演化轨迹（character-sim · TraitShift 进程内重写）。

来源与改写：character-sim 的 ``trait_evolution.py`` 提供了「经验类型 → 特质增量」
影响向量表与三重护栏（单轮上限 / 里程碑上限 / 取值钳制）。本项目：

- 保留全部影响向量与护栏常量（数值原样，见各常量注释）；
- 分类阶段改为**中文关键词规则**（零模型调用）：原实现每轮调用 LLM 分类，
  在群聊高频场景下成本不可接受；规则判定带置信度，只把高置信信号写入画像；
- 落地对象是 PersonaForge 三层人格的大五人格轴（``forge.apply_shift``），
  经轴映射把 character-sim 的 8 个人格维度折算到 5 轴；
- 事件留痕在 ``persona_events``，面板据此绘制漂移折线、失谐点与里程碑时间线。

失谐（dissonance）定义：同一轮里检出**互斥经验**（如「想休息」与「怕落后」），
或正面与负面经验同时高置信命中——这是 character-sim 里 SelfReflection 的触发条件。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..harness.protocols import EventView
from ..support import truncate

# --------------------------------------------------------------------------- #
# 经验类型与影响向量（character-sim 原表，数值不变）
# --------------------------------------------------------------------------- #

EXPERIENCE_TYPES: tuple[str, ...] = (
    "conflict",
    "vulnerability",
    "success",
    "rejection",
    "connection",
    "betrayal",
    "discovery",
    "loss",
    "humor",
    "humiliation",
    "triumph",
    "forgiveness",
    "loneliness",
    "inspiration",
    "gratitude",
    "neutral",
)

EXPERIENCE_LABELS: dict[str, str] = {
    "conflict": "冲突",
    "vulnerability": "坦露脆弱",
    "success": "成功",
    "rejection": "被拒绝",
    "connection": "建立连接",
    "betrayal": "背叛",
    "discovery": "新发现",
    "loss": "失去",
    "humor": "玩笑",
    "humiliation": "受挫丢脸",
    "triumph": "高光时刻",
    "forgiveness": "和解",
    "loneliness": "孤独",
    "inspiration": "被启发",
    "gratitude": "感激",
    "neutral": "平常",
}

EXPERIENCE_INFLUENCE_VECTORS: dict[str, dict[str, float]] = {
    "conflict": {"assertiveness": 0.02, "agreeableness": -0.01, "emotional_stability": -0.01},
    "vulnerability": {"warmth": 0.02, "openness": 0.01, "emotional_stability": -0.02},
    "success": {"assertiveness": 0.01, "emotional_stability": 0.02},
    "rejection": {"warmth": -0.01, "emotional_stability": -0.02, "openness": -0.01},
    "connection": {"warmth": 0.02, "agreeableness": 0.01, "extraversion": 0.01},
    "betrayal": {"warmth": -0.02, "agreeableness": -0.02, "emotional_stability": -0.01},
    "discovery": {"openness": 0.02, "humor_inclination": 0.01},
    "loss": {"emotional_stability": -0.02, "warmth": 0.01},
    "humor": {"humor_inclination": 0.02, "warmth": 0.01, "formality": -0.01},
    "humiliation": {"assertiveness": -0.02, "emotional_stability": -0.02, "formality": 0.01},
    "triumph": {"assertiveness": 0.02, "emotional_stability": 0.02, "extraversion": 0.01},
    "forgiveness": {"warmth": 0.02, "agreeableness": 0.02, "emotional_stability": 0.01},
    "loneliness": {"extraversion": -0.01, "emotional_stability": -0.01},
    "inspiration": {"openness": 0.02, "assertiveness": 0.01},
    "gratitude": {"warmth": 0.02, "agreeableness": 0.01, "emotional_stability": 0.01},
    "neutral": {},
}

MAX_TRAIT_DELTA_PER_EXCHANGE = 0.03
"""单轮互动对任一维度的最大改动（character-sim 原护栏）。"""

MAX_MILESTONE_SHIFT = 0.10
"""被判定为里程碑时允许的最大单次改动（character-sim 原护栏）。"""

MILESTONE_TOTAL_THRESHOLD = 0.05
"""一次事件的总漂移量达到该值即记为里程碑。"""

TRAIT_MIN, TRAIT_MAX = 0.0, 1.0

# character-sim 维度 → 大五人格轴的折算权重（改写的核心映射，全表可审计）
_AXIS_MAP: dict[str, tuple[str, float]] = {
    "warmth": ("agreeableness", 1.0),
    "agreeableness": ("agreeableness", 1.0),
    "assertiveness": ("extraversion", 1.0),
    "extraversion": ("extraversion", 1.0),
    "openness": ("openness", 1.0),
    "emotional_stability": ("neuroticism", -1.0),
    "humor_inclination": ("extraversion", 0.6),
    "formality": ("conscientiousness", 0.4),
}

AXIS_LABELS: dict[str, str] = {
    "openness": "好奇开放",
    "conscientiousness": "条理尽责",
    "extraversion": "主动外向",
    "agreeableness": "亲和宜人",
    "emotional_stability": "情绪稳定",
    "warmth": "温暖体贴",
    "assertiveness": "果断坚定",
    "humor_inclination": "幽默感",
    "interest_breadth": "兴趣广度",
}

# 经验分类关键词（中文；命中即给置信度，多命中加权）
_EXPERIENCE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "conflict": ("吵架", "争执", "针对", "对立", "吵起来", "顶回去", "不爽", "翻脸", "掰扯"),
    "vulnerability": (
        "其实我",
        "说点心里话",
        "不敢说",
        "怕被",
        "第一次说",
        "只能跟你",
        "倾诉",
        "绷不住",
    ),
    "success": (
        "搞定",
        "通过",
        "拿到",
        "赢了",
        "成功",
        "过了",
        "上岸",
        "完成",
        "达标",
        "入职",
        "上线",
        "做完",
        "解决了",
        "通关",
    ),
    "rejection": ("被拒", "没要我", "不要我", "落选", "被刷", "没通过", "被退货"),
    "connection": ("一起", "约", "陪你", "同道", "同好", "加好友", "组队", "带带我"),
    "betrayal": ("背叛", "背刺", "坑我", "出卖", "背后说", "翻旧账", "骗我"),
    "discovery": ("发现", "原来", "才知道", "新东西", "学到", "看了一篇", "研究", "资料"),
    "loss": ("走了", "失去", "分手", "离开", "没了", "去世", "走了吧", "散伙"),
    "humor": ("哈哈", "笑死", "梗", "乐", "段子", "整活", "太逗", "笑不活了"),
    "humiliation": ("丢人", "社死", "出丑", "打脸", "被嘲", "尴尬", "难堪"),
    "triumph": ("高光", "无敌", "横扫", "冠军", "第一名", "封神", "完美"),
    "forgiveness": ("算了", "原谅", "和好", "不怪你", "翻篇", "重新来"),
    "loneliness": ("一个人", "没人理", "孤单", "孤独", "没人懂", "被忽略", "透明"),
    "inspiration": ("启发", "被打动", "燃起来", "有道理", "开窍", "想通了"),
    "gratitude": ("谢谢", "感谢", "多谢", "亏了你", "帮大忙"),
}

# 互斥经验对：同一轮同时检出 ⇒ 失谐（SelfReflection 触发条件）
_DISSONANCE_PAIRS: tuple[tuple[str, str], ...] = (
    ("triumph", "humiliation"),
    ("success", "rejection"),
    ("connection", "loneliness"),
    ("gratitude", "betrayal"),
    ("humor", "loss"),
)

_POSITIVE = {
    "success",
    "connection",
    "discovery",
    "humor",
    "triumph",
    "forgiveness",
    "inspiration",
    "gratitude",
}
_NEGATIVE = {
    "conflict",
    "vulnerability",
    "rejection",
    "betrayal",
    "loss",
    "humiliation",
    "loneliness",
}


@dataclass(frozen=True)
class ExperienceVerdict:
    """一次经验判定。"""

    experience: str
    confidence: float
    matched: tuple[str, ...] = ()
    dissonance: tuple[str, ...] = ()
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "experience": self.experience,
            "label": EXPERIENCE_LABELS.get(self.experience, self.experience),
            "confidence": round(self.confidence, 4),
            "matched": list(self.matched),
            "dissonance": list(self.dissonance),
            "reason": self.reason,
        }


def classify_experience(user_text: str, reply_text: str = "") -> ExperienceVerdict:
    """规则分类：返回主经验类型 + 置信度 + 失谐信号（零模型调用）。"""
    blob = f"{user_text or ''}\n{reply_text or ''}"
    if not blob.strip():
        return ExperienceVerdict("neutral", 0.0, reason="空文本")

    scores: dict[str, float] = {}
    matched: dict[str, list[str]] = {}
    for experience, words in _EXPERIENCE_KEYWORDS.items():
        hits = [word for word in words if word in blob]
        if hits:
            scores[experience] = min(1.0, 0.45 + 0.18 * (len(hits) - 1))
            matched[experience] = hits[:3]
    if not scores:
        return ExperienceVerdict("neutral", 0.2, reason="无关键词命中")

    top = max(scores.items(), key=lambda kv: kv[1])
    experience, confidence = top

    # 失谐检测：互斥对同时命中，或正负极性同时高置信
    dissonance: list[str] = []
    for left, right in _DISSONANCE_PAIRS:
        if left in scores and right in scores:
            dissonance.append(f"{EXPERIENCE_LABELS[left]}×{EXPERIENCE_LABELS[right]}")
    positives = [item for item in scores if item in _POSITIVE]
    negatives = [item for item in scores if item in _NEGATIVE]
    if positives and negatives and not dissonance:
        top_positive = max(positives, key=lambda item: scores[item])
        top_negative = max(negatives, key=lambda item: scores[item])
        if min(scores[top_positive], scores[top_negative]) >= 0.5:
            dissonance.append(
                f"{EXPERIENCE_LABELS[top_positive]}×{EXPERIENCE_LABELS[top_negative]}"
            )
    reason = "命中：" + "、".join(matched.get(experience, []))
    return ExperienceVerdict(
        experience=experience,
        confidence=confidence,
        matched=tuple(matched.get(experience, [])),
        dissonance=tuple(dissonance),
        reason=reason,
    )


def compute_deltas(
    verdict: ExperienceVerdict,
    *,
    max_delta: float = MAX_TRAIT_DELTA_PER_EXCHANGE,
    milestone: bool = False,
) -> dict[str, float]:
    """把经验向量折算为大五人格增量（含单轮上限与里程碑上限）。"""
    vector = EXPERIENCE_INFLUENCE_VECTORS.get(verdict.experience) or {}
    if not vector:
        return {}
    ceiling = MAX_MILESTONE_SHIFT if milestone else max_delta
    deltas: dict[str, float] = {}
    for axis, weight in vector.items():
        target = _AXIS_MAP.get(axis)
        if target is None:
            continue
        big_five_axis, sign = target
        value = float(weight) * sign * max(0.3, min(1.0, verdict.confidence))
        value = max(-ceiling, min(ceiling, value))
        deltas[big_five_axis] = round(deltas.get(big_five_axis, 0.0) + value, 6)
    return {axis: max(-ceiling, min(ceiling, value)) for axis, value in deltas.items()}


class TraitEvolutionService:
    """人格演化：判定 → 增量 → 写回画像 → 留痕（可查询时间线 / 漂移曲线）。"""

    def __init__(
        self,
        *,
        db: Any | None = None,
        forge: Any | None = None,
        min_confidence: float = 0.45,
        clock: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._db = db
        self._forge = forge
        self._min_confidence = float(min_confidence)
        self._clock = clock or time.time
        self._logger = logger

    def enabled(self) -> bool:
        return self._forge is not None

    async def observe(self, view: EventView, reply: str = "") -> dict[str, Any] | None:
        """在消息发送后推进一次演化。返回事件摘要（未达门槛返回 ``None``）。"""
        if self._forge is None:
            return None
        text = str(getattr(view, "text", "") or "")
        verdict = classify_experience(text, reply)
        if verdict.experience == "neutral" or verdict.confidence <= self._min_confidence:
            return None
        milestone = bool(verdict.dissonance) or verdict.confidence >= 0.9
        deltas = compute_deltas(verdict, milestone=milestone)
        if not deltas:
            return None
        applied = await self._forge.apply_shift(
            deltas,
            experience=verdict.experience,
            summary=self._summary(view, verdict, milestone),
            confidence=verdict.confidence,
        )
        if not applied:
            return None
        if milestone:
            await self._record_milestone(applied, verdict)
        result = {
            "experience": verdict.experience,
            "label": EXPERIENCE_LABELS.get(verdict.experience, verdict.experience),
            "confidence": round(verdict.confidence, 4),
            "deltas": applied,
            "milestone": milestone,
            "dissonance": list(verdict.dissonance),
        }
        self._debug("人格演化：%s → %s", verdict.experience, applied)
        return result

    def _summary(self, view: EventView, verdict: ExperienceVerdict, milestone: bool) -> str:
        who = str(getattr(view, "sender_name", "") or getattr(view, "sender_id", "") or "群友")
        parts = [f"{who}的互动"]
        if milestone and verdict.dissonance:
            parts.append(f"失谐：{'；'.join(verdict.dissonance)}")
        parts.append(verdict.reason or "")
        text = truncate(str(getattr(view, "text", "") or ""), 40)
        if text:
            parts.append(f"原文「{text}」")
        return "｜".join(part for part in parts if part)

    async def _record_milestone(
        self, deltas: Mapping[str, float], verdict: ExperienceVerdict
    ) -> None:
        """在 ``persona_events`` 额外写一条 ``kind='milestone'`` 摘要，供时间线区分。

        ``forge.apply_shift`` 已把 shift 事件写入 ``persona_events``；这里只补里程碑语义。
        """
        if self._db is None:
            return
        total = sum(abs(value) for value in deltas.values())
        if total < MILESTONE_TOTAL_THRESHOLD:
            return
        try:
            await self._db.execute(
                "INSERT INTO persona_events(kind, experience, sender_id, scope_type, scope_id,"
                " summary, deltas, confidence, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "milestone",
                    verdict.experience,
                    "",
                    "user",
                    "self",
                    "里程碑：" + (verdict.reason or EXPERIENCE_LABELS.get(verdict.experience, "")),
                    json.dumps(dict(deltas), ensure_ascii=False),
                    1.0 if verdict.dissonance else verdict.confidence,
                    self._clock(),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._debug("里程碑落库失败：%s", exc)

    # ------------------------------------------------------------------ #
    # 查询：时间线 / 漂移曲线 / 雷达
    # ------------------------------------------------------------------ #

    async def timeline(
        self, *, limit: int = 30, kinds: Sequence[str] | None = None
    ) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        cap = max(1, min(200, int(limit)))
        params: list[Any] = []
        where = ""
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            where = f" WHERE kind IN ({placeholders})"
            params.extend(kinds)
        rows = await self._db.query(
            f"SELECT * FROM persona_events{where} ORDER BY created_at DESC LIMIT ?",
            (*params, cap),
        )
        return [self._format_event(row) for row in rows]

    async def drift_series(self, *, days: int = 30) -> dict[str, Any]:
        """按日聚合各轴的漂移量（面板折线图数据）。"""
        if self._db is None:
            return {"days": [], "axes": {}, "labels": dict(AXIS_LABELS)}
        since = self._clock() - max(1, min(365, int(days))) * 86400.0
        rows = await self._db.query(
            "SELECT created_at, deltas FROM persona_events"
            " WHERE created_at >= ? AND kind IN ('shift','milestone') ORDER BY created_at ASC",
            (since,),
        )
        buckets: dict[str, dict[str, float]] = {}
        for row in rows:
            try:
                deltas = json.loads(str(row["deltas"] or "{}"))
            except (TypeError, ValueError):
                continue
            if not isinstance(deltas, dict):
                continue
            day = time.strftime("%Y-%m-%d", time.localtime(float(row["created_at"] or 0.0)))
            bucket = buckets.setdefault(day, {})
            for axis, value in deltas.items():
                try:
                    bucket[axis] = round(bucket.get(axis, 0.0) + float(value), 6)
                except (TypeError, ValueError):
                    continue
        return {
            "days": sorted(buckets.keys()),
            "axes": buckets,
            "labels": dict(AXIS_LABELS),
        }

    async def radar(self) -> dict[str, Any]:
        """9 轴雷达：5 个大五轴 + 3 个派生轴 + 兴趣广度。

        派生轴是可审计的线性折算（不是额外自由度）：
        温暖 = 宜人×0.8 + 外向×0.2；果断 = 外向×0.6 + 尽责×0.4；
        幽默 = 外向×0.5 + 开放×0.5；兴趣广度 = min(1, 兴趣数 / 6)。
        """
        profile = self._forge.profile_sync() if self._forge is not None else None
        if profile is None:
            return {"axes": [], "values": {}}
        big_five = profile.core_traits.big_five
        values = {
            "openness": round(float(big_five.get("openness", 0.5)), 4),
            "conscientiousness": round(float(big_five.get("conscientiousness", 0.5)), 4),
            "extraversion": round(float(big_five.get("extraversion", 0.5)), 4),
            "agreeableness": round(float(big_five.get("agreeableness", 0.5)), 4),
            "emotional_stability": round(1.0 - float(big_five.get("neuroticism", 0.5)), 4),
        }
        values["warmth"] = round(values["agreeableness"] * 0.8 + values["extraversion"] * 0.2, 4)
        values["assertiveness"] = round(
            values["extraversion"] * 0.6 + values["conscientiousness"] * 0.4, 4
        )
        values["humor_inclination"] = round(
            values["extraversion"] * 0.5 + values["openness"] * 0.5, 4
        )
        values["interest_breadth"] = round(min(1.0, len(profile.interests) / 6.0), 4)
        return {
            "axes": list(AXIS_LABELS.keys()),
            "labels": dict(AXIS_LABELS),
            "values": values,
        }

    async def stats(self, *, days: int = 7) -> dict[str, Any]:
        if self._db is None:
            return {"total": 0, "recent": 0, "milestones": 0, "by_experience": []}
        since = self._clock() - max(1, int(days)) * 86400.0
        total = await self._db.scalar("SELECT COUNT(*) FROM persona_events", (), 0)
        recent = await self._db.scalar(
            "SELECT COUNT(*) FROM persona_events WHERE created_at >= ?", (since,), 0
        )
        milestones = await self._db.scalar(
            "SELECT COUNT(*) FROM persona_events WHERE kind='milestone'", (), 0
        )
        rows = await self._db.query(
            "SELECT experience, COUNT(*) AS count FROM persona_events"
            " WHERE created_at >= ? GROUP BY experience ORDER BY count DESC LIMIT 8",
            (since,),
        )
        return {
            "total": int(total or 0),
            "recent": int(recent or 0),
            "milestones": int(milestones or 0),
            "window_days": days,
            "by_experience": [
                {
                    "experience": str(row["experience"] or ""),
                    "label": EXPERIENCE_LABELS.get(str(row["experience"] or ""), ""),
                    "count": int(row["count"] or 0),
                }
                for row in rows
            ],
        }

    async def overview(self, *, limit: int = 30, days: int = 30) -> dict[str, Any]:
        """面板「演化轨迹」页的一次性数据包。"""
        return {
            "timeline": await self.timeline(limit=limit),
            "drift": await self.drift_series(days=days),
            "radar": await self.radar(),
            "stats": await self.stats(days=7),
            "labels": {
                "experience": dict(EXPERIENCE_LABELS),
                "axes": dict(AXIS_LABELS),
            },
        }

    async def reset_events(self) -> dict[str, Any]:
        """清空演化事件（重置轨迹，不影响当前画像）。"""
        if self._db is None:
            return {"ok": False, "message": "持久层未就绪"}
        cursor = await self._db.execute("DELETE FROM persona_events")
        return {"ok": True, "deleted": int(getattr(cursor, "rowcount", 0) or 0)}

    def _format_event(self, row: Any) -> dict[str, Any]:
        try:
            deltas = json.loads(str(row["deltas"] or "{}"))
        except (TypeError, ValueError):
            deltas = {}
        experience = str(row["experience"] or "")
        return {
            "id": int(row["id"]),
            "kind": str(row["kind"] or "shift"),
            "experience": experience,
            "label": EXPERIENCE_LABELS.get(experience, experience),
            "summary": str(row["summary"] or ""),
            "deltas": deltas if isinstance(deltas, dict) else {},
            "delta_labels": {
                AXIS_LABELS.get(axis, axis): round(float(value), 4)
                for axis, value in (deltas or {}).items()
            },
            "confidence": round(float(row["confidence"] or 0.0), 4),
            "created_at": float(row["created_at"] or 0.0),
        }

    def _debug(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            try:
                self._logger.debug(message, *args)
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "EXPERIENCE_INFLUENCE_VECTORS",
    "EXPERIENCE_LABELS",
    "EXPERIENCE_TYPES",
    "MAX_MILESTONE_SHIFT",
    "MAX_TRAIT_DELTA_PER_EXCHANGE",
    "MILESTONE_TOTAL_THRESHOLD",
    "AXIS_LABELS",
    "ExperienceVerdict",
    "TraitEvolutionService",
    "classify_experience",
    "compute_deltas",
]
