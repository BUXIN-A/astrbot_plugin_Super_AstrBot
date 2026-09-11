"""统一错误类型与结构化降级结果。

原则（来自分析文档的「容错规范」）：

1. 对外部依赖（LLM、向量库、宿主能力）的失败，**优先返回结构化降级结果**而非抛异常；
2. 只有「调用方必须处理」的编程性错误才抛异常；
3. 任何降级都带 ``reason``，便于日志定位与面板展示。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class SuperAstrBotError(Exception):
    """本插件所有显式异常的基类。"""


class ConfigError(SuperAstrBotError):
    """配置非法且无法安全修正。"""


class StorageError(SuperAstrBotError):
    """持久层不可恢复的错误。"""


class LlmError(SuperAstrBotError):
    """LLM 调用失败（网络、鉴权、空输出等）。"""


class BudgetExhaustedError(LlmError):
    """辅助 LLM 调用超出预算（每日上限或并发上限）。"""


class RuntimeNotReadyError(SuperAstrBotError):
    """运行时尚未完成初始化（例如插件刚加载、数据库未就绪）。"""


@dataclass(frozen=True)
class Degraded(Generic[T]):
    """结构化降级结果。

    ``ok=True`` 时 ``value`` 有效；``ok=False`` 时 ``value`` 为兜底值，
    ``reason``/``detail`` 说明降级原因。
    """

    ok: bool
    value: T | None = None
    reason: str = ""
    detail: str = ""

    @classmethod
    def success(cls, value: T) -> "Degraded[T]":
        return cls(ok=True, value=value)

    @classmethod
    def fallback(cls, reason: str, value: T | None = None, detail: str = "") -> "Degraded[T]":
        return cls(ok=False, value=value, reason=reason, detail=detail)

    def __bool__(self) -> bool:  # 便于 `if result:` 快速判断
        return self.ok


def safe_detail(exc: BaseException, limit: int = 300) -> str:
    """把异常压成一行可入库/可上报的简短描述。"""
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def unwrap(value: Any, default: Any = None) -> Any:
    """从 ``Degraded`` 中取值，非 ``Degraded`` 时原样返回。"""
    if isinstance(value, Degraded):
        return value.value if value.value is not None else default
    return value
