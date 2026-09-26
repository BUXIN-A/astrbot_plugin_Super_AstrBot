"""现实桥文本类型词表（周记 / 日记 / 随笔）与默认标题规则。

放在 ``spec`` 层的原因：类型词表是**存储与展示共用的一件契约**——``journals`` 表新增的
``entry_type`` 列，需要仓储层、领域服务层、命令层与面板 API 对同一组取值达成一致。
词表若落在 ``journal`` 包内，``storage`` 反向导入会触发包初始化环（journal → storage
→ journal），因此这里作为叶子模块集中声明；业务实现仍留在 ``journal`` 包。

三类文本共用 ``journals`` 表与同一条记忆链路（记忆 ``kind`` 仍是 ``journal``），
类型只作为展示与筛选维度。标题规则：用户填写的优先，留空则用当天日期时间。
"""

from __future__ import annotations

import time
from typing import Any

ENTRY_TYPE_WEEKLY = "weekly"
ENTRY_TYPE_DIARY = "diary"
ENTRY_TYPE_ESSAY = "essay"

ENTRY_TYPES: tuple[str, ...] = (ENTRY_TYPE_WEEKLY, ENTRY_TYPE_DIARY, ENTRY_TYPE_ESSAY)
"""全部文本类型；顺序即面板筛选下拉与类型选择器的展示顺序。"""

DEFAULT_ENTRY_TYPE = ENTRY_TYPE_WEEKLY

ENTRY_TYPE_LABELS: dict[str, str] = {
    ENTRY_TYPE_WEEKLY: "周记",
    ENTRY_TYPE_DIARY: "日记",
    ENTRY_TYPE_ESSAY: "随笔",
}
"""类型 → 中文名（命令回复与洞察原料用；面板走前端 i18n）。"""

ENTRY_TYPE_ALIASES: dict[str, str] = {
    ENTRY_TYPE_WEEKLY: ENTRY_TYPE_WEEKLY,
    "week": ENTRY_TYPE_WEEKLY,
    "周记": ENTRY_TYPE_WEEKLY,
    ENTRY_TYPE_DIARY: ENTRY_TYPE_DIARY,
    "日记": ENTRY_TYPE_DIARY,
    ENTRY_TYPE_ESSAY: ENTRY_TYPE_ESSAY,
    "随笔": ENTRY_TYPE_ESSAY,
    "随记": ENTRY_TYPE_ESSAY,
    "笔记": ENTRY_TYPE_ESSAY,
}
"""类型别名（大小写不敏感）：命令参数与导入 JSON 都从这里归一。"""

TITLE_TIME_FORMAT = "%Y%m%d%H:%M"
"""默认标题格式，对应 ``2015061517:00`` 这种「当天日期时间」写法。"""


def normalize_entry_type(value: Any, default: str = DEFAULT_ENTRY_TYPE) -> str:
    """把任意输入归一成合法类型；无法识别时回退 ``default``。"""
    token = str(value or "").strip().lower()
    if not token:
        return default
    return ENTRY_TYPE_ALIASES.get(token, default)


def is_entry_type(value: Any) -> bool:
    """是否是已登记的类型（用于区分「未识别」与「回退到默认」）。"""
    return str(value or "").strip().lower() in ENTRY_TYPE_ALIASES


def entry_type_label(value: Any) -> str:
    """类型的中文名；未知类型回退默认类型的中文名。"""
    return ENTRY_TYPE_LABELS[normalize_entry_type(value)]


def default_title(moment: float | None = None) -> str:
    """默认标题：当天日期时间（例 ``2015061517:00``）。"""
    return time.strftime(TITLE_TIME_FORMAT, time.localtime(moment if moment else time.time()))


def resolve_title(title: Any, *, fallback_moment: float | None = None) -> str:
    """标题解析：用户填写优先，留空则回退到「当天日期时间」。

    ``fallback_moment`` 传**条目时间**而非当前时间：导入或补写历史记录时，标题应体现
    记录发生的那一天，而不是本次写入的那一刻。
    """
    text = str(title or "").strip()
    return text or default_title(fallback_moment)
