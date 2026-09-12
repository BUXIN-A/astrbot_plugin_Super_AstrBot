"""把 ``AstrMessageEvent`` 转换为纯数据视图。

业务层只持有 ``EventView``，避免在协程之外持有框架对象（也便于单测构造）。
所有属性访问都做防御式处理：任何一个可选 API 缺失都不应让整条链路失败。
"""

from __future__ import annotations

from typing import Any

from .protocols import EventView


def _call(obj: Any, name: str, default: Any = None) -> Any:
    """安全调用无参方法/读取属性。"""
    attr = getattr(obj, name, None)
    if attr is None:
        return default
    if callable(attr):
        try:
            return attr()
        except Exception:  # 单个可选 API 失败不应中断
            return default
    return attr


def to_event_view(event: Any, *, now: float | None = None) -> EventView:
    """从事件对象提取视图。

    Args:
        event: ``AstrMessageEvent`` 实例。
        now: 用于缺省时间戳注入（便于测试确定性）。
    """
    umo = str(_call(event, "unified_msg_origin", "") or "")
    private_flag = _call(event, "is_private_chat", None)
    group_id = str(_call(event, "get_group_id", "") or "")
    # 优先以 is_private_chat 为准：部分适配器在私聊下也会返回空 group_id，
    # 若反过来用 umo/group_id 推断，在 API 缺失时会把私聊误判成群聊。
    if isinstance(private_flag, bool):
        is_group = not private_flag
    else:
        is_group = bool(group_id)

    timestamp = getattr(event, "created_at", None)
    if not isinstance(timestamp, (int, float)) or timestamp <= 0:
        timestamp = now if now is not None else 0.0

    return EventView(
        umo=umo,
        platform=str(_call(event, "get_platform_name", "") or ""),
        session_id=str(_call(event, "get_session_id", "") or ""),
        is_group=is_group,
        group_id=group_id,
        sender_id=str(_call(event, "get_sender_id", "") or ""),
        sender_name=str(_call(event, "get_sender_name", "") or ""),
        text=str(_call(event, "get_message_str", "") or ""),
        timestamp=float(timestamp),
        is_admin=bool(_call(event, "is_admin", False)),
        stopped=bool(_call(event, "is_stopped", False)),
    )


def extract_result_text(event: Any) -> str:
    """提取事件当前的待发送文本（用于记录 Bot 回复作为反思原料）。

    注意：AstrBot 的 ``RespondStage`` 会在触发 ``OnAfterMessageSentEvent`` **之后**
    才 ``clear_result()``，因此在该钩子里仍能拿到结果。
    """
    result = _call(event, "get_result", None)
    if result is None:
        return ""
    getter = getattr(result, "get_plain_text", None)
    if callable(getter):
        try:
            return str(getter() or "")
        except Exception:
            return ""
    return ""
