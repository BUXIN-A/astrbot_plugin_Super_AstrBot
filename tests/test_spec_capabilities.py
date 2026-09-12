"""能力注册表与依赖解析测试。"""

from __future__ import annotations

from super_astrbot.spec.capabilities import (
    as_bool,
    as_float,
    as_int,
    as_str,
    capability,
    explain_disabled,
    get_path,
    resolve_capabilities,
    set_path,
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


# --------------------------------------------------------------------------- #
# 功能开关所需的写路径与元数据
# --------------------------------------------------------------------------- #


def test_set_path_creates_missing_layers() -> None:
    config: dict = {}
    assert set_path(config, "memory.enabled", False) is True
    assert config == {"memory": {"enabled": False}}
    assert set_path(config, "a.b.c", 1) is True
    assert config["a"]["b"]["c"] == 1


def test_set_path_rejects_broken_structure() -> None:
    config = {"memory": "not-a-mapping"}
    assert set_path(config, "memory.enabled", True) is False
    assert config["memory"] == "not-a-mapping", "不得覆盖异常结构"


def test_set_path_rejects_empty_path() -> None:
    assert set_path({}, "", 1) is False
    assert set_path({}, "a..b", 1) is False


def test_new_capabilities_registered() -> None:
    for key in ("basic.debug_log", "memory.capture_groups", "memory.capture_private"):
        assert capability(key).key == key


def test_capture_subflags_follow_capture() -> None:
    resolved = resolve_capabilities({"memory": {"capture": False}})
    assert resolved["memory.capture"] is False
    assert resolved["memory.capture_groups"] is False
    assert resolved["memory.capture_private"] is False


def test_hot_reloadable_flags() -> None:
    # 总开关无法热切换（涉及整条运行时链路），其余功能均可热切换
    assert capability("basic.enabled").hot_reloadable is False
    for key in ("memory.enabled", "reflection.enabled", "journal.enabled", "basic.debug_log"):
        assert capability(key).hot_reloadable is True


def test_capability_metadata_complete_for_ui() -> None:
    """控制台需要 title/domain/description，缺失会导致开关列表空白。"""
    for key in (
        "basic.enabled",
        "memory.enabled",
        "reflection.enabled",
        "journal.enabled",
    ):
        item = capability(key)
        assert item.title and item.domain and item.description


def test_config_value_helpers() -> None:
    """统一后的配置值解析必须在各域之间保持同一套语义。"""
    assert as_bool("false", True) is False
    assert as_bool("0", True) is False
    assert as_bool("on", False) is True
    assert as_bool(None, True) is True

    assert as_int("12", 0, low=1, high=10) == 10
    assert as_int("bad", 7, low=1, high=10) == 7
    assert as_float("0.5", 1.0, low=0.0, high=1.0) == 0.5
    assert as_float(None, 2.5) == 2.5
    assert as_str(None, "x") == "x"
    assert as_str(3, "") == "3"
