"""三层人格数据模型（PersonaForge vendor 版）。

来源与改写：PersonaForge ``personaforge/personality_model.py``（三层人格：
核心特质 / 表层风格 / 动态状态）。改写点：

1. 去掉 ``enum`` 依赖的枚举校验改为字符串 + 白名单常量（配置来自面板 JSON，
   宽松读取、严格输出，避免旧数据导致反序列化失败）；
2. 保留 ``to_dict`` / ``from_dict`` / ``to_profile_text`` 三个契约方法，
   面板读写与提示词注入都走它们；
3. 增加 ``default_profile()``：单人格的出厂画像（id10「谷雨」），
   避免「人格为空 → 注入空块」的降级路径。

数值约定：大五人格 0~1；能量 0~100（与 PersonaForge 原模型一致），
但对外注入时统一归一化为 0~1 的百分比表达。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Mapping

# --------------------------------------------------------------------------- #
# 枚举白名单（宽松读取、严格输出）
# --------------------------------------------------------------------------- #

DEFENSE_MECHANISMS: tuple[str, ...] = (
    "Rationalization",
    "Projection",
    "Denial",
    "Repression",
    "Sublimation",
    "Displacement",
    "ReactionFormation",
    "Humor",
    "Intellectualization",
)
"""防御机制：PersonaForge 原枚举的字符串版（简称不变，便于提示词直接引用）。"""

DEFENSE_LABELS: dict[str, str] = {
    "Rationalization": "合理化",
    "Projection": "投射",
    "Denial": "否认",
    "Repression": "压抑",
    "Sublimation": "升华",
    "Displacement": "转移",
    "ReactionFormation": "反向形成",
    "Humor": "幽默自嘲",
    "Intellectualization": "理智化",
}

SENTENCE_LENGTHS: tuple[str, ...] = ("short", "medium", "long", "mixed")
VOCABULARY_LEVELS: tuple[str, ...] = ("academic", "casual", "network", "mixed")
PUNCTUATION_HABITS: tuple[str, ...] = ("minimal", "standard", "excessive", "mixed")
EMOJI_FREQUENCIES: tuple[str, ...] = ("none", "low", "medium", "high")

SENTENCE_LABELS = {"short": "短句为主", "medium": "中等长度", "long": "长句为主", "mixed": "混合"}
VOCABULARY_LABELS = {
    "academic": "学术/正式",
    "casual": "口语化",
    "network": "网络用语",
    "mixed": "混合",
}
PUNCTUATION_LABELS = {
    "minimal": "少用标点",
    "standard": "标准使用",
    "excessive": "频繁使用",
    "mixed": "混合",
}
EMOJI_LABELS = {"none": "不用", "low": "偶尔", "medium": "适中", "high": "频繁"}

BIG_FIVE_AXES: tuple[str, ...] = (
    "openness",
    "conscientiousness",
    "extraversion",
    "agreeableness",
    "neuroticism",
)

BIG_FIVE_LABELS: dict[str, str] = {
    "openness": "开放性",
    "conscientiousness": "尽责性",
    "extraversion": "外向性",
    "agreeableness": "宜人性",
    "neuroticism": "神经质",
}


def _clamp(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _pick(value: Any, allowed: tuple[str, ...], default: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else default


# --------------------------------------------------------------------------- #
# 三层模型
# --------------------------------------------------------------------------- #


@dataclass
class CoreTraits:
    """内核层：认知与特质（大五人格 / 价值观 / 防御机制）。"""

    mbti: str = "INFJ-T"
    big_five: dict[str, float] = field(default_factory=dict)
    values: list[str] = field(default_factory=list)
    defense_mechanism: str = "Humor"

    def __post_init__(self) -> None:
        normalized: dict[str, float] = {}
        for axis in BIG_FIVE_AXES:
            normalized[axis] = _clamp(self.big_five.get(axis, 0.5), 0.5)
        self.big_five = normalized
        self.defense_mechanism = _pick(self.defense_mechanism, DEFENSE_MECHANISMS, "Humor")
        self.values = [str(item).strip() for item in (self.values or []) if str(item).strip()]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mbti": self.mbti,
            "big_five": dict(self.big_five),
            "values": list(self.values),
            "defense_mechanism": self.defense_mechanism,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "CoreTraits":
        data = data or {}
        return cls(
            mbti=str(data.get("mbti") or "INFJ-T"),
            big_five=dict(data.get("big_five") or {}),
            values=list(data.get("values") or []),
            defense_mechanism=str(data.get("defense_mechanism") or "Humor"),
        )


@dataclass
class SpeakingStyle:
    """表象层：语言风格矩阵。"""

    sentence_length: str = "short"
    vocabulary_level: str = "casual"
    punctuation_habit: str = "minimal"
    emoji_frequency: str = "low"
    emoji_preferred: list[str] = field(default_factory=list)
    emoji_avoided: list[str] = field(default_factory=list)
    catchphrases: list[str] = field(default_factory=list)
    tone_markers: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.sentence_length = _pick(self.sentence_length, SENTENCE_LENGTHS, "short")
        self.vocabulary_level = _pick(self.vocabulary_level, VOCABULARY_LEVELS, "casual")
        self.punctuation_habit = _pick(self.punctuation_habit, PUNCTUATION_HABITS, "minimal")
        self.emoji_frequency = _pick(self.emoji_frequency, EMOJI_FREQUENCIES, "none")
        self.emoji_preferred = _clean_list(self.emoji_preferred)
        self.emoji_avoided = _clean_list(self.emoji_avoided)
        self.catchphrases = _clean_list(self.catchphrases)[:8]
        self.tone_markers = _clean_list(self.tone_markers)[:8]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sentence_length": self.sentence_length,
            "vocabulary_level": self.vocabulary_level,
            "punctuation_habit": self.punctuation_habit,
            "emoji_frequency": self.emoji_frequency,
            "emoji_preferred": list(self.emoji_preferred),
            "emoji_avoided": list(self.emoji_avoided),
            "catchphrases": list(self.catchphrases),
            "tone_markers": list(self.tone_markers),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "SpeakingStyle":
        data = data or {}
        emoji = data.get("emoji_usage")
        emoji = emoji if isinstance(emoji, Mapping) else {}
        return cls(
            sentence_length=str(data.get("sentence_length") or "short"),
            vocabulary_level=str(data.get("vocabulary_level") or "casual"),
            punctuation_habit=str(data.get("punctuation_habit") or "minimal"),
            emoji_frequency=str(
                data.get("emoji_frequency") or emoji.get("frequency") or "none"
            ),
            emoji_preferred=list(data.get("emoji_preferred") or emoji.get("preferred") or []),
            emoji_avoided=list(data.get("emoji_avoided") or emoji.get("avoided") or []),
            catchphrases=list(data.get("catchphrases") or []),
            tone_markers=list(data.get("tone_markers") or []),
        )


@dataclass
class RelationshipInfo:
    """关系信息（对某位对象）：亲密度 0~100 + 历史摘要。"""

    intimacy: float = 0.0
    history_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"intimacy": round(float(self.intimacy), 2), "history_summary": self.history_summary}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "RelationshipInfo":
        data = data or {}
        try:
            intimacy = float(data.get("intimacy") or 0.0)
        except (TypeError, ValueError):
            intimacy = 0.0
        return cls(
            intimacy=max(0.0, min(100.0, intimacy)),
            history_summary=str(data.get("history_summary") or ""),
        )


@dataclass
class DynamicState:
    """记忆层：动态状态（心情 / 能量 / 关系映射）。"""

    current_mood: str = "平静"
    energy_level: int = 60
    relationship_map: dict[str, RelationshipInfo] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            self.energy_level = max(0, min(100, int(self.energy_level)))
        except (TypeError, ValueError):
            self.energy_level = 60
        self.current_mood = str(self.current_mood or "平静")

    def update_mood(self, mood: str) -> None:
        text = str(mood or "").strip()
        if text:
            self.current_mood = text

    def update_energy(self, delta: int) -> None:
        self.energy_level = max(0, min(100, int(self.energy_level) + int(delta)))

    def relationship(self, target: str) -> RelationshipInfo:
        return self.relationship_map.setdefault(target, RelationshipInfo())

    def update_relationship(
        self, target: str, *, intimacy: float | None = None, history_summary: str = ""
    ) -> None:
        info = self.relationship(target)
        if intimacy is not None:
            info.intimacy = max(0.0, min(100.0, float(intimacy)))
        if history_summary:
            info.history_summary = history_summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_mood": self.current_mood,
            "energy_level": self.energy_level,
            "relationship_map": {
                key: value.to_dict() for key, value in self.relationship_map.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "DynamicState":
        data = data or {}
        raw_map = data.get("relationship_map")
        relationships: dict[str, RelationshipInfo] = {}
        if isinstance(raw_map, Mapping):
            for key, value in raw_map.items():
                relationships[str(key)] = RelationshipInfo.from_dict(
                    value if isinstance(value, Mapping) else {}
                )
        return cls(
            current_mood=str(data.get("current_mood") or "平静"),
            energy_level=data.get("energy_level") or 60,
            relationship_map=relationships,
        )


@dataclass
class PersonalityProfile:
    """完整三层人格画像（单人格）。"""

    core_traits: CoreTraits = field(default_factory=CoreTraits)
    speaking_style: SpeakingStyle = field(default_factory=SpeakingStyle)
    dynamic_state: DynamicState = field(default_factory=DynamicState)
    interests: list[str] = field(default_factory=list)
    social_goals: list[str] = field(default_factory=list)
    long_term_goals: list[str] = field(default_factory=list)
    style_examples: list[dict[str, str]] = field(default_factory=list)

    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        return {
            "core_traits": self.core_traits.to_dict(),
            "speaking_style": self.speaking_style.to_dict(),
            "dynamic_state": self.dynamic_state.to_dict(),
            "interests": list(self.interests),
            "social_goals": list(self.social_goals),
            "long_term_goals": list(self.long_term_goals),
            "style_examples": [
                {"context": str(item.get("context") or ""), "response": str(item.get("response") or "")}
                for item in self.style_examples[:10]
            ],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "PersonalityProfile":
        data = data or {}
        return cls(
            core_traits=CoreTraits.from_dict(data.get("core_traits")),
            speaking_style=SpeakingStyle.from_dict(data.get("speaking_style")),
            dynamic_state=DynamicState.from_dict(data.get("dynamic_state")),
            interests=_clean_list(data.get("interests")),
            social_goals=_clean_list(data.get("social_goals")),
            long_term_goals=_clean_list(data.get("long_term_goals")),
            style_examples=[
                {
                    "context": str(item.get("context") or ""),
                    "response": str(item.get("response") or ""),
                }
                for item in (data.get("style_examples") or [])
                if isinstance(item, Mapping)
            ][:10],
        )

    def merge(self, patch: Mapping[str, Any]) -> "PersonalityProfile":
        """用面板提交的补丁合并出新画像（缺失层沿用现值，不整体替换）。"""
        merged = copy.deepcopy(self.to_dict())
        if isinstance(patch.get("core_traits"), Mapping):
            merged["core_traits"].update(patch["core_traits"])
        if isinstance(patch.get("speaking_style"), Mapping):
            merged["speaking_style"].update(patch["speaking_style"])
        if isinstance(patch.get("dynamic_state"), Mapping):
            state_patch = dict(patch["dynamic_state"])
            # 关系映射是增量补丁：只覆盖提交的键，避免面板回写时丢别的群友
            incoming = state_patch.pop("relationship_map", None)
            merged["dynamic_state"].update(state_patch)
            if isinstance(incoming, Mapping):
                current = dict(merged["dynamic_state"].get("relationship_map") or {})
                current.update({str(key): value for key, value in incoming.items()})
                merged["dynamic_state"]["relationship_map"] = current
        for key in ("interests", "social_goals", "long_term_goals", "style_examples"):
            if key in patch and isinstance(patch[key], (list, tuple)):
                merged[key] = list(patch[key])
        return PersonalityProfile.from_dict(merged)

    def to_profile_text(self) -> str:
        """生成注入用的人格摘要（结构化、克制字数）。"""
        core = self.core_traits
        style = self.speaking_style
        state = self.dynamic_state
        big_five = "、".join(
            f"{BIG_FIVE_LABELS[axis]} {core.big_five.get(axis, 0.5):.2f}" for axis in BIG_FIVE_AXES
        )
        lines = [
            "【人格内核 · 三层建模】",
            f"① 核心特质：{core.mbti}；{big_five}；"
            f"价值观 {'、'.join(core.values) if core.values else '—'}；"
            f"压力下的防御机制是{DEFENSE_LABELS.get(core.defense_mechanism, core.defense_mechanism)}。",
            "② 表层风格："
            f"{SENTENCE_LABELS.get(style.sentence_length, style.sentence_length)}、"
            f"{VOCABULARY_LABELS.get(style.vocabulary_level, style.vocabulary_level)}、"
            f"{PUNCTUATION_LABELS.get(style.punctuation_habit, style.punctuation_habit)}"
            + (f"；口头禅「{'、'.join(style.catchphrases)}」" if style.catchphrases else "")
            + (f"；语气词 {'、'.join(style.tone_markers)}" if style.tone_markers else "")
            + f"；表情使用{EMOJI_LABELS.get(style.emoji_frequency, style.emoji_frequency)}。",
            f"③ 当前状态：心情{state.current_mood}，能量 {state.energy_level}/100。",
        ]
        if self.interests:
            lines.append(f"兴趣：{'、'.join(self.interests[:6])}。")
        if self.social_goals:
            lines.append(f"社交目标：{'、'.join(self.social_goals[:4])}。")
        lines.append("保持以上人格一致；这是你的内核，不随对话对象改变。")
        return "\n".join(lines)


def _clean_list(value: Any) -> list[str]:
    if isinstance(value, str):
        items = [part.strip() for part in value.replace("，", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value]
    else:
        items = []
    return [item for item in items if item]


def default_profile() -> PersonalityProfile:
    """单人格出厂画像（id10「谷雨」）。

    取值刻意保守：不写「毒舌」「自来熟」这类强设定，只给中性可调的三层骨架，
    由群友策略（members）承担表层差异，避免与用户的既有系统提示词冲突。
    """
    return PersonalityProfile(
        core_traits=CoreTraits(
            mbti="INFJ-T",
            big_five={
                "openness": 0.72,
                "conscientiousness": 0.58,
                "extraversion": 0.46,
                "agreeableness": 0.68,
                "neuroticism": 0.42,
            },
            values=["真诚", "好奇", "分寸感"],
            defense_mechanism="Humor",
        ),
        speaking_style=SpeakingStyle(
            sentence_length="mixed",
            vocabulary_level="casual",
            punctuation_habit="minimal",
            emoji_frequency="low",
            catchphrases=[],
            tone_markers=["吧", "呢"],
        ),
        dynamic_state=DynamicState(current_mood="平静", energy_level=60),
        interests=[],
        social_goals=["记住群友的近况", "让对话自然、不端着"],
        long_term_goals=[],
    )


__all__ = [
    "BIG_FIVE_AXES",
    "BIG_FIVE_LABELS",
    "DEFENSE_LABELS",
    "DEFENSE_MECHANISMS",
    "EMOJI_FREQUENCIES",
    "EMOJI_LABELS",
    "PUNCTUATION_HABITS",
    "PUNCTUATION_LABELS",
    "SENTENCE_LABELS",
    "SENTENCE_LENGTHS",
    "VOCABULARY_LABELS",
    "VOCABULARY_LEVELS",
    "CoreTraits",
    "DynamicState",
    "PersonalityProfile",
    "RelationshipInfo",
    "SpeakingStyle",
    "default_profile",
]
