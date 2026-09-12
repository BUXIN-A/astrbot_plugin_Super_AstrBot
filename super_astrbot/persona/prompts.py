"""拟人化学习的提示词与结构化解析。

两处需要模型参与：**黑话词义推断**（批量）与**好感度交互判定**（单条兜底）。
二者都要求 JSON 输出并做**强校验**：错误的学习结果会直接改变 Bot 的表达，
宁可丢弃不合格条目，也不写入一条可能错误的样本。
"""

from __future__ import annotations

from typing import Any, Sequence

from ..support import PromptOverrides, PromptSpec, extract_json, render, truncate

PROMPT_JARGON_SYSTEM = "jargon_system"
PROMPT_JARGON_TEMPLATE = "jargon_template"
PROMPT_AFFINITY_SYSTEM = "affinity_system"
PROMPT_AFFINITY_TEMPLATE = "affinity_template"
"""配置键：与 `_conf_schema.json` 的 `prompts.*` 一一对应。"""

_JARGON_REQUIRED = ("candidates",)
_AFFINITY_REQUIRED = ("message",)

JARGON_SYSTEM = (
    "你是一个群聊语言分析助手。你的任务是判断给定词条是否属于该群聊的「内部用语」，"
    "并给出简明解释。判断要求：\n"
    "1. 只有在该群语境下有特殊含义、或属于网络流行语/圈内缩写时才判为内部用语；\n"
    "2. 普通词汇、人名、地名、常见英文单词一律判为否；\n"
    "3. 严禁编造含义；若例句不足以判断，confidence 给低分并判为否。"
)

AFFINITY_SYSTEM = (
    "你是一个社交情绪分析助手。请判断用户这句话对对话对象表达出的态度类型，"
    "只输出 JSON，不要解释。类型取值：praise（夸赞欣赏）、thanks（感谢）、care（关心）、"
    "greet（问候）、joke（玩笑调侃）、apology（道歉）、question（提问）、"
    "criticism（不满/抱怨）、insult（辱骂攻击）、conflict（争执）、neutral（普通交流）。"
)

_JARGON_TEMPLATE = """以下是某个群聊中频繁出现、且尚未被收录的候选词条（含例句）：

<candidates>
{candidates}
</candidates>

请逐个判断，只输出 JSON 数组，不要输出任何解释文字。每个元素格式：
{{
  "term": "候选词原文",
  "is_jargon": true 或 false,
  "meaning": "若为内部用语，用一句第三人称说明其含义；否则填空字符串",
  "confidence": 0.0 到 1.0 之间的小数
}}

要求：
1. 必须为每个候选词输出一项，term 必须与候选词完全一致，不得增删；
2. 含义不得超过 60 字，不要重复词条本身，不要写「这是一个词」之类的废话。
"""

_AFFINITY_TEMPLATE = """请判断下面这句话对对话对象的态度类型：

<message>
{message}
</message>

只输出 JSON：{{"type": "上述类型之一", "confidence": 0.0 到 1.0 之间的小数}}

注意：否定表达要按整体语义判断，例如「不难过」属于 neutral，「你太笨了」属于 insult。
"""

_MIN_MEANING = 2
_MAX_MEANING = 60

AFFINITY_TYPES = (
    "praise",
    "thanks",
    "care",
    "greet",
    "joke",
    "apology",
    "question",
    "criticism",
    "insult",
    "conflict",
    "neutral",
)

STYLE_BLOCK_HEADER = (
    "[SuperAstrBot 表达参考 · 以下是过往对话中的说话方式，只用于模仿语气与措辞，"
    "不要照抄内容、不要提及这份参考]"
)
JARGON_BLOCK_HEADER = (
    "[SuperAstrBot 群内用语 · 以下词条只用于帮助你理解对话含义，"
    "不要复述、不要在回复中主动使用这些词]"
)
AFFINITY_BLOCK_HEADER = "[SuperAstrBot 社交参考 · 以下是你与对方的关系状态，用于把握语气]"


PROMPT_SPECS: tuple[PromptSpec, ...] = (
    PromptSpec(
        key=PROMPT_JARGON_SYSTEM,
        title="群内用语推断系统提示词",
        group="拟人化学习",
        default=JARGON_SYSTEM,
        hint="约束判定标准（普通词一律判否）；留空即用内置默认。",
    ),
    PromptSpec(
        key=PROMPT_JARGON_TEMPLATE,
        title="群内用语推断模板",
        group="拟人化学习",
        default=_JARGON_TEMPLATE,
        required=_JARGON_REQUIRED,
    ),
    PromptSpec(
        key=PROMPT_AFFINITY_SYSTEM,
        title="好感度判定系统提示词",
        group="拟人化学习",
        default=AFFINITY_SYSTEM,
        hint="类型取值需与内置一致（praise/thanks/…/neutral）；留空即用内置默认。",
    ),
    PromptSpec(
        key=PROMPT_AFFINITY_TEMPLATE,
        title="好感度判定模板",
        group="拟人化学习",
        default=_AFFINITY_TEMPLATE,
        required=_AFFINITY_REQUIRED,
    ),
)


