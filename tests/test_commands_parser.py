"""指令参数解析测试。

这层是「单一顶层指令」方案的核心：解析错了会导致子命令全部失效，
因此对前缀、别名、空输入、多词参数都要覆盖。
"""

from __future__ import annotations

from super_astrbot.commands.parser import (
    resolve_action,
    split_command_args,
    strip_command_prefix,
)


def test_strip_command_prefix() -> None:
    assert strip_command_prefix("/sab status") == "sab status"
    assert strip_command_prefix("!sab status") == "sab status"
    assert strip_command_prefix("#sab status") == "sab status"
    assert strip_command_prefix(".sab status") == "sab status"
    assert strip_command_prefix("  sab status  ") == "sab status"
    assert strip_command_prefix("") == ""


def test_split_with_prefix_and_alias() -> None:
    assert split_command_args("/sab status") == ["status"]
    assert split_command_args("sab search 项目 上线 疲惫") == ["search", "项目", "上线", "疲惫"]
    assert split_command_args("/superastrbot help") == ["help"]
    # 大小写不敏感
    assert split_command_args("/SAB STATUS") == ["STATUS"]


def test_split_without_prefix() -> None:
    # 某些部署不带前缀
    assert split_command_args("sab journals") == ["journals"]
    assert split_command_args("superastrbot review") == ["review"]


def test_split_empty_and_only_command() -> None:
    assert split_command_args("/sab") == []
    assert split_command_args("/sab   ") == []
    assert split_command_args("") == []


def test_split_keeps_token_equal_to_command_name_when_not_first() -> None:
    # 只有首个词是指令名时才剥离，避免误伤参数内容
    assert split_command_args("/sab remember sab 是有用的指令") == [
        "remember",
        "sab",
        "是有用的指令",
    ]


def test_split_preserves_multiword_argument() -> None:
    assert split_command_args("/sab journal 这周开始跑步 #运动 #健康") == [
        "journal",
        "这周开始跑步",
        "#运动",
        "#健康",
    ]


def test_resolve_action_defaults_to_help() -> None:
    assert resolve_action([]) == ("help", [])
    assert resolve_action(["status"]) == ("status", [])
    assert resolve_action(["search", "爬山"]) == ("search", ["爬山"])
    assert resolve_action(["approve", "3"]) == ("approve", ["3"])


def test_resolve_action_aliases_and_case() -> None:
    assert resolve_action(["?", "x"]) == ("help", ["x"])
    assert resolve_action(["Status"]) == ("status", [])
    assert resolve_action(["周记", "今天很累"]) == ("journal", ["今天很累"])
    assert resolve_action(["批准", "5"]) == ("approve", ["5"])


def test_resolve_action_unknown_passes_through() -> None:
    # 未知动作原样返回，由 CommandService 统一给出帮助
    assert resolve_action(["nonsense", "a"]) == ("nonsense", ["a"])
