"""规格与契约层。

本包只声明「什么是合法的」「能力如何组合」「失败如何表达」，
不包含任何业务实现，也不依赖 AstrBot。
"""

from .capabilities import (
    CAPABILITIES,
    Capability,
    capability,
    get_path,
    resolve_capabilities,
)
from .errors import (
    BudgetExhaustedError,
    ConfigError,
    Degraded,
    LlmError,
    StorageError,
    SuperAstrBotError,
)

__all__ = [
    "CAPABILITIES",
    "Capability",
    "capability",
    "get_path",
    "resolve_capabilities",
    "SuperAstrBotError",
    "ConfigError",
    "StorageError",
    "LlmError",
    "BudgetExhaustedError",
    "Degraded",
]
