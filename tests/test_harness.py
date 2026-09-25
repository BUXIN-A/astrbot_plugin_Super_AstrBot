"""Harness 层测试：框架诊断、事件视图映射、结果文本提取。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from super_astrbot.harness import compat
from super_astrbot.harness.astrbot_event import extract_result_text, to_event_view


class FakeEvent:
    def __init__(self, **overrides: Any) -> None:
        self.unified_msg_origin = "aiocqhttp:GroupMessage:12345"
        self.created_at = 1_700_000_000.0
        self._data = {
            "is_private_chat": False,
            "get_group_id": "12345",
            "get_platform_name": "aiocqhttp",
            "get_session_id": "12345",
            "get_sender_id": "u-9",
            "get_sender_name": "小明",
            "get_message_str": "你好呀",
            "is_admin": False,
            "is_stopped": False,
        }
        self._data.update(overrides)

    def __getattr__(self, item: str) -> Any:
        data = self.__dict__.get("_data") or {}
        if item in data:
            value = data[item]
            return (lambda: value) if callable(value) else value
        raise AttributeError(item)


def test_diagnostics_shape() -> None:
    info = compat.diagnostics()
    assert set(info) >= {"version", "symbols_ok", "error", "available", "missing"}
    assert isinstance(info["available"], list)
    assert isinstance(info["missing"], list)
    # 无 AstrBot 环境下应报告符号不可用，而不是抛异常
    assert info["symbols_ok"] is False
    assert "TextPart" in info["missing"]


def test_to_event_view_maps_all_fields() -> None:
    view = to_event_view(FakeEvent())
    assert view.umo == "aiocqhttp:GroupMessage:12345"
    assert view.platform == "aiocqhttp"
    assert view.session_id == "12345"
    assert view.is_group is True
    assert view.is_private is False
    assert view.group_id == "12345"
    assert view.sender_id == "u-9"
    assert view.sender_name == "小明"
    assert view.text == "你好呀"
    assert view.timestamp == 1_700_000_000.0
    assert view.is_admin is False
    assert view.stopped is False
    assert view.display_user == "小明"


def test_to_event_view_private_chat_and_admin() -> None:
    event = FakeEvent(is_private_chat=True, get_group_id="", is_admin=True)
    view = to_event_view(event)
    assert view.is_group is False
    assert view.is_private is True
    assert view.group_id == ""
    assert view.is_admin is True


def test_to_event_view_private_flag_takes_precedence() -> None:
    """私聊判定以 is_private_chat 为准，不被残留的 group_id 覆盖。"""
    view = to_event_view(FakeEvent(is_private_chat=True, get_group_id="12345"))
    assert view.is_group is False
    assert view.is_private is True


def test_to_event_view_group_id_fallback_when_private_api_missing() -> None:
    """缺失 is_private_chat 时退化为按 group_id 推断，而不是把私聊判成群聊。"""
    event = FakeEvent()
    del event._data["is_private_chat"]
    assert to_event_view(event).is_group is True

    private = FakeEvent(is_private_chat=True, get_group_id="")
    del private._data["is_private_chat"]
    assert to_event_view(private).is_group is False


def test_to_event_view_tolerates_broken_attributes() -> None:
    """单个可选 API 抛异常不应导致整个视图构造失败。"""

    class Broken:
        unified_msg_origin = "aiocqhttp:FriendMessage:1"

        def get_sender_name(self) -> str:
            raise RuntimeError("适配器异常")

        def is_private_chat(self) -> bool:
            return True

    view = to_event_view(Broken())
    assert view.umo == "aiocqhttp:FriendMessage:1"
    assert view.sender_name == ""
    assert view.is_private is True


class _MessageObjEvent:
    """身份只在 ``message_obj`` 上的事件：``get_sender_*`` 一律返回空。

    这是线上最常见的形态——``on_llm_request`` / ``on_after_message_sent`` 里拿到的事件，
    ``get_sender_name()`` 常常是空串，真名挂在 ``message_obj.sender.nickname``。
    """

    def __init__(self, *, sender: Any = None, raw_message: Any = None) -> None:
        self.unified_msg_origin = "aiocqhttp:GroupMessage:12345"
        self.created_at = 1_700_000_000.0
        self.message_obj = SimpleNamespace(sender=sender, raw_message=raw_message)

    def is_private_chat(self) -> bool:
        return False

    def get_group_id(self) -> str:
        return "12345"

    def get_platform_name(self) -> str:
        return "aiocqhttp"

    def get_session_id(self) -> str:
        return "12345"

    def get_sender_id(self) -> str:
        return ""

    def get_sender_name(self) -> str:
        return ""

    def get_message_str(self) -> str:
        return "你好呀"


def test_to_event_view_reads_sender_from_message_obj() -> None:
    """回归：只认 ``get_sender_*`` 会让大量事件取不到发送者，记忆退化成「用户(未知用户)」。"""
    event = _MessageObjEvent(sender=SimpleNamespace(user_id=2606684478, nickname="眩晕～～"))

    view = to_event_view(event)

    assert view.sender_id == "2606684478", "应回退到 message_obj.sender.user_id"
    assert view.sender_name == "眩晕～～", "应回退到 message_obj.sender.nickname"
    assert view.display_user == "眩晕～～"


def test_to_event_view_reads_sender_from_raw_message() -> None:
    """onebot 把群名片放在原始事件的 dict 里：那里也得能取到。"""
    raw_message = {"sender": {"user_id": "3507043758", "card": "群名片名"}}
    event = _MessageObjEvent(sender=SimpleNamespace(), raw_message=raw_message)

    view = to_event_view(event)

    assert view.sender_id == "3507043758"
    assert view.sender_name == "群名片名", "昵称缺失时应回退到群名片"


def test_to_event_view_skips_placeholder_sender_name() -> None:
    """平台占位昵称不算昵称：宁可用 ID，也不要写成「用户(Unknown)」。"""
    event = _MessageObjEvent(sender=SimpleNamespace(user_id="u-9", nickname="Unknown"))

    view = to_event_view(event)

    assert view.sender_name == ""
    assert view.display_user == "u-9"


def test_to_event_view_joins_split_sender_name() -> None:
    """分字段姓名（Telegram 风格）应拼成完整姓名。"""
    event = _MessageObjEvent(
        sender=SimpleNamespace(user_id="7", first_name="Ada", last_name="Lovelace")
    )

    view = to_event_view(event)

    assert view.sender_name == "Ada Lovelace"


def test_to_event_view_reads_sender_dict_from_message_obj() -> None:
    """``message_obj.sender`` 本身是 dict 时也要能读（部分适配器就是 dict）。"""
    event = _MessageObjEvent(sender={"user_id": "u-3", "nickname": "谷雨"})

    view = to_event_view(event)

    assert view.sender_id == "u-3"
    assert view.sender_name == "谷雨"


def test_to_event_view_uses_injected_clock_when_timestamp_missing() -> None:
    class NoTimestamp:
        unified_msg_origin = "aiocqhttp:FriendMessage:1"

        def is_private_chat(self) -> bool:
            return True

    view = to_event_view(NoTimestamp(), now=1_234.0)
    assert view.timestamp == 1_234.0


def test_extract_result_text_variants() -> None:
    class Result:
        def get_plain_text(self) -> str:
            return "机器人回复内容"

    class WithResult:
        def get_result(self) -> Any:
            return Result()

    class WithoutResult:
        def get_result(self) -> Any:
            return None

    class BrokenResult:
        def get_result(self) -> Any:
            raise RuntimeError("boom")

    class NoMethod:
        pass

    assert extract_result_text(WithResult()) == "机器人回复内容"
    assert extract_result_text(WithoutResult()) == ""
    assert extract_result_text(BrokenResult()) == ""
    assert extract_result_text(NoMethod()) == ""


def test_stopped_event_is_mapped() -> None:
    """``/stop`` 后的停止标记必须传到事件视图：循环控制据此停止感知。"""
    assert to_event_view(FakeEvent(is_stopped=True)).stopped is True
    assert to_event_view(object()).stopped is False


def test_to_event_view_ignores_callable_sender_fields() -> None:
    """把 sender 字段暴露成方法的适配器不能被写进记忆（否则是 <bound method ...>）。"""

    class Sender:
        user_id = "u-5"

        def nickname(self) -> str:  # 可调用属性：应被跳过
            return "不该被采用"

    event = _MessageObjEvent(sender=Sender())

    view = to_event_view(event)

    assert view.sender_id == "u-5"
    assert view.sender_name == ""
    assert view.display_user == "u-5"

