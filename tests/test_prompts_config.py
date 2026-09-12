"""提示词可配置化、能力与配置 schema 一致性、MaiBot 增强。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from super_astrbot.harness.protocols import EventView
from super_astrbot.learning.prompts import build_reflection_prompt
from super_astrbot.maibot import MaiBotConfig, MaiBotService
from super_astrbot.persona import PersonaConfig, PersonaService
from super_astrbot.spec.capabilities import CAPABILITIES, get_path
from super_astrbot.spec.scopes import MemoryScope, ScopeType
from super_astrbot.storage import Database, StyleRepository
from super_astrbot.support import (
    PromptOverlay,
    PromptOverrides,
    PromptStore,
    missing_placeholders,
    render,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
NOW = 1_700_000_000.0

VIEW = EventView(
    umo="aiocqhttp:GroupMessage:10086",
    platform="aiocqhttp",
    session_id="10086",
    is_group=True,
    sender_id="u1",
    sender_name="小明",
    text="今天天气不错",
    timestamp=NOW,
)


# --------------------------------------------------------------------------- #
# 能力 ↔ 配置 schema 一致性（防止「开关写了但读不到」再次出现）
# --------------------------------------------------------------------------- #


def _schema_paths(node: dict, prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for key, value in node.items():
        path = f"{prefix}{key}"
        paths.add(path)
        items = value.get("items") if isinstance(value, dict) else None
        if isinstance(items, dict) and value.get("type") == "object":
            paths |= _schema_paths(items, f"{path}.")
    return paths


def test_every_capability_key_is_declared_in_conf_schema() -> None:
    schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    paths = _schema_paths(schema)
    missing = [item.key for item in CAPABILITIES if item.key not in paths]
    assert missing == [], f"能力键在配置 schema 中不存在：{missing}"


def test_context_capability_uses_schema_key() -> None:
    # 回归：能力键曾是 context.governance，而 schema 与配置读取用的是 context.enabled，
    # 导致「配置页勾选不生效、面板开关写入的键在重载后被框架清理」。
    keys = {item.key for item in CAPABILITIES}
    assert "context.enabled" in keys
    assert "context.governance" not in keys


def test_journal_admin_only_write_has_no_cross_level_condition() -> None:
    schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    # condition 只支持同层级比较，写 basic.enabled 会让该项在配置页永远不显示。
    assert "condition" not in schema["journal"]["items"]["admin_only_write"]


def test_prompts_are_moved_out_of_conf_schema() -> None:
    # 提示词已迁移到插件页面：配置页不再声明这些键，改由 prompts.json 承载，
    # 既避免长文本塞进配置页，也避免未声明键在重载时被框架当脏键清理。
    schema = json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    assert "prompts" not in schema


def test_prompt_catalog_defaults_are_self_consistent() -> None:
    from super_astrbot.prompt_catalog import BY_KEY, PROMPT_SPECS

    assert len(BY_KEY) == len(PROMPT_SPECS), "提示词 key 存在重复"
    for spec in PROMPT_SPECS:
        assert spec.default.strip(), f"{spec.key} 缺少内置默认文本"
        assert not missing_placeholders(spec.default, spec.required), (
            f"{spec.key} 的内置默认缺少必填占位符 {spec.required}"
        )


def test_prompt_catalog_covers_every_domain_key() -> None:
    from super_astrbot.prompt_catalog import BY_KEY

    for key in (
        "reflection_system",
        "reflection_template",
        "weekly_system",
        "weekly_template",
        "summary_system",
        "summary_template",
        "summary_update_template",
        "proactive_system",
        "proactive_template",
        "jargon_system",
        "jargon_template",
        "affinity_system",
        "affinity_template",
        "graph_system",
        "graph_template",
        "auto_review_system",
        "auto_review_template",
    ):
        assert key in BY_KEY, f"面板目录缺少提示词 {key}"


# --------------------------------------------------------------------------- #
# 提示词覆盖
# --------------------------------------------------------------------------- #


def test_override_is_used_when_placeholders_present() -> None:
    overrides = PromptOverrides(
        {"prompts": {"reflection_template": "素材：{transcript}｜上限：{max_facts}"}}
    )
    prompt = build_reflection_prompt("一段对话", max_facts=3, overrides=overrides)
    assert prompt == "素材：一段对话｜上限：3"


def test_override_falls_back_when_placeholder_missing() -> None:
    PromptOverrides.REJECTED.clear()
    try:
        overrides = PromptOverrides({"prompts": {"reflection_template": "没有占位符"}})
        prompt = build_reflection_prompt("一段对话", max_facts=3, overrides=overrides)
        assert "没有占位符" not in prompt
        assert "reflection_template" in PromptOverrides.REJECTED
    finally:
        PromptOverrides.REJECTED.clear()


def test_override_falls_back_on_broken_braces() -> None:
    PromptOverrides.REJECTED.clear()
    try:
        overrides = PromptOverrides(
            {"prompts": {"reflection_template": "坏模板 { {transcript} {max_facts}"}}
        )
        prompt = build_reflection_prompt("一段对话", max_facts=3, overrides=overrides)
        # 花括号不配对 → 视为无效模板并回退内置默认。
        assert "坏模板" not in prompt
    finally:
        PromptOverrides.REJECTED.clear()


def test_render_never_raises() -> None:
    assert render("坏模板 { 未闭合") == "坏模板 { 未闭合"
    assert render("未知 {foo}", bar=1) == "未知 {foo}"
    assert render("{a}-{b}", a=1, b=2) == "1-2"


# --------------------------------------------------------------------------- #
# 提示词持久化与热更新
# --------------------------------------------------------------------------- #


def test_prompt_store_roundtrip(tmp_path: Path) -> None:
    store = PromptStore(tmp_path / "prompts.json")
    assert store.available is True
    assert store.load() == {}

    assert store.save({"reflection_template": "素材：{transcript}｜{max_facts}", "blank": "   "})
    # 空值不入表：空字符串等价于「使用内置默认」。
    assert store.load() == {"reflection_template": "素材：{transcript}｜{max_facts}"}


def test_prompt_store_tolerates_broken_file(tmp_path: Path) -> None:
    path = tmp_path / "prompts.json"
    path.write_text("{ 不是 JSON", encoding="utf-8")
    assert PromptStore(path).load() == {}


def test_prompt_store_without_path_is_readonly() -> None:
    store = PromptStore(None)
    assert store.available is False
    assert store.load() == {}
    assert store.save({"a": "b"}) is False


def test_prompt_overlay_masks_plugin_config_prompts() -> None:
    # 即使覆盖为空，也必须是覆盖值说了算：否则配置页残留的旧提示词会「复活」。
    base = {"memory": {"enabled": True}, "prompts": {"reflection_system": "旧值"}}
    empty = PromptOverlay(base, {})
    assert get_path(empty, "memory.enabled") is True
    assert get_path(empty, "prompts.reflection_system", "") == ""

    filled = PromptOverlay(base, {"reflection_system": "新值"})
    assert filled["prompts"]["reflection_system"] == "新值"


def test_prompt_overrides_bind_refreshes_cache() -> None:
    # 服务持有同一个 PromptOverrides 实例，就地换源必须让新值立刻生效。
    handle = PromptOverrides({"prompts": {"reflection_template": "A{transcript}{max_facts}"}})
    assert build_reflection_prompt("x", max_facts=1, overrides=handle) == "Ax1"

    handle.bind({"prompts": {"reflection_template": "B{transcript}{max_facts}"}})
    assert build_reflection_prompt("x", max_facts=1, overrides=handle) == "Bx1"

    handle.bind(None)
    assert build_reflection_prompt("x", max_facts=1, overrides=handle) != "Bx1"


# --------------------------------------------------------------------------- #
# MaiBot 增强
# --------------------------------------------------------------------------- #


def test_user_scope_requires_enabled_and_sender(tmp_path: Path) -> None:
    async def _run() -> None:
        db = Database(tmp_path / "m.db")
        await db.connect()
        try:
            styles = StyleRepository(db)
            enabled = MaiBotService(
                config=MaiBotConfig.from_mapping({"maibot": {"enabled": True}}),
                styles=styles,
                clock=lambda: NOW,
            )
            scope = enabled.user_scope(VIEW)
            assert scope is not None
            assert scope.scope_type == ScopeType.USER
            assert scope.scope_id == "u1"

            disabled = MaiBotService(
                config=MaiBotConfig.from_mapping({"maibot": {"enabled": False}}),
                styles=styles,
                clock=lambda: NOW,
            )
            assert disabled.user_scope(VIEW) is None
            assert enabled.user_scope(EventView(umo="x", sender_id="")) is None
        finally:
            await db.close()

    asyncio.run(_run())


def test_maintain_decays_style_weights(tmp_path: Path) -> None:
    async def _run() -> None:
        db = Database(tmp_path / "m.db")
        await db.connect()
        try:
            styles = StyleRepository(db)
            scope = MemoryScope.for_session(VIEW.umo)
            await styles.add(
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
                situation="今天天气不错",
                expression="是啊，挺舒服的",
                weight=1.0,
                source="pair",
                created_at=NOW,
            )
            service = MaiBotService(
                config=MaiBotConfig.from_mapping(
                    {"maibot": {"enabled": True, "time_decay_half_life_days": 1.0}}
                ),
                styles=styles,
                clock=lambda: NOW,
            )
            assert service.decay_enabled() is True
            await service.maintain(now=NOW)
            rows = await styles.list_active([scope], limit=5)
            assert rows and float(rows[0]["weight"]) < 1.0
            assert float(rows[0]["weight"]) > 0.4
        finally:
            await db.close()

    asyncio.run(_run())


def test_persona_service_learns_in_extra_scope(tmp_path: Path) -> None:
    async def _run() -> None:
        from super_astrbot.loop.state_store import MemoryStateStore
        from super_astrbot.storage import (
            AffinityRepository,
            JargonRepository,
            ReviewRepository,
        )

        db = Database(tmp_path / "p.db")
        await db.connect()
        try:
            styles = StyleRepository(db)
            persona = PersonaService(
                config=PersonaConfig.from_mapping(
                    {"persona": {"style": True, "style_approval_required": False}}
                ),
                patterns=styles,
                jargons=JargonRepository(db),
                affinities=AffinityRepository(db),
                reviews=ReviewRepository(db),
                llm=None,  # type: ignore[arg-type]
                injector=None,  # type: ignore[arg-type]
                store=MemoryStateStore(),
                extra_scope=lambda view: MemoryScope(ScopeType.USER, view.sender_id),
                clock=lambda: NOW,
            )
            outcome = await persona.learn_style(
                VIEW, user_text="今天天气不错", reply_text="是啊，挺舒服的"
            )
            assert outcome.stored is True
            session_rows = await persona.style_patterns(MemoryScope.for_session(VIEW.umo))
            user_rows = await persona.style_patterns(MemoryScope(ScopeType.USER, "u1"))
            assert session_rows and user_rows
        finally:
            await db.close()

    asyncio.run(_run())
