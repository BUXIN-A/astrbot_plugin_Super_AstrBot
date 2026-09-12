"""配置 schema 运行时注入 与 嵌入模型枚举 测试。

对应修复：AstrBot 的 ``_special: "select_provider"`` 硬编码为对话模型，
所以嵌入模型只能由插件运行时把真实列表注入到字段的 options/labels。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from super_astrbot.harness.astrbot_llm import AstrBotEmbeddingGateway
from super_astrbot.harness.schema_options import clear_options, inject_string_options


class NullHost:
    class _Logger:
        def debug(self, *args: Any, **kwargs: Any) -> None: ...
        def info(self, *args: Any, **kwargs: Any) -> None: ...
        def warning(self, *args: Any, **kwargs: Any) -> None: ...

    def log(self) -> Any:
        return self._Logger()


class FakeConfig(dict):
    """模拟 AstrBotConfig：既是 dict，又带 schema 属性。"""

    def __init__(self, data: dict[str, Any], schema: dict[str, Any] | None = None) -> None:
        super().__init__(data)
        if schema is not None:
            self.schema = schema


def _schema() -> dict[str, Any]:
    return {
        "memory": {
            "embedding_provider_id": {
                "description": "嵌入模型提供商",
                "type": "string",
                "default": "",
            }
        }
    }


# --------------------------------------------------------------------------- #
# schema 注入
# --------------------------------------------------------------------------- #


def test_inject_string_options_success() -> None:
    schema = _schema()
    config = FakeConfig({}, schema)
    ok = inject_string_options(
        config,
        ("memory", "embedding_provider_id"),
        ["", "ollama_embedding"],
        ["（自动）", "ollama_embedding · all-minilm"],
    )
    assert ok is True
    field = schema["memory"]["embedding_provider_id"]
    assert field["options"] == ["", "ollama_embedding"]
    assert field["labels"] == ["（自动）", "ollama_embedding · all-minilm"]
    # 不改动其它字段
    assert field["type"] == "string"


def test_inject_without_labels_uses_options_as_labels() -> None:
    schema = _schema()
    config = FakeConfig({}, schema)
    assert inject_string_options(config, ("memory", "embedding_provider_id"), ["a", "b"]) is True
    assert schema["memory"]["embedding_provider_id"]["labels"] == ["a", "b"]


def test_inject_returns_false_on_bad_paths() -> None:
    config = FakeConfig({}, _schema())
    assert inject_string_options(config, ("memory", "missing"), ["a"]) is False
    assert inject_string_options(config, ("missing", "deeper"), ["a"]) is False
    assert (
        inject_string_options(config, ("memory", "embedding_provider_id", "type"), ["a"]) is False
    )


def test_inject_returns_false_without_schema() -> None:
    plain = FakeConfig({})
    assert inject_string_options(plain, ("memory", "embedding_provider_id"), ["a"]) is False
    assert (
        inject_string_options({"memory": {}}, ("memory", "embedding_provider_id"), ["a"]) is False
    )


def test_clear_options_reverts_to_text_field() -> None:
    schema = _schema()
    config = FakeConfig({}, schema)
    inject_string_options(config, ("memory", "embedding_provider_id"), ["a"])
    assert clear_options(config, ("memory", "embedding_provider_id")) is True
    field = schema["memory"]["embedding_provider_id"]
    assert "options" not in field
    assert "labels" not in field


# --------------------------------------------------------------------------- #
# 嵌入模型枚举
# --------------------------------------------------------------------------- #


class FakeEmbeddingProvider:
    def __init__(self, provider_id: str, model: str = "") -> None:
        self._meta = SimpleNamespace(id=provider_id, model=model, type="embedding")

    def meta(self) -> Any:
        return self._meta


class FakeContext:
    def __init__(
        self,
        loaded: list[Any] | None = None,
        configured: list[dict[str, Any]] | None = None,
    ) -> None:
        self._loaded = loaded or []
        self.provider_manager = SimpleNamespace(providers_config=configured or [])

    def get_all_embedding_providers(self) -> list[Any]:
        return list(self._loaded)


def test_list_providers_merges_loaded_and_configured() -> None:
    context = FakeContext(
        loaded=[FakeEmbeddingProvider("ollama_embedding", "all-minilm:22m")],
        configured=[
            {"id": "ollama_embedding", "provider_type": "embedding", "model": "dup"},
            {
                "id": "openai_embedding",
                "provider_type": "embedding",
                "model": "text-embedding-3-small",
            },
            {"id": "some_chat", "provider_type": "chat_completion", "model": "gpt"},
        ],
    )
    gateway = AstrBotEmbeddingGateway(context, NullHost())
    providers = gateway.list_providers()
    ids = [info.id for info in providers]

    # 去重 + 只保留 embedding 类型 + 合并未启用的配置项
    assert ids == ["ollama_embedding", "openai_embedding"]
    assert providers[0].model == "all-minilm:22m"
    assert providers[1].model == "text-embedding-3-small"
    assert all(info.type == "embedding" for info in providers)


def test_list_providers_handles_legacy_type_suffix() -> None:
    context = FakeContext(configured=[{"id": "legacy", "provider_type": "ollama_embedding"}])
    gateway = AstrBotEmbeddingGateway(context, NullHost())
    assert [info.id for info in gateway.list_providers()] == ["legacy"]


def test_list_providers_empty_when_nothing_configured() -> None:
    gateway = AstrBotEmbeddingGateway(FakeContext(), NullHost())
    assert gateway.list_providers() == []


def test_list_providers_tolerates_broken_context() -> None:
    class Broken:
        def get_all_embedding_providers(self) -> list[Any]:
            raise RuntimeError("适配器异常")

        @property
        def provider_manager(self) -> Any:
            raise RuntimeError("不可用")

    gateway = AstrBotEmbeddingGateway(Broken(), NullHost())
    assert gateway.list_providers() == []
