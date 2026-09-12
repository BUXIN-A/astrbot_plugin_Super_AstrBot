"""群聊语义的框架适配：filter 门控、信号提取、决策落地。

为什么必须用自定义 filter 而不是直接注册群消息 handler：
AstrBot 的 ``WakingCheckStage`` 在**插件的 event_filter 通过时会把事件标记为已唤醒**，
而「未被 @ 的消息」只有先被唤醒，才可能走到调用 LLM 的那一步。反过来，如果我们
无条件通过 filter，能力关闭时也会把本该被框架 ``stop_event()`` 的群消息唤醒，
产生额外副作用。因此 filter 必须跟随能力开关：关闭即返回 ``False``，完全不参与唤醒判定。

能力开启后，是否真的要回复仍由 ``group`` 域决策，本模块只负责：

1. 把消息段里的「被 @ / 被引用」解析成纯数据（``GroupSignals``）；
2. 把决策落地到事件对象（改写文本 / 标记唤醒 / 终止事件）。
"""

from __future__ import annotations

from typing import Any, Callable

from . import astrbot_compat as compat
from .astrbot_event import _call
from .protocols import GroupDecision, GroupSignals

_CustomFilter: Any = compat.SYMBOLS.CustomFilter

GROUP_FILTER_AVAILABLE = _CustomFilter is not None and compat.SYMBOLS.EventMessageType is not None
"""框架是否具备注册群消息 handler 所需的符号（缺失时该能力整体降级为不可用）。"""

_GATE: Callable[[], bool] | None = None


def set_group_gate(gate: Callable[[], bool] | None) -> None:
    """注入「群聊语义是否生效」的判定函数，由 ``app`` 在装配与热切换时调用。"""
    global _GATE
    _GATE = gate


def release_group_gate(gate: Callable[[], bool] | None) -> None:
    """仅在当前门控正是该对象时清除它。

    插件重载时新旧实例可能短暂并存，无条件清空会让新实例的门控失效
    （表现为群聊语义静默失效，直到下一次能力刷新）。
    """
    global _GATE
    if gate is not None and _GATE is gate:
        _GATE = None


def group_gate_open() -> bool:
    """当前是否允许群消息 handler 参与唤醒判定。门控异常一律按关闭处理。"""
    if _GATE is None:
        return False
    try:
        return bool(_GATE())
    except Exception:  # noqa: BLE001 - 门控异常不能影响消息链路
        return False


if _CustomFilter is not None:

    class GroupMessageFilter(_CustomFilter):  # type: ignore[misc, valid-type]
        """群消息 handler 的门控 filter。"""

        def filter(self, event: Any, cfg: Any) -> bool:
            return group_gate_open()

else:  # pragma: no cover - 仅老版本框架会走到

    class GroupMessageFilter:  # type: ignore[no-redef]
        """占位实现：框架无自定义 filter 时永远不通过。"""

        def __init__(self, raise_error: bool = True) -> None:
            self.raise_error = raise_error

        def filter(self, event: Any, cfg: Any) -> bool:
            return False


def to_group_signals(event: Any) -> GroupSignals:
    """提取群消息的额外信号（被 @ / 被引用 / 框架唤醒标记）。"""
    self_id = str(_call(event, "get_self_id", "") or "")
    messages = _call(event, "get_messages", None)
    return GroupSignals(
        self_id=self_id,
        mentioned=_mentions_bot(messages, self_id),
        wake=bool(getattr(event, "is_at_or_wake_command", False)),
    )


def apply_group_decision(event: Any, decision: GroupDecision) -> None:
    """把决策落地到框架事件对象。

    - ``silent``：``stop_event()``。事件不再进入后续 stage，因此既不会调用模型，
      也不会发送任何内容；
    - ``interject``：标记为唤醒消息并可选改写文本，使框架继续走 LLM 链路；
    - ``reply``：不干预，沿用框架既有判定。
    """
    if decision.action == "silent":
        event.stop_event()
        return
    if decision.action != "interject":
        return

    if decision.text:
        try:
            event.message_str = decision.text
        except Exception:  # noqa: BLE001 - 只读属性等异常场景保持原文
            pass
    try:
        event.is_at_or_wake_command = True
        event.is_wake = True
    except Exception:  # noqa: BLE001 - 标记失败即退回框架原判定
        pass


def _mentions_bot(messages: Any, self_id: str) -> bool:
    """消息段中是否存在指向 Bot 的 ``At`` 或引用。"""
    if not self_id or not isinstance(messages, (list, tuple)):
        return False
    at_cls = compat.SYMBOLS.At
    reply_cls = compat.SYMBOLS.Reply
    for component in messages:
        if at_cls is not None and isinstance(component, at_cls):
            if str(getattr(component, "qq", "") or "") == self_id:
                return True
        elif reply_cls is not None and isinstance(component, reply_cls):
            if str(getattr(component, "sender_id", "") or "") == self_id:
                return True
    return False
