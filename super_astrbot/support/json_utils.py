"""JSON 容错解析工具。

两类场景共用一个出口：

- **模型输出**：经常把 JSON 包在 Markdown 围栏里，或在前后附带解释性文字，
  需要「剥围栏 + 截取最外层括号」；各业务域原本各写一套，容错口径互不一致。
- **待审记录的 payload**：数据库里存的是 JSON 文本，读取时需要同样的容错。

约定：**任何失败都返回 None / 空值，绝不抛异常**——解析失败由调用方决定降级方式。
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Mapping

_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*(.*?)```", re.DOTALL)

JsonKind = Literal["object", "array", "any"]


def strip_fences(text: str) -> str:
    """剥掉 Markdown 代码围栏；没有完整围栏时原样返回（两侧仅去空白）。"""
    body = str(text or "").strip()
    match = _FENCE_RE.search(body)
    if match:
        return match.group(1).strip()
    return body


def extract_json(text: str, *, kind: JsonKind = "object") -> Any:
    """截取最外层括号并解析为 JSON。

    Args:
        text: 模型原始输出。
        kind: ``object`` 只接受 ``{...}``；``array`` 只接受 ``[...]``；
            ``any`` 依次尝试对象与数组。

    Returns:
        解析结果；失败、类型不符或为空时返回 ``None``。
    """
    if not text:
        return None
    body = strip_fences(text)
    pairs: tuple[tuple[str, str], ...]
    if kind == "array":
        pairs = (("[", "]"),)
    elif kind == "object":
        pairs = (("{", "}"),)
    else:
        pairs = (("{", "}"), ("[", "]"))

    for opener, closer in pairs:
        start = body.find(opener)
        end = body.rfind(closer)
        if start == -1 or end <= start:
            continue
        try:
            parsed = json.loads(body[start : end + 1])
        except (TypeError, ValueError):
            continue
        if kind == "array" and not isinstance(parsed, list):
            continue
        if kind == "object" and not isinstance(parsed, dict):
            continue
        return parsed
    return None


def parse_payload(raw: Any) -> dict[str, Any] | None:
    """把待审记录的 ``payload`` 解析为字典；失败返回 ``None``（不抛异常）。

    已是字典（例如直接从内存传入）时原样复制；空值视为空字典。
    """
    if isinstance(raw, Mapping):
        return dict(raw)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


__all__ = ["JsonKind", "extract_json", "parse_payload", "strip_fences"]
