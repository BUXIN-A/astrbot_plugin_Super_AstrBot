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

    hot_reloadable: bool = True
    """是否支持运行时热切换。

    为 ``False`` 时（例如总开关），控制台仍会写入配置，但需要重载插件才生效。
    """


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        key="basic.enabled",
        title="插件总开关",
        domain="basic",
        default=True,
        hot_reloadable=False,
        description="关闭后所有增强能力停止工作，但不卸载插件、不清理数据。需重载插件生效。",
    ),
    Capability(
        key="basic.debug_log",
        title="调试日志",
        domain="basic",
        default=False,
        description="输出检索路命中、注入字符数、注入方式等细节，用于排障。",
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
        key="memory.capture_groups",
        title="在群聊中采集",
        domain="memory",
        default=True,
        depends_on=("memory.capture",),
        description="关闭后群聊消息不进入对话缓冲（私聊不受影响）。",
    ),
    Capability(
        key="memory.capture_private",
        title="在私聊中采集",
        domain="memory",
        default=True,
        depends_on=("memory.capture",),
        description="关闭后私聊消息不进入对话缓冲（群聊不受影响）。",
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
        key="memory.rerank_enabled",
        title="重排序（Rerank）",
        domain="memory",
        default=False,
        depends_on=("memory.enabled",),
        description=(
            "召回后用重排序模型对候选按相关性重新打分，提升 top-k 精度；"
            "模型未配置或调用失败时自动回退（默认本地词法重排），不影响召回本身。默认关闭。"
        ),
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
        key="consolidation.enabled",
        title="记忆整合",
        domain="consolidation",
        default=False,
        depends_on=("memory.enabled",),
        description=(
            "周期性把「足够旧 + 重要度低」的零散记忆按作用域聚类，"
            "用模型汇聚成一条更完整的记忆，原始条目归档；默认关闭。"
        ),
    ),
    Capability(
        key="journal.enabled",
        title="现实桥（周记 / 日记 / 随笔）",
        domain="journal",
        default=True,
        depends_on=("basic.enabled",),
        description="把用户的现实书写（周记 / 日记 / 随笔）作为高优先级的记忆来源。",
    ),
    Capability(
        key="journal.weekly_reflection",
        title="周度洞察生成",
        domain="journal",
        default=True,
        depends_on=("journal.enabled", "reflection.enabled"),
        description="每周固定时间阅读本周现实桥记录并产出洞察。",
    ),
    Capability(
        key="agent.memory_tools",
        title="Agent 记忆工具",
        domain="agent",
        default=False,
        depends_on=("memory.enabled",),
        hot_reloadable=False,
        description=(
            "向模型暴露「记忆检索 / 记忆写入」函数工具，让 Bot 能主动回忆与记录。"
            "会改变所有会话的模型行为，默认关闭；开启后需重载插件生效。"
        ),
    ),
    Capability(
        key="context.enabled",
        title="上下文治理",
        domain="context",
        default=False,
        depends_on=("basic.enabled",),
        description=(
            "请求级压缩：估算 token，折叠早期工具结果与图片，必要时把更早的历史压成摘要。"
            "只影响本次请求、不改写对话历史；默认关闭。"
        ),
    ),
    Capability(
        key="group.enabled",
        title="群聊语义",
        domain="group",
        default=False,
        depends_on=("basic.enabled",),
        description=(
            "读空气决策：按注意力得分 + 冷却 + 每小时配额决定是否在群聊中主动插话，"
            "并把短时间内的连续消息合并为一次请求。关闭时完全不参与唤醒判定。"
        ),
    ),
    Capability(
        key="proactive.enabled",
        title="主动交互",
        domain="proactive",
        default=False,
        depends_on=("basic.enabled",),
        description=(
            "双轨调度：按固定时间或会话静默时长，基于记忆素材生成并主动发送一条消息。"
            "受安静时段、每日上限与手动暂停约束；默认关闭。"
        ),
    ),
    Capability(
        key="persona.style",
        title="风格模仿",
        domain="persona",
        default=False,
        depends_on=("basic.enabled",),
        description=(
            "从「用户提问 → Bot 回答」邻接对中零成本学习表达方式，"
            "遇到相似场景时作为 few-shot 示例注入。学习结果默认需审批；默认关闭。"
        ),
    ),
    Capability(
        key="persona.jargon",
        title="群内用语理解",
        domain="persona",
        default=False,
        depends_on=("basic.enabled",),
        description=(
            "先按词频筛出候选，再用模型推断词义；仅在对话中出现该词时注入含义"
            "（明确要求不复读）。学习结果默认需审批；默认关闭。"
        ),
    ),
    Capability(
        key="persona.affinity",
        title="社交好感度",
        domain="persona",
        default=False,
        depends_on=("basic.enabled",),
        description=(
            "按交互类型累积对每个用户的好感度，随时间回归基线，"
            "并按档位给出语气指引。规则优先、模型兜底；默认关闭。"
        ),
    ),
    Capability(
        key="graph.enabled",
        title="记忆知识图谱",
        domain="graph",
        default=False,
        depends_on=("memory.enabled",),
        description=(
            "从记忆内容抽取实体与关系建图（零成本共现为主，可选模型增强），"
            "检索时把图中相邻记忆一并召回，并在面板提供图谱可视化。默认关闭。"
        ),
    ),
    Capability(
        key="review.auto",
        title="自动审核",
        domain="review",
        default=False,
        depends_on=("basic.enabled",),
        description=(
            "按规则先审待审队列，规则不确定时（可配置）交由模型裁决；"
            "自动通过的记录带留痕、可审计，人工审批始终优先。默认关闭。"
        ),
    ),
    Capability(
        key="maibot.enabled",
        title="MaiBot 增强",
        domain="maibot",
        default=False,
        depends_on=("basic.enabled",),
        description=(
            "MaiBot 风格的扩展学习：表达模式按发送者个性化、学习产物统一时间衰减。"
            "与拟人化学习、知识图谱配合使用；默认关闭。"
        ),
    ),
    # ------------------------------------------------------------------ #
    # 群聊拟人化融合域（全部为进程内模块，来源见「蒸馏项目」）
    # ------------------------------------------------------------------ #
    Capability(
        key="members.enabled",
        title="群友识别",
        domain="members",
        default=True,
        depends_on=("basic.enabled",),
        description=(
            "把身份观测聚合成「一位群友一份档案」：稳定称呼、关系类型、"
            "个人记忆摘要与差异化对话策略（语气 / 称呼 / 话题 / 禁忌）。"
            "策略注入只改写表层表达，不切换人格；默认开启，无档案时零影响。"
        ),
    ),
    Capability(
        key="forge.enabled",
        title="PersonaForge 三层人格",
        domain="forge",
        default=True,
        depends_on=("basic.enabled",),
        description=(
            "单人格的三层建模：核心特质（大五人格 / 价值观 / 防御机制）· "
            "表层风格（句长 / 词汇 / 口头禅 / 语气词）· 动态状态（心情 / 能量 / 关系）。"
            "回复前注入人格摘要，抑制长对话中的风格漂移（来源 PersonaForge，进程内）。"
        ),
    ),
    Capability(
        key="forge.introspection",
        title="选择性双过程内省",
        domain="forge",
        default=False,
        depends_on=("forge.enabled",),
        description=(
            "仅在关键轮（首次对话 / 情绪强度高 / 触及核心兴趣）先做一次「内心独白」"
            "再组织回复，用少量模型调用换取一致性。会产生额外模型调用；默认关闭。"
        ),
    ),
    Capability(
        key="evolution.enabled",
        title="人格演化轨迹",
        domain="evolution",
        default=True,
        depends_on=("forge.enabled",),
        description=(
            "每轮互动按经验类型（冲突 / 脆弱 / 连接 / 成功…）施加微量特质漂移并留痕，"
            "长期累积成真实的人物弧光（来源 character-sim 影响向量，零成本规则判定）。"
        ),
    ),
    Capability(
        key="empathy.enabled",
        title="CogEmp 共情管线",
        domain="empathy",
        default=True,
        depends_on=("basic.enabled",),
        description=(
            "三阶段共情：情绪识别 → 原因理解 → 认知共情润色。"
            "识别到情绪信号时注入对应语气指引（来源 CogEmp，进程内提示词模块）。"
        ),
    ),
    Capability(
        key="latrace.enabled",
        title="LATRACE 时序图谱",
        domain="latrace",
        default=True,
        depends_on=("memory.enabled",),
        description=(
            "进程内时序知识图谱：实体与关系带时间语义与证据链（来源对话 / 离线蒸馏），"
            "检索时把图上相邻的记忆一并召回并附 provenance（来源 LATRACE 思想，进程内重写）。"
        ),
    ),
    Capability(
        key="tiers.enabled",
        title="letta 三级记忆",
        domain="tiers",
        default=True,
        depends_on=("memory.enabled",),
        description=(
            "把记忆分入核心（core）/ 归档（archive）/ 召回（recall）三级并给出占比，"
            "核心层优先注入、归档层不参与召回（来源 letta 概念移植，不引入其服务）。"
        ),
    ),
    Capability(
        key="worldbook.enabled",
        title="世界书 / Lorebook",
        domain="worldbook",
        default=True,
        depends_on=("basic.enabled",),
        description=(
            "关于用户 / 群 / 世界的事实卡：按触发词命中后注入（来源 AMBRACE Lorebook）。"
            "没有条目时零影响；默认开启。"
        ),
    ),
    Capability(
        key="fusion.decay",
        title="艾宾浩斯衰减接管",
        domain="fusion",
        default=False,
        depends_on=("memory.enabled",),
        description=(
            "用 R=exp(-Δt/S) 统一记忆保留率口径，面板展示自然遗忘与主动复习双曲线；"
            "「写回重要度」默认关闭，避免影响既有检索权重（来源 AMBRACE，进程内）。"
        ),
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


def set_path(config: Any, path: str, value: Any) -> bool:
    """按点号路径写入嵌套配置，自动创建缺失的中间层。

    仅供控制台的「功能开关」使用；返回是否写入成功。
    对非 dict/Mapping 的中间层不做覆盖（避免把异常结构写坏）。
    """
    parts = path.split(".")
    if not parts or any(not part for part in parts):
        return False

    node: Any = config
    for part in parts[:-1]:
        if not isinstance(node, MutableMapping):
            return False
        child = node.get(part)
        if child is None:
            child = {}
            node[part] = child
        if not isinstance(child, MutableMapping):
            return False
        node = child

    if not isinstance(node, MutableMapping):
        return False
    node[parts[-1]] = value
    return True


_TRUE_STRINGS = frozenset({"1", "true", "yes", "on", "是", "开启"})


def as_bool(value: Any, default: bool = False) -> bool:
    """把任意配置值规整为布尔。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    return default


def as_int(
    value: Any,
    default: int = 0,
    *,
    low: int | None = None,
    high: int | None = None,
) -> int:
    """把任意配置值规整为整数，可选上下界裁剪。"""
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if low is not None:
        result = max(low, result)
    if high is not None:
        result = min(high, result)
    return result


def as_float(
    value: Any,
    default: float = 0.0,
    *,
    low: float | None = None,
    high: float | None = None,
) -> float:
    """把任意配置值规整为浮点数，可选上下界裁剪。"""
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if low is not None:
        result = max(low, result)
    if high is not None:
        result = min(high, result)
    return result


def as_str(value: Any, default: str = "") -> str:
    """把任意配置值规整为字符串（``None`` 取默认值）。"""
    return default if value is None else str(value)


def as_str_tuple(value: Any) -> tuple[str, ...]:
    """把配置值规整为去空白、去空的字符串元组。

    兼容三种写法：字符串列表（配置页原生）、逗号分隔字符串（手填更省事）、
    换行分隔字符串；中文逗号一并接受。
    """
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = str(value or "").replace("，", ",").replace("\n", ",").split(",")
    return tuple(str(item).strip() for item in items if str(item).strip())


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
        configured = as_bool(get_path(config, item.key, item.default), item.default)
        if item.key in overrides:
            configured = as_bool(overrides[item.key], configured)
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
        if item.key in overrides and not as_bool(overrides[item.key], True):
            reasons.append((item.key, "运行环境不支持，已自动降级"))
            continue
        blocking: Iterable[str] = (dep for dep in item.depends_on if not resolved.get(dep, False))
        blocked_by = [f"{dep}（{_BY_KEY[dep].title}）" for dep in blocking]
        if blocked_by:
            reasons.append((item.key, "前置能力未生效：" + "、".join(blocked_by)))
        else:
            reasons.append((item.key, "配置中已关闭"))
    return reasons
