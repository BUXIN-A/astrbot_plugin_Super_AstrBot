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


# --------------------------------------------------------------------------- #
# 配置页动态下拉（嵌入模型列表注入）
# --------------------------------------------------------------------------- #


class EmbeddingProviderStub:
    def __init__(self, provider_id: str, model: str) -> None:
        self._meta = SimpleNamespace(id=provider_id, model=model, type="embedding")

    def meta(self) -> object:
        return self._meta


class EmbeddingContext(FakeContext):
    """带嵌入提供商的 Context 替身。"""

    def __init__(self, providers: list[object]) -> None:
        self._providers = providers

    def get_all_embedding_providers(self) -> list[object]:
        return list(self._providers)


class SchemaConfig(dict):
    """模拟 AstrBotConfig：既是 dict，又带可变的 schema。"""

    def __init__(self, data: dict, schema: dict) -> None:
        super().__init__(data)
        self.schema = schema


def _embedding_schema() -> dict:
    return {
        "memory": {
            "embedding_provider_id": {
                "description": "嵌入模型提供商",
                "type": "string",
                "default": "",
            }
        }
    }


def test_app_injects_embedding_options_into_schema(tmp_path: Path) -> None:
    async def _run() -> tuple[list[str], list[str], bool, bool]:
        schema = _embedding_schema()
        config = SchemaConfig({"memory": {"embedding_provider_id": "ollama_embedding"}}, schema)
        context = EmbeddingContext([EmbeddingProviderStub("ollama_embedding", "all-minilm:22m")])
        app = SuperAstrBotApp(star=FakeStar(), context=context, config=config, data_dir=tmp_path)
        await app.start()

        providers = [info.id for info in app.embedding_providers()]
        injected = app.sync_schema_options()
        field = schema["memory"]["embedding_provider_id"]
        options = list(field.get("options") or [])
        labels = list(field.get("labels") or [])

        await app.shutdown()
        return providers, options, bool(labels), injected

    providers, options, has_labels, injected = asyncio.run(_run())
    assert providers == ["ollama_embedding"]
    assert injected is True
    assert options[0] == "", "第一项应为「自动选择」"
    assert "ollama_embedding" in options
    assert has_labels is True


def test_app_keeps_text_field_when_no_embedding_provider(tmp_path: Path) -> None:
    """没有嵌入提供商时必须退回文本框，否则字段会变成无法输入的空下拉框。"""

    async def _run() -> tuple[bool, dict]:
        schema = _embedding_schema()
        config = SchemaConfig({}, schema)
        app = SuperAstrBotApp(
            star=FakeStar(), context=FakeContext(), config=config, data_dir=tmp_path
        )
        await app.start()
        injected = app.sync_schema_options()
        field = dict(schema["memory"]["embedding_provider_id"])
        await app.shutdown()
        return injected, field

    injected, field = asyncio.run(_run())
    assert injected is False
    assert field["type"] == "string"
    assert "options" not in field


def test_app_sync_schema_options_tolerates_plain_dict_config(tmp_path: Path) -> None:
    """配置是普通 dict（无 schema）时不得抛异常。"""

    async def _run() -> bool:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        result = app.sync_schema_options()
        await app.shutdown()
        return result

    assert asyncio.run(_run()) is False


# --------------------------------------------------------------------------- #
# 功能开关（控制台）
# --------------------------------------------------------------------------- #


