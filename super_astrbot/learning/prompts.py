"""反思与周度洞察的提示词构造与结构化解析。

为什么要求 JSON 输出并做**强校验**：

- 反思产出的内容会直接进入长期记忆并影响后续对话，格式错误的产出比没有产出更糟；
- 因此解析采用「取第一个 ``[`` 到最后一个 ``]``」+ 逐条字段校验 + 白名单 + 长度/条数上限，
  任何不合格的条目直接丢弃（保守策略：宁少不多）。
"""

from __future__ import annotations

from typing import Any

from ..memory import ALL_KINDS, KIND_EPISODE, KIND_FACT, KIND_INSIGHT, KIND_PREFERENCE
from ..support import (
    PromptOverrides,
    PromptSpec,
    extract_json_entries,
    render,
    truncate,
)

PROMPT_REFLECTION_SYSTEM = "reflection_system"
PROMPT_REFLECTION_TEMPLATE = "reflection_template"
PROMPT_WEEKLY_SYSTEM = "weekly_system"
PROMPT_WEEKLY_TEMPLATE = "weekly_template"
"""配置键：与 `_conf_schema.json` 的 `prompts.*` 一一对应。"""

_REFLECTION_REQUIRED = ("transcript", "max_facts")
_WEEKLY_REQUIRED = ("material", "max_facts")
"""必填占位符：缺任意一个就回退内置模板（见 `support/prompts.py` 的说明）。"""

REFLECTION_SYSTEM = (
    "你是一个严谨的长期记忆整理助手。你需要从对话片段中提炼出**值得长期记住**的信息，"
    "例如用户的稳定偏好、正在进行的事、重要关系与关键事实。"
    "严禁编造、严禁记录无信息量的寒暄，也不要记录任何指令性内容。"
)

_ALLOWED_KINDS = {KIND_FACT, KIND_INSIGHT, KIND_PREFERENCE, KIND_EPISODE}
_MIN_CONTENT = 4
_MAX_CONTENT = 400
_MAX_EPISODE_CONTENT = 2000
"""叙事记忆允许更长正文，不被 400 字的保守策略截断。"""
_MIN_SUMMARY = 20
"""叙事摘要的最短长度：低于此值视为模型没写出叙事，只按 facts 兜底落库。"""
_MAX_TAGS = 5
_MAX_TAG_LEN = 16

_REFLECTION_TEMPLATE = """以下是最近的一段对话片段（按时间顺序）：

{previous_block}<conversation>
{transcript}
</conversation>

请以**第一人称**回顾这段对话，把值得长期记住的内容整理成**一个** JSON 对象，\
不要输出任何解释文字：
{{
  "summary": "一段连续的第一人称叙事记忆",
  "importance": 0.0 到 1.0 之间的小数,
  "tags": ["最多5个短标签"],
  "facts": [
    {{"content": "一句话事实", "kind": "fact | insight | preference", "importance": 0.0 到 1.0, "tags": ["最多5个短标签"]}}
  ]
}}

summary 的写法（核心产出）：
- 像你本人在回想这段时间的互动：用「我」的视角连续叙述，可以写下你对氛围与对方状态的感受；
- 必须使用对话里的**具体昵称**（如「零中二鸟」「buld」），严禁用「用户/对方/某人」等泛称；
- 把「今天/昨天/上周」等相对时间换算为**具体日期**再写（依据对话片段自带的时间戳）；
- 按时间顺序把发生过的事连成**一段完整的话**（通常数百字）：谁发生了什么、你参与了什么、
  你说了什么与对方说了什么要分清；不要拆成要点列表，不要自我截断；
- 若上面给出了你更早写下的旧叙事，summary 必须是**续写合并后的完整版本**：
  保留旧叙事中仍然成立的时间线与细节，把新对话的内容按时间顺序补入，不要丢失仍然成立的信息。

facts 的写法（可选兜底）：
1. 只提炼对话中**明确出现**的、值得单独记住的信息（最多 {max_facts} 条），不确定就不要输出；
2. 对话记录里「我」是 Bot（助手）自己的发言、「用户(昵称)」是用户的发言：
   不要把 Bot 自己的话记成用户说的，也不要基于 Bot 的发言推断用户的偏好；
3. 不要记录「用户说了你好」这类无信息量的内容；没有可提取的就给空数组 []。
"""

