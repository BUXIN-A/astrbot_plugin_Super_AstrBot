"""记忆知识图谱配置。

图谱是「可选增强」：默认关闭，且抽取方式可选零成本共现、模型抽取或两者结合。
把配置集中到一处，是为了让「开关 / 抽取策略 / 规模上限 / 提示词」都从同一份事实
出发，避免面板、服务与提示词三处各写一套默认值而漂移。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..spec.capabilities import as_bool, as_float, as_int, as_str, get_path
from ..support import PromptOverrides

PROMPT_GRAPH_SYSTEM = "graph_system"
"""系统提示词在配置中的键名。"""

PROMPT_GRAPH_TEMPLATE = "graph_template"
"""用户提示词模板在配置中的键名。"""

DEFAULT_GRAPH_SYSTEM = (
    "你是一个知识图谱抽取助手。你需要从一段记忆文本中识别出实体及其关系，"
    "只输出 JSON，不要任何解释。"
)

DEFAULT_GRAPH_TEMPLATE = """从下面的记忆文本中抽取实体与关系。

<memory>
{content}
</memory>

只输出 JSON，格式如下：
{{
  "entities": [{{"name": "实体名", "type": "person|place|org|event|concept|thing"}}],
  "relations": [{{"subject": "实体A", "predicate": "关系", "object": "实体B"}}]
}}

要求：
1. 实体名必须是文本中**明确出现**的词，不要推断、不要代词消解成未知对象；
2. 最多 {max_entities} 个实体、{max_relations} 条关系；
3. 关系必须是「实体A 与 实体B」之间可读的短关系词（如「喜欢」「属于」「参与」）；
4. 没有可抽取内容时输出 {{"entities": [], "relations": []}}。
"""

EXTRACTOR_MODES = ("deterministic", "llm", "both")
"""允许的抽取模式；非法值一律回退 ``deterministic``。"""


@dataclass
class GraphConfig:
    """记忆知识图谱的生效配置。"""

    enabled: bool = False
    extractor: str = "deterministic"
    max_entities: int = 8
    max_relations: int = 8
    min_term_chars: int = 2
    max_term_chars: int = 12
    max_entities_per_scope: int = 2000
    expansion_hops: int = 1
    expansion_limit: int = 24
    second_hop_weight: float = 0.4
    half_life_days: float = 30.0
    weight_floor: float = 0.05
    prune_min_weight: float = 0.1
    provider_id: str = ""
    timeout_seconds: float = 90.0
    prompts: PromptOverrides = field(default_factory=PromptOverrides)
    """用户自定义提示词（留空即用内置默认）。"""

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "GraphConfig":
        timeout = as_int(get_path(config, "runtime.llm_timeout_seconds", 45), 45, low=5, high=300)
        extractor = as_str(get_path(config, "graph.extractor", "deterministic")).strip().lower()
        if extractor not in EXTRACTOR_MODES:
            extractor = "deterministic"
        result = cls(
            enabled=as_bool(get_path(config, "graph.enabled", False), False),
            extractor=extractor,
            max_entities=as_int(get_path(config, "graph.max_entities", 8), 8, low=1, high=30),
            max_relations=as_int(get_path(config, "graph.max_relations", 8), 8, low=1, high=30),
            min_term_chars=as_int(get_path(config, "graph.min_term_chars", 2), 2, low=2, high=8),
            max_term_chars=as_int(get_path(config, "graph.max_term_chars", 12), 12, low=4, high=32),
            max_entities_per_scope=as_int(
                get_path(config, "graph.max_entities_per_scope", 2000),
                2000,
                low=100,
                high=50000,
            ),
            expansion_hops=as_int(get_path(config, "graph.expansion_hops", 1), 1, low=1, high=2),
            expansion_limit=as_int(
                get_path(config, "graph.expansion_limit", 24), 24, low=1, high=200
            ),
            second_hop_weight=as_float(
                get_path(config, "graph.second_hop_weight", 0.4), 0.4, low=0.0, high=1.0
            ),
            half_life_days=as_float(
                get_path(config, "graph.half_life_days", 30.0), 30.0, low=1.0, high=365.0
            ),
            weight_floor=as_float(
                get_path(config, "graph.weight_floor", 0.05), 0.05, low=0.0, high=1.0
            ),
            prune_min_weight=as_float(
                get_path(config, "graph.prune_min_weight", 0.1), 0.1, low=0.0, high=1.0
            ),
            provider_id=as_str(get_path(config, "graph.provider_id", "")),
            timeout_seconds=float(min(180, max(20, timeout * 2))),
        )
        result.prompts = PromptOverrides(config)
        return result


__all__ = [
    "GraphConfig",
    "PROMPT_GRAPH_SYSTEM",
    "PROMPT_GRAPH_TEMPLATE",
    "DEFAULT_GRAPH_SYSTEM",
    "DEFAULT_GRAPH_TEMPLATE",
    "EXTRACTOR_MODES",
]
