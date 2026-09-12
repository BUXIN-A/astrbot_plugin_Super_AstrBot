"""持久层：连接、迁移、仓储、可恢复写日志。

对外出口：

- ``Database``          异步 SQLite 封装（连接 / 事务 / 迁移 / 备份 / 写日志）
- ``SqliteStateStore``  调度器状态存储（当日幂等）
- 各 ``*Repository``    领域仓储
"""

from .db import Database, SqliteStateStore, Transaction
from .migrations import CURRENT_VERSION, MIGRATIONS, Migration
from .repositories import (
    DEFAULT_JOURNAL_SORT,
    DEFAULT_MEMORY_SORT,
    JOURNAL_SORT_OPTIONS,
    MEMORY_SORT_OPTIONS,
    AffinityRepository,
    GraphRepository,
    JargonRepository,
    JournalRepository,
    MemoryRepository,
    MetricSeriesRepository,
    ReflectionRepository,
    ReviewRepository,
    StyleRepository,
    VectorRepository,
    journal_order_clause,
    memory_order_clause,
    row_to_dict,
    rows_to_dicts,
)

__all__ = [
    "Database",
    "Transaction",
    "SqliteStateStore",
    "Migration",
    "MIGRATIONS",
    "CURRENT_VERSION",
    "MemoryRepository",
    "VectorRepository",
    "JournalRepository",
    "ReflectionRepository",
    "ReviewRepository",
    "StyleRepository",
    "JargonRepository",
    "AffinityRepository",
    "GraphRepository",
    "MetricSeriesRepository",
    "MEMORY_SORT_OPTIONS",
    "DEFAULT_MEMORY_SORT",
    "JOURNAL_SORT_OPTIONS",
    "DEFAULT_JOURNAL_SORT",
    "memory_order_clause",
    "journal_order_clause",
    "row_to_dict",
    "rows_to_dicts",
]
