"""把 ``AstrMessageEvent`` 转换为纯数据视图。

业务层只持有 ``EventView``，避免在协程之外持有框架对象（也便于单测构造）。
所有属性访问都做防御式处理：任何一个可选 API 缺失都不应让整条链路失败。
"""

from __future__ import annotations

from typing import Any, Sequence

from .protocols import EventView

_PLACEHOLDER_NAMES = frozenset(
    {"unknown", "未知", "未知用户", "未命名", "n/a", "na", "none", "null", "-", "—"}
)
"""平台占位昵称：按「取不到」处理。

存下来只会把记忆写成「用户(Unknown)」，比留空更糟——留空至少还能回退到用户 ID。
"""

_NAME_FIELDS: tuple[str, ...] = (
    "nickname",
    "card",
    "remark",
    "display_name",
    "name",
    "nick",
    "username",
)
"""昵称候选字段：昵称优先，群名片（``card``）/ 备注（``remark``）/ 展示名依次回退。"""

_FULL_NAME_FIELDS = (("first_name", "last_name"),)
"""分字段的姓名（Telegram 等平台把它拆成 first/last）。"""


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


def _read(obj: Any, name: str) -> str:
    """从对象**或字典**里读一个非空字段（onebot 的原始 sender 是 dict）。

    可调用属性按「取不到」跳过：部分适配器把 ``sender_id`` 暴露成方法，
    直接 ``str()`` 会得到 ``<bound method ...>`` 这种垃圾值写进记忆。
    """
    if obj is None:
        return ""
    value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    if value is None or isinstance(value, bool) or callable(value):
        return ""
    return str(value).strip()


def _read_name(obj: Any, fields: Sequence[str] = _NAME_FIELDS) -> str:
    """按候选字段取昵称，跳过平台占位值。"""
    for field in fields:
        value = _read(obj, field)
        if value and value.lower() not in _PLACEHOLDER_NAMES:
            return value
    return ""


def _read_full_name(obj: Any) -> str:
    """把 ``first_name`` / ``last_name`` 拼成完整姓名。"""
    parts: list[str] = []
    for fields in _FULL_NAME_FIELDS:
        values = [_read(obj, field) for field in fields]
        joined = " ".join(
            item for item in values if item and item.lower() not in _PLACEHOLDER_NAMES
        ).strip()
        if joined:
            parts.append(joined)
    return " ".join(parts)


def _clean_name(value: Any) -> str:
    """清洗「已经是昵称」的取值：空串与占位名一律视为取不到。"""
    text = str(value or "").strip()
    return "" if text.lower() in _PLACEHOLDER_NAMES else text


def _sender_obj(event: Any) -> Any:
    """``event.message_obj.sender``（缺失时返回 ``None``）。"""
    return getattr(getattr(event, "message_obj", None), "sender", None)


def _raw_sender(event: Any) -> Any:
    """平台原始事件里的发送者。

    onebot 适配器把群名片放在 ``message_obj.raw_message["sender"]``（dict）里，
    而 ``message_obj.sender`` 可能只有昵称甚至为空，因此这里再兜一层。
    """
    message_obj = getattr(event, "message_obj", None)
    raw = message_obj.get("raw_message") if isinstance(message_obj, dict) else getattr(
        message_obj, "raw_message", None
    )
    return raw.get("sender") if isinstance(raw, dict) else getattr(raw, "sender", None)


def _sender_id(event: Any) -> str:
    """稳健提取发送者 ID。

    AstrBot 不同适配器 / 不同钩子事件里，发送者字段的暴露方式不一致：
    ``get_sender_id()`` 在不少上下文返回空，真正的 ID 往往挂在
    ``message_obj.sender``（对象或 onebot 的原始 dict）上。
    因此按多条路径依次尝试，任一命中即返回。
    """
    for value in (
        _clean_name(_call(event, "get_sender_id", None)),
        _read(event, "sender_id"),
        _read(_sender_obj(event), "user_id"),
        _read(_sender_obj(event), "id"),
        _read(getattr(event, "message_obj", None), "sender_id"),
        _read(_raw_sender(event), "user_id"),
    ):
        if value:
            return value
    return ""


def _sender_name(event: Any) -> str:
    """稳健提取发送者昵称（取不到时回退空串，由 ``display_user`` 决定兜底文案）。

    依次尝试：框架标准接口 → 事件属性 → ``message_obj.sender`` 的昵称/群名片/备注
    → 分字段姓名 → 原始事件里的 sender。占位名（Unknown / 未知用户 / N/A…）
    一律按取不到处理，避免把「用户(Unknown)」写进记忆。
    """
    for value in (
        _clean_name(_call(event, "get_sender_name", None)),
        _clean_name(_read(event, "sender_name")),
        _read_name(_sender_obj(event)),
        _read_full_name(_sender_obj(event)),
        _clean_name(_read(getattr(event, "message_obj", None), "sender_name")),
        _read_name(_raw_sender(event)),
        _read_full_name(_raw_sender(event)),
    ):
        if value:
            return value
    return ""


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
        sender_id=_sender_id(event),
        sender_name=_sender_name(event),
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
