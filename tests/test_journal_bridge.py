"""现实桥测试：三种文本类型、默认标题规则与按类型/勾选导出。

覆盖用户可见的核心约定：

1. 默认标题是「当天日期时间」（``2015061517:00`` 这种写法），用户填了标题就用用户的；
2. 周记 / 日记 / 随笔三类共用一张表与同一条记忆链路，只靠 ``entry_type`` 区分；
3. 导出既能按勾选的 id，也能按类型/关键词筛选。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from super_astrbot.journal import (
    ENTRY_TYPE_DIARY,
    ENTRY_TYPE_ESSAY,
    ENTRY_TYPE_WEEKLY,
    default_title,
    entry_type_label,
    normalize_entry_type,
    resolve_title,
)
from super_astrbot.spec.scopes import MemoryScope, ScopeType, retrieval_scopes
from tests.helpers import build_stack

# 2015-06-15 17:00（本地时区）：与需求里给的示例标题完全一致
_SAMPLE_MOMENT = time.mktime((2015, 6, 15, 17, 0, 0, 0, 0, -1))
_SAMPLE_TITLE = "2015061517:00"


def _scope() -> MemoryScope:
    return MemoryScope(ScopeType.GLOBAL, "*")


# --------------------------------------------------------------------------- #
# 标题与类型：纯逻辑
# --------------------------------------------------------------------------- #


def test_default_title_matches_requested_format() -> None:
    assert default_title(_SAMPLE_MOMENT) == _SAMPLE_TITLE


def test_resolve_title_prefers_user_input() -> None:
    assert resolve_title("面试复盘", fallback_moment=_SAMPLE_MOMENT) == "面试复盘"
    assert resolve_title("  带空格的标题  ", fallback_moment=_SAMPLE_MOMENT) == "带空格的标题"
    # 留空（含纯空白）才回落到默认标题
    assert resolve_title("", fallback_moment=_SAMPLE_MOMENT) == _SAMPLE_TITLE
    assert resolve_title("   ", fallback_moment=_SAMPLE_MOMENT) == _SAMPLE_TITLE
    assert resolve_title(None, fallback_moment=_SAMPLE_MOMENT) == _SAMPLE_TITLE


def test_normalize_entry_type_and_labels() -> None:
    assert normalize_entry_type("diary") == ENTRY_TYPE_DIARY
    assert normalize_entry_type("日记") == ENTRY_TYPE_DIARY
    assert normalize_entry_type("随笔") == ENTRY_TYPE_ESSAY
    assert normalize_entry_type("WEEKLY") == ENTRY_TYPE_WEEKLY
    assert normalize_entry_type("") == ENTRY_TYPE_WEEKLY
    assert normalize_entry_type("不认识") == ENTRY_TYPE_WEEKLY
    assert entry_type_label("essay") == "随笔"
    assert entry_type_label(None) == "周记"


# --------------------------------------------------------------------------- #
# 写入：默认标题、类型与记忆联动
# --------------------------------------------------------------------------- #


def test_add_defaults_title_and_keeps_memory_link(tmp_path: Path) -> None:
    async def _run() -> dict:
        stack = await build_stack(tmp_path)
        try:
            result = await stack.journal.add(
                _scope(),
                "今天把面试问题清单过了一遍",
                entry_type="diary",
                event_time=_SAMPLE_MOMENT,
            )
            row = await stack.journals_repo.get(int(result["journal_id"]))
            memories = await stack.memory.list_all(offset=0, limit=10, kind="journal")
            return {"result": result, "row": row, "memory_ids": [item.id for item in memories]}
        finally:
            await stack.close()

    data = asyncio.run(_run())
    assert data["result"]["entry_type"] == ENTRY_TYPE_DIARY
    assert data["result"]["title"] == _SAMPLE_TITLE, "未填标题时应使用条目时间对应的默认标题"
    assert data["row"]["entry_type"] == ENTRY_TYPE_DIARY
    assert data["row"]["title"] == _SAMPLE_TITLE
    assert data["row"]["memory_id"] in data["memory_ids"], "写入应同时生成 journal 类长期记忆"


def test_add_keeps_user_title(tmp_path: Path) -> None:
    async def _run() -> dict:
        stack = await build_stack(tmp_path)
        try:
            result = await stack.journal.add(_scope(), "内容", title="自定义标题", entry_type="essay")
            return await stack.journals_repo.get(int(result["journal_id"]))
        finally:
            await stack.close()

    row = asyncio.run(_run())
    assert row["title"] == "自定义标题"
    assert row["entry_type"] == ENTRY_TYPE_ESSAY


def test_add_rejects_unknown_type_to_weekly(tmp_path: Path) -> None:
    async def _run() -> dict:
        stack = await build_stack(tmp_path)
        try:
            result = await stack.journal.add(_scope(), "内容", entry_type="不存在")
            return await stack.journals_repo.get(int(result["journal_id"]))
        finally:
            await stack.close()

    row = asyncio.run(_run())
    assert row["entry_type"] == ENTRY_TYPE_WEEKLY


# --------------------------------------------------------------------------- #
# 编辑：标题与类型可改，留空则回填默认标题
# --------------------------------------------------------------------------- #


def test_update_changes_title_and_type(tmp_path: Path) -> None:
    async def _run() -> dict:
        stack = await build_stack(tmp_path)
        try:
            added = await stack.journal.add(_scope(), "原始内容", event_time=_SAMPLE_MOMENT)
            journal_id = int(added["journal_id"])

            # 只改正文：标题与类型保持原值
            await stack.journal.update(journal_id, content="改后的内容")
            kept = await stack.journals_repo.get(journal_id)

            # 改标题与类型
            await stack.journal.update(
                journal_id, content="再改一次", title="新的标题", entry_type="essay"
            )
            changed = await stack.journals_repo.get(journal_id)

            # 清空标题：按条目时间回填默认标题，不留下空白标题
            await stack.journal.update(journal_id, content="再改一次", title="")
            restored = await stack.journals_repo.get(journal_id)
            return {"kept": kept, "changed": changed, "restored": restored}
        finally:
            await stack.close()

    data = asyncio.run(_run())
    assert data["kept"]["title"] == _SAMPLE_TITLE and data["kept"]["entry_type"] == ENTRY_TYPE_WEEKLY
    assert data["changed"]["title"] == "新的标题"
    assert data["changed"]["entry_type"] == ENTRY_TYPE_ESSAY
    assert data["restored"]["title"] == _SAMPLE_TITLE


def test_update_syncs_memory_content(tmp_path: Path) -> None:
    async def _run() -> str:
        stack = await build_stack(tmp_path)
        try:
            added = await stack.journal.add(_scope(), "第一版正文")
            await stack.journal.update(int(added["journal_id"]), content="第二版正文")
            row = await stack.journals_repo.get(int(added["journal_id"]))
            item = await stack.memory.get_memory(int(row["memory_id"]))
            return item.content if item else ""
        finally:
            await stack.close()

    assert asyncio.run(_run()) == "第二版正文"


# --------------------------------------------------------------------------- #
# 查询与导出：类型筛选、勾选导出
# --------------------------------------------------------------------------- #


def test_list_and_export_filter_by_type(tmp_path: Path) -> None:
    async def _run() -> dict:
        stack = await build_stack(tmp_path)
        try:
            weekly = await stack.journal.add(_scope(), "周记正文")
            diary = await stack.journal.add(_scope(), "日记正文", entry_type="diary")
            await stack.journal.add(_scope(), "随笔正文", entry_type="essay")

            all_rows = await stack.journal.list_recent(_scope(), limit=10)
            diary_rows = await stack.journal.list_recent(_scope(), limit=10, entry_type="diary")
            diary_export = await stack.journal.export_items(entry_type="diary")
            picked = await stack.journal.export_items(ids=[int(weekly["journal_id"])])
            counts = await stack.journal.count_by_type()
            return {
                "all": len(all_rows),
                "diary": len(diary_rows),
                "diary_export": diary_export,
                "picked": picked,
                "counts": counts,
                "diary_id": int(diary["journal_id"]),
                "scopes": retrieval_scopes(_scope()),
            }
        finally:
            await stack.close()

    data = asyncio.run(_run())
    assert data["all"] == 3
    assert data["diary"] == 1
    assert [item["content"] for item in data["diary_export"]] == ["日记正文"]
    assert [item["content"] for item in data["picked"]] == ["周记正文"]
    assert data["counts"]["diary"] == 1 and data["counts"]["essay"] == 1
    # 导出结构固定为「面板表头顺序」，字段齐全才能被导入往返消费
    assert list(data["diary_export"][0].keys())[:7] == [
        "id",
        "title",
        "content",
        "type",
        "tags",
        "emotion",
        "created_at",
    ]


def test_export_returns_tags_as_list(tmp_path: Path) -> None:
    """面板与导出都直接消费标签；``tags`` 是 JSON 字符串会让前端渲染成空。"""

    async def _run() -> dict:
        stack = await build_stack(tmp_path)
        try:
            await stack.journal.add(_scope(), "带标签的记录", tags=["运动", "健康"])
            return (await stack.journal.export_items())[0]
        finally:
            await stack.close()

    item = asyncio.run(_run())
    assert item["tags"] == ["运动", "健康"]


def test_weekly_material_marks_type_and_title(tmp_path: Path) -> None:
    """周度洞察原料要能看出「这是日记还是周记」，否则三类文本混在一起无法区分。"""

    async def _run() -> str:
        stack = await build_stack(tmp_path)
        try:
            await stack.journal.add(_scope(), "今天跑了五公里", entry_type="diary", title="夜跑")
            return await stack.journal.weekly_material(_scope(), days=7)
        finally:
            await stack.close()

    material = asyncio.run(_run())
    assert "日记" in material
    assert "夜跑" in material
    assert "今天跑了五公里" in material
