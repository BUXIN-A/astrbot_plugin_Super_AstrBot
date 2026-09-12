"""记忆知识图谱服务：抽取 → 落地 → 检索扩展 → 维护。

分层意图：

- 索引是**增量幂等**的：重复索引同一条记忆会先清掉旧链接再重建，避免内容变更后
  节点残留；实体/关系本身按「作用域 + 规范化名称」合并，只累加权重。
- 检索扩展给图谱检索路用：先按查询词命中实体，再取这些实体反向关联的记忆；
  可选两跳扩展（邻居实体），并用更低的权重表达「间接相关」。
- 维护（衰减/裁剪/剪枝）由调度器按天触发，权重用半衰期换算成每日衰减系数。

任何 LLM 失败都必须降级为零成本结果或空，绝不抛给调用方——图谱只是增强，
不能因为模型抖动打断记忆主链路。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Sequence

from ..spec.errors import LlmError, safe_detail
from ..spec.scopes import MemoryScope, ScopeType
from ..storage import GraphRepository
from ..support import half_life_factor, tokenize
from .config import GraphConfig
from .extractor import (
    build_graph_prompt,
    canonicalize,
    deterministic_extract,
    graph_system_prompt,
    parse_graph_json,
)

SOURCE_DETERMINISTIC = "deterministic"
SOURCE_LLM = "llm"

_CONFIDENCE_DETERMINISTIC = 0.35
_CONFIDENCE_LLM = 0.7
_DIRECT_WEIGHT = 1.0


@dataclass
class GraphOutcome:
    """一次记忆索引的结果。"""

    indexed: bool = False
    entities: int = 0
    relations: int = 0
    reason: str = ""


class GraphService:
    """图谱索引、检索扩展与维护。"""

    def __init__(
        self,
        *,
        config: GraphConfig,
        entities: GraphRepository,
        llm: Any | None = None,
        observer: Any | None = None,
        logger: Any | None = None,
    ) -> None:
        self._config = config
        self._entities = entities
        self._llm = llm
        self._observer = observer
        """索引完成回调 ``(实体数,)``，用于运行监控埋点（可为 None）。"""
        self._logger = logger

    @property
    def config(self) -> GraphConfig:
        return self._config

    def enabled(self) -> bool:
        return self._config.enabled

    # ------------------------------------------------------------------ #
    # 索引
    # ------------------------------------------------------------------ #

    async def index_memory(
        self,
        scope: MemoryScope,
        *,
        memory_id: int,
        content: str,
        now: float | None = None,
    ) -> GraphOutcome:
        """把一条记忆抽取为实体与关系并落库；失败降级为带 reason 的结果。"""
        if not self._config.enabled:
            return GraphOutcome(reason="知识图谱未启用")
        text = (content or "").strip()
        if not text:
            return GraphOutcome(reason="记忆内容为空")
        at = time.time() if now is None else now

        try:
            await self._entities.unlink_memory(memory_id)
        except Exception as exc:  # 清理失败不阻断重建
            self._debug("清理图谱旧链接失败：%s", safe_detail(exc))

        entities, relations, source = await self._extract(text)
        if not entities:
            return GraphOutcome(reason="未抽取到实体")

        try:
            entity_count, relation_count = await self._apply(
                scope,
                memory_id,
                entities,
                relations,
                source=source,
                at=at,
            )
        except Exception as exc:  # 单条记忆失败不影响其它记忆
            self._warn("图谱落地失败（memory=%s）：%s", memory_id, safe_detail(exc))
            return GraphOutcome(reason="图谱写入失败")
        self._notify(entity_count)
        return GraphOutcome(
            indexed=True, entities=entity_count, relations=relation_count, reason=""
        )

    def _notify(self, entities: int) -> None:
        if self._observer is None:
            return
        try:
            self._observer(entities)
        except Exception as exc:  # 埋点失败不影响索引
            self._debug("图谱观察者回调失败：%s", safe_detail(exc))

    async def _extract(
        self, content: str
    ) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]], str]:
        mode = self._config.extractor
        deterministic: tuple[list[str], list[tuple[str, str, str]]] | None = None
        if mode in ("deterministic", "both"):
            deterministic = deterministic_extract(
                content,
                min_chars=self._config.min_term_chars,
                max_chars=self._config.max_term_chars,
                limit=self._config.max_entities,
            )
        if mode == "deterministic":
            entities, relations = _from_deterministic(deterministic)
            return entities, relations, SOURCE_DETERMINISTIC

        llm_entities, llm_relations = await self._llm_extract(content)
        if llm_entities or llm_relations:
            if mode == "both" and deterministic is not None:
                det_entities, det_relations = _from_deterministic(deterministic)
                entities, relations = _merge_candidates(
                    llm_entities,
                    llm_relations,
                    det_entities,
                    det_relations,
                    max_entities=self._config.max_entities,
                )
                return entities, relations, SOURCE_LLM
            return llm_entities, llm_relations, SOURCE_LLM

        # 模型失败或空产出：both 退回零成本，llm 返回空。
        if mode == "both" and deterministic is not None:
            entities, relations = _from_deterministic(deterministic)
            return entities, relations, SOURCE_DETERMINISTIC
        return [], [], SOURCE_LLM

    async def _llm_extract(
        self, content: str
    ) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]]]:
        if self._llm is None:
            return [], []
        prompt = build_graph_prompt(
            content,
            max_entities=self._config.max_entities,
            max_relations=self._config.max_relations,
            overrides=self._config.prompts,
        )
        try:
            result = await self._llm.chat(
                prompt=prompt,
                system_prompt=graph_system_prompt(self._config.prompts),
                provider_id=self._config.provider_id or None,
                timeout=self._config.timeout_seconds,
                purpose="graph",
            )
        except asyncio.CancelledError:
            raise
        except LlmError as exc:
            self._debug("图谱抽取失败，降级：%s", safe_detail(exc))
            return [], []
        except Exception as exc:  # 模型异常不得外溢
            self._debug("图谱抽取异常，降级：%s", safe_detail(exc))
            return [], []

        entities, relations = parse_graph_json(
            getattr(result, "text", "") or "",
            max_entities=self._config.max_entities,
            max_relations=self._config.max_relations,
        )
        return _from_llm(entities, relations)

    async def _apply(
        self,
        scope: MemoryScope,
        memory_id: int,
        entities: Sequence[dict[str, Any]],
        relations: Sequence[tuple[str, str, str]],
        *,
        source: str,
        at: float,
    ) -> tuple[int, int]:
        confidence = _CONFIDENCE_LLM if source == SOURCE_LLM else _CONFIDENCE_DETERMINISTIC
        id_by_name: dict[str, int] = {}
        entity_ids: list[int] = []

        for item in entities:
            canonical = canonicalize(str(item.get("name") or ""))
            if not canonical or canonical in id_by_name:
                continue
            entity_id = await self._entities.upsert_entity(
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
                name=str(item.get("name") or ""),
                canonical_name=canonical,
                entity_type=str(item.get("type") or "concept"),
                source=source,
                confidence=confidence,
                at=at,
            )
            if entity_id <= 0:
                continue
            id_by_name[canonical] = entity_id
            entity_ids.append(entity_id)

        if entity_ids:
            await self._entities.link_memory(memory_id, entity_ids, at=at)

        stored = 0
        for subject, predicate, object_ in relations:
            src = await self._ensure_entity(
                scope, id_by_name, subject, source=source, confidence=confidence, at=at
            )
            dst = await self._ensure_entity(
                scope, id_by_name, object_, source=source, confidence=confidence, at=at
            )
            if src <= 0 or dst <= 0 or src == dst:
                continue
            await self._entities.upsert_relation(
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
                src_entity_id=src,
                dst_entity_id=dst,
                relation=predicate,
                source=source,
                confidence=confidence,
                at=at,
            )
            stored += 1
        return len(entity_ids), stored

    async def _ensure_entity(
        self,
        scope: MemoryScope,
        cache: dict[str, int],
        name: str,
        *,
        source: str,
        confidence: float,
        at: float,
    ) -> int:
        canonical = canonicalize(name)
        if not canonical:
            return 0
        found = cache.get(canonical)
        if found:
            return found
        entity_id = await self._entities.upsert_entity(
            scope_type=scope.scope_type.value,
            scope_id=scope.scope_id,
            name=name,
            canonical_name=canonical,
            entity_type="concept",
            source=source,
            confidence=confidence,
            at=at,
        )
        if entity_id > 0:
            cache[canonical] = entity_id
        return entity_id

    # ------------------------------------------------------------------ #
    # 检索扩展
    # ------------------------------------------------------------------ #

    async def expand(
        self,
        scopes: Sequence[MemoryScope],
        query: str,
        *,
        limit: int,
    ) -> list[tuple[int, float]]:
        """按查询词做一跳（可选两跳）实体扩展，返回 ``(memory_id, weight)``。"""
        if not self._config.enabled or limit <= 0:
            return []
        names = [name for name in (canonicalize(token) for token in tokenize(query)) if name]
        if not names:
            return []

        try:
            hits = await self._entities.search_entities(scopes, names, limit=20)
        except Exception as exc:  # 检索失败降级为空
            self._debug("图谱实体检索失败：%s", safe_detail(exc))
            return []
        entity_ids = [int(row.get("id") or 0) for row in hits]
        entity_ids = [entity_id for entity_id in entity_ids if entity_id > 0]
        if not entity_ids:
            return []

        scores: dict[int, float] = {}
        try:
            direct = await self._entities.memories_for_entities(scopes, entity_ids, limit=limit)
        except Exception as exc:
            self._debug("图谱直接命中查询失败：%s", safe_detail(exc))
            direct = []
        _accumulate(scores, direct, factor=_DIRECT_WEIGHT)

        if self._config.expansion_hops >= 2:
            try:
                neighbors = await self._entities.related_entities(
                    scopes, entity_ids, limit=self._config.expansion_limit
                )
            except Exception as exc:
                self._debug("图谱邻居查询失败：%s", safe_detail(exc))
                neighbors = []
            neighbor_ids = [int(row.get("entity_id") or 0) for row in neighbors]
            neighbor_ids = [entity_id for entity_id in neighbor_ids if entity_id > 0]
            if neighbor_ids:
                try:
                    expanded = await self._entities.memories_for_entities(
                        scopes, neighbor_ids, limit=limit
                    )
                except Exception as exc:
                    self._debug("图谱二跳命中查询失败：%s", safe_detail(exc))
                    expanded = []
                _accumulate(scores, expanded, factor=self._config.second_hop_weight)

        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]
        return [(memory_id, round(weight, 6)) for memory_id, weight in ordered]

    async def related(
        self, scope: MemoryScope, memory_id: int, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        """以某条记忆为中心取相关记忆（面板点节点时用），排除自身。"""
        entities = await self._entities.entities_for_memories([memory_id])
        entity_ids = [int(row.get("id") or 0) for row in entities]
        entity_ids = [entity_id for entity_id in entity_ids if entity_id > 0]
        if not entity_ids:
            return []
        rows = await self._entities.memories_for_entities(
            [scope], entity_ids, limit=max(limit + 1, 1)
        )
        return [row for row in rows if int(row.get("memory_id") or 0) != memory_id][:limit]

    # ------------------------------------------------------------------ #
    # 可视化、维护与清理
    # ------------------------------------------------------------------ #

    async def snapshot(
        self,
        *,
        scope: MemoryScope | None = None,
        limit_nodes: int = 120,
        limit_edges: int = 240,
    ) -> dict[str, Any]:
        """取可视化子图，并为节点补上 ``label`` / ``degree``。"""
        if scope is None:
            data = await self._entities.snapshot(limit_nodes=limit_nodes, limit_edges=limit_edges)
        else:
            data = await self._entities.snapshot(
                scope_type=scope.scope_type.value,
                scope_id=scope.scope_id,
                limit_nodes=limit_nodes,
                limit_edges=limit_edges,
            )
        nodes = data.get("nodes") or []
        degrees: dict[int, int] = {}
        for edge in data.get("edges") or []:
            for key in ("src_entity_id", "dst_entity_id"):
                node_id = int(edge.get(key) or 0)
                degrees[node_id] = degrees.get(node_id, 0) + 1
        for node in nodes:
            node["label"] = node.get("name") or node.get("canonical_name") or ""
            node["degree"] = degrees.get(int(node.get("id") or 0), 0)
        return data

    async def memory_subgraph(
        self, memory_id: int, *, limit_nodes: int = 60, limit_edges: int = 120
    ) -> dict[str, Any]:
        """以某条记忆为中心取子图（面板点开一条记忆时用）。"""
        return await self._entities.memory_subgraph(
            memory_id, limit_nodes=limit_nodes, limit_edges=limit_edges
        )

    async def maintain(
        self, *, now: float | None = None, with_decay: bool = True
    ) -> dict[str, Any]:
        """每日维护：衰减 → 逐作用域裁剪 → 剪枝孤立节点与弱关系。

        ``with_decay=False`` 时只做容量裁剪与剪枝：MaiBot 增强开启后衰减由该域统一承担。
        """
        at = time.time() if now is None else now
        result: dict[str, Any] = {
            "decayed": 0,
            "trimmed": 0,
            "pruned": {},
            "scopes": 0,
        }
        if not self._config.enabled:
            return result

        if with_decay:
            factor = half_life_factor(self._config.half_life_days)
            result["decayed"] = await self._entities.apply_decay(
                factor=factor, floor=self._config.weight_floor, at=at
            )

        scopes = await self._entities.all_scopes()
        trimmed = 0
        for scope_type, scope_id in scopes:
            trimmed += await self._entities.trim_entities(
                [MemoryScope(ScopeType.parse(scope_type), scope_id)],
                keep=self._config.max_entities_per_scope,
                at=at,
            )
        result["trimmed"] = trimmed
        result["scopes"] = len(scopes)
        result["pruned"] = await self._entities.prune(
            min_weight=self._config.prune_min_weight, at=at
        )
        return result

    async def stats(self) -> dict[str, int]:
        data = dict(await self._entities.count())
        scopes = await self._entities.all_scopes()
        data["scopes"] = len(scopes)
        return data

    async def clear(self, scope: MemoryScope) -> dict[str, int]:
        return await self._entities.clear_scopes([scope])

    async def clear_memories(self, memory_ids: Sequence[int]) -> int:
        return await self._entities.clear_memories(memory_ids)

    # ------------------------------------------------------------------ #

    def _debug(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.debug(message, *args)

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)


def _from_deterministic(
    pairs: tuple[list[str], list[tuple[str, str, str]]] | None,
) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]]]:
    if not pairs:
        return [], []
    names, relations = pairs
    return [{"name": name, "type": "concept"} for name in names], list(relations)


def _from_llm(
    entities: Sequence[dict], relations: Sequence[dict]
) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]]]:
    normalized_entities = [
        {"name": str(item.get("name") or ""), "type": str(item.get("type") or "concept")}
        for item in entities
    ]
    normalized_relations = [
        (
            str(item.get("subject") or ""),
            str(item.get("predicate") or ""),
            str(item.get("object") or ""),
        )
        for item in relations
    ]
    return normalized_entities, normalized_relations


def _merge_candidates(
    primary_entities: Sequence[dict[str, Any]],
    primary_relations: Sequence[tuple[str, str, str]],
    extra_entities: Sequence[dict[str, Any]],
    extra_relations: Sequence[tuple[str, str, str]],
    *,
    max_entities: int,
) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]]]:
    """以模型结果为主、零成本结果做补充，按规范化名称与三元组去重。"""
    entities: list[dict[str, Any]] = []
    seen_entity: set[str] = set()
    for item in list(primary_entities) + list(extra_entities):
        canonical = canonicalize(str(item.get("name") or ""))
        if not canonical or canonical in seen_entity:
            continue
        seen_entity.add(canonical)
        entities.append(
            {"name": str(item.get("name") or ""), "type": str(item.get("type") or "concept")}
        )
        if len(entities) >= max_entities:
            break

    relations: list[tuple[str, str, str]] = []
    seen_relation: set[tuple[str, str, str]] = set()
    for subject, predicate, object_ in list(primary_relations) + list(extra_relations):
        key = (canonicalize(subject), predicate, canonicalize(object_))
        if not key[0] or not key[2] or key in seen_relation:
            continue
        seen_relation.add(key)
        relations.append((subject, predicate, object_))
    return entities, relations


def _accumulate(scores: dict[int, float], rows: Sequence[dict[str, Any]], *, factor: float) -> None:
    """把 ``memories_for_entities`` 的结果按命中数归一化后合并进打分表（取最大）。"""
    if not rows:
        return
    peak = max((int(row.get("hits") or 0) for row in rows), default=0) or 1
    for row in rows:
        memory_id = int(row.get("memory_id") or 0)
        if memory_id <= 0:
            continue
        value = factor * (int(row.get("hits") or 0) / peak)
        scores[memory_id] = max(scores.get(memory_id, 0.0), value)


__all__ = ["GraphService", "GraphOutcome", "SOURCE_DETERMINISTIC", "SOURCE_LLM"]
