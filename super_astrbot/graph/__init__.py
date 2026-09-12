"""记忆知识图谱业务域。

只导出图谱自身的配置、结果与服务。检索路 ``GraphRetriever`` 属于 ``memory`` 域，
由调用方直接从 ``super_astrbot.memory.retriever.graph`` 导入，避免本域反向依赖 memory。
"""

from .config import (
    DEFAULT_GRAPH_SYSTEM,
    DEFAULT_GRAPH_TEMPLATE,
    PROMPT_GRAPH_SYSTEM,
    PROMPT_GRAPH_TEMPLATE,
    GraphConfig,
)
from .service import GraphOutcome, GraphService

__all__ = [
    "GraphConfig",
    "GraphOutcome",
    "GraphService",
    "DEFAULT_GRAPH_SYSTEM",
    "DEFAULT_GRAPH_TEMPLATE",
    "PROMPT_GRAPH_SYSTEM",
    "PROMPT_GRAPH_TEMPLATE",
]
