"""上下文治理领域（P2）。

对外出口：

- ``ContextGovernor``  请求级上下文治理：token 估算 → 工具/图片占位 → 历史摘要
- ``ContextConfig``    治理参数

本域只消费 ``ProviderRequest.contexts``（普通 ``list[dict]``）与 ``LlmGateway`` 协议，
不 import ``astrbot``，也不改写持久化对话历史。
"""

from .config import MAX_SUMMARY_CHARS, ContextConfig
from .governor import (
    IMAGE_PLACEHOLDER,
    SUMMARY_HEADER,
    TOOL_PLACEHOLDER,
    ContextGovernor,
    GovernanceResult,
)

__all__ = [
    "ContextGovernor",
    "ContextConfig",
    "GovernanceResult",
    "SUMMARY_HEADER",
    "TOOL_PLACEHOLDER",
    "IMAGE_PLACEHOLDER",
    "MAX_SUMMARY_CHARS",
]
