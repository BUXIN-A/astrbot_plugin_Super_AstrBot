"""记忆领域：模型、配置、生命周期、分层检索与注入。

对外主要出口：

- ``MemoryService``  面向业务域的门面（唯一的调用入口）
- ``MemoryConfig``   强类型配置
- ``MemoryDraft`` / ``MemoryItem``  数据模型
"""

from .agent_tools import AgentMemoryBackend
from .config import (
    INJECTION_DISABLED,
    INJECTION_EXTRA,
    INJECTION_SYSTEM,
    MemoryConfig,
)
from .formatter import build_memory_body, format_search_results
from .lifecycle import MemoryLifecycle
from .models import (
    ALL_KINDS,
    KIND_EPISODE,
    KIND_FACT,
    KIND_INSIGHT,
    KIND_JOURNAL,
    KIND_PREFERENCE,
    SOURCE_AGENT,
    SOURCE_CAPTURE,
    SOURCE_JOURNAL,
    SOURCE_MANUAL,
    SOURCE_REFLECTION,
    SOURCE_WEEKLY,
    STATUS_ACTIVE,
    STATUS_ARCHIVED,
    STATUS_BUFFERED,
    STATUS_FORGOTTEN,
    STATUS_PENDING,
    MemoryDraft,
    MemoryItem,
)
from .retriever import (
    HybridRetriever,
    KeywordRetriever,
    RetrievalConfig,
    RetrievalResult,
    VectorRetriever,
)
from .service import MemoryService

__all__ = [
    "MemoryService",
    "AgentMemoryBackend",
    "MemoryConfig",
    "MemoryLifecycle",
    "MemoryDraft",
    "MemoryItem",
    "HybridRetriever",
    "KeywordRetriever",
    "VectorRetriever",
    "RetrievalConfig",
    "RetrievalResult",
    "build_memory_body",
    "format_search_results",
    "INJECTION_DISABLED",
    "INJECTION_EXTRA",
    "INJECTION_SYSTEM",
    "ALL_KINDS",
    "KIND_EPISODE",
    "KIND_FACT",
    "KIND_INSIGHT",
    "KIND_JOURNAL",
    "KIND_PREFERENCE",
    "SOURCE_CAPTURE",
    "SOURCE_REFLECTION",
    "SOURCE_WEEKLY",
    "SOURCE_JOURNAL",
    "SOURCE_AGENT",
    "SOURCE_MANUAL",
    "STATUS_ACTIVE",
    "STATUS_BUFFERED",
    "STATUS_PENDING",
    "STATUS_ARCHIVED",
    "STATUS_FORGOTTEN",
]
