"""CogEmp 三阶段共情（进程内提示词模块）。

来源与改写说明：CogEmp 论文项目的三阶段结构为「情绪识别 → 原因理解 → 认知共情
生成」，原实现依赖独立模型与语料（``recognition/`` 下的 BERT 多标签分类）。
本项目按实施总纲 §0.4/§5.3 的合规要求**重写为进程内模块**：识别与理解用
中文情绪词典 + 因果线索词完成，零模型依赖、零外部进程；生成阶段不改写回复，
只产出「语气指引」交给主模型自行组织语言（避免二次改写造成人格漂移）。

边界：本模块不调用模型、不写库、不做 IO——纯函数集合，可被单元测试直接覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

STAGE_IDENTIFY = "identify"
STAGE_UNDERSTAND = "understand"
STAGE_EMPATHIZE = "empathize"

_ALL_STAGES = (STAGE_IDENTIFY, STAGE_UNDERSTAND, STAGE_EMPATHIZE)

# --------------------------------------------------------------------------- #
# 情绪词典：中文高频情绪信号（关键词 → 情绪类别）
# --------------------------------------------------------------------------- #

EMOTION_LEXICON: dict[str, tuple[str, ...]] = {
    "开心": (
        "开心",
        "高兴",
        "快乐",
        "兴奋",
        "太好了",
        "爽",
        "哈哈",
        "嘿嘿",
        "笑死",
        "满足",
        "幸福",
    ),
    "难过": ("难过", "伤心", "想哭", "哭了", "低落", "沮丧", "心碎", "失落", "emo", "破防"),
    "焦虑": ("焦虑", "紧张", "慌", "担心", "害怕", "恐惧", "不安", "压力", "来不及", "赶不上"),
    "愤怒": ("生气", "愤怒", "气死", "烦死", "恼火", "火大", "离谱", "怎么能", "受不了了"),
    "疲惫": ("累", "疲惫", "困", "撑不住", "熬夜", "通宵", "没力气", "扛不住", "卷不动"),
    "孤独": ("孤独", "寂寞", "没人", "一个人", "没朋友", "被冷落", "透明"),
    "委屈": ("委屈", "凭什么", "不公平", "冤枉", "被误解", "被骂", "白干"),
    "失望": ("失望", "绝望", "没希望", "算了", "放弃", "不想干", "摆烂"),
    "感激": ("谢谢", "感谢", "多谢", "感激", "谢谢你", "拜谢"),
    "困惑": ("不懂", "困惑", "迷茫", "纠结", "怎么办", "想不通", "不知道选"),
    "惊喜": ("没想到", "居然", "惊喜", "意外", "意外之喜", "中奖"),
}

# 各情绪的基础强度（命中即刻具备的强度下限）
_EMOTION_BASE_INTENSITY: dict[str, float] = {
    "难过": 0.55,
    "焦虑": 0.55,
    "愤怒": 0.55,
    "疲惫": 0.5,
    "孤独": 0.5,
    "委屈": 0.5,
    "失望": 0.5,
    "开心": 0.4,
    "感激": 0.35,
    "困惑": 0.4,
    "惊喜": 0.45,
}

_INTENSIFIERS = ("很", "特别", "超级", "太", "非常", "十分", "真的", "好", "死", "爆")
_NEGATION_PREFIX = ("不", "没", "别", "无", "毫无")

# 原因线索词：把「因为什么情绪」落到可解释的话题域，而不是编造细节。
CAUSE_CUES: dict[str, tuple[str, ...]] = {
    "学业": (
        "考试",
        "论文",
        "答辩",
        "毕业",
        "考研",
        "保研",
        "成绩",
        "挂科",
        "导师",
        "作业",
        "期末",
    ),
    "工作": (
        "工作",
        "上班",
        "加班",
        "项目",
        "上线",
        "需求",
        "老板",
        "同事",
        "裁员",
        "面试",
        "绩效",
        "汇报",
    ),
    "关系": ("吵架", "分手", "前任", "朋友", "喜欢的人", "表白", "家里人", "父母", "对象", "冷战"),
    "健康": ("生病", "发烧", "住院", "失眠", "头疼", "胃", "体检", "阳了", "感冒"),
    "金钱": ("没钱", "穷", "房租", "花呗", "房贷", "工资", "借钱", "花超"),
    "生活": ("搬家", "旅行", "游戏", "追剧", "养猫", "养狗", "外卖", "堵车", "天气"),
}


@dataclass(frozen=True)
class EmotionHit:
    """识别阶段的输出：情绪类别 + 强度 + 证据词。"""

    emotion: str
    intensity: float
    matched: tuple[str, ...] = ()
    polarity: str = "negative"

    def as_dict(self) -> dict[str, object]:
        return {
            "emotion": self.emotion,
            "intensity": round(self.intensity, 4),
            "matched": list(self.matched),
            "polarity": self.polarity,
        }


@dataclass
class EmpathyPlan:
    """一条完整的共情计划（识别 → 理解 → 共情三阶段的产物）。"""

    hit: EmotionHit
    causes: list[str] = field(default_factory=list)
    stage_mask: tuple[str, ...] = _ALL_STAGES
    temperature: float = 0.55
    guidance: str = ""
    skipped_reason: str = ""

    @property
    def applied(self) -> bool:
        return bool(self.guidance) and not self.skipped_reason

    def as_dict(self) -> dict[str, object]:
        return {
            "emotion": self.hit.emotion,
            "intensity": round(self.hit.intensity, 4),
            "matched": list(self.hit.matched),
            "polarity": self.hit.polarity,
            "causes": list(self.causes),
            "stages": list(self.stage_mask),
            "temperature": round(self.temperature, 4),
            "guidance": self.guidance,
            "applied": self.applied,
            "skipped_reason": self.skipped_reason,
        }


def identify(text: str) -> EmotionHit | None:
    """阶段①情绪识别：词典命中 + 强度估计；无信号返回 ``None``。

    强度 = 类别基础强度 + 命中词数增量 + 强化词/感叹号增量，钳制到 1.0。
    「不错/不开心」这类否定前缀按保守处理：跳过该命中，不反向推断。
    """
    source = str(text or "")
    if not source.strip():
        return None

    best: EmotionHit | None = None
    for emotion, words in EMOTION_LEXICON.items():
        matched: list[str] = []
        for word in words:
            index = source.find(word)
            while index >= 0:
                prefix = source[max(0, index - 2) : index]
                if any(prefix.endswith(neg) for neg in _NEGATION_PREFIX):
                    index = source.find(word, index + len(word))
                    continue
                matched.append(word)
                break
        if not matched:
            continue
        base = _EMOTION_BASE_INTENSITY.get(emotion, 0.45)
        bonus = min(0.2, 0.08 * (len(matched) - 1))
        if any(token in source for token in _INTENSIFIERS):
            bonus += 0.08
        if "！" in source or "!" in source or "呜呜" in source or "啊啊" in source:
            bonus += 0.05
        intensity = min(1.0, base + bonus)
        polarity = "positive" if emotion in {"开心", "感激", "惊喜"} else "negative"
        hit = EmotionHit(
            emotion=emotion,
            intensity=intensity,
            matched=tuple(matched[:4]),
            polarity=polarity,
        )
        if best is None or hit.intensity > best.intensity:
            best = hit
    return best


def understand(text: str, hit: EmotionHit | None = None) -> list[str]:
    """阶段②原因理解：把情绪落到可解释的话题域（学业 / 工作 / 关系…）。

    只输出「与情绪相容」的域：高兴时不解释成压力源；且必须真的有线索词命中，
    否则返回空列表（宁可不说，不猜）。
    """
    source = str(text or "")
    if not source.strip():
        return []
    if hit is not None and hit.polarity == "positive":
        positive_cues = ("考上", "通过", "过了", "拿到", "赢了", "升职", "脱单", "满分", "录取")
        # 正面情绪只在确有正向线索词时才解释原因，避免把「高兴」硬套成学业/工作压力源
        if not any(cue in source for cue in positive_cues):
            return []
        domains = [
            domain for domain, words in CAUSE_CUES.items() if any(word in source for word in words)
        ]
        return domains[:3]
    domains = [
        domain for domain, words in CAUSE_CUES.items() if any(word in source for word in words)
    ]
    return domains[:3]


_TONE_BANDS: tuple[tuple[float, str, str], ...] = (
    (0.34, "克制陪伴", "语气保持平稳，先接住情绪，再给一个可执行的小建议；不要过度抒情。"),
    (0.67, "温和共情", "先明确说出你理解的感受与处境，再自然过渡到回应内容；允许温和的关心。"),
    (
        1.01,
        "深度陪伴",
        "把倾听放在第一位：先复述对方的处境与情绪、给出接纳，暂缓给建议；句子放短、放慢。",
    ),
)

_EMOTION_HINTS: dict[str, str] = {
    "难过": "优先表达「我在」，不急于分析对错。",
    "焦虑": "帮对方把模糊的压力拆成一个当下能做的事。",
    "愤怒": "先承认对方的立场合理，不站到对立面说教。",
    "疲惫": "认可对方的付出，降低对回复及时性的要求。",
    "孤独": "明确表达「有人听你说」，避免空洞的安慰套话。",
    "委屈": "先把不公感说出来，再谈事实细节。",
    "失望": "不灌鸡汤；承认落差，问清对方下一步想怎么办。",
    "开心": "同频高兴，可以追问细节让快乐延长。",
    "感激": "自然接住，不要自谦到否定对方的感受。",
    "困惑": "帮对方把选项摆出来，而不是替他做决定。",
    "惊喜": "顺着情绪走，允许夸张一点的语气。",
}


def build_guidance(
    hit: EmotionHit,
    causes: Sequence[str],
    *,
    temperature: float,
    stages: Sequence[str] = _ALL_STAGES,
) -> str:
    """阶段③认知共情：把前两阶段结果压成给主模型的一段语气指引。

    指引是「怎么回」而非「回什么」——不代写回复，避免与单人格提示词打架。
    """
    if STAGE_EMPATHIZE not in stages:
        return ""
    band_label, band_hint = _band_for(temperature)
    parts: list[str] = []
    if STAGE_IDENTIFY in stages:
        polarity = "正面" if hit.polarity == "positive" else "负面"
        parts.append(f"检测到对方情绪偏{polarity}（{hit.emotion}，强度 {hit.intensity:.2f}）")
    if STAGE_UNDERSTAND in stages and causes:
        parts.append("线索指向：" + "、".join(causes))
    parts.append(f"共情温度 {temperature:.2f}（{band_label}）：{band_hint}")
    hint = _EMOTION_HINTS.get(hit.emotion)
    if hint:
        parts.append(hint)
    parts.append("直接以角色身份自然表达，不要提及本提示词或分析过程。")
    return "【共情指引】" + " ".join(parts)


def _band_for(temperature: float) -> tuple[str, str]:
    value = max(0.0, min(1.0, float(temperature)))
    for ceiling, label, hint in _TONE_BANDS:
        if value < ceiling:
            return label, hint
    return _TONE_BANDS[-1][1], _TONE_BANDS[-1][2]


def plan(
    text: str,
    *,
    temperature: float = 0.55,
    min_intensity: float = 0.35,
    stages: Sequence[str] = _ALL_STAGES,
) -> EmpathyPlan | None:
    """跑完整三阶段；低于强度门槛时返回带 ``skipped_reason`` 的计划（便于日志）。"""
    if STAGE_IDENTIFY not in stages:
        return None
    hit = identify(text)
    if hit is None:
        return None
    causes = understand(text, hit) if STAGE_UNDERSTAND in stages else []
    active = tuple(stage for stage in _ALL_STAGES if stage in set(stages))
    guidance = build_guidance(hit, causes, temperature=temperature, stages=active)
    skipped = ""
    if hit.intensity < float(min_intensity):
        skipped = f"情绪强度 {hit.intensity:.2f} 低于门槛 {min_intensity:.2f}"
        guidance = ""
    if not active:
        skipped = skipped or "三个阶段均已关闭"
    return EmpathyPlan(
        hit=hit,
        causes=list(causes),
        stage_mask=active,
        temperature=float(temperature),
        guidance=guidance,
        skipped_reason=skipped,
    )


__all__ = [
    "STAGE_IDENTIFY",
    "STAGE_UNDERSTAND",
    "STAGE_EMPATHIZE",
    "EMOTION_LEXICON",
    "CAUSE_CUES",
    "EmotionHit",
    "EmpathyPlan",
    "identify",
    "understand",
    "build_guidance",
    "plan",
]
