"""应用容器集成测试。

这一层此前未被任何测试覆盖（``app.py`` 依赖 ``star`` / ``context``），因此这里用
最小替身把「启动 → 写入 → 召回注入 → 采集缓冲 → 状态 → 卸载」整条链路跑通，
确保装配逻辑、调度注册与资源收敛真的可用。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

from super_astrbot.app import SuperAstrBotApp
from super_astrbot.harness.astrbot_llm import MEMORY_BLOCK_START
from super_astrbot.learning.prompts import build_reflection_prompt
from super_astrbot.spec.scopes import MemoryScope, ScopeType

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


class _ReplyEvent(FakeEvent):
    """带「已生成结果」的事件，用于走通 ``on_after_message_sent``。"""

    def __init__(self, text: str, reply: str) -> None:
        super().__init__(text)
        self._reply = reply

    def get_result(self) -> Any:
        return SimpleNamespace(get_plain_text=lambda: self._reply)


def test_bot_reply_is_buffered_as_first_person(tmp_path: Path) -> None:
    """Bot 自己的发言以「我：」进入缓冲，与「用户(昵称)：」成对（不再是第三人称「助手：」）。"""

    async def _run() -> tuple[list[str], list[str]]:
        app = _new_app(tmp_path)
        await app.start()
        scope = MemoryScope.for_session(UMO)
        try:
            await app.on_llm_request(
                FakeEvent("在吗"),
                SimpleNamespace(prompt="在吗", system_prompt="", extra_user_content_parts=[]),
            )
            await app.on_after_message_sent(_ReplyEvent("在吗", "在的，我一直都在"))
            await asyncio.sleep(0.05)
            bot_lines = [
                item.content
                for item in await app.memory.buffer_material(scope)
                if item.content.startswith("我：")
            ]
            user_lines = [
                item.content
                for item in await app.memory.buffer_material(scope)
                if item.content.startswith("用户(")
            ]
            return bot_lines, user_lines
        finally:
            await app.shutdown()

    bot_lines, user_lines = asyncio.run(_run())
    assert bot_lines == ["我：在的，我一直都在"], "Bot 发言应用第一人称「我：」记录"
    assert user_lines == ["用户(阿澈)：在吗"], "用户发言前缀保持不变，两者成对"
    assert all("助手：" not in line for line in bot_lines + user_lines)


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


# --------------------------------------------------------------------------- #
# 面板 Web API（handler 工厂回归）
# --------------------------------------------------------------------------- #


def test_web_api_registered_handlers_are_not_coroutines(tmp_path: Path) -> None:
    """回归测试：handler 工厂误写成 ``async def`` 会把协程对象注册成处理函数，
    请求到达时 ``coroutine is not callable`` → 面板直接 500 Internal Server Error。
    （真实案例：feature-setting 曾因此每次保存都 500。）
    """

    async def _run() -> list[str]:
        import sys
        import types

        stub = types.ModuleType("astrbot.api.web")
        stub.request = types.SimpleNamespace(username=None, query={})
        stub.request.json = _async_none
        stub.request.files = _async_empty
        stub.json_response = lambda data: data
        stub.error_response = lambda message, status_code=400: {
            "status": "error",
            "message": message,
        }
        stub.file_response = lambda path, filename=None, content_type=None: {}
        stub.stream_response = lambda gen: {}
        stub.PluginUploadFile = type("PluginUploadFile", (), {})
        sys.modules.setdefault("astrbot.api.web", stub)

        from super_astrbot.web.api import register_web_apis

        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        registered: dict[str, object] = {}

        class _Ctx:
            def register_web_api(self, route, handler, methods, desc):
                registered[route] = handler

        register_web_apis(_Ctx(), app)
        await app.shutdown()
        return list(registered.values())


    handlers = asyncio.run(_run())
    assert handlers, "未注册任何面板接口"
    import inspect

    for handler in handlers:
        assert not inspect.iscoroutine(handler), "有协程被当成 handler 注册"
        assert callable(handler)


async def _async_none(default=None):
    return default


async def _async_empty():
    return {}

def test_prompt_customization_roundtrip(tmp_path: Path) -> None:
    """面板提示词定制：内置默认回填 → 保存即时生效 → 校验占位符 → 重置恢复。"""

    async def _run() -> tuple[dict, dict, str, dict, dict, dict, dict, dict]:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:

            def item() -> dict:
                return {row["key"]: row for row in app.prompt_catalog()}["reflection_template"]

            initial = item()
            saved = await app.set_prompt(
                "reflection_template", "素材：{transcript}｜上限：{max_facts}"
            )
            effective = build_reflection_prompt(
                "一段对话", max_facts=3, overrides=app.reflection_config.prompts
            )
            after_save = item()
            rejected = await app.set_prompt("reflection_template", "缺少占位符")
            persisted = json.loads((tmp_path / "prompts.json").read_text(encoding="utf-8"))
            reset = await app.reset_prompt("reflection_template")
            after_reset = item()
        finally:
            await app.shutdown()
        return initial, saved, effective, after_save, rejected, persisted, reset, after_reset

    initial, saved, effective, after_save, rejected, persisted, reset, after_reset = asyncio.run(
        _run()
    )

    # 未自定义时面板看到的就是内置默认文本，可直接在此基础上修改。
    assert initial["custom"] is False
    assert initial["value"] == initial["default"]
    assert initial["required"] == ["transcript", "max_facts"]

    assert saved["ok"] is True
    assert effective == "素材：一段对话｜上限：3", "保存后必须立刻生效"
    assert after_save["custom"] is True

    assert rejected["ok"] is False
    assert "占位符" in rejected["message"]
    assert persisted == {"reflection_template": "素材：{transcript}｜上限：{max_facts}"}

    assert reset["ok"] is True
    assert after_reset["custom"] is False
    assert after_reset["value"] == after_reset["default"]
    assert json.loads((tmp_path / "prompts.json").read_text(encoding="utf-8")) == {}


def test_rerank_disabled_by_default_and_falls_back_when_enabled(tmp_path: Path) -> None:
    """重排序默认关闭；开启但没有提供商时状态应显示「已回退」而不影响就绪。"""

    async def _run(enabled: bool) -> tuple[dict, dict, dict, str, bool]:
        config = {"memory": {"rerank_enabled": True}} if enabled else {}
        app = SuperAstrBotApp(
            star=FakeStar(), context=FakeContext(), config=config, data_dir=tmp_path
        )
        await app.start()
        try:
            feature = {item["key"]: item for item in app.feature_catalog()}
            status = await app.status()
            return (
                dict(app.capabilities),
                status.get("rerank") or {},
                feature["memory.rerank_enabled"],
                app.memory_config.rerank_enabled and app._retriever.rerank_note or "",
                app.ready,
            )
        finally:
            await app.shutdown()

    caps, status, feature, note, ready = asyncio.run(_run(True))
    assert ready is True, "没有重排序提供商也必须正常就绪"
    assert caps["memory.rerank_enabled"] is True
    assert feature["enabled"] is True
    assert status["enabled"] is True
    assert status["available"] is False
    assert status["fallback"] == "lexical"
    assert "不可用" in note and "回退" in note

    caps_off, status_off, feature_off, _, _ = asyncio.run(_run(False))
    assert caps_off["memory.rerank_enabled"] is False
    assert feature_off["enabled"] is False
    assert status_off["enabled"] is False


# --------------------------------------------------------------------------- #
# 周记管理 + 每周总结（面板管理链路 + 导入导出）
# --------------------------------------------------------------------------- #


def test_journal_panel_management_roundtrip(tmp_path: Path) -> None:
    """周记管理模式：新增 → 列表 → 编辑 → 删除，且周记与记忆联动。"""

    async def _run() -> dict:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            added = await app.panel_journal_add(
                {"content": "这周打算早睡早起", "tags": "生活, 计划", "emotion": 4, "scope": "user:10086"}
            )
            page = await app.memory.list_all_journals(offset=0, limit=20, keyword="早睡")
            journal_id = page[0]["id"]

            updated = await app.panel_journal_update(
                journal_id, {"content": "改成晚睡两周后调整", "tags": ["生活"]}
            )
            row = (await app.memory.list_all_journals(offset=0, limit=20, keyword="晚睡"))[0]
            memory_content = ""
            if row.get("memory_id"):
                items = await app.memory.list_all(offset=0, limit=50, kind="journal")
                match = next((m for m in items if m.id == row["memory_id"]), None)
                memory_content = match.content if match else ""

            deleted = await app.panel_journal_delete(journal_id)
            remaining = await app.memory.list_all_journals(offset=0, limit=20, keyword="晚睡")

            # 记忆联动：编辑后记忆正文同步，删除后记忆一并遗忘
            return {
                "added": added,
                "updated": updated,
                "memory_content": memory_content,
                "deleted": deleted,
                "remaining": len(remaining),
            }
        finally:
            await app.shutdown()

    result = asyncio.run(_run())
    assert result["added"]["ok"] is True
    assert result["added"]["journal_id"] > 0
    assert result["updated"]["ok"] is True
    assert result["memory_content"] == "改成晚睡两周后调整", "编辑周记应联动同步记忆正文"
    assert result["deleted"]["ok"] is True
    assert result["remaining"] == 0, "删除周记应连带遗忘对应记忆"


def test_journal_export_import_roundtrip(tmp_path: Path) -> None:
    """周记导出/导入：导出含 scope/标签/事件时间，导入走同一写入路径并保留元数据。"""

    async def _run() -> tuple[list[dict], dict]:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            await app.panel_journal_add(
                {
                    "content": "本周完成插件面板重制",
                    "tags": ["开发"],
                    "emotion": 5,
                    "scope": "user:10086",
                    "event_time": 1757500000.0,
                }
            )
            exported = await app.panel_journal_export()
            # 换一个干净实例导入（模拟迁移）
            other = SuperAstrBotApp(
                star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path / "other"
            )
            await other.start()
            try:
                result = await other.panel_journal_import(exported)
                page = await other.memory.list_all_journals(offset=0, limit=20)
            finally:
                await other.shutdown()
            return exported, {**result, "page": page}
        finally:
            await app.shutdown()

    exported, result = asyncio.run(_run())
    assert exported and exported[0]["content"] == "本周完成插件面板重制"
    assert result["imported"] == 1 and result["skipped"] == 0
    assert len(result["page"]) == 1
    row = result["page"][0]
    assert row["content"] == "本周完成插件面板重制"
    assert float(row["event_time"]) == 1757500000.0, "导入应保留事件时间"
    assert "开发" in (row["tags"] or "[]")


def test_bridge_panel_types_titles_and_selected_export(tmp_path: Path) -> None:
    """现实桥面板链路：三类文本 + 标题（留空用条目时间）+ 勾选导出。"""

    async def _run() -> dict:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            # 1) 显式标题 + 类型
            await app.panel_journal_add(
                {
                    "content": "面试前夜的碎念",
                    "title": "面试前夜",
                    "type": "diary",
                    "scope": "user:10086",
                    "event_time": 1757500000.0,
                }
            )
            # 2) 不填标题 + 类型缺省 → 默认标题取自条目时间，类型回落到周记
            await app.panel_journal_add(
                {"content": "本周跑步三次", "scope": "user:10086", "event_time": 1757500000.0}
            )
            # 3) 随笔
            await app.panel_journal_add(
                {"content": "地铁上的比喻", "entry_type": "essay", "scope": "user:10086"}
            )

            diary_page = await app.memory.list_all_journals(offset=0, limit=20, entry_type="diary")
            all_page = await app.memory.list_all_journals(offset=0, limit=20)
            counts = await app.panel_journal_types()

            # 勾选导出：只导出勾选的两条
            picked = await app.panel_journal_export_selected(
                {"ids": [item["id"] for item in all_page[:2]], "all": False}
            )
            # 全选当前筛选：按类型导出该类型的全部
            filtered = await app.panel_journal_export_selected(
                {"all": True, "type": "diary", "keyword": ""}
            )
            empty = await app.panel_journal_export_selected({"ids": [], "all": False})
            return {
                "diary_page": diary_page,
                "all_page": all_page,
                "counts": counts,
                "picked": picked,
                "filtered": filtered,
                "empty": empty,
            }
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    assert len(data["all_page"]) == 3
    assert len(data["diary_page"]) == 1
    assert data["counts"].get("diary") == 1 and data["counts"].get("essay") == 1
    assert data["counts"].get("weekly") == 1

    diary_row = data["diary_page"][0]
    assert diary_row["title"] == "面试前夜"
    assert diary_row["entry_type"] == "diary"

    default_row = next(row for row in data["all_page"] if row["content"] == "本周跑步三次")
    assert default_row["entry_type"] == "weekly"
    assert default_row["title"].startswith("2025091"), default_row["title"]
    assert ":" in default_row["title"], "默认标题应包含时间（YYYYMMDDHH:MM）"

    assert data["picked"]["ok"] is True and data["picked"]["count"] == 2
    assert data["picked"]["mode"] == "ids"
    assert all("title" in item and "type" in item for item in data["picked"]["items"])
    assert data["filtered"]["mode"] == "filter" and data["filtered"]["count"] == 1
    assert data["empty"]["ok"] is False, "没有勾选任何条目时应拒绝导出"


def test_bridge_panel_update_keeps_title_when_omitted(tmp_path: Path) -> None:
    """面板编辑：未带 title/type 时保持原值，显式清空标题则回填默认标题。"""

    async def _run() -> dict:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            added = await app.panel_journal_add(
                {
                    "content": "原始内容",
                    "title": "自定义标题",
                    "type": "essay",
                    "scope": "user:10086",
                    "event_time": 1757500000.0,
                }
            )
            journal_id = int(added["journal_id"])
            # 面板编辑只送正文（等同旧版行为）→ 标题与类型不动
            await app.panel_journal_update(journal_id, {"content": "改后的内容"})
            kept = (await app.memory.list_all_journals(offset=0, limit=5))[0]
            # 用户清空标题框 → 按条目时间回填默认标题
            await app.panel_journal_update(journal_id, {"content": "改后的内容", "title": ""})
            restored = (await app.memory.list_all_journals(offset=0, limit=5))[0]
            # 改类型
            await app.panel_journal_update(journal_id, {"content": "改后的内容", "type": "diary"})
            changed = (await app.memory.list_all_journals(offset=0, limit=5))[0]
            return {"kept": kept, "restored": restored, "changed": changed}
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    assert data["kept"]["title"] == "自定义标题"
    assert data["kept"]["entry_type"] == "essay"
    assert data["restored"]["title"] != "", "清空标题后不应留下空白标题"
    assert data["restored"]["entry_type"] == "essay"
    assert data["changed"]["entry_type"] == "diary"


def test_weeklies_list_delete_export_import(tmp_path: Path) -> None:
    """每周总结：独立列表（source=weekly_reflection）→ 删除 → 导出/导入往返。"""

    async def _run() -> dict:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            memory = app.memory
            await memory.remember_text(
                MemoryScope(ScopeType.GLOBAL, "*"),
                "第一周总结：完成面板重制",
                kind="insight",
                importance=0.8,
                source="weekly_reflection",
            )
            await memory.remember_text(
                MemoryScope(ScopeType.GLOBAL, "*"),
                "第二周总结：周记管理上线",
                kind="insight",
                importance=0.75,
                source="weekly_reflection",
            )

            page = await app.weeklies_page(offset=0, limit=10, keyword="总结")
            assert page["total"] == 2, page

            # 普通记忆列表不应混入每周总结（按来源过滤）
            all_active = await memory.list_all(offset=0, limit=50, source="manual")

            first_id = page["items"][0]["id"]
            deleted = await app.panel_weekly_delete(first_id)
            after = await app.weeklies_page(offset=0, limit=10)

            exported = await app.panel_weekly_export()
            other = SuperAstrBotApp(
                star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path / "wk"
            )
            await other.start()
            try:
                imported = await other.panel_weekly_import(exported)
                other_page = await other.weeklies_page(offset=0, limit=10)
            finally:
                await other.shutdown()

            return {
                "all_active": len(all_active),
                "deleted": deleted,
                "after_total": after["total"],
                "exported": exported,
                "imported": imported,
                "other_total": other_page["total"],
                "other_first": other_page["items"][0]["content"] if other_page["items"] else "",
            }
        finally:
            await app.shutdown()

    result = asyncio.run(_run())
    assert result["all_active"] == 0, "手动来源列表不应混入每周总结"
    assert result["deleted"]["ok"] is True
    assert result["after_total"] == 1
    assert result["imported"]["imported"] == 1
    assert result["other_total"] == 1
    assert "完成面板重制" in result["other_first"]


def test_journal_import_rejects_garbage(tmp_path: Path) -> None:
    """导入容错：非 dict / 空内容条目跳过，不中断整体导入。"""

    async def _run() -> dict:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            return await app.panel_journal_import(
                [{"content": "有效条目"}, {"content": "   "}, "垃圾", {"title": "没内容"}]
            )
        finally:
            await app.shutdown()

    result = asyncio.run(_run())
    assert result["imported"] == 1 and result["skipped"] == 3


# --------------------------------------------------------------------------- #
# 配置维护 + 记忆导入导出
# --------------------------------------------------------------------------- #


def _make_config_like_loader(schema: dict, values: dict | None = None) -> dict:
    """模拟真实 AstrBotConfig 构造：以 schema 默认值打底，再叠加已保存值。"""
    import copy

    def defaults_of(node: dict) -> dict:
        out = {}
        for key, field in node.items():
            if not isinstance(field, dict) or "type" not in field:
                continue
            ftype = field.get("type")
            if ftype == "object":
                out[key] = defaults_of(field.get("items") or {})
            elif "default" in field:
                out[key] = copy.deepcopy(field["default"])
            else:
                out[key] = {"int": 0, "float": 0.0, "bool": False, "string": "", "text": ""}.get(ftype, "")
        return out

    config = defaults_of(schema)
    if values:
        for key, value in values.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key].update(value)
            else:
                config[key] = value
    return config


def test_config_export_import_roundtrip(tmp_path: Path) -> None:
    """配置导出/导入：导出即全量配置；导入剔除未知键、保留已知值、热应用生效。"""

    schema = {
        "basic": {"type": "object", "items": {
            "enabled": {"type": "bool", "default": True},
            "admin_only_commands": {"type": "bool", "default": True},
        }},
        "memory": {"type": "object", "items": {
            "enabled": {"type": "bool", "default": True},
            "retrieval_top_k": {"type": "int", "default": 5},
        }},
        "persona": {"type": "object", "items": {
            "style": {"type": "bool", "default": False},
        }},
    }

    async def _run() -> dict:
        class LoaderLikeConfig(dict):
            """模拟真实 AstrBotConfig：构造时按 schema 补默认值，且带 .schema 与规范化方法。"""

            def __init__(self, data):
                super().__init__(_make_config_like_loader(schema))
                self.update(data)
                object.__setattr__(self, "schema", schema)

            def check_config_integrity(self, refer_conf, conf, path="", schema=None):
                """核心同款语义：补缺失默认、剔除未知键、类型不符回退默认。"""
                import copy as _copy

                def fix(reference, target):
                    for key, ref_value in reference.items():
                        if key not in target or target[key] is None:
                            target[key] = _copy.deepcopy(ref_value)
                        elif isinstance(ref_value, dict):
                            if not isinstance(target[key], dict):
                                target[key] = _copy.deepcopy(ref_value)
                            else:
                                fix(ref_value, target[key])
                    for key in [k for k in target if k not in reference]:
                        del target[key]

                fix(refer_conf, conf)
                return True

        app = SuperAstrBotApp(
            star=FakeStar(), context=FakeContext(),
            config=LoaderLikeConfig({}), data_dir=tmp_path,
        )
        await app.start()
        try:
            exported = app.config_export()
            config = dict(exported["config"])
            assert config.get("basic", {}).get("enabled") is not None, "导出应包含全量配置"

            # 篡改：未知键 + 已知键改动 + 类型错误
            config["hacked_key"] = "should be dropped"
            config.setdefault("memory", {})["retrieval_top_k"] = "13"
            config.setdefault("persona", {})["style"] = True

            result = await app.config_import(config)  # api 层已解包信封，门面直接收配置字典
            after = app.config_export()["config"]
            caps = app.capabilities
            return {
                "result": result,
                "after": after,
                "caps": caps,
                "unknown_gone": "hacked_key" not in after,
                "top_k": after.get("memory", {}).get("retrieval_top_k"),
                "style_on": caps.get("persona.style"),
            }
        finally:
            await app.shutdown()

    result = asyncio.run(_run())
    assert result["result"]["ok"] is True
    assert result["result"]["persisted"] is False, "普通 dict 无落盘能力，应标记而非报错"
    assert result["unknown_gone"], "未知键必须被剔除"
    assert result["top_k"] == 13, "字符串数字应按 schema 收敛为 int"
    assert result["style_on"] is True, "导入的能力开关应热应用"


def test_config_import_rejects_garbage(tmp_path: Path) -> None:
    async def _run() -> tuple[dict, dict]:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            not_dict = await app.config_import(["nope"])
            empty = await app.config_import({})
            return not_dict, empty
        finally:
            await app.shutdown()

    not_dict, empty = asyncio.run(_run())
    assert not_dict["ok"] is False
    assert empty["ok"] is False


def test_memories_export_import_roundtrip(tmp_path: Path) -> None:
    """记忆导出/导入：排除遗忘与缓冲；导入保留类型/来源/标签/作用域。"""

    async def _run() -> dict:
        app = SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)
        await app.start()
        try:
            memory = app.memory
            scope = MemoryScope(ScopeType.USER, "10086")
            await memory.remember_text(
                scope, "有效记忆：喜欢简洁回复", kind="preference",
                importance=0.7, source="manual", tags=["偏好"],
            )
            await memory.remember_text(
                scope, "被遗忘的记忆", kind="fact",
                importance=0.5, source="manual", status="forgotten",
            )
            await memory.remember_text(
                scope, "对话缓冲的原始消息", kind="fact",
                importance=0.3, source="chat", status="buffered",
            )

            exported = await app.panel_memory_export()
            other = SuperAstrBotApp(
                star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path / "mem"
            )
            await other.start()
            try:
                result = await other.panel_memory_import(exported)
                page = await other.memory.list_all(offset=0, limit=50)
            finally:
                await other.shutdown()

            contents = sorted(item.content for item in page)
            return {
                "exported_count": len(exported),
                "imported": result["imported"],
                "skipped": result["skipped"],
                "contents": contents,
                "scopes": [item.scope_type + ":" + item.scope_id for item in page],
            }
        finally:
            await app.shutdown()

    result = asyncio.run(_run())
    assert result["exported_count"] == 1, "只应导出有效记忆（排除遗忘/缓冲）"
    assert result["imported"] == 1
    assert result["skipped"] == 0
    assert result["contents"] == ["有效记忆：喜欢简洁回复"]
    assert result["scopes"] == ["user:10086"], "导入应保留作用域"


class _RecordingLogger:
    """日志替身：把每条日志留档，供「后台能不能看见」这类断言使用。"""

    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def _write(self, level: str, message: object, *args: object) -> None:
        self.records.append((level, str(message) % args if args else str(message)))

    def debug(self, message: object, *args: object, **kwargs: object) -> None:
        self._write("debug", message, *args)

    def info(self, message: object, *args: object, **kwargs: object) -> None:
        self._write("info", message, *args)

    def warning(self, message: object, *args: object, **kwargs: object) -> None:
        self._write("warning", message, *args)

    def error(self, message: object, *args: object, **kwargs: object) -> None:
        self._write("error", message, *args)

    def exception(self, message: object, *args: object, **kwargs: object) -> None:
        self._write("error", message, *args)


def test_reflection_scan_logs_skip_reason(tmp_path: Path) -> None:
    """「为什么没反思」必须能在后台日志里看到，而不是只写在 debug 里。

    回归：跳过原因原本只走 debug，而 ``basic.debug_log`` 默认关闭，
    于是「反思从不触发」在后台完全没有线索。
    """

    async def _run() -> list[tuple[str, str]]:
        app = _new_app(
            tmp_path,
            config={
                "reflection": {
                    "enabled": True,
                    "mode": "rounds",
                    "min_messages": 20,
                    "trigger_rounds": 50,
                },
            },
        )
        await app.start()
        logger = _RecordingLogger()
        app._logger = logger
        try:
            await app.memory.buffer_episode(MemoryScope.for_session(UMO), "用户：只想问一句")
            await app._job_reflection_scan()
            return list(logger.records)
        finally:
            await app.shutdown()

    records = asyncio.run(_run())
    infos = [message for level, message in records if level == "info"]
    assert any("反思扫描" in msg for msg in infos), infos
    assert any("本作用域 1/20" in msg and "全库 1/50" in msg for msg in infos), infos
