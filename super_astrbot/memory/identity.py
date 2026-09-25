"""用户身份解析：把「谁在说话」变成稳定、可配置、可观测的作用域键。

背景（来自线上排查）：记忆的隔离键原本直接使用会话 ``umo``，而部分平台的 ``umo``
每次连接都带随机 UUID（webchat 实测如此），于是同一用户的记忆被切碎在几十个作用域里，
「无法识别回头用户」。修复方向是让作用域键落到**用户**上，但前提是平台能提供稳定标识——
本模块把这件事拆成三步，避免拍脑袋切换：

1. **策略可选**（``identity_strategy``）：
   - ``sender_id``（默认）：用平台用户 ID，最干净；
   - ``sender_name``：昵称，接受重名歧义，用于 ID 不稳定的平台；
   - ``auto``：优先 ID，缺失时回退昵称——空 ID 的适配器也能识别用户。
2. **解析可解释**：``resolve_identity`` 同时返回实际采用的来源（``source``），
   面板与日志能说清「这条记忆是按什么键归属的」。
3. **观测可验证**：身份观测表记录「同一条 umo 上出现过的发送者标识」，
   据此判断平台标识是否稳定——若同一昵称对应多个 ID，就该改用 ``sender_name``。

所有函数都是纯函数，便于单测；观测写入由 ``MemoryService`` 负责。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

IDENTITY_SENDER_ID = "sender_id"
IDENTITY_SENDER_NAME = "sender_name"
IDENTITY_AUTO = "auto"

IDENTITY_STRATEGIES: tuple[str, ...] = (
    IDENTITY_SENDER_ID,
    IDENTITY_SENDER_NAME,
    IDENTITY_AUTO,
)

DEFAULT_IDENTITY_STRATEGY = IDENTITY_SENDER_ID

_STRATEGY_ALIASES: dict[str, str] = {
    IDENTITY_SENDER_ID: IDENTITY_SENDER_ID,
    "id": IDENTITY_SENDER_ID,
    "user_id": IDENTITY_SENDER_ID,
    IDENTITY_SENDER_NAME: IDENTITY_SENDER_NAME,
    "name": IDENTITY_SENDER_NAME,
    "nickname": IDENTITY_SENDER_NAME,
    "昵称": IDENTITY_SENDER_NAME,
    IDENTITY_AUTO: IDENTITY_AUTO,
    "自动": IDENTITY_AUTO,
}
"""策略别名（大小写不敏感）：配置页写中文也能识别。"""

UNKNOWN_USER_KEY = "unknown"
"""既没有 ID 也没有昵称时的兜底键（避免整类消息互相覆盖成同一条空作用域）。"""


def normalize_strategy(value: Any) -> str:
    """把配置值归一成合法策略；无法识别时回退默认。"""
    token = str(value or "").strip().lower()
    if not token:
        return DEFAULT_IDENTITY_STRATEGY
    return _STRATEGY_ALIASES.get(token, DEFAULT_IDENTITY_STRATEGY)


@dataclass(frozen=True)
class MemoryIdentity:
    """一条记忆的说话者身份。

    - ``sender_id`` / ``sender_name``：平台给出的用户标识与昵称；
    - ``origin_umo``：这条记忆来自哪个会话（保留溯源能力，便于反查群号与平台）。

    三者都会写进 ``memories`` 表，使「历史记忆属于谁」不再只能靠猜。
    """

    sender_id: str = ""
    sender_name: str = ""
    origin_umo: str = ""

    @property
    def empty(self) -> bool:
        return not (self.sender_id or self.sender_name or self.origin_umo)

    @classmethod
    def from_view(cls, view: Any) -> "MemoryIdentity":
        """从 ``EventView``/事件对象取身份（缺字段时留空，不抛异常）。"""
        return cls(
            sender_id=str(getattr(view, "sender_id", "") or ""),
            sender_name=str(getattr(view, "sender_name", "") or ""),
            origin_umo=str(getattr(view, "umo", "") or ""),
        )

@dataclass(frozen=True)
class ResolvedIdentity:
    """身份解析结果：作用域键 + 可解释的来源。"""

    user_key: str
    source: str
    """实际采用的来源：``sender_id`` / ``sender_name`` / ``unknown``。"""

    @property
    def degraded(self) -> bool:
        """是否走了兜底（说明该平台没给出可用标识，识别用户会打折）。"""
        return self.source != IDENTITY_SENDER_ID


def resolve_identity(
    *,
    sender_id: str,
    sender_name: str,
    strategy: str = DEFAULT_IDENTITY_STRATEGY,
) -> ResolvedIdentity:
    """按策略解析出用户作用域键。

    ``auto`` 的语义是「能用 ID 就用 ID，否则用昵称」，从而让只提供昵称的适配器
    （部分 webchat / 第三方适配）也能跨会话识别同一用户。
    """
    key = str(sender_id or "").strip()
    name = str(sender_name or "").strip()
    mode = normalize_strategy(strategy)

    if mode == IDENTITY_SENDER_NAME:
        if name:
            return ResolvedIdentity(name, IDENTITY_SENDER_NAME)
        if key:
            return ResolvedIdentity(key, IDENTITY_SENDER_ID)
        return ResolvedIdentity(UNKNOWN_USER_KEY, "unknown")

    if key:
        return ResolvedIdentity(key, IDENTITY_SENDER_ID)
    if mode == IDENTITY_AUTO and name:
        return ResolvedIdentity(name, IDENTITY_SENDER_NAME)
    return ResolvedIdentity(UNKNOWN_USER_KEY, "unknown")


def describe_observation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """对身份观测行做「标识是否稳定」的判定，供面板直接展示结论。

    判据：
    - 同一昵称对应多个 ``sender_id`` → 该平台 ID 不稳定（换会话就换 ID）；
    - 同一 ``sender_id`` 对应多个 ``umo`` → 正常（说明 ID 稳定、跨会话复用）。

    返回结构固定，前端不需要自己算。
    """
    by_name: dict[str, set[str]] = {}
    by_id: dict[str, set[str]] = {}
    for row in rows:
        name = str(row.get("sender_name") or "").strip()
        identifier = str(row.get("sender_id") or "").strip()
        umo = str(row.get("umo") or "").strip()
        if name:
            by_name.setdefault(name, set()).add(identifier)
        if identifier:
            by_id.setdefault(identifier, set()).add(umo)

    unstable_names = {
        name: sorted(item for item in ids if item)
        for name, ids in by_name.items()
        if len(ids) > 1
    }
    stable_ids = {identifier: sorted(umos) for identifier, umos in by_id.items() if len(umos) > 1}

    if not rows:
        verdict = "empty"
        hint = "还没有观测数据：让用户先说一句话，或在面板点「刷新」。"
    elif unstable_names:
        verdict = "unstable_id"
        hint = (
            "检测到同一昵称对应多个发送者 ID，说明本平台的用户 ID 会随会话变化；"
            "建议把「用户身份策略」改为 auto 或 sender_name，否则记忆仍会按会话切碎。"
        )
    elif stable_ids:
        verdict = "stable_id"
        hint = "同一发送者 ID 已在多个会话出现，平台标识稳定，可以放心使用 user 作用域。"
    else:
        verdict = "single"
        hint = "样本还不足（同一用户还没跨会话出现），多聊几轮再回来看结论。"

    return {
        "verdict": verdict,
        "hint": hint,
        "unstable_names": unstable_names,
        "reused_ids": stable_ids,
        "name_count": len(by_name),
        "id_count": len(by_id),
    }
