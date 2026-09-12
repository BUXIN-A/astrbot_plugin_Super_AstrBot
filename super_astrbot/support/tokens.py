"""token 估算（纯启发式、零依赖）。

为什么不引入 ``tiktoken``：不同 Provider 的分词器不同，精确计数需要按模型选择编码，
而这里只需要「量级判断」来决定是否触发上下文治理，±10% 误差无影响；
零依赖也让插件在任何环境都能工作（与持久层选择标准库 ``sqlite3`` 同一取舍）。

经验系数（按常见中英模型的量级取整）：

- CJK 文本 ≈ 1 字符 / token；
- 其余非空白字符 ≈ 4 字符 / token；
- 每条消息固定开销 ≈ 4 token（role 与分隔符）；
- 图片/音频按固定值计入（实际开销由 Provider 决定，这里只求量级正确）。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

MESSAGE_OVERHEAD = 4
IMAGE_TOKENS = 512
AUDIO_TOKENS = 512

_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uff00-\uffef]")
_NON_SPACE_RE = re.compile(r"\S")
_IMAGE_PART_TYPES = frozenset({"image_url", "image", "input_image"})
_AUDIO_PART_TYPES = frozenset({"audio_url", "input_audio"})


def estimate_tokens(text: str) -> int:
    """估算一段文本的 token 数。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    others = len(_NON_SPACE_RE.findall(text)) - cjk
    return cjk + (others + 3) // 4


def _part_tokens(part: Any) -> int:
    if isinstance(part, str):
        return estimate_tokens(part)
    if not isinstance(part, dict):
        return 0
    part_type = str(part.get("type") or "")
    if part_type in _IMAGE_PART_TYPES:
        return IMAGE_TOKENS
    if part_type in _AUDIO_PART_TYPES:
        return AUDIO_TOKENS
    text = part.get("text")
    return estimate_tokens(str(text)) if text else 0


def estimate_message_tokens(message: Any) -> int:
    """估算单条 OpenAI 风格消息的 token 数（含 tool_calls）。"""
    if not isinstance(message, dict):
        return MESSAGE_OVERHEAD

    total = MESSAGE_OVERHEAD
    content = message.get("content")
    if isinstance(content, str):
        total += estimate_tokens(content)
    elif isinstance(content, list):
        total += sum(_part_tokens(part) for part in content)

    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        if not isinstance(function, dict):
            continue
        total += estimate_tokens(str(function.get("name") or ""))
        total += estimate_tokens(str(function.get("arguments") or ""))
    return total


def estimate_messages_tokens(messages: Sequence[Any] | None) -> int:
    """估算一组消息的总 token 数。"""
    if not messages:
        return 0
    return sum(estimate_message_tokens(message) for message in messages)
