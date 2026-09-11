"""能力注册表与依赖解析测试。"""

from __future__ import annotations

from super_astrbot.spec.capabilities import (
    explain_disabled,
    get_path,
    resolve_capabilities,
)


def test_get_path_never_raises() -> None:
    config = {"memory": {"enabled": False}}
    assert get_path(config, "memory.enabled") is False
    assert get_path(config, "memory.missing", "fallback") == "fallback"
    assert get_path({}, "a.b.c", 1) == 1
    assert get_path({"a": "not-a-mapping"}, "a.b", 2) == 2


def test_total_switch_disables_everything() -> None:
    resolved = resolve_capabilities({"basic": {"enabled": False}})
    assert resolved["basic.enabled"] is False
    assert resolved["memory.enabled"] is False
    assert resolved["journal.enabled"] is False
    assert resolved["journal.weekly_reflection"] is False
    assert not any(resolved.values())


def test_runtime_override_only_disables_vector() -> None:
    resolved = resolve_capabilities({}, overrides={"memory.vector_enabled": False})
    assert resolved["basic.enabled"] is True
    assert resolved["memory.enabled"] is True
    assert resolved["memory.fts_enabled"] is True
    assert resolved["memory.vector_enabled"] is False


def test_weekly_reflection_needs_reflection() -> None:
    resolved = resolve_capabilities({"reflection": {"enabled": False}})
    assert resolved["journal.enabled"] is True
    assert resolved["reflection.enabled"] is False
    assert resolved["journal.weekly_reflection"] is False


def test_explain_disabled_reports_reason() -> None:
    resolved = resolve_capabilities({"basic": {"enabled": False}})
    reasons = dict(explain_disabled(resolved, {}))
    assert "memory.enabled" in reasons
    assert "前置能力" in reasons["memory.enabled"]

    resolved2 = resolve_capabilities({}, overrides={"memory.vector_enabled": False})
    reasons2 = dict(explain_disabled(resolved2, {"memory.vector_enabled": False}))
    assert "运行环境不支持" in reasons2["memory.vector_enabled"]


def test_string_bool_coercion() -> None:
    resolved = resolve_capabilities({"memory": {"enabled": "false"}})
    assert resolved["memory.enabled"] is False
    resolved_on = resolve_capabilities({"memory": {"enabled": "on"}})
    assert resolved_on["memory.enabled"] is True
