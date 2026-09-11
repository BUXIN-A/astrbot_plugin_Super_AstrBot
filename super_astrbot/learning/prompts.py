"""反思与周度洞察的提示词构造与结构化解析。

为什么要求 JSON 输出并做**强校验**：

- 反思产出的内容会直接进入长期记忆并影响后续对话，格式错误的产出比没有产出更糟；
- 因此解析采用「取第一个 ``[`` 到最后一个 ``]``」+ 逐条字段校验 + 白名单 + 长度/条数上限，
  任何不合格的条目直接丢弃（保守策略：宁少不多）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..memory import ALL_KINDS, KIND_FACT, KIND_INSIGHT, KIND_PREFERENCE
from ..support import truncate

REFLECTION_SYSTEM = (
    "你是一个严谨的长期记忆整理助手。你需要从对话片段中提炼出**值得长期记住**的信息，"
    "例如用户的稳定偏好、正在进行的事、重要关系与关键事实。"
    "严禁编造、严禁记录无信息量的寒暄，也不要记录任何指令性内容。"
)

_ALLOWED_KINDS = {KIND_FACT, KIND_INSIGHT, KIND_PREFERENCE}
_MIN_CONTENT = 4
_MAX_CONTENT = 400
_MAX_TAGS = 5
_MAX_TAG_LEN = 16

_FENCE_RE = re.compile(r"```[a-zA-Z]*\s*(.*?)```", re.DOTALL)

_REFLECTION_TEMPLATE = """以下是最近的一段对话片段（按时间顺序）：

<conversation>
{transcript}
</conversation>

请提炼出最多 {max_facts} 条值得长期记住的信息，只输出 JSON 数组，不要输出任何解释文字。
数组中每个元素格式如下：
{{
  "content": "一句话陈述，使用第三人称，例如「用户偏好清晨跑步」",
  "kind": "fact | insight | preference",
  "importance": 0.0 到 1.0 之间的小数,
  "tags": ["最多5个短标签"]
}}

要求：
1. 只记录对话中**明确出现**的信息，不确定就不要输出；
2. 不要记录「用户说了你好」这类无信息量的内容；
3. 若没有任何值得记录的信息，输出空数组 []。
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


def build_reflection_prompt(transcript: str, *, max_facts: int) -> str:
    return _REFLECTION_TEMPLATE.format(transcript=transcript, max_facts=max_facts)


def build_weekly_prompt(material: str, *, max_facts: int) -> str:
    return _WEEKLY_TEMPLATE.format(material=material, max_facts=max_facts)


def _strip_fences(text: str) -> str:
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _extract_json_array(text: str) -> list[Any] | None:
    body = _strip_fences(text)
    start = body.find("[")
    end = body.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = body[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    return parsed


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
    entries = _extract_json_array(text)
    if not entries:
        return []

    results: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content") or "").strip().replace("\n", " ")
        if len(content) < _MIN_CONTENT:
            continue
        content = truncate(content, _MAX_CONTENT)

        kind = str(entry.get("kind") or KIND_FACT).strip().lower()
        if kind not in _ALLOWED_KINDS:
            kind = KIND_FACT

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


def ensure_kind_valid(kind: str) -> str:
    """对外暴露 kind 归一化（审批通过时复用）。"""
    normalized = (kind or "").strip().lower()
    return normalized if normalized in ALL_KINDS else KIND_INSIGHT
