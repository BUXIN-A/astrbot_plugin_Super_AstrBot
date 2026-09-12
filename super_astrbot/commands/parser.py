"""指令参数解析（纯逻辑，不依赖 AstrBot，可独立测试）。

为什么自己解析而不是用 ``@filter.command_group`` 的子指令：

- AstrBot 的指令冲突检测以「完整名」为键；子指令会被记为 ``"sab xxx"``，
  虽然不会与顶层 ``help``/``reset`` 撞名，但仍会在指令列表中产生多条注册项，
  且依赖框架的 ``GreedyStr``/参数推导行为（跨版本有差异风险）；
- 收敛为**单一顶层入口**后，冲突面只剩 ``sab`` 这一个名字，注册项最少、最稳，
  帮助与错误提示也完全由我们自己掌控。

``CommandService.dispatch`` 只认「动作名 + 参数列表」，因此这里只负责把原始消息
拆解成这两部分。
"""

from __future__ import annotations

from typing import Sequence

DEFAULT_COMMAND_NAMES: tuple[str, ...] = ("sab", "superastrbot")
"""顶层指令名与别名（小写）。"""

_COMMAND_PREFIX_CHARS = "/!#.、。"
"""需要剥离的命令前缀字符（覆盖常见 AstrBot 前缀配置）。"""

_ACTION_ALIASES: dict[str, str] = {
    "?": "help",
    "h": "help",
    "帮助": "help",
    "状态": "status",
    "搜索": "search",
    "检索": "search",
    "记": "remember",
    "记住": "remember",
    "周记": "journal",
    "日记": "journal",
    "周记列表": "journals",
    "待审": "review",
    "批准": "approve",
    "驳回": "reject",
    "重置": "reset",
    "重建": "reindex",
}


def strip_command_prefix(text: str) -> str:
    """去掉消息开头的命令前缀符号。"""
    return (text or "").strip().lstrip(_COMMAND_PREFIX_CHARS).strip()


def split_command_args(
    raw: str,
    names: Sequence[str] = DEFAULT_COMMAND_NAMES,
) -> list[str]:
    """把 ``"/sab search 项目 上线"`` 拆成 ``["search", "项目", "上线"]``。

    容错要点：
    - 前缀可有可无（不同部署的指令前缀不同）；
    - 首个词若命中指令名/别名才剥离，避免误伤「动作名恰好等于指令名」的极端情况；
    - 连续空白折叠。
    """
    text = strip_command_prefix(raw)
    if not text:
        return []
    tokens = text.split()
    lowered = {name.lower() for name in names}
    if tokens and tokens[0].lower() in lowered:
        tokens = tokens[1:]
    return tokens


def resolve_action(args: Sequence[str]) -> tuple[str, list[str]]:
    """从参数列表解析出 ``(动作名, 剩余参数)``；无参数时默认 ``help``。"""
    if not args:
        return "help", []
    action = str(args[0]).strip().lower()
    action = _ACTION_ALIASES.get(action, action)
    return action, [str(item) for item in args[1:]]
