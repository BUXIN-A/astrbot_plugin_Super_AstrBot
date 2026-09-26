"""现实桥领域（现实记忆：周记 / 日记 / 随笔）。

类型词表与标题规则定义在 ``spec.entry_types``（存储层也要用，放在这里会形成包导入环），
本包按领域语义重新导出，业务代码从 ``super_astrbot.journal`` 取用即可。
"""

from ..spec.entry_types import (
    DEFAULT_ENTRY_TYPE,
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
from .config import JournalConfig
from .service import JournalService

__all__ = [
    "DEFAULT_ENTRY_TYPE",
    "ENTRY_TYPE_DIARY",
    "ENTRY_TYPE_ESSAY",
    "ENTRY_TYPE_LABELS",
    "ENTRY_TYPE_WEEKLY",
    "ENTRY_TYPES",
    "TITLE_TIME_FORMAT",
    "JournalConfig",
    "JournalService",
    "default_title",
    "entry_type_label",
    "is_entry_type",
    "normalize_entry_type",
    "resolve_title",
]
