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
    # 现实桥：动作名本身决定文本类型（周记 / 日记 / 随笔）
    "周记": "journal",
    "现实桥": "journal",
    "桥": "journal",
    "日记": "diary",
    "随笔": "essay",
    "随记": "essay",
    "周记列表": "journals",
    "日记列表": "journals",
    "现实桥列表": "journals",
    "列表": "journals",
    "修改": "journal-edit",
    "编辑": "journal-edit",
    "改": "journal-edit",
    "待审": "review",
    "批准": "approve",
    "驳回": "reject",
    "重置": "reset",
    "重建": "reindex",
    "安静": "quiet",
    "免打扰": "quiet",
    "学习": "persona",
    "拟人化": "persona",
    "人格": "persona",
    "图谱": "graph",
    "知识图谱": "graph",
}

_TITLE_SEPARATORS: tuple[str, ...] = ("|", "｜")
"""标题与正文的分隔符：``标题 | 正文``；全角竖线一并接受（中文输入法下更顺手）。"""


def strip_command_prefix(text: str) -> str:
    """去掉消息开头的命令前缀符号。"""
    return (text or "").strip().lstrip(_COMMAND_PREFIX_CHARS).strip()


def parse_journal_payload(payload: str) -> tuple[str, str, list[str]]:
    """把现实桥命令的正文拆成 ``(标题, 内容, 标签)``。

    约定：

    - ``#标签`` 可写在任意位置，拆解后从标题与正文里移除；
    - 出现 ``|``（或全角 ``｜``）时，其前为**标题**、其后为**正文**；
    - 不写分隔符时整体都是正文，标题交给服务层补「当天日期时间」默认值
      （即用户标注了标题才用自定义标题，否则一律用默认标题）。
    """
    raw = (payload or "").strip()
    if not raw:
        return "", "", []
    tokens = raw.split()
    tags = [token[1:] for token in tokens if token.startswith("#") and len(token) > 1]
    if tags:
        raw = " ".join(token for token in tokens if not token.startswith("#")).strip()
    for separator in _TITLE_SEPARATORS:
        if separator in raw:
            title, _, content = raw.partition(separator)
            return title.strip(), content.strip(), tags
    return "", raw, tags


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
