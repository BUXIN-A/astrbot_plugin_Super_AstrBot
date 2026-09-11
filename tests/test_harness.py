"""Harness 层测试：框架诊断、事件视图映射、结果文本提取。"""

from __future__ import annotations

from typing import Any

from super_astrbot.harness import compat
from super_astrbot.harness.astrbot_event import (
    extract_result_text,
    is_event_stopped,
    to_event_view,
)


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


def test_is_event_stopped() -> None:
    assert is_event_stopped(FakeEvent(is_stopped=False)) is False
    assert is_event_stopped(FakeEvent(is_stopped=True)) is True
    assert is_event_stopped(object()) is False
