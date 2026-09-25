"""统一错误类型。

原则（来自分析文档的「容错规范」）：

1. 对外部依赖（LLM、向量库、宿主能力）的失败，**优先在调用处降级**——返回显式的
   降级结果（如 ``InjectResult`` / ``RouteOutcome`` / ``RetrievalResult``）或吞掉并记录，
   只有「调用方必须处理」的编程性错误才抛异常；
2. 任何降级都带 ``reason``，便于日志定位与面板展示。
"""

from __future__ import annotations


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


def safe_detail(exc: BaseException, limit: int = 300) -> str:
    """把异常压成一行可入库/可上报的简短描述。"""
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text

