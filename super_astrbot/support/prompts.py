"""提示词覆盖解析：页面优先、内置默认兜底。

为什么要做占位符校验而不是「用户填什么就用什么」：

- 提示词模板里的 ``{transcript}`` / ``{max_facts}`` 是结构化产出的必要条件，
  缺了它模型拿不到素材、或拿不到条数上限，产出会直接不可用；
- 而用户手写模板很容易漏占位符或写错花括号。这里宁可**回退到内置默认**，
  也不把坏模板送进模型——坏模板比没有模板更糟。

因此本模块的三条硬性约定：

1. ``get()`` 只在模板「非空且包含全部必填占位符」时才采用它，否则回退默认，
   并把被拒绝的键记入 ``PromptOverrides.REJECTED``（启动时一次性告警）；
2. ``render()`` 永不抛异常：即使模板花括号不配对，也原样返回而不会打断对话；
3. 覆盖值存放在插件数据目录的 ``prompts.json``（``PromptStore``），而不是
   AstrBot 插件配置——配置项受 ``_conf_schema.json`` 约束，未声明的键会在重载时
   被框架当脏键清理，且长文本在配置页里也难维护。
"""

from __future__ import annotations

import json
import string
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..spec.capabilities import get_path

PROMPT_PREFIX = "prompts"


@dataclass(frozen=True)
class PromptSpec:
    """一项可定制的提示词声明（供面板渲染与保存校验）。"""

    key: str
    """覆盖键；与各域 ``prompts.py`` 使用的一致，最终落在 ``prompts.<key>``。"""

    title: str
    group: str
    default: str
    """内置默认文本：面板用它在留空时回填，也是「重置为默认」的目标值。"""

    required: tuple[str, ...] = ()
    """必填占位符；保存时缺失即拒绝（``{}`` 内的名字）。"""

    hint: str = ""


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


def missing_placeholders(template: str, required: Sequence[str]) -> list[str]:
    """列出模板中缺失的必填占位符（花括号非法时视为全部缺失）。"""
    placeholders = _placeholders(template)
    return [name for name in required if name not in placeholders]


def render(template: str, **values: Any) -> str:
    """安全渲染：任何格式错误都退化为「原样返回」，绝不抛异常。"""
    try:
        return template.format_map(_SafeValues(values))
    except (KeyError, IndexError, ValueError):
        return template


class PromptOverrides:
    """从覆盖映射中读取用户自定义提示词，校验后在渲染时覆盖内置默认。"""

    REJECTED: set[str] = set()
    """被拒绝的自定义模板键（缺占位符 / 花括号非法），供启动日志一次性提示。"""

    def __init__(
        self, config: Mapping[str, Any] | None = None, *, prefix: str = PROMPT_PREFIX
    ) -> None:
        self._config: Mapping[str, Any] = config or {}
        self._prefix = prefix
        self._cache: dict[str, str] = {}

    def bind(self, config: Mapping[str, Any] | None) -> None:
        """重新指向覆盖映射并清空缓存。

        提示词从面板热更新时必须走这里：服务持有的是同一个 ``PromptOverrides``
        实例，就地换源才能让所有消费方立刻看到新值（换实例则会留下旧引用）。
        """
        self._config = config or {}
        self._cache.clear()

    def get(self, key: str, default: str, *, required: Sequence[str] = ()) -> str:
        """取最终使用的模板：覆盖值合法则用它，否则用 ``default``。"""
        if key in self._cache:
            return self._cache[key]

        candidate = str(get_path(self._config, f"{self._prefix}.{key}", "") or "").strip()
        resolved = default
        if candidate:
            if missing_placeholders(candidate, required):
                self.REJECTED.add(key)
            else:
                resolved = candidate
        self._cache[key] = resolved
        return resolved


class PromptOverlay(Mapping[str, Any]):
    """把提示词覆盖叠加到基础配置：其余键透传，``prompts`` 一律用覆盖值。

    ``prompts`` 即使为空也由覆盖值提供，从而彻底屏蔽插件配置页里可能残留的旧值——
    提示词已迁移到面板，配置页不再声明这些键。
    """

    def __init__(self, base: Mapping[str, Any], values: Mapping[str, str]) -> None:
        self._base = base
        self._prompts: dict[str, str] = {
            str(k): str(v) for k, v in values.items() if str(v).strip()
        }

    def __getitem__(self, key: str) -> Any:
        if key == PROMPT_PREFIX:
            return self._prompts
        return self._base[key]

    def __iter__(self) -> Iterator[str]:
        yield from self._base
        if PROMPT_PREFIX not in self._base:
            yield PROMPT_PREFIX

    def __len__(self) -> int:
        return len(self._base) + (0 if PROMPT_PREFIX in self._base else 1)


class PromptStore:
    """提示词覆盖的持久化：插件数据目录内的一个 JSON 文件。

    写入采用「临时文件 + 原子替换」，避免进程在写一半时被中断而留下半个文件；
    任何读写失败都只返回空/``False``，由调用方决定如何提示，绝不抛给对话链路。
    """

    FILENAME = "prompts.json"

    def __init__(self, path: Path | None) -> None:
        self._path = path

    @property
    def available(self) -> bool:
        """数据目录不可用时为 ``False``：面板仍可查看，但保存会失败。"""
        return self._path is not None

    def load(self) -> dict[str, str]:
        """读取覆盖值；文件缺失或损坏时返回空字典。"""
        if self._path is None or not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items() if str(value).strip()}

    def save(self, values: Mapping[str, str]) -> bool:
        """整体覆写覆盖值；返回是否写入成功。"""
        if self._path is None:
            return False
        payload = {str(k): str(v) for k, v in values.items() if str(v).strip()}
        tmp = self._path.with_name(self._path.name + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            return False
        return True


__all__ = [
    "PROMPT_PREFIX",
    "PromptOverlay",
    "PromptOverrides",
    "PromptSpec",
    "PromptStore",
    "missing_placeholders",
    "render",
]
