"""Harness 层的装配入口。

对外只暴露 ``Harness`` 容器与 ``create_harness`` 工厂；业务层通过容器拿到
``host`` / ``llm`` / ``embedding`` / ``injector`` 四个协议实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from . import astrbot_compat as compat
from .astrbot_event import is_event_stopped, to_event_view
from .astrbot_host import AstrBotHost
from .astrbot_llm import (
    MEMORY_BLOCK_END,
    MEMORY_BLOCK_START,
    AstrBotEmbeddingGateway,
    AstrBotInjector,
    AstrBotLlmGateway,
)
from .protocols import (
    BudgetGuard,
    ChatMessage,
    EmbeddingGateway,
    EventView,
    Host,
    Injector,
    InjectResult,
    LlmGateway,
    LlmResult,
    LoggerLike,
    ProviderInfo,
    TokenUsage,
)
from .schema_options import clear_options, inject_string_options

__all__ = [
    "Harness",
    "create_harness",
    "AstrBotHost",
    "AstrBotLlmGateway",
    "AstrBotEmbeddingGateway",
    "AstrBotInjector",
    "MEMORY_BLOCK_START",
    "MEMORY_BLOCK_END",
    "to_event_view",
    "is_event_stopped",
    "compat",
    "inject_string_options",
    "clear_options",
    "Host",
    "LlmGateway",
    "EmbeddingGateway",
    "Injector",
    "BudgetGuard",
    "LoggerLike",
    "EventView",
    "ChatMessage",
    "LlmResult",
    "TokenUsage",
    "ProviderInfo",
    "InjectResult",
]


@dataclass
class Harness:
    """宿主适配容器。"""

    host: AstrBotHost
    llm: AstrBotLlmGateway
    embedding: AstrBotEmbeddingGateway
    injector: AstrBotInjector

    def describe(self) -> str:
        """一行描述，用于启动日志。"""
        parts = [compat.describe()]
        parts.append(
            f"向量能力：{'可用' if self.embedding.available else '不可用（已降级为关键词检索）'}"
        )
        try:
            parts.append(f"可用对话模型数：{len(self.llm.list_providers())}")
        except Exception:  # noqa: BLE001
            parts.append("可用对话模型数：未知")
        return "；".join(parts)


def create_harness(
    star: Any,
    context: Any,
    config: Mapping[str, Any] | None = None,
    *,
    budget: Any | None = None,
    llm_timeout: float = 45.0,
    embedding_provider_id: str = "",
) -> Harness:
    """构建 Harness 容器。"""
    host = AstrBotHost(star, context, config)
    llm = AstrBotLlmGateway(context, host, timeout=llm_timeout, budget=budget)
    embedding = AstrBotEmbeddingGateway(context, host, provider_id=embedding_provider_id)
    injector = AstrBotInjector(host)
    return Harness(host=host, llm=llm, embedding=embedding, injector=injector)
