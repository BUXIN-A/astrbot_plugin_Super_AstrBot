"""提示词覆盖解析：配置优先、内置默认兜底。

为什么要做占位符校验而不是「用户填什么就用什么」：

- 提示词模板里的 ``{transcript}`` / ``{max_facts}`` 是结构化产出的必要条件，
  缺了它模型拿不到素材、或拿不到条数上限，产出会直接不可用；
- 而用户手写模板很容易漏占位符或写错花括号。这里宁可**回退到内置默认**，
  也不把坏模板送进模型——坏模板比没有模板更糟。

因此本模块的两条硬性约定：

1. ``get()`` 只在模板「非空且包含全部必填占位符」时才采用它，否则回退默认，
   并把被拒绝的键记入 ``PromptOverrides.REJECTED``（启动时一次性告警）；
2. ``render()`` 永不抛异常：即使模板花括号不配对，也原样返回而不会打断对话。
"""

from __future__ import annotations

import string
from typing import Any, Mapping, Sequence

from ..spec.capabilities import get_path

PROMPT_PREFIX = "prompts"


class _SafeValues(dict):
    """``str.format_map`` 的兜底映射：未知占位符原样保留，而不是抛 ``KeyError``。"""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _placeholders(template: str) -> set[str]:
    """收集模板中的命名占位符；花括号不配对时返回空集合（视为无效）。"""
    try:
        return {field for _, field, _, _ in string.Formatter().parse(template) if field}
    except ValueError:
        return set()


def render(template: str, **values: Any) -> str:
    """安全渲染：任何格式错误都退化为「原样返回」，绝不抛异常。"""
    try:
        return template.format_map(_SafeValues(values))
    except (KeyError, IndexError, ValueError):
        return template


class PromptOverrides:
    """从配置中读取用户自定义提示词，校验后在渲染时覆盖内置默认。"""

    REJECTED: set[str] = set()
    """被拒绝的自定义模板键（缺占位符 / 花括号非法），供启动日志一次性提示。"""

    def __init__(
        self, config: Mapping[str, Any] | None = None, *, prefix: str = PROMPT_PREFIX
    ) -> None:
        self._config: Mapping[str, Any] = config or {}
        self._prefix = prefix
        self._cache: dict[str, str] = {}

    def get(self, key: str, default: str, *, required: Sequence[str] = ()) -> str:
        """取最终使用的模板：配置值合法则用它，否则用 ``default``。"""
        if key in self._cache:
            return self._cache[key]

        candidate = str(get_path(self._config, f"{self._prefix}.{key}", "") or "").strip()
        resolved = default
        if candidate:
            placeholders = _placeholders(candidate)
            missing = [name for name in required if name not in placeholders]
            if missing:
                self.REJECTED.add(key)
            else:
                resolved = candidate
        self._cache[key] = resolved
        return resolved

    def is_custom(self, key: str, default: str, *, required: Sequence[str] = ()) -> bool:
        """用户模板是否真的生效（供面板/命令展示来源）。"""
        return self.get(key, default, required=required) != default


__all__ = ["PROMPT_PREFIX", "PromptOverrides", "render"]
