"""应用容器集成测试。

这一层此前未被任何测试覆盖（``app.py`` 依赖 ``star`` / ``context``），因此这里用
最小替身把「启动 → 写入 → 召回注入 → 采集缓冲 → 状态 → 卸载」整条链路跑通，
确保装配逻辑、调度注册与资源收敛真的可用。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from super_astrbot.app import SuperAstrBotApp
from super_astrbot.harness.astrbot_llm import MEMORY_BLOCK_START
from super_astrbot.spec.scopes import MemoryScope

PLUGIN_NAME = "astrbot_plugin_Super_AstrBot"
UMO = "aiocqhttp:FriendMessage:10086"


class NullLogger:
    def debug(self, *args: object, **kwargs: object) -> None: ...
    def info(self, *args: object, **kwargs: object) -> None: ...
    def warning(self, *args: object, **kwargs: object) -> None: ...
    def error(self, *args: object, **kwargs: object) -> None: ...
    def exception(self, *args: object, **kwargs: object) -> None: ...


class FakeStar:
    """最小插件实例替身。"""

    name = PLUGIN_NAME

    def __init__(self) -> None:
        self.logger = NullLogger()
        self._kv: dict[str, object] = {}

    async def get_kv_data(self, key: str, default: object = None) -> object:
        return self._kv.get(key, default)

    async def put_kv_data(self, key: str, value: object) -> None:
        self._kv[key] = value

    async def delete_kv_data(self, key: str) -> None:
        self._kv.pop(key, None)


class FakeContext:
    """最小 Context 替身：无 Embedding、无对话模型（用于验证降级路径）。"""

    def get_all_embedding_providers(self) -> list[object]:
        return []

    def get_provider_by_id(self, provider_id: str) -> object | None:
        return None

    def get_all_providers(self) -> list[object]:
        return []

    async def send_message(self, umo: str, chain: object) -> bool:
        return True


class FakeEvent:
    def __init__(self, text: str) -> None:
        self.created_at = time.time()
        self.unified_msg_origin = UMO
        self._text = text

    def is_private_chat(self) -> bool:
        return True

    def get_group_id(self) -> str:
        return ""

    def get_platform_name(self) -> str:
        return "aiocqhttp"

    def get_session_id(self) -> str:
        return "10086"

    def get_sender_id(self) -> str:
        return "u-1"

    def get_sender_name(self) -> str:
        return "阿澈"

    def get_message_str(self) -> str:
        return self._text

    def is_admin(self) -> bool:
        return True

    def is_stopped(self) -> bool:
        return False

    def get_result(self) -> object | None:
        return None


def _new_app(tmp_path: Path, config: dict | None = None) -> SuperAstrBotApp:
    return SuperAstrBotApp(
        star=FakeStar(),
        context=FakeContext(),
        config=config or {},
        data_dir=tmp_path,
    )


def test_app_full_loop(tmp_path: Path) -> None:
    async def _run() -> tuple[str, int, dict, bool, str]:
        app = _new_app(tmp_path)
        await app.start()
        assert app.ready is True

        scope = MemoryScope.for_session(UMO)
        await app.memory.remember_text(scope, "用户阿澈喜欢在周末爬山放松", importance=0.9)

        request = SimpleNamespace(
            prompt="周末做什么好", system_prompt="基础提示", extra_user_content_parts=[]
        )
        await app.on_llm_request(FakeEvent("周末做什么好"), request)

        # 采集走后台任务，给它一点时间落库
        await asyncio.sleep(0.05)
        buffered = await app.memory.count_buffer(scope)

        status = await app.status()
        database_path = str(status.get("database") or "")

        await app.shutdown()
        ready_after_shutdown = app.ready

        # 卸载后再取一次状态，应优雅降级而不是抛异常
        post_status = await app.status()
        return (
            str(request.system_prompt),
            buffered,
            status,
            ready_after_shutdown,
            f"{database_path}|{post_status.get('database')}",
        )

    injected, buffered, status, ready_after_shutdown, db_paths = asyncio.run(_run())

    assert MEMORY_BLOCK_START in injected, "应注入记忆块"
    assert "爬山" in injected, "应召回相关记忆"
    assert "基础提示" in injected, "不应破坏原有 system_prompt"

    assert buffered == 1, "用户消息应进入对话缓冲"

    assert status["ready"] is True
    assert status["plugin_version"], "状态中应包含插件版本"
    framework = status["framework"]
    assert set(framework) >= {"version", "symbols_ok", "available", "missing"}
    assert framework["symbols_ok"] is False, "无 AstrBot 环境时应如实报告符号不可用"
    assert status["fts"] in (True, False)
    assert status["capabilities"]["memory.enabled"] is True
    # 无 Embedding 提供商 → 向量能力应被自动降级
    assert status["capabilities"]["memory.vector_enabled"] is False
    assert any(item["capability"] == "memory.vector_enabled" for item in status["degraded"])
    assert "keyword" in (status["memory"].get("routes") or [])

    job_keys = {job["key"] for job in status["scheduler"]}
    assert {"memory-maintenance", "reflection-scan"} <= job_keys

    assert ready_after_shutdown is False
    first_path, second_path = db_paths.split("|")
    assert "super_astrbot.db" in first_path and str(tmp_path) in first_path
    assert second_path == ""


def test_app_skips_capture_when_disabled(tmp_path: Path) -> None:
    async def _run() -> int:
        app = _new_app(tmp_path, config={"memory": {"capture": False}})
        await app.start()
        scope = MemoryScope.for_session(UMO)
        await app.on_llm_request(
            FakeEvent("随便说点什么"),
            SimpleNamespace(prompt="随便说点什么", system_prompt="", extra_user_content_parts=[]),
        )
        await asyncio.sleep(0.05)
        buffered = await app.memory.count_buffer(scope)
        await app.shutdown()
        return buffered

    assert asyncio.run(_run()) == 0


def test_app_disabled_total_switch_does_not_touch_storage(tmp_path: Path) -> None:
    async def _run() -> tuple[bool, bool, bool]:
        app = _new_app(tmp_path, config={"basic": {"enabled": False}})
        await app.start()
        ready = app.ready
        status = await app.status()
        db_created = (tmp_path / "super_astrbot.db").exists()
        await app.shutdown()
        return ready, db_created, bool(status["ready"])

    ready, db_created, status_ready = asyncio.run(_run())
    assert ready is False
    assert db_created is False, "总开关关闭时不应创建数据库"
    assert status_ready is False


def test_command_prefix_messages_are_not_captured(tmp_path: Path) -> None:
    async def _run() -> int:
        app = _new_app(tmp_path)
        await app.start()
        scope = MemoryScope.for_session(UMO)
        await app.on_llm_request(
            FakeEvent("/sab status"),
            SimpleNamespace(prompt="/sab status", system_prompt="", extra_user_content_parts=[]),
        )
        await asyncio.sleep(0.05)
        buffered = await app.memory.count_buffer(scope)
        await app.shutdown()
        return buffered

    assert asyncio.run(_run()) == 0


def test_journal_and_weekly_material_integration(tmp_path: Path) -> None:
    async def _run() -> tuple[int, bool, int]:
        app = _new_app(tmp_path)
        await app.start()
        scope = MemoryScope.for_session(UMO)
        result = await app.journal.add(scope, "这周开始跑步，心情好多了", tags=["运动"], emotion=4)
        material = await app.journal.weekly_material(scope)
        stats = await app.memory.stats_all()
        await app.shutdown()
        return int(result["journal_id"]) if result else 0, bool(material), int(stats["journals"])

    journal_id, has_material, journal_count = asyncio.run(_run())
    assert journal_id > 0
    assert has_material is True
    assert journal_count == 1