def jargon_system(overrides: PromptOverrides | None = None) -> str:
    """黑话推断系统提示词：页面优先、内置兜底。"""
    if overrides is None:
        return JARGON_SYSTEM
    return overrides.get(PROMPT_JARGON_SYSTEM, JARGON_SYSTEM)


def affinity_system(overrides: PromptOverrides | None = None) -> str:
    """好感度判定系统提示词：配置优先、内置兜底。"""
    if overrides is None:
        return AFFINITY_SYSTEM
    return overrides.get(PROMPT_AFFINITY_SYSTEM, AFFINITY_SYSTEM)


def build_jargon_prompt(
    candidates: Sequence[tuple[str, Sequence[str]]], *, overrides: PromptOverrides | None = None
) -> str:
    """``candidates`` 为 ``[(词, 例句列表)]``。"""

    lines: list[str] = []
    for term, samples in candidates:
        lines.append(f"- term: {term}")
        for sample in samples:
            lines.append(f"  例句：{truncate(sample, 120)}")
    template = _JARGON_TEMPLATE
    if overrides is not None:
        template = overrides.get(
            PROMPT_JARGON_TEMPLATE, _JARGON_TEMPLATE, required=_JARGON_REQUIRED
        )
    return render(template, candidates="\n".join(lines))


def build_affinity_prompt(message: str, *, overrides: PromptOverrides | None = None) -> str:
    template = _AFFINITY_TEMPLATE
    if overrides is not None:
        template = overrides.get(
            PROMPT_AFFINITY_TEMPLATE, _AFFINITY_TEMPLATE, required=_AFFINITY_REQUIRED
        )
    return render(template, message=truncate(message, 300))


def parse_jargon_insights(
    text: str, *, candidates: Sequence[str], min_confidence: float
) -> list[dict[str, Any]]:
    """解析黑话推断产出；只保留候选集合内、判定为是、且置信度达标的条目。"""
    entries = extract_json(text, kind="array")
    if not isinstance(entries, list):
        return []

    allowed = {term for term in candidates}
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        term = str(entry.get("term") or "").strip()
        if not term or term not in allowed or term in seen:
            continue
        if not _truthy(entry.get("is_jargon")):
            continue
        meaning = str(entry.get("meaning") or "").strip().replace("\n", " ")
        if len(meaning) < _MIN_MEANING:
            continue
        confidence = _as_float(entry.get("confidence"), 0.0)
        if confidence < min_confidence:
            continue
        seen.add(term)
        results.append(
            {
                "term": term,
                "meaning": truncate(meaning, _MAX_MEANING),
                "confidence": confidence,
            }
        )
    return results


def parse_affinity_verdict(text: str) -> tuple[str, float]:
    """解析好感度判定产出，返回 ``(类型, 置信度)``；无法解析时返回 ``("", 0.0)``。"""
    payload = extract_json(text, kind="object")
    if not isinstance(payload, dict):
        return "", 0.0
    kind = str(payload.get("type") or "").strip().lower()
    if kind not in AFFINITY_TYPES:
        return "", 0.0
    return kind, _as_float(payload.get("confidence"), 0.0)


def render_style_block(examples: Sequence[tuple[str, str]], *, max_chars: int) -> str:
    """渲染风格 few-shot 块；``examples`` 为 ``[(场景, 表达)]``。"""
    lines = [STYLE_BLOCK_HEADER]
    used = len(STYLE_BLOCK_HEADER)
    for situation, expression in examples:
        line = f"场景：{situation}\n表达：{expression}"
        if used + len(line) > max_chars and len(lines) > 1:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def render_jargon_block(entries: Sequence[tuple[str, str]], *, max_chars: int) -> str:
    """渲染黑话理解块；``entries`` 为 ``[(词, 含义)]``。"""
    lines = [JARGON_BLOCK_HEADER]
    used = len(JARGON_BLOCK_HEADER)
    for term, meaning in entries:
        line = f"- {term}：{meaning}"
        if used + len(line) > max_chars and len(lines) > 1:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def render_affinity_block(guidance: str, *, max_chars: int) -> str:
    if not guidance:
        return ""
    return f"{AFFINITY_BLOCK_HEADER}\n{truncate(guidance, max_chars)}"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "是"}
    return bool(value)


def _as_float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if result <= 0.0 or result > 1.0:
        return default
    return round(result, 3)
