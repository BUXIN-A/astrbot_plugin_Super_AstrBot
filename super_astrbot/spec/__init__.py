"""规格与契约层。

本包只声明「什么是合法的」「能力如何组合」「失败如何表达」，
不包含任何业务实现，也不依赖 AstrBot。
"""

from .capabilities import (
    CAPABILITIES,
    Capability,
    as_bool,
    as_float,
    as_int,
    as_str,
    capability,
    get_path,
    resolve_capabilities,
    set_path,
)
from .entry_types import (
    DEFAULT_ENTRY_TYPE,
    ENTRY_TYPE_ALIASES,
    ENTRY_TYPE_DIARY,
    ENTRY_TYPE_ESSAY,
    ENTRY_TYPE_LABELS,
    ENTRY_TYPE_WEEKLY,
    ENTRY_TYPES,
    TITLE_TIME_FORMAT,
    default_title,
    entry_type_label,
    is_entry_type,
    normalize_entry_type,
    resolve_title,
)
from .errors import (
    BudgetExhaustedError,
    ConfigError,
    LlmError,
    StorageError,
    SuperAstrBotError,
)

__all__ = [
    "CAPABILITIES",
    "Capability",
    "capability",
    "get_path",
    "set_path",
    "as_bool",
    "as_int",
    "as_float",
    "as_str",
    "resolve_capabilities",
    "SuperAstrBotError",
    "ConfigError",
    "StorageError",
    "LlmError",
    "BudgetExhaustedError",
    "DEFAULT_ENTRY_TYPE",
    "ENTRY_TYPE_ALIASES",
    "ENTRY_TYPE_DIARY",
    "ENTRY_TYPE_ESSAY",
    "ENTRY_TYPE_LABELS",
    "ENTRY_TYPE_WEEKLY",
    "ENTRY_TYPES",
    "TITLE_TIME_FORMAT",
    "default_title",
    "entry_type_label",
    "is_entry_type",
    "normalize_entry_type",
    "resolve_title",
]