def test_feature_catalog_and_hot_toggle(tmp_path: Path) -> None:
    async def _run() -> None:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()

        catalog = {item["key"]: item for item in app.feature_catalog()}
        assert catalog, "功能清单不应为空"
        assert catalog["reflection.enabled"]["enabled"] is True
        assert catalog["basic.enabled"]["status"] == "needs_reload"
        assert catalog["memory.vector_enabled"]["status"] == "runtime_unsupported"

        # 热切换：关闭自我学习 → 级联关闭周度洞察 + 停用对应调度任务
        result = await app.set_capability("reflection.enabled", False)
        assert result["ok"] is True
        assert result["enabled"] is False
        caps = app.capabilities
        assert caps["reflection.enabled"] is False
        assert caps["journal.weekly_reflection"] is False, "依赖传导应级联关闭"
        assert app._scheduler.get("reflection-scan").enabled is False
        assert app._scheduler.get("weekly-insight").enabled is False

        # 依赖未开启时不允许开启
        blocked = await app.set_capability("journal.weekly_reflection", True)
        assert blocked["ok"] is False
        assert "前置功能" in blocked["message"]

        # 总开关需重载
        needs_reload = await app.set_capability("basic.enabled", False)
        assert needs_reload["ok"] is False
        assert needs_reload["needs_reload"] is True

        # 运行环境不支持的能力不允许开启
        unsupported = await app.set_capability("memory.vector_enabled", True)
        assert unsupported["ok"] is False

        # 未知 key
        assert (await app.set_capability("nope.nope", True))["ok"] is False

        # 普通 dict 没有 save_config：流程成功但 persisted=False
        enabled = await app.set_capability("reflection.enabled", True)
        assert enabled["ok"] is True
        assert enabled["enabled"] is True
        assert enabled["persisted"] is False
        assert app._scheduler.get("reflection-scan").enabled is True

        await app.shutdown()

    asyncio.run(_run())


def test_feature_catalog_coerces_string_bool(tmp_path: Path) -> None:
    """配置写成字符串 "false" 时，面板的「已配置」必须与能力解析一致地显示为关闭。"""

    async def _run() -> dict:
        app = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config={"memory": {"enabled": "false"}},
            data_dir=tmp_path,
        )
        await app.start()
        catalog = {item["key"]: item for item in app.feature_catalog()}
        await app.shutdown()
        return catalog

    catalog = asyncio.run(_run())
    assert catalog["memory.enabled"]["enabled"] is False
    assert catalog["memory.enabled"]["configured"] is False


def test_app_agent_tools_degrade_without_framework(tmp_path: Path) -> None:
    """开启 Agent 记忆工具但框架不支持时，插件仍须正常就绪（只告警、不报错）。"""

    async def _run() -> tuple[bool, int, bool]:
        app = SuperAstrBotApp(
            star=FakeStar(),
            context=FakeContext(),
            config={"agent": {"memory_tools": True}},
            data_dir=tmp_path,
        )
        await app.start()
        ready = app.ready
        # 本地无 AstrBot → 拿不到 FunctionTool，注册数应为 0 且不影响就绪
        registered = app._setup_agent_tools()
        await app.shutdown()
        return ready, registered, bool(app.capabilities["agent.memory_tools"])

    ready, registered, capability_on = asyncio.run(_run())
    assert ready is True
    assert registered == 0
    assert capability_on is True


class MutableEmbeddingContext(FakeContext):
    """可动态增减嵌入提供商的 Context 替身（模拟 ProviderManager 后初始化）。"""

    def __init__(self) -> None:
        self.providers: list[object] = []

    def get_all_embedding_providers(self) -> list[object]:
        return list(self.providers)

    def add_embedding_provider(self, provider: object) -> None:
        self.providers.append(provider)


def test_refresh_capabilities_enables_vector_after_provider_appears(tmp_path: Path) -> None:
    """回归：ProviderManager 就绪后向量能力必须能自动启用。

    v0.2.0 在服务器上一直显示「向量能力不可用」：插件加载早于 ProviderManager
    初始化，且探测失败结果被永久缓存。
    """

    async def _run() -> tuple[dict, list[str], dict, list[str]]:
        context = MutableEmbeddingContext()
        app = SuperAstrBotApp(star=FakeStar(), context=context, config={}, data_dir=tmp_path)
        await app.start()

        before_caps = dict(app.capabilities)
        before_routes = list(app.memory.route_names)

        # 模拟 ProviderManager 初始化完成
        context.add_embedding_provider(EmbeddingProviderStub("ollama_embedding", "all-minilm:22m"))
        after_caps = app.refresh_capabilities()
        after_routes = list(app.memory.route_names)

        await app.shutdown()
        return before_caps, before_routes, after_caps, after_routes

    before_caps, before_routes, after_caps, after_routes = asyncio.run(_run())
    assert before_caps["memory.vector_enabled"] is False
    assert before_routes == ["keyword"]

    assert after_caps["memory.vector_enabled"] is True, "探测应能恢复，不得永久缓存失败结果"
    assert "vector" in after_routes
    assert "keyword" in after_routes
