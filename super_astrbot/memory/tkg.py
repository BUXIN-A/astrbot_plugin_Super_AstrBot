"""时序知识图谱（LATRACE 思想的进程内重写，对应总纲 §0.5 修正表）。

合规约束：LATRACE-AI 原方案是 docker REST 微服务；本项目按实施总纲要求
**重写为进程内模块**——建图 / 召回 / 证据链全部是普通 Python 函数调用，
数据落在本插件自己的 SQLite（``tkg_nodes`` / ``tkg_edges``），零外部进程。

与其他模块的边界：
- 实体抽取复用既有 ``graph.extractor``（零成本共现为主），本模块只负责
  「时序化 + 证据链 + 按发送者归属」，不重复实现抽取；
- 召回走既有 ``MemoryService.recall``（混合召回），本模块在结果之外
  追加「图上相邻记忆」并给每条结果附 provenance（来源路 / 证据实体 / 命中时间）；
- 不 import embedding / 不调用模型：GraphRAG 的「图扩展」用一跳邻居完成。

时间语义：
- 节点 ``first_seen`` / ``last_seen``：实体在记忆中的最早 / 最近出现；
- 边 ``valid_from`` / ``valid_to``：关系有效区间（``valid_to=0`` 表示仍在有效）；
- 证据 ``evidence``：来源引用（``memory:<id>`` / ``journal:<id>`` / ``distilly:offline``）。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Sequence

from ..spec.scopes import MemoryScope

DEFAULT_ENTITY_TYPES: tuple[str, ...] = ("topic", "person", "place", "thing", "event")


class TemporalGraphService:
    """时序知识图谱：ingest（增量建图）+ expand（图扩展召回）+ snapshot（可视化）。"""

    def __init__(
        self,
        *,
        db: Any | None = None,
        memories: Any | None = None,
        extractor: Any | None = None,
        max_nodes: int = 4000,
        recall_extra: int = 3,
        evidence_limit: int = 3,
        clock: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._db = db
        self._memories = memories
        self._extractor = extractor
        self._max_nodes = int(max_nodes)
        self._recall_extra = int(recall_extra)
        self._evidence_limit = int(evidence_limit)
        self._clock = clock or time.time
        self._logger = logger

    def enabled(self) -> bool:
        return self._db is not None and self._extractor is not None

    # ------------------------------------------------------------------ #
    # 建图
    # ------------------------------------------------------------------ #

    async def ingest_memory(
        self,
        *,
        memory_id: int,
        content: str,
        scope_type: str,
        scope_id: str,
        sender_id: str = "",
        created_at: float | None = None,
        source: str = "memory",
        evidence_key: str = "",
    ) -> dict[str, Any]:
        """把一条记忆写入时序图：实体 upsert + 共现边 upsert + 证据追加。

        ``evidence_key`` 用于非记忆来源（对话轮次 / 离线蒸馏产物）指定稳定引用；
        留空时按 ``<source>:<memory_id>`` 生成。
        """
        if not self.enabled():
            return {"entities": 0, "edges": 0}
        names = self._extract(content)
        if not names:
            return {"entities": 0, "edges": 0}
        moment = float(created_at or self._clock())
        evidence = str(evidence_key or "").strip() or f"{source}:{memory_id}"
        node_ids: list[int] = []
        for name in names:
            node_id = await self._upsert_node(
                name=name,
                scope_type=scope_type,
                scope_id=scope_id,
                sender_id=sender_id,
                evidence=evidence,
                moment=moment,
            )
            if node_id > 0:
                node_ids.append(node_id)
        edges = 0
        for index, src in enumerate(node_ids):
            for dst in node_ids[index + 1 :]:
                if await self._upsert_edge(
                    src=src,
                    dst=dst,
                    relation="co_occur",
                    sender_id=sender_id,
                    evidence=evidence,
                    moment=moment,
                ):
                    edges += 1
        # 容量上限：超出即淘汰最久未出现的节点（连带其边）
        await self._trim()
        return {"entities": len(node_ids), "edges": edges}

    async def rebuild(self, *, scope: MemoryScope | None = None, limit: int = 500) -> dict[str, Any]:
        """从既有记忆重建图谱（面板「重建时序图谱」按钮 / 首次启用时的回填）。"""
        if not self.enabled():
            return {"ok": False, "message": "时序图谱不可用（缺少抽取器）"}
        if scope is None:
            rows = await self._db.query(
                "SELECT id, content, scope_type, scope_id, sender_id, created_at FROM memories"
                " WHERE status IN ('active','archived') ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            )
        else:
            from ..spec.scopes import retrieval_scopes

            scopes = retrieval_scopes(scope)
            placeholders = ",".join("(?,?)" for _ in scopes)
            params: list[Any] = []
            for item in scopes:
                params.extend([item.scope_type, item.scope_id])
            rows = await self._db.query(
                f"SELECT id, content, scope_type, scope_id, sender_id, created_at FROM memories"
                f" WHERE (scope_type, scope_id) IN ({placeholders})"
                f" AND status IN ('active','archived') ORDER BY created_at DESC LIMIT ?",
                (*params, max(1, int(limit))),
            )
        total_entities = 0
        total_edges = 0
        for row in rows:
            result = await self.ingest_memory(
                memory_id=int(row["id"]),
                content=str(row["content"] or ""),
                scope_type=str(row["scope_type"] or ""),
                scope_id=str(row["scope_id"] or ""),
                sender_id=str(row["sender_id"] or ""),
                created_at=float(row["created_at"] or 0.0),
            )
            total_entities += int(result.get("entities") or 0)
            total_edges += int(result.get("edges") or 0)
        return {
            "ok": True,
            "memories": len(rows),
            "entities": total_entities,
            "edges": total_edges,
        }

    def _extract(self, content: str) -> list[str]:
        try:
            names = self._extractor.extract(content)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001  抽取失败只跳过该条
            self._debug("实体抽取失败：%s", exc)
            return []
        cleaned: list[str] = []
        for name in names or []:
            text = str(name).strip()
            if not text or len(text) > 24:
                continue
            if text not in cleaned:
                cleaned.append(text)
        return cleaned[:12]

    async def _upsert_node(
        self,
        *,
        name: str,
        scope_type: str,
        scope_id: str,
        sender_id: str,
        evidence: str,
        moment: float,
    ) -> int:
        canonical = name.lower()
        row = await self._db.query_one(
            "SELECT id, evidence, mentions FROM tkg_nodes"
            " WHERE scope_type=? AND scope_id=? AND canonical=?",
            (scope_type, scope_id, canonical),
        )
        if row is None:
            cursor = await self._db.execute(
                "INSERT INTO tkg_nodes(scope_type, scope_id, name, canonical, entity_type,"
                " sender_id, weight, mentions, evidence, first_seen, last_seen)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    scope_type,
                    scope_id,
                    name,
                    canonical,
                    _guess_type(name),
                    sender_id,
                    1.0,
                    1,
                    evidence,
                    moment,
                    moment,
                ),
            )
            return int(getattr(cursor, "lastrowid", 0) or 0)

        node_id = int(row["id"])
        evidences = str(row["evidence"] or "")
        refs = {part.strip() for part in evidences.split(",") if part.strip()}
        if evidence in refs:
            # 同一条来源重复入库（例如重复执行重建）：只刷新时间，不重复计数
            await self._db.execute(
                "UPDATE tkg_nodes SET last_seen = MAX(last_seen, ?) WHERE id=?", (moment, node_id)
            )
            return node_id
        merged = _merge_evidence(evidences, evidence, self._evidence_limit)
        await self._db.execute(
            "UPDATE tkg_nodes SET mentions = mentions + 1, last_seen = ?, weight = weight + 0.2,"
            " evidence = ?, sender_id = COALESCE(NULLIF(?, ''), sender_id) WHERE id=?",
            (moment, merged, sender_id, node_id),
        )
        return node_id

    async def _upsert_edge(
        self,
        *,
        src: int,
        dst: int,
        relation: str,
        sender_id: str,
        evidence: str,
        moment: float,
    ) -> bool:
        left, right = (src, dst) if src <= dst else (dst, src)
        row = await self._db.query_one(
            "SELECT id, confidence, evidence FROM tkg_edges"
            " WHERE src_id=? AND dst_id=? AND relation=?",
            (left, right, relation),
        )
        if row is None:
            await self._db.execute(
                "INSERT INTO tkg_edges(src_id, dst_id, relation, weight, confidence, evidence,"
                " sender_id, valid_from, valid_to, created_at) VALUES (?,?,?,?,?,?,?,?,0,?)",
                (
                    left,
                    right,
                    relation,
                    1.0,
                    min(1.0, 0.4 + 0.1),
                    evidence,
                    sender_id,
                    moment,
                    moment,
                ),
            )
            return True
        confidence = min(1.0, float(row["confidence"] or 0.5) + 0.1)
        existing = str(row["evidence"] or "")
        refs = {part.strip() for part in existing.split(",") if part.strip()}
        if evidence in refs:
            await self._db.execute(
                "UPDATE tkg_edges SET valid_from = MIN(valid_from, ?), valid_to = 0 WHERE id=?",
                (moment, int(row["id"])),
            )
            return False
        merged = _merge_evidence(existing, evidence, self._evidence_limit)
        await self._db.execute(
            "UPDATE tkg_edges SET weight = weight + 0.2, confidence = ?, evidence = ?,"
            " valid_to = 0 WHERE id=?",
            (confidence, merged, int(row["id"])),
        )
        return False

    async def _trim(self) -> None:
        total = await self._db.scalar("SELECT COUNT(*) FROM tkg_nodes", (), 0)
        extra = int(total or 0) - self._max_nodes
        if extra <= 0:
            return
        rows = await self._db.query(
            "SELECT id FROM tkg_nodes ORDER BY last_seen ASC LIMIT ?", (extra,)
        )
        ids = [int(row["id"]) for row in rows]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        await self._db.execute(f"DELETE FROM tkg_edges WHERE src_id IN ({placeholders}) OR dst_id IN ({placeholders})", (*ids, *ids))
        await self._db.execute(f"DELETE FROM tkg_nodes WHERE id IN ({placeholders})", tuple(ids))
        self._debug("时序图谱超出容量，淘汰 %s 个最久未活跃节点", len(ids))

    # ------------------------------------------------------------------ #
    # 召回（证据链 + 图扩展）
    # ------------------------------------------------------------------ #

    async def provenance(
        self, *, memory_ids: Sequence[int], scope: MemoryScope | None = None
    ) -> dict[str, dict[str, Any]]:
        """为若干记忆查证据链：命中的实体、邻居实体、来源引用与时间。"""
        if self._db is None or not memory_ids:
            return {}
        evidence_keys = {f"memory:{int(mid)}" for mid in memory_ids}
        rows = await self._db.query(
            "SELECT id, name, canonical, entity_type, weight, mentions, evidence, first_seen, last_seen,"
            " scope_type, scope_id, sender_id FROM tkg_nodes"
            " WHERE evidence != '' ORDER BY last_seen DESC LIMIT 4000"
        )
        matched: dict[int, list[dict[str, Any]]] = {}
        node_ids: list[int] = []
        for row in rows:
            refs = {part.strip() for part in str(row["evidence"] or "").split(",") if part.strip()}
            common = evidence_keys & refs
            if not common:
                continue
            node = self._format_node(row)
            node_ids.append(int(row["id"]))
            for key in common:
                memory_id = int(key.split(":", 1)[1])
                matched.setdefault(memory_id, []).append(node)

        neighbors: dict[int, list[dict[str, Any]]] = {}
        if node_ids:
            placeholders = ",".join("?" for _ in node_ids)
            edges = await self._db.query(
                f"SELECT * FROM tkg_edges WHERE src_id IN ({placeholders}) OR dst_id IN ({placeholders})"
                f" ORDER BY weight DESC LIMIT 400",
                (*node_ids, *node_ids),
            )
            node_index = await self._node_index(
                {int(edge["src_id"]) for edge in edges} | {int(edge["dst_id"]) for edge in edges}
            )
            for edge in edges:
                src = int(edge["src_id"])
                dst = int(edge["dst_id"])
                formatted = {
                    "src": node_index.get(src, {}).get("name", str(src)),
                    "dst": node_index.get(dst, {}).get("name", str(dst)),
                    "relation": str(edge["relation"] or "co_occur"),
                    "weight": round(float(edge["weight"] or 0.0), 4),
                    "confidence": round(float(edge["confidence"] or 0.0), 4),
                    "evidence": str(edge["evidence"] or ""),
                    "valid_from": float(edge["valid_from"] or 0.0),
                    "valid_to": float(edge["valid_to"] or 0.0),
                }
                if src in node_ids:
                    neighbors.setdefault(src, []).append(formatted)
                if dst in node_ids:
                    neighbors.setdefault(dst, []).append(formatted)

        result: dict[int, dict[str, Any]] = {}
        for memory_id, nodes in matched.items():
            chains = []
            for node in nodes[: self._evidence_limit]:
                chains.append(
                    {
                        "entity": node["name"],
                        "entity_type": node["entity_type"],
                        "mentions": node["mentions"],
                        "first_seen": node["first_seen"],
                        "last_seen": node["last_seen"],
                        "evidence": node["evidence"],
                        "relations": neighbors.get(node["id"], [])[: self._evidence_limit],
                    }
                )
            result[memory_id] = {
                "entities": [node["name"] for node in nodes],
                "chains": chains,
                "source": "tkg",
            }
        return result

    async def expand(
        self, *, memory_ids: Sequence[int], scope: MemoryScope | None = None, limit: int | None = None
    ) -> dict[str, Any]:
        """图扩展召回：给定已召回记忆，取其图上邻居实体最近关联的记忆。"""
        if self._db is None or not memory_ids:
            return {"extra_ids": [], "via": {}}
        cap = int(limit or self._recall_extra)
        evidence_keys = [f"memory:{int(mid)}" for mid in memory_ids]
        rows = await self._db.query(
            "SELECT id, evidence FROM tkg_nodes WHERE evidence != '' LIMIT 4000"
        )
        seed_ids: set[int] = set()
        for row in rows:
            refs = {part.strip() for part in str(row["evidence"] or "").split(",") if part.strip()}
            if refs & set(evidence_keys):
                seed_ids.add(int(row["id"]))
        if not seed_ids:
            return {"extra_ids": [], "via": {}}
        node_placeholders = ",".join("?" for _ in seed_ids)
        edges = await self._db.query(
            f"SELECT src_id, dst_id, relation FROM tkg_edges"
            f" WHERE src_id IN ({node_placeholders}) OR dst_id IN ({node_placeholders})"
            f" ORDER BY weight DESC LIMIT 200",
            (*seed_ids, *seed_ids),
        )
        neighbor_ids: set[int] = set()
        for edge in edges:
            src = int(edge["src_id"])
            dst = int(edge["dst_id"])
            if src in seed_ids:
                neighbor_ids.add(dst)
            if dst in seed_ids:
                neighbor_ids.add(src)
        neighbor_ids -= seed_ids
        if not neighbor_ids:
            return {"extra_ids": [], "via": {}}

        n_placeholders = ",".join("?" for _ in neighbor_ids)
        neighbors = await self._db.query(
            f"SELECT id, name, evidence FROM tkg_nodes WHERE id IN ({n_placeholders})",
            tuple(neighbor_ids),
        )
        extra_ids: list[int] = []
        via: dict[int, str] = {}
        excluded = {int(mid) for mid in memory_ids}
        for node in neighbors:
            refs = [
                part.strip()
                for part in str(node["evidence"] or "").split(",")
                if part.strip().startswith("memory:")
            ]
            for ref in reversed(refs):  # evidence 尾部是最新的引用
                memory_id = int(ref.split(":", 1)[1])
                if memory_id in excluded or memory_id in extra_ids:
                    continue
                extra_ids.append(memory_id)
                via[memory_id] = str(node["name"] or "")
                break
            if len(extra_ids) >= cap:
                break
        return {"extra_ids": extra_ids[:cap], "via": via}

    async def snapshot(
        self,
        *,
        scope: MemoryScope | None = None,
        limit_nodes: int = 120,
        limit_edges: int = 240,
        sender_id: str = "",
    ) -> dict[str, Any]:
        """子图快照（可视化用）：带时间与证据字段。"""
        if self._db is None:
            return {"nodes": [], "edges": [], "truncated": False}
        params: list[Any] = []
        where = ""
        if scope is not None:
            where = " WHERE scope_type=? AND scope_id=?"
            params = [scope.scope_type, scope.scope_id]
        if sender_id:
            where = (where + " AND" if where else " WHERE") + " sender_id=?"
            params.append(sender_id)
        rows = await self._db.query(
            f"SELECT * FROM tkg_nodes{where} ORDER BY weight DESC, last_seen DESC LIMIT ?",
            (*params, max(1, int(limit_nodes))),
        )
        nodes = [self._format_node(row) for row in rows]
        ids = [node["id"] for node in nodes]
        truncated = False
        if not ids:
            return {"nodes": [], "edges": [], "truncated": False}
        placeholders = ",".join("?" for _ in ids)
        edge_rows = await self._db.query(
            f"SELECT * FROM tkg_edges WHERE src_id IN ({placeholders}) AND dst_id IN ({placeholders})"
            f" ORDER BY weight DESC LIMIT ?",
            (*ids, *ids, max(1, int(limit_edges))),
        )
        total_edges = await self._db.scalar(
            f"SELECT COUNT(*) FROM tkg_edges WHERE src_id IN ({placeholders}) AND dst_id IN ({placeholders})",
            (*ids, *ids),
            0,
        )
        if int(total_edges or 0) > len(edge_rows):
            truncated = True
        edges = [
            {
                "id": int(row["id"]),
                "src_id": int(row["src_id"]),
                "dst_id": int(row["dst_id"]),
                "relation": str(row["relation"] or "co_occur"),
                "weight": round(float(row["weight"] or 0.0), 4),
                "confidence": round(float(row["confidence"] or 0.0), 4),
                "evidence": str(row["evidence"] or ""),
                "sender_id": str(row["sender_id"] or ""),
                "valid_from": float(row["valid_from"] or 0.0),
                "valid_to": float(row["valid_to"] or 0.0),
            }
            for row in edge_rows
        ]
        return {"nodes": nodes, "edges": edges, "truncated": truncated}

    async def timeline(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """最近发生的关系（时间线回溯）。"""
        if self._db is None:
            return []
        rows = await self._db.query(
            "SELECT * FROM tkg_edges ORDER BY valid_from DESC LIMIT ?",
            (max(1, min(100, int(limit))),),
        )
        node_ids = {int(row["src_id"]) for row in rows} | {int(row["dst_id"]) for row in rows}
        index = await self._node_index(node_ids)
        return [
            {
                "id": int(row["id"]),
                "src": index.get(int(row["src_id"]), {}).get("name", ""),
                "dst": index.get(int(row["dst_id"]), {}).get("name", ""),
                "relation": str(row["relation"] or "co_occur"),
                "weight": round(float(row["weight"] or 0.0), 4),
                "evidence": str(row["evidence"] or ""),
                "sender_id": str(row["sender_id"] or ""),
                "valid_from": float(row["valid_from"] or 0.0),
                "valid_to": float(row["valid_to"] or 0.0),
            }
            for row in rows
        ]

    async def _node_index(self, ids: Iterable[int]) -> dict[int, dict[str, Any]]:
        cleaned = [int(item) for item in ids if int(item) > 0]
        if not cleaned:
            return {}
        placeholders = ",".join("?" for _ in cleaned)
        rows = await self._db.query(
            f"SELECT id, name, canonical FROM tkg_nodes WHERE id IN ({placeholders})",
            tuple(cleaned),
        )
        return {
            int(row["id"]): {"name": str(row["name"] or ""), "canonical": str(row["canonical"] or "")}
            for row in rows
        }

    # ------------------------------------------------------------------ #

    async def stats(self) -> dict[str, Any]:
        if self._db is None:
            return {"nodes": 0, "edges": 0}
        nodes = await self._db.scalar("SELECT COUNT(*) FROM tkg_nodes", (), 0)
        edges = await self._db.scalar("SELECT COUNT(*) FROM tkg_edges", (), 0)
        recent = await self._db.scalar(
            "SELECT COUNT(*) FROM tkg_edges WHERE valid_from >= ?",
            (self._clock() - 7 * 86400.0,),
            0,
        )
        senders = await self._db.scalar(
            "SELECT COUNT(DISTINCT sender_id) FROM tkg_nodes WHERE sender_id != ''", (), 0
        )
        return {
            "nodes": int(nodes or 0),
            "edges": int(edges or 0),
            "recent_edges_7d": int(recent or 0),
            "senders": int(senders or 0),
            "ready": bool(nodes),
        }

    async def clear(self) -> dict[str, Any]:
        if self._db is None:
            return {"ok": False, "message": "持久层未就绪"}
        await self._db.execute("DELETE FROM tkg_edges")
        cursor = await self._db.execute("DELETE FROM tkg_nodes")
        return {"ok": True, "deleted": int(getattr(cursor, "rowcount", 0) or 0)}

    def _format_node(self, row: Any) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "name": str(row["name"] or ""),
            "canonical": str(row["canonical"] or ""),
            "entity_type": str(row["entity_type"] or "topic"),
            "scope": f"{row['scope_type']}:{row['scope_id']}",
            "scope_type": str(row["scope_type"] or ""),
            "scope_id": str(row["scope_id"] or ""),
            "sender_id": str(row["sender_id"] or ""),
            "weight": round(float(row["weight"] or 0.0), 4),
            "mentions": int(row["mentions"] or 0),
            "evidence": str(row["evidence"] or ""),
            "first_seen": float(row["first_seen"] or 0.0),
            "last_seen": float(row["last_seen"] or 0.0),
            "label": str(row["name"] or ""),
        }

    def _debug(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            try:
                self._logger.debug(message, *args)
            except Exception:  # noqa: BLE001
                pass


def _merge_evidence(existing: str, new: str, limit: int) -> str:
    """证据链按「最新在尾部」合并，去重并保留最近 ``limit`` 条。"""
    items = [part.strip() for part in str(existing or "").split(",") if part.strip()]
    incoming = str(new or "").strip()
    if incoming:
        items = [item for item in items if item != incoming]
        items.append(incoming)
    return ",".join(items[-max(1, int(limit)) :])


def _guess_type(name: str) -> str:
    """零成本实体类型猜测：只做可解释的形态判断，不调用模型。"""
    text = str(name or "")
    if not text:
        return "topic"
    if text.startswith("@") or (2 <= len(text) <= 6 and not any(ch.isdigit() for ch in text) and _is_cjk(text)):
        return "person" if len(text) <= 4 else "topic"
    if any(token in text for token in ("群", "号", "房间", "实验室", "公司", "学校", "城市", "深圳", "北京", "上海")):
        return "place"
    if any(token in text for token in ("考研", "论文", "项目", "答辩", "上线", "考试", "生日", "会议", "旅行")):
        return "event"
    return "topic"


def _is_cjk(text: str) -> bool:
    return all("\u4e00" <= ch <= "\u9fff" for ch in text)


__all__ = ["DEFAULT_ENTITY_TYPES", "TemporalGraphService"]
