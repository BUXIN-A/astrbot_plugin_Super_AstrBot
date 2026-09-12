"""命令层测试：权限、参数校验、周记与重置流程。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from super_astrbot.commands import HELP_TEXT, CommandService
from super_astrbot.harness.protocols import EventView
from super_astrbot.journal import JournalConfig, JournalService

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
