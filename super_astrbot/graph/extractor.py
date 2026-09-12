"""实体与关系抽取（纯函数、无状态）。

设计取舍：把「抽取」做成纯函数而不是服务方法，是为了让最核心、最易出错的部分
（名称归一化、模型 JSON 解析与校验）能被独立测试，也不掺入任何持久化副作用。
模型产出永远不可信：任何字段缺失、类型不符、超长或 JSON 破损都要降级为空，
绝不能把坏数据写进图谱，更不能把异常抛给调用方。
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..support import PromptOverrides, render, tokenize, truncate
from .config import (
    DEFAULT_GRAPH_SYSTEM,
    DEFAULT_GRAPH_TEMPLATE,
    PROMPT_GRAPH_SYSTEM,
    PROMPT_GRAPH_TEMPLATE,
)

ENTITY_TYPES = ("person", "place", "org", "event", "concept", "thing")
"""实体类型白名单；不在其中的一律归为 ``concept``。"""

MAX_CANONICAL_CHARS = 32
"""规范化名称的最大长度，超出即截断（作为唯一键需可控）。"""

_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*")
_ASCII_RE = re.compile(r"^[\x00-\x7f]+$")
_EDGE_CHARS = " \t\r\n，。！？、；：,.!?;:\"'“”‘’（）()【】[]{}<>《》·…—～-_/\\|*#@$%^&+="
_DIGIT_RE = re.compile(r"^\d+$")


def canonicalize(name: str) -> str:
    """归一化实体名：去首尾标点与空白、压缩内部空白、ASCII 转小写、超长截断。"""
    if not name:
        return ""
    text = re.sub(r"\s+", " ", str(name)).strip()
    text = text.strip(_EDGE_CHARS)
    if not text:
        return ""
    text = text.lower()
    return text[:MAX_CANONICAL_CHARS]


def deterministic_extract(
    content: str,
    *,
    min_chars: int,
    max_chars: int,
    limit: int,
) -> tuple[list[str], list[tuple[str, str, str]]]:
    """零成本抽取：用分词结果作为实体候选，相邻候选配对为「共现」关系。

    只取相邻对（``i`` 与 ``i+1``）而不是两两组合，是为了保留文本中的位置相关性，
    避免长文本里两个无关词被强行连边，也把关系数量控制在实体数量级。
    """
    tokens = tokenize(content, max_tokens=max(limit * 4, 32))
    entities: list[str] = []
    for token in tokens:
        if len(token) < min_chars or len(token) > max_chars:
            continue
        if _DIGIT_RE.match(token):
            continue
        if _ASCII_RE.match(token) and len(token) > 6:
            continue
        entities.append(token)
        if len(entities) >= limit:
            break

    relations: list[tuple[str, str, str]] = []
    for index in range(len(entities) - 1):
        relations.append((entities[index], "共现", entities[index + 1]))
        if len(relations) >= limit:
            break
    return entities, relations


def parse_graph_json(
    text: str,
    *,
    max_entities: int,
    max_relations: int,
) -> tuple[list[dict], list[dict]]:
    """解析模型产出；任何失败都返回空而不抛异常。"""
    payload = _extract_json(text)
    if not isinstance(payload, dict):
        return [], []
    entities = _parse_entities(payload.get("entities"), max_entities)
    relations = _parse_relations(payload.get("relations"), max_relations)
    return entities, relations


def build_graph_prompt(
    content: str,
    *,
    max_entities: int,
    max_relations: int,
    overrides: PromptOverrides | None = None,
) -> str:
    """用（可覆盖的）模板渲染实体抽取提示词。"""
    template = DEFAULT_GRAPH_TEMPLATE
    if overrides is not None:
        template = overrides.get(
            PROMPT_GRAPH_TEMPLATE,
            DEFAULT_GRAPH_TEMPLATE,
            required=("content", "max_entities", "max_relations"),
        )
    return render(
        template,
        content=truncate(content, 1200),
        max_entities=max_entities,
        max_relations=max_relations,
    )


def graph_system_prompt(overrides: PromptOverrides | None = None) -> str:
    """取系统提示词（可被用户覆盖）。"""
    if overrides is None:
        return DEFAULT_GRAPH_SYSTEM
    return overrides.get(PROMPT_GRAPH_SYSTEM, DEFAULT_GRAPH_SYSTEM)


def _extract_json(text: str) -> Any:
    if not text:
        return None
    cleaned = _FENCE_RE.sub(" ", text)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except (TypeError, ValueError):
        return None


def _parse_entities(raw: Any, limit: int) -> list[dict]:
    if not isinstance(raw, list) or limit <= 0:
        return []
    result: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        name = name.strip()
        if not name or len(name) > MAX_CANONICAL_CHARS:
            continue
        entity_type = item.get("type")
        if not isinstance(entity_type, str) or entity_type not in ENTITY_TYPES:
            entity_type = "concept"
        result.append({"name": name, "type": entity_type})
        if len(result) >= limit:
            break
    return result


def _parse_relations(raw: Any, limit: int) -> list[dict]:
    if not isinstance(raw, list) or limit <= 0:
        return []
    result: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        subject = item.get("subject")
        predicate = item.get("predicate")
        object_ = item.get("object")
        if not (_is_text(subject) and _is_text(predicate) and _is_text(object_)):
            continue
        subject, predicate, object_ = subject.strip(), predicate.strip(), object_.strip()
        if len(subject) > 32 or len(predicate) > 16 or len(object_) > 32:
            continue
        result.append({"subject": subject, "predicate": predicate, "object": object_})
        if len(result) >= limit:
            break
    return result


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


__all__ = [
    "ENTITY_TYPES",
    "canonicalize",
    "deterministic_extract",
    "parse_graph_json",
    "build_graph_prompt",
    "graph_system_prompt",
]
