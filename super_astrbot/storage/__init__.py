"""持久层：连接、迁移、仓储、可恢复写日志。

对外出口：

- ``Database``          异步 SQLite 封装（连接 / 事务 / 迁移 / 备份 / 写日志）
- ``SqliteStateStore``  调度器状态存储（当日幂等）
- 各 ``*Repository``    领域仓储
"""

from .db import Database, SqliteStateStore, Transaction
from .migrations import CURRENT_VERSION, MIGRATIONS, Migration
from .repositories import (
    JournalRepository,
    MemoryRepository,
    ReflectionRepository,
    ReviewRepository,
    VectorRepository,
    row_to_dict,
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
    "row_to_dict",
]