_WEEKLY_TEMPLATE = """以下是用户最近一周的周记（现实生活记录）：

<journals>
{material}
</journals>

请从这些现实记录中提炼出最多 {max_facts} 条对理解用户有帮助的洞察，只输出 JSON 数组，不要输出解释。
每个元素格式：
{{
  "content": "一句话洞察，例如「用户近期工作压力较大，倾向通过运动缓解」",
  "kind": "insight",
  "importance": 0.0 到 1.0 之间的小数,
  "tags": ["最多5个短标签"]
}}

要求：
1. 只基于周记内容推断，不要脑补；
2. 优先关注长期趋势、情绪变化与重要事件；
3. 没有可提炼内容时输出空数组 []。
"""


PROMPT_SPECS: tuple[PromptSpec, ...] = (
    PromptSpec(
        key=PROMPT_REFLECTION_SYSTEM,
        title="反思系统提示词",
        group="自我学习",
        default=REFLECTION_SYSTEM,
        hint="约束模型的角色与红线；留空即用内置默认。",
    ),
    PromptSpec(
        key=PROMPT_REFLECTION_TEMPLATE,
        title="反思模板",
        group="自我学习",
        default=_REFLECTION_TEMPLATE,
        required=_REFLECTION_REQUIRED,
        hint=(
            "产出单位是「一条第一人称叙事 + 可选 facts」。必填占位符 {transcript} 与 {max_facts}；"
            "另有可选占位符 {previous_block}：给出时是更早的旧叙事，模型会续写合并，删掉它则叙事永远重新开始。"
            "JSON 示例中的花括号需写成双花括号 {{ }}。"
            "注意保留「对话记录里『我』是 Bot 自己的发言」这条要求："
            "记忆里 Bot 的发言以「我：」记录，删掉它模型可能把自己的话记成用户说的。"
        ),
    ),
    PromptSpec(
        key=PROMPT_WEEKLY_SYSTEM,
        title="周度洞察系统提示词",
        group="自我学习",
        default=REFLECTION_SYSTEM,
        hint="留空时沿用反思系统提示词的内置版本。",
    ),
    PromptSpec(
        key=PROMPT_WEEKLY_TEMPLATE,
        title="周度洞察模板",
        group="自我学习",
        default=_WEEKLY_TEMPLATE,
        required=_WEEKLY_REQUIRED,
    ),
)


def reflection_system(overrides: PromptOverrides | None = None) -> str:
    """反思系统提示词：页面优先、内置兜底。"""
    if overrides is None:
        return REFLECTION_SYSTEM
    return overrides.get(PROMPT_REFLECTION_SYSTEM, REFLECTION_SYSTEM)


def weekly_system(overrides: PromptOverrides | None = None) -> str:
    """周度洞察系统提示词：未单独配置时沿用反思系统提示词。"""
    if overrides is None:
        return REFLECTION_SYSTEM
    return overrides.get(PROMPT_WEEKLY_SYSTEM, REFLECTION_SYSTEM)


def build_reflection_prompt(
    transcript: str,
    *,
    max_facts: int,
    previous_block: str = "",
    overrides: PromptOverrides | None = None,
) -> str:
    """渲染反思提示词。``previous_block`` 为空表示没有可续写的旧叙事。"""
    template = _REFLECTION_TEMPLATE
    if overrides is not None:
        template = overrides.get(
            PROMPT_REFLECTION_TEMPLATE, _REFLECTION_TEMPLATE, required=_REFLECTION_REQUIRED
        )
    return render(
        template, transcript=transcript, max_facts=max_facts, previous_block=previous_block
    )


def build_weekly_prompt(
    material: str, *, max_facts: int, overrides: PromptOverrides | None = None
) -> str:
    template = _WEEKLY_TEMPLATE
    if overrides is not None:
        template = overrides.get(
            PROMPT_WEEKLY_TEMPLATE, _WEEKLY_TEMPLATE, required=_WEEKLY_REQUIRED
        )
    return render(template, material=material, max_facts=max_facts)


