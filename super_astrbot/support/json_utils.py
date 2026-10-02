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

_THINK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.DOTALL | re.IGNORECASE)

JsonKind = Literal["object", "array", "any"]


def strip_fences(text: str) -> str:
    """剥掉 Markdown 代码围栏；没有完整围栏时原样返回（两侧仅去空白）。"""
    body = str(text or "").strip()
    match = _FENCE_RE.search(body)
    if match:
        return match.group(1).strip()
    return body


def _strip_think(text: str) -> str:
    """去掉 <think>...</think> 思考块（部分模型会把思维链一起吐出来）。"""
    return _THINK_RE.sub("", text or "")


def _repair_json(body: str) -> str:
    """常见畸形 JSON 的确定性修复：中文引号、尾随逗号。"""
    s = body or ""
    s = s.replace("\u201c", '"').replace("\u201d", '"')  # 中文双引号 -> "
    s = s.replace("\u2018", "'").replace("\u2019", "'")  # 中文单引号 -> '
    s = re.sub(r",\s*([}\]])", r"\1", s)  # ,} / ,] 去掉尾随逗号
    return s


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
    body = _repair_json(_strip_think(strip_fences(text)))
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


def extract_json_entries(text: str) -> list[dict[str, Any]]:
    """尽力从模型输出里取出一组对象；失败返回空列表，绝不抛异常。

    依次尝试：① JSON 数组 -> ② JSONL（一行一个对象）-> ③ 单个 JSON 对象。
    反思/周度洞察/整合要求模型输出对象集合，而实际输出常带围栏、思考块、
    中文引号或逐行对象——本入口把这几种形状都收敛成一个出口。

    注意①不能短路：要求输出对象数组时，模型常输出**单个对象**，而
    「最外层括号」启发式会命中对象内部的数组字段（如 ``tags: [...]``）
    并返回一个非对象列表——此时必须继续降级尝试，否则拿不到任何条目。
    """
    if not text:
        return []

    parsed = extract_json(text, kind="array")
    if isinstance(parsed, list):
        entries = [item for item in parsed if isinstance(item, dict)]
        if entries:
            return entries

    cleaned = _repair_json(_strip_think(strip_fences(text)))
    line_entries: list[dict[str, Any]] = []
    for line in cleaned.splitlines():
        line = line.strip().rstrip(",")
        if not line or line in ("[", "]"):
            continue
        try:
            obj = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(obj, dict):
            line_entries.append(obj)
    if line_entries:
        return line_entries

    single = extract_json(text, kind="object")
    return [single] if isinstance(single, dict) else []


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


__all__ = ["JsonKind", "extract_json", "extract_json_entries", "parse_payload", "strip_fences"]
