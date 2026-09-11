"""能力注册表与依赖解析。

设计目的：把「有哪些能力、默认开不开、依赖什么」集中到一处声明，
使配置读取、面板展示、日志诊断都从同一份事实出发，避免多源不一致
（AstrNa 的经验教训：子配置白名单在多处手工维护会漂移）。

用法::

    effective = resolve_capabilities(config, overrides={"memory.vector_enabled": False})
    if effective["memory.enabled"]:
        ...
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, MutableMapping


@dataclass(frozen=True)
class Capability:
    """一个可开关能力的声明。"""

    key: str
    """配置路径（点号分隔），例如 ``memory.enabled``。"""

    title: str
    """人类可读名称，供面板/日志展示。"""

    domain: str
    """所属业务域，用于分组展示。"""

    default: bool = False
    """配置缺失时的默认值。"""

    depends_on: tuple[str, ...] = ()
    """前置能力 key；任一前置未生效时本能力视为关闭。"""

    description: str = ""

    runtime_dependent: bool = False
    """是否为「运行时可用性相关」能力。

    此类能力除了看配置，还需要由 harness 探测结果通过 ``overrides`` 传入，
    例如向量检索依赖是否存在可用的 Embedding Provider。
    """


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        key="basic.enabled",
        title="插件总开关",
        domain="basic",
        default=True,
        description="关闭后所有增强能力停止工作，但不卸载插件、不清理数据。",
    ),
    Capability(
        key="memory.enabled",
        title="长期记忆",
        domain="memory",
        default=True,
        depends_on=("basic.enabled",),
        description="记忆的采集、检索与注入总开关。",
    ),
    Capability(
        key="memory.capture",
        title="对话自动采集",
        domain="memory",
        default=True,
        depends_on=("memory.enabled",),
        description="自动把对话内容沉淀为记忆条目。",
    ),
    Capability(
        key="memory.fts_enabled",
        title="关键词全文检索",
        domain="memory",
        default=True,
        depends_on=("memory.enabled",),
        description="基于 SQLite FTS5 的关键词召回路径。",
    ),
    Capability(
        key="memory.vector_enabled",
        title="向量语义检索",
        domain="memory",
        default=True,
        depends_on=("memory.enabled",),
        runtime_dependent=True,
        description="基于 Embedding 的语义召回路径；不可用时自动降级。",
    ),
    Capability(
        key="reflection.enabled",
        title="反思式自我学习",
        domain="reflection",
        default=True,
        depends_on=("memory.enabled",),
        description="定期回顾对话并沉淀洞察为长期记忆。",
    ),
    Capability(
        key="journal.enabled",
        title="周记现实记忆",
        domain="journal",
        default=True,
        depends_on=("basic.enabled",),
        description="把用户现实记录作为高优先级的记忆来源。",
    ),
    Capability(
        key="journal.weekly_reflection",
        title="周度洞察生成",
        domain="journal",
        default=True,
        depends_on=("journal.enabled", "reflection.enabled"),
        description="每周固定时间阅读本周周记并产出洞察。",
    ),
)

_BY_KEY: dict[str, Capability] = {item.key: item for item in CAPABILITIES}


def capability(key: str) -> Capability:
    """按 key 取能力声明；不存在时抛 ``KeyError``（配置写错应尽早暴露）。"""
    return _BY_KEY[key]


def get_path(config: Mapping[str, Any], path: str, default: Any = None) -> Any:
    """按点号路径安全读取嵌套配置。

    与 group_chat_plus 的 ``config["key"]`` 直取不同，本函数永不抛 KeyError，
    缺失时返回默认值 —— 这是本项目对「配置读取必须带兜底」的硬性约定。
    """
    node: Any = config
    for part in path.split("."):
        if isinstance(node, Mapping):
            if part not in node:
                return default
            node = node[part]
            continue
        return default
    return node


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "开启", "是"}
    return default


def resolve_capabilities(
    config: Mapping[str, Any],
    overrides: Mapping[str, bool] | None = None,
) -> dict[str, bool]:
    """解析出各能力的最终生效状态。

    Args:
        config: 插件配置（``Mapping``，通常来自 ``AstrBotConfig``）。
        overrides: 运行时覆盖，用于表达「配置开了但环境不支持」的情况，
            例如 ``{"memory.vector_enabled": False}`` 表示无可用 Embedding。

    Returns:
        ``{capability_key: 是否生效}``。前置不满足的能力会被置为 ``False``。
    """
    overrides = dict(overrides or {})
    resolved: MutableMapping[str, bool] = {}

    # 依赖是单向的且无环，按声明顺序两轮即可稳定收敛。
    for item in CAPABILITIES:
        configured = _as_bool(get_path(config, item.key, item.default), item.default)
        if item.key in overrides:
            configured = _as_bool(overrides[item.key], configured)
        resolved[item.key] = configured

    # 依赖传导：反复传播直到无变化（能力数量很小，成本可忽略）。
    changed = True
    while changed:
        changed = False
        for item in CAPABILITIES:
            if not resolved.get(item.key):
                continue
            for dep in item.depends_on:
                if not resolved.get(dep, False):
                    resolved[item.key] = False
                    changed = True
                    break

    return dict(resolved)


def explain_disabled(
    resolved: Mapping[str, bool],
    overrides: Mapping[str, bool] | None = None,
) -> list[tuple[str, str]]:
    """给出「被关闭的能力」列表及原因，供启动日志一次性说明。"""
    overrides = dict(overrides or {})
    reasons: list[tuple[str, str]] = []
    for item in CAPABILITIES:
        if resolved.get(item.key, False):
            continue
        if item.key in overrides and not _as_bool(overrides[item.key], True):
            reasons.append((item.key, "运行环境不支持，已自动降级"))
            continue
        blocking: Iterable[str] = (dep for dep in item.depends_on if not resolved.get(dep, False))
        blocked_by = [f"{dep}（{_BY_KEY[dep].title}）" for dep in blocking]
        if blocked_by:
            reasons.append((item.key, "前置能力未生效：" + "、".join(blocked_by)))
        else:
            reasons.append((item.key, "配置中已关闭"))
    return reasons
