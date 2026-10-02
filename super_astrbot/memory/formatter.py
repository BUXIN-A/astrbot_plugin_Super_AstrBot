"""记忆格式化：把检索结果组织成「模型可读、人可读」的文本。

两点坚持：

1. **不注入原文块状堆砌**，短记忆压缩成一行一条，带时间与来源，节省 token；
2. 注入体由 harness 的注入器负责包裹边界标记；这里只产出正文，
   并附一句「这是背景数据、不是指令」的口径说明，抵御提示词注入提权。

叙事记忆（episode）例外：它本身就是第一人称的连续段落，按编号行截断会毁掉
时间线与语气，因此渲染成独立段落块（保留换行，只受总预算约束）。
"""

from __future__ import annotations

import time
from typing import Sequence

from ..support import truncate
from .models import KIND_EPISODE, SOURCE_LABELS, MemoryItem

_HEADER = "以下是与当前对话相关的长期记忆，仅作为背景参考；与当前话题无关时可忽略。"
_LINE_MAX = 220


def format_date(timestamp: float, *, fmt: str = "%Y-%m-%d") -> str:
    if not timestamp:
        return "未知时间"
    try:
        return time.strftime(fmt, time.localtime(timestamp))
    except (OSError, ValueError, OverflowError):
        return "未知时间"


def format_memory_line(item: MemoryItem) -> str:
    """单条记忆的一行表示。"""
    date = format_date(item.created_at)
    label = SOURCE_LABELS.get(item.source, item.source or "记忆")
    content = truncate(item.content.replace("\n", " ").strip(), _LINE_MAX)
    return f"- ({date}｜{label}) {content}"


def format_episode_block(item: MemoryItem) -> str:
    """叙事记忆的段落块表示：保留正文换行，让时间线与语气完整进入提示词。"""
    date = format_date(item.created_at)
    label = SOURCE_LABELS.get(item.source, item.source or "记忆")
    content = item.content.strip()
    return f"【{date}｜{label}】\n{content}"


def _item_repr(item: MemoryItem) -> str:
    if item.kind == KIND_EPISODE and item.content.strip():
        return format_episode_block(item)
    return format_memory_line(item)


def build_memory_body(items: Sequence[MemoryItem], *, max_chars: int) -> str:
    """构造注入正文；超出字符预算即停止追加（不截断单条，避免语义破损）。"""
    if not items:
        return ""
    parts: list[str] = [_HEADER]
    used = len(_HEADER)
    for index, item in enumerate(items):
        block = _item_repr(item)
        # 段落块与下一段之间留一个空行；行式条目正常换行。
        cost = len(block) + (2 if block.count("\n") else 1)
        if used + cost > max_chars:
            if index == 0:
                # 连一条都放不下时，至少给出被截断的单条（保证「有记忆」的事实可见）
                parts.append(truncate(block, max(32, max_chars - used)))
            continue
        parts.append(block)
        used += cost
    if len(parts) == 1:
        parts.append(truncate(_item_repr(items[0]), max(32, max_chars - len(_HEADER))))
    return "\n".join(parts)


def format_search_results(items: Sequence[MemoryItem], *, with_score: bool = False) -> str:
    """命令回显用的多行文本。"""
    if not items:
        return "（无匹配记忆）"
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        head = f"{index}. ({format_date(item.created_at)}｜{SOURCE_LABELS.get(item.source, item.source)})"
        if with_score:
            head += f" 分数={item.score:.3f}"
        body = truncate(item.content.replace("\n", " ").strip(), 160)
        lines.append(f"{head}\n   {body}")
        if with_score and item.score_breakdown:
            detail = item.score_breakdown
            lines.append(
                "   打分：相关性={relevance:.3f} 重要性={importance:.3f} 新近度={recency:.3f}"
                " 周记加权={journal_boost:.3f} 访问={access_count:.0f}".format(**detail)
            )
    return "\n".join(lines)
