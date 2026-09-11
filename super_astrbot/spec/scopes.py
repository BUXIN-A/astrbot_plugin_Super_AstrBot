"""作用域契约。

记忆/周记的可见范围由「作用域」决定。检索时按「会话 + 用户 + 全局」三层并集召回，
写入时按配置的默认作用域落库。改动作用域只影响之后新写入的数据（不做历史回溯迁移），
这一点与 livingmemory 的取舍保持一致并在面板中明示。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ScopeType(str, Enum):
    """作用域类型。"""

    SESSION = "session"
    """按会话（umo）隔离，默认策略。"""

    USER = "user"
    """按发送者跨会话共享。"""

    GLOBAL = "global"
    """全局共享。"""

    @classmethod
    def parse(cls, value: object, default: "ScopeType" = None) -> "ScopeType":  # type: ignore[assignment]
        fallback = default or cls.SESSION
        if isinstance(value, ScopeType):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            for item in cls:
                if item.value == normalized:
                    return item
        return fallback


GLOBAL_SCOPE_ID = "*"
"""全局作用域的固定 scope_id。"""


@dataclass(frozen=True)
class MemoryScope:
    """一次读写所使用的具体作用域。"""

    scope_type: ScopeType
    scope_id: str

    def __post_init__(self) -> None:
        if self.scope_type is ScopeType.GLOBAL and self.scope_id != GLOBAL_SCOPE_ID:
            object.__setattr__(self, "scope_id", GLOBAL_SCOPE_ID)

    @property
    def key(self) -> str:
        """作用域的稳定标识，用于缓存键与去重。"""
        return f"{self.scope_type.value}:{self.scope_id}"

    @classmethod
    def for_session(cls, umo: str) -> "MemoryScope":
        return cls(ScopeType.SESSION, umo or "unknown")

    @classmethod
    def for_user(cls, user_id: str) -> "MemoryScope":
        return cls(ScopeType.USER, user_id or "unknown")

    @classmethod
    def global_scope(cls) -> "MemoryScope":
        return cls(ScopeType.GLOBAL, GLOBAL_SCOPE_ID)

    @classmethod
    def from_event(
        cls,
        scope_type: ScopeType,
        *,
        umo: str,
        user_id: str,
    ) -> "MemoryScope":
        """按作用域类型从事件信息构造。"""
        if scope_type is ScopeType.GLOBAL:
            return cls.global_scope()
        if scope_type is ScopeType.USER:
            return cls.for_user(user_id)
        return cls.for_session(umo)


def retrieval_scopes(active: MemoryScope) -> tuple[MemoryScope, ...]:
    """给出检索时应并集查询的作用域集合（含全局兜底）。

    去重并保持稳定顺序：具体作用域优先，全局兜底最后。
    """
    scopes: list[MemoryScope] = [active]
    if active.scope_type is not ScopeType.GLOBAL:
        scopes.append(MemoryScope.global_scope())
    seen: set[str] = set()
    unique: list[MemoryScope] = []
    for scope in scopes:
        if scope.key in seen:
            continue
        seen.add(scope.key)
        unique.append(scope)
    return tuple(unique)
