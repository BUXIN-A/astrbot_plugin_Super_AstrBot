"""命令层测试：权限、参数校验、现实桥（周记 / 日记 / 随笔）与重置流程。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from super_astrbot.commands import HELP_TEXT, CommandService
from super_astrbot.harness.protocols import EventView
from super_astrbot.journal import JournalConfig, JournalService
from super_astrbot.spec.scopes import MemoryScope, ScopeType

from .helpers import build_stack

ADMIN_CONFIG = {"basic": {"admin_only_commands": True}}


class FakeApp:
    """CommandService 所需的最小应用替身。"""

    def __init__(self, stack: object, *, reflection: object | None = None) -> None:
        self.memory = stack.memory  # type: ignore[attr-defined]
        self.journal = stack.journal  # type: ignore[attr-defined]
        self.reflection = reflection
        self.memory_config = stack.config  # type: ignore[attr-defined]
        self.persona_service = None
        self.graph_service = None
        self.capabilities: dict[str, bool] = {}
        self.ready = True
        self.host = object()

    async def status(self, *, umo: str = "") -> dict[str, object]:
        return {
            "ready": True,
            "capabilities": {"basic.enabled": True, "memory.enabled": True},
            "degraded": [],
            "memory": {"active": 1, "buffered": 0, "journals": 0, "routes": ["keyword"]},
            "scheduler": [],
            "budget": {},
            "database": "test.db",
            "fts": True,
            "pending_tasks": 0,
        }

    async def pending_reviews(self, scope: object, *, limit: int = 20) -> list[dict[str, object]]:
        return []

    async def approve_review(self, review_id: int) -> tuple[bool, str]:
        return False, ""

    async def reject_review(self, review_id: int) -> bool:
        return False


def _view(
    *, is_admin: bool = True, text: str = "", umo: str = "aiocqhttp:FriendMessage:1"
) -> EventView:
    return EventView(umo=umo, sender_id="u1", sender_name="测试用户", text=text, is_admin=is_admin)


def _scope() -> MemoryScope:
    """命令层默认落在会话作用域（session:umo）；这里按同一作用域查库。"""
    return MemoryScope.from_event(ScopeType.SESSION, umo="aiocqhttp:FriendMessage:1", user_id="u1")


def test_help_and_status_are_available_to_everyone(tmp_path: Path) -> None:
    async def _run() -> tuple[str, str]:
        stack = await build_stack(tmp_path)
        service = CommandService(app=FakeApp(stack), config=ADMIN_CONFIG)
        help_text = await service.dispatch("help", _view(is_admin=False), [])
        status_text = await service.dispatch("status", _view(is_admin=False), [])
        await stack.close()
        return help_text, status_text

    help_text, status_text = asyncio.run(_run())
    assert help_text == HELP_TEXT
    assert "Super_AstrBot 状态" in status_text


def test_admin_only_blocks_other_actions_for_members(tmp_path: Path) -> None:
    async def _run() -> str:
        stack = await build_stack(tmp_path)
        service = CommandService(app=FakeApp(stack), config=ADMIN_CONFIG)
        text = await service.dispatch("search", _view(is_admin=False), ["任意"])
        await stack.close()
        return text

    assert "仅管理员可用" in asyncio.run(_run())


def test_remember_then_search(tmp_path: Path) -> None:
    async def _run() -> tuple[str, str]:
        stack = await build_stack(tmp_path)
        service = CommandService(app=FakeApp(stack), config=ADMIN_CONFIG)
        remember_text = await service.dispatch("remember", _view(), ["用户喜欢在周末爬山放松"])
        search_text = await service.dispatch("search", _view(), ["爬山"])
        await stack.close()
        return remember_text, search_text

    remember_text, search_text = asyncio.run(_run())
    assert "已记住" in remember_text
    assert "命中 1 条" in search_text
    assert "爬山" in search_text


def test_search_without_query_shows_usage(tmp_path: Path) -> None:
    async def _run() -> str:
        stack = await build_stack(tmp_path)
        service = CommandService(app=FakeApp(stack), config=ADMIN_CONFIG)
        text = await service.dispatch("search", _view(), [])
        await stack.close()
        return text

    assert "用法" in asyncio.run(_run())


def test_journal_add_parses_tags_and_lists(tmp_path: Path) -> None:
    async def _run() -> tuple[str, str]:
        stack = await build_stack(tmp_path)
        service = CommandService(app=FakeApp(stack), config=ADMIN_CONFIG)
        add_text = await service.dispatch("journal", _view(), ["这周开始跑步", "#运动", "#健康"])
        list_text = await service.dispatch("journals", _view(), [])
        await stack.close()
        return add_text, list_text

    add_text, list_text = asyncio.run(_run())
    assert "周记已记录" in add_text
    assert "运动" in add_text
    assert "这周开始跑步" in list_text
    assert "周记内容不能为空" not in add_text


def test_journal_add_requires_content(tmp_path: Path) -> None:
    async def _run() -> str:
        stack = await build_stack(tmp_path)
        service = CommandService(app=FakeApp(stack), config=ADMIN_CONFIG)
        text = await service.dispatch("journal", _view(), [])
        await stack.close()
        return text

    assert "用法" in asyncio.run(_run())


def test_journal_blocked_when_user_write_disabled(tmp_path: Path) -> None:
    async def _run() -> str:
        stack = await build_stack(tmp_path)
        stack.journal = JournalService(
            config=JournalConfig(allow_user_write=False, admin_only_write=False),
            journals=stack.journals_repo,
            memory_service=stack.memory,
        )
        # 关闭「仅管理员可用」，以便走到周记自身的写入权限判定
        service = CommandService(
            app=FakeApp(stack), config={"basic": {"admin_only_commands": False}}
        )
        text = await service.dispatch("journal", _view(is_admin=False), ["内容足够长的一条周记"])
        await stack.close()
        return text

    assert "不允许" in asyncio.run(_run())


def test_bridge_types_write_and_default_title(tmp_path: Path) -> None:
    """三类文本走同一动作族：动作名决定类型，未填标题则用当天日期时间。"""

    async def _run() -> dict:
        stack = await build_stack(tmp_path)
        service = CommandService(app=FakeApp(stack), config=ADMIN_CONFIG)
        diary = await service.dispatch("diary", _view(), ["今天把问题清单过了一遍"])
        essay = await service.dispatch("essay", _view(), ["路上的比喻"])
        weekly = await service.dispatch("journal", _view(), ["这周开始跑步"])
        rows = await stack.journal.list_recent(_scope(), limit=10)
        await stack.close()
        return {
            "diary": diary,
            "essay": essay,
            "weekly": weekly,
            "rows": {row["id"]: row for row in rows},
            "today": time.strftime("%Y%m%d%H:%M"),
        }

    data = asyncio.run(_run())
    assert "日记已记录" in data["diary"]
    assert "随笔已记录" in data["essay"]
    assert "周记已记录" in data["weekly"]
    types = {row["entry_type"] for row in data["rows"].values()}
    assert types == {"weekly", "diary", "essay"}
    # 未标注标题 → 默认标题是「当天日期时间」（分钟级，允许跨分钟）
    for row in data["rows"].values():
        assert row["title"].startswith(data["today"][:11]), row["title"]


def test_bridge_title_written_by_user_wins(tmp_path: Path) -> None:
    async def _run() -> dict:
        stack = await build_stack(tmp_path)
        service = CommandService(app=FakeApp(stack), config=ADMIN_CONFIG)
        text = await service.dispatch("diary", _view(), ["面试复盘", "|", "准备确实不足", "#工作"])
        rows = await stack.journal.list_recent(_scope(), limit=5)
        await stack.close()
        return {"text": text, "row": rows[0]}

    data = asyncio.run(_run())
    assert data["row"]["title"] == "面试复盘"
    assert data["row"]["content"] == "准备确实不足"
    assert data["row"]["entry_type"] == "diary"
    assert "面试复盘" in data["text"]


def test_bridge_list_filters_by_type(tmp_path: Path) -> None:
    async def _run() -> tuple[str, str]:
        stack = await build_stack(tmp_path)
        service = CommandService(app=FakeApp(stack), config=ADMIN_CONFIG)
        await service.dispatch("diary", _view(), ["今天的日记"])
        await service.dispatch("essay", _view(), ["今天的随笔"])
        diary_list = await service.dispatch("journals", _view(), ["日记"])
        all_list = await service.dispatch("journals", _view(), [])
        await stack.close()
        return diary_list, all_list

    diary_list, all_list = asyncio.run(_run())
    assert "今天的日记" in diary_list
    assert "今天的随笔" not in diary_list, "按类型筛选时不应混入其它类型"
    assert "今天的随笔" in all_list


def test_bridge_edit_updates_record(tmp_path: Path) -> None:
    async def _run() -> dict:
        stack = await build_stack(tmp_path)
        service = CommandService(app=FakeApp(stack), config=ADMIN_CONFIG)
        added = await stack.journal.add(_scope(), "原始正文")
        journal_id = int(added["journal_id"])

        # 只改正文：标题不动
        keep = await service.dispatch("journal-edit", _view(), [str(journal_id), "改后的正文"])
        # 换类型 + 换标题
        change = await service.dispatch(
            "journal-edit", _view(), [str(journal_id), "随笔", "新的标题", "|", "再改一次"]
        )
        bad = await service.dispatch("journal-edit", _view(), ["abc", "正文"])
        empty = await service.dispatch("journal-edit", _view(), [str(journal_id)])
        row = await stack.journals_repo.get(journal_id)
        await stack.close()
        return {"keep": keep, "change": change, "bad": bad, "empty": empty, "row": row}

    data = asyncio.run(_run())
    assert data["row"]["content"] == "再改一次"
    assert data["row"]["title"] == "新的标题"
    assert data["row"]["entry_type"] == "essay"
    assert "已更新" in data["change"]
    assert "编号" in data["bad"], "非数字编号应给出可读提示"
    assert "用法" in data["empty"]
    assert data["keep"].startswith("已更新"), "只改正文也应成功（标题保持原值）"


def test_reset_requires_confirmation(tmp_path: Path) -> None:
    async def _run() -> tuple[str, str, int]:
        stack = await build_stack(tmp_path)
        scope = stack.config.default_scope
        from super_astrbot.spec.scopes import MemoryScope

        await stack.memory.remember_text(
            MemoryScope.from_event(scope, umo="aiocqhttp:FriendMessage:1", user_id="u1"),
            "一条将被清空的记忆",
        )
        service = CommandService(app=FakeApp(stack), config=ADMIN_CONFIG)
        refused = await service.dispatch("reset", _view(), [])
        confirmed = await service.dispatch("reset", _view(), ["confirm"])
        remaining = len(
            await stack.memory.list_memories(
                MemoryScope.for_session("aiocqhttp:FriendMessage:1"), limit=10
            )
        )
        await stack.close()
        return refused, confirmed, remaining

    refused, confirmed, remaining = asyncio.run(_run())
    assert "不可逆" in refused
    assert "已清空" in confirmed
    assert remaining == 0


def test_unknown_action_returns_help(tmp_path: Path) -> None:
    async def _run() -> str:
        stack = await build_stack(tmp_path)
        service = CommandService(app=FakeApp(stack), config=ADMIN_CONFIG)
        text = await service.dispatch("不存在的动作", _view(), [])
        await stack.close()
        return text

    text = asyncio.run(_run())
    assert "未知子指令" in text
    assert "Super_AstrBot 指令" in text


def test_review_lists_pending_records_from_any_origin(tmp_path: Path) -> None:
    """待审队列统一展示反思与拟人化学习两类来源。"""

    class _WithItems(FakeApp):
        async def pending_reviews(self, scope: object, *, limit: int = 20):
            return [
                {
                    "id": 1,
                    "origin": "style",
                    "scope": "session:aiocqhttp:FriendMessage:1",
                    "created_at": 0.0,
                    "summary": "风格样本：你好 → 你好呀",
                }
            ]

    async def _run() -> tuple[str, str]:
        stack = await build_stack(tmp_path)
        service = CommandService(app=FakeApp(stack), config=ADMIN_CONFIG)
        empty = await service.dispatch("review", _view(), [])

        service2 = CommandService(app=_WithItems(stack), config=ADMIN_CONFIG)
        listed = await service2.dispatch("review", _view(), [])
        await stack.close()
        return empty, listed

    empty, listed = asyncio.run(_run())
    assert "待审队列为空" in empty
    assert "风格样本" in listed
    assert "/sab approve" in listed
