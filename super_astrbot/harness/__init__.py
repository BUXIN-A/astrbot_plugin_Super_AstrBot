"""Harness 层的装配入口。

对外只暴露 ``Harness`` 容器与 ``create_harness`` 工厂；业务层通过容器拿到
``host`` / ``llm`` / ``embedding`` / ``injector`` 四个协议实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from . import astrbot_compat as compat
from .astrbot_event import is_event_stopped, to_event_view
from .astrbot_group import (
    GROUP_FILTER_AVAILABLE,
    GroupMessageFilter,
    apply_group_decision,
    release_group_gate,
    set_group_gate,
    to_group_signals,
)
from .astrbot_host import AstrBotHost
from .astrbot_llm import (
    MEMORY_BLOCK_END,
    MEMORY_BLOCK_START,
    PERSONA_BLOCK_END,
    PERSONA_BLOCK_START,
    AstrBotEmbeddingGateway,
    AstrBotInjector,
    AstrBotLlmGateway,
)
from .astrbot_rerank import AstrBotRerankGateway
from .protocols import (
    BudgetGuard,
    ChatMessage,
    EmbeddingGateway,
    EventView,
    GroupDecision,
    GroupSignals,
    Host,
    Injector,
    InjectResult,
    LlmGateway,
    LlmResult,
    LoggerLike,
    MemoryToolBackend,
    ProviderInfo,
    RerankGateway,
    RerankHit,
    TokenUsage,
)
from .schema_options import clear_options, inject_string_options
from .tools import (
    MEMORY_SEARCH_TOOL,
    MEMORY_TOOL_NAMES,
    MEMORY_WRITE_TOOL,
    create_memory_tools,
    register_memory_tools,
    register_tools,
    unregister_tools,
)

__all__ = [
    "Harness",
    "create_harness",
    "AstrBotHost",
    "AstrBotLlmGateway",
    "AstrBotEmbeddingGateway",
    "AstrBotRerankGateway",
    "AstrBotInjector",
    "MEMORY_BLOCK_START",
    "MEMORY_BLOCK_END",
    "PERSONA_BLOCK_START",
    "PERSONA_BLOCK_END",
    "MEMORY_SEARCH_TOOL",
    "MEMORY_WRITE_TOOL",
    "MEMORY_TOOL_NAMES",
    "create_memory_tools",
    "register_memory_tools",
    "register_tools",
    "unregister_tools",
    "to_event_view",
    "is_event_stopped",
    "GROUP_FILTER_AVAILABLE",
    "GroupMessageFilter",
    "set_group_gate",
    "release_group_gate",
    "to_group_signals",
    "apply_group_decision",
    "compat",
    "inject_string_options",
    "clear_options",
    "Host",
    "LlmGateway",
    "EmbeddingGateway",
    "RerankGateway",
    "RerankHit",
    "Injector",
    "BudgetGuard",
    "MemoryToolBackend",
    "LoggerLike",
    "EventView",
    "GroupSignals",
    "GroupDecision",
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
    rerank: AstrBotRerankGateway
    injector: AstrBotInjector
    persona_injector: AstrBotInjector
    """拟人化学习专用注入器：使用独立边界标记，与记忆注入互不覆盖。"""

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
    rerank_provider_id: str = "",
    llm_observer: Any | None = None,
) -> Harness:
    """构建 Harness 容器。"""
    host = AstrBotHost(star, context, config)
    llm = AstrBotLlmGateway(
        context, host, timeout=llm_timeout, budget=budget, observer=llm_observer
    )
    embedding = AstrBotEmbeddingGateway(context, host, provider_id=embedding_provider_id)
    rerank = AstrBotRerankGateway(context, host, provider_id=rerank_provider_id)
    injector = AstrBotInjector(host)
    persona_injector = AstrBotInjector(
        host, block_start=PERSONA_BLOCK_START, block_end=PERSONA_BLOCK_END
    )
    return Harness(
        host=host,
        llm=llm,
        embedding=embedding,
        rerank=rerank,
        injector=injector,
        persona_injector=persona_injector,
    )