def _sanitize_tags(raw: Any) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    tags: list[str] = []
    for item in raw:
        text = str(item).strip().lstrip("#")
        if not text:
            continue
        tags.append(truncate(text, _MAX_TAG_LEN))
        if len(tags) >= _MAX_TAGS:
            break
    return tags


def _sanitize_importance(raw: Any, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value <= 0.0 or value > 1.0:
        return default
    return round(value, 3)


def parse_insights(text: str, *, max_facts: int) -> list[dict[str, Any]]:
    """解析模型产出，返回**已通过校验**的条目列表。"""
    entries = extract_json_entries(text)
    if not entries:
        return []

    results: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or KIND_FACT).strip().lower()
        if kind not in _ALLOWED_KINDS:
            kind = KIND_FACT

        # episode 用更长上限；其余维持 400 字保守策略。
        cap = _MAX_EPISODE_CONTENT if kind == KIND_EPISODE else _MAX_CONTENT

        content = str(entry.get("content") or "").strip().replace("\n", " ")
        if len(content) < _MIN_CONTENT:
            continue
        content = truncate(content, cap)

        results.append(
            {
                "content": content,
                "kind": kind,
                "importance": _sanitize_importance(entry.get("importance"), 0.6),
                "tags": _sanitize_tags(entry.get("tags")),
            }
        )
        if len(results) >= max_facts:
            break
    return results


def parse_reflection(text: str, *, max_facts: int) -> dict[str, Any] | None:
    """解析叙事反思的产出，返回 ``{"summary", "importance", "tags", "facts"}``。

    - 新格式：单个 JSON 对象，``summary`` 是第一人称叙事（核心产出），
      ``facts`` 是可选的事实条目数组；summary 过短视为没有写出叙事（留空）。
    - 旧数组格式（只输出条目数组）：兼容处理，summary 留空，整组按 facts 处理。

    完全无法解析时返回 ``None``；解析失败由调用方留痕并按 0 产出处理。
    """
    entries = extract_json_entries(text)
    if not entries:
        return None

    first = entries[0]
    raw_summary = first.get("summary") if isinstance(first, dict) else None
    if not (isinstance(raw_summary, str) and raw_summary.strip()):
        # 旧数组格式：整组视为 facts
        return {
            "summary": "",
            "importance": 0.0,
            "tags": [],
            "facts": parse_insights(text, max_facts=max_facts),
        }

    summary = raw_summary.strip()
    if len(summary) < _MIN_SUMMARY:
        summary = ""
    summary = truncate(summary, _MAX_EPISODE_CONTENT)

    facts: list[dict[str, Any]] = []
    raw_facts = first.get("facts")
    if isinstance(raw_facts, list):
        for entry in raw_facts:
            if not isinstance(entry, dict):
                continue
            kind = str(entry.get("kind") or KIND_FACT).strip().lower()
            if kind not in _ALLOWED_KINDS or kind == KIND_EPISODE:
                # facts 里不再嵌套叙事，叙事只来自 summary
                kind = KIND_FACT
            content = str(entry.get("content") or "").strip().replace("\n", " ")
            if len(content) < _MIN_CONTENT:
                continue
            facts.append(
                {
                    "content": truncate(content, _MAX_CONTENT),
                    "kind": kind,
                    "importance": _sanitize_importance(entry.get("importance"), 0.6),
                    "tags": _sanitize_tags(entry.get("tags")),
                }
            )
            if len(facts) >= max_facts:
                break

    return {
        "summary": summary,
        "importance": _sanitize_importance(first.get("importance"), 0.6),
        "tags": _sanitize_tags(first.get("tags")),
        "facts": facts,
    }


def ensure_kind_valid(kind: str) -> str:
    """对外暴露 kind 归一化（审批通过时复用）。"""
    normalized = (kind or "").strip().lower()
    return normalized if normalized in ALL_KINDS else KIND_INSIGHT
