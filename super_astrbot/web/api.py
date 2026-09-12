"""Web API 适配层（框架相关）。

约定（均已对照 AstrBot 源码确认）：

- 使用 ``context.register_web_api(route, handler, methods, desc)`` 注册；
- 路由**必须带插件名前缀**，而 Page 端 bridge 的 endpoint **不含**插件名
  （Dashboard 会把 bridge 调用转发到 ``/api/v1/plugins/extensions/<pluginName>/<endpoint>``）；
- 同时注册「原始大小写」与「全小写」两套前缀：AstrBot 在部分路径会规范化插件名，
  两套前缀可避免因大小写不一致导致面板 404；
- handler 无参数（本项目未使用路径参数），query/body 通过 ``astrbot.api.web.request`` 读取。

响应契约统一为 ``{"status": "ok", "data": ...}`` / ``error_response``，
由前端 bridge 自动解包 ``data`` 一层。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from astrbot.api.web import error_response, json_response, request

from ..monitor import CORE_METRICS, MAX_RANGE_HOURS
from ..spec.scopes import MemoryScope

PLUGIN_NAME = "astrbot_plugin_Super_AstrBot"
PLUGIN_NAME_LOWER = PLUGIN_NAME.lower()

Handler = Callable[[], Awaitable[Any]]

_ALLOWED_MEMORY_STATUS = {"active", "buffered", "pending", "archived", "forgotten"}


def register_web_apis(context: Any, app: Any) -> None:
    """注册面板后端接口（大小写两套前缀）。"""
    routes: list[tuple[str, Handler, list[str], str]] = [
        ("overview", _overview(app), ["GET"], "Super_AstrBot 总览与系统诊断"),
        ("features", _features(app), ["GET"], "功能清单与开关状态"),
        ("feature-toggle", _feature_toggle(app), ["POST"], "开启/关闭某个功能"),
        ("memories", _memories(app), ["GET"], "记忆列表（支持筛选与分页）"),
        ("memory", _memory_detail(app), ["GET"], "单条记忆详情"),
        ("search", _search(app), ["POST"], "混合检索记忆"),
        ("journals", _journals(app), ["GET"], "周记列表"),
        ("reviews", _reviews(app), ["GET"], "待审队列"),
        ("review-action", _review_action(app), ["POST"], "审批待审记录"),
        ("persona", _persona(app), ["GET"], "拟人化学习数据（风格 / 黑话 / 好感度）"),
        ("graph", _graph(app), ["GET"], "知识图谱子图（可视化用）"),
        ("monitor", _monitor(app), ["GET"], "运行监控指标与时间序列"),
        ("maintenance", _maintenance(app), ["POST"], "维护操作（重建索引）"),
    ]
    for prefix in (f"/{PLUGIN_NAME}", f"/{PLUGIN_NAME_LOWER}"):
        for endpoint, handler, methods, desc in routes:
            context.register_web_api(f"{prefix}/{endpoint}", handler, methods, desc)


# --------------------------------------------------------------------------- #
# 参数与响应辅助
# --------------------------------------------------------------------------- #


def _not_ready() -> Any:
    return error_response("插件尚未就绪（可能正在初始化或持久层不可用）", status_code=503)


def _int_param(name: str, default: int, *, low: int, high: int) -> int:
    raw = request.query.get(name, None, type=int)
    value = default if raw is None else int(raw)
    return max(low, min(high, value))


def _str_param(name: str, default: str = "") -> str:
    raw = request.query.get(name, default)
    return str(raw) if raw is not None else default


def _ok(data: Any) -> Any:
    return json_response({"status": "ok", "data": data})


def _memory_payload(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "content": item.content,
        "kind": item.kind,
        "source": item.source,
        "scope_type": item.scope_type,
        "scope_id": item.scope_id,
        "scope": f"{item.scope_type}:{item.scope_id}",
        "importance": round(float(item.importance), 4),
        "confidence": round(float(item.confidence), 4),
        "status": item.status,
        "tags": item.tags,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "last_access_at": item.last_access_at,
        "access_count": item.access_count,
    }


# --------------------------------------------------------------------------- #
# 总览
# --------------------------------------------------------------------------- #


def _overview(app: Any) -> Handler:
    async def handler() -> Any:
        try:
            status = await app.status()
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取状态失败：{exc}")

        stats: dict[str, Any] = {}
        if app.memory is not None:
            try:
                stats = await app.memory.stats_all()
            except Exception as exc:  # noqa: BLE001
                stats = {"error": str(exc)}

        providers = [{"id": info.id, "model": info.model} for info in app.embedding_providers()]

        return _ok(
            {
                "ready": status.get("ready"),
                "plugin_version": status.get("plugin_version"),
                "framework": status.get("framework"),
                "capabilities": status.get("capabilities"),
                "degraded": status.get("degraded"),
                "database": status.get("database"),
                "fts": status.get("fts"),
                "pending_tasks": status.get("pending_tasks"),
                "budget": status.get("budget"),
                "scheduler": status.get("scheduler"),
                "memory": stats,
                "embedding_providers": providers,
            }
        )

    return handler


# --------------------------------------------------------------------------- #
# 功能开关
# --------------------------------------------------------------------------- #


def _features(app: Any) -> Handler:
    async def handler() -> Any:
        try:
            features = app.feature_catalog()
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取功能清单失败：{exc}")

        domains: list[str] = []
        for item in features:
            if item.get("domain") and item["domain"] not in domains:
                domains.append(item["domain"])

        return _ok(
            {
                "items": features,
                "domains": domains,
                "capabilities": dict(app.capabilities),
            }
        )

    return handler


def _feature_toggle(app: Any) -> Handler:
    async def handler() -> Any:
        payload = await request.json(default={}) or {}
        key = str(payload.get("key") or "").strip()
        if not key:
            return error_response("缺少参数 key")
        if not isinstance(payload.get("enabled"), bool):
            return error_response("enabled 必须是布尔值")

        try:
            result = await app.set_capability(key, bool(payload["enabled"]))
        except Exception as exc:  # noqa: BLE001
            return error_response(f"切换失败：{exc}")

        if not result.get("ok"):
            # 依赖未满足 / 需要重载 / 运行环境不支持，均以 400 + 可读信息返回
            return error_response(str(result.get("message") or "切换失败"))

        return _ok(
            {
                "key": result.get("key"),
                "enabled": result.get("enabled"),
                "persisted": result.get("persisted"),
                "message": result.get("message"),
                "capabilities": dict(app.capabilities),
            }
        )

    return handler


# --------------------------------------------------------------------------- #
# 记忆
# --------------------------------------------------------------------------- #


def _memories(app: Any) -> Handler:
    async def handler() -> Any:
        memory = app.memory
        if memory is None:
            return _not_ready()

        limit = _int_param("limit", 20, low=1, high=100)
        offset = _int_param("offset", 0, low=0, high=1_000_000)
        keyword = _str_param("keyword").strip()
        kind = _str_param("kind").strip()
        status = _str_param("status", "active").strip().lower()
        if status not in _ALLOWED_MEMORY_STATUS:
            status = "active"

        try:
            items = await memory.list_all(
                offset=offset,
                limit=limit,
                keyword=keyword,
                status=status,
                kind=kind,
            )
            total = await memory.count_filtered(status=status, kind=kind, keyword=keyword)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取记忆失败：{exc}")

        return _ok(
            {
                "items": [_memory_payload(item) for item in items],
                "total": total,
                "offset": offset,
                "limit": limit,
                "status": status,
                "kind": kind,
                "keyword": keyword,
            }
        )

    return handler


def _memory_detail(app: Any) -> Handler:
    async def handler() -> Any:
        memory = app.memory
        if memory is None:
            return _not_ready()
        raw_id = request.query.get("id", None, type=int)
        if raw_id is None:
            return error_response("缺少参数 id")
        try:
            item = await memory.get_memory(int(raw_id))
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取记忆失败：{exc}")
        if item is None:
            return error_response("记忆不存在", status_code=404)

        return _ok(_memory_payload(item))

    return handler


def _search(app: Any) -> Handler:
    async def handler() -> Any:
        memory = app.memory
        if memory is None:
            return _not_ready()

        payload = await request.json(default={}) or {}
        query = str(payload.get("query") or "").strip()
        if not query:
            return error_response("query 不能为空")

        raw_limit = payload.get("limit")
        try:
            limit = max(1, min(20, int(raw_limit))) if raw_limit is not None else None
        except (TypeError, ValueError):
            limit = None

        umo = str(payload.get("umo") or "").strip()
        try:
            if umo:
                result = await memory.recall(MemoryScope.for_session(umo), query, limit=limit)
                items = [
                    {
                        **_memory_payload(item),
                        "score": round(float(item.score), 4),
                        "breakdown": item.score_breakdown,
                    }
                    for item in result.items
                ]
                return _ok(
                    {
                        "items": items,
                        "total": len(items),
                        "routes": result.route_summary,
                        "elapsed_ms": round(result.elapsed_ms, 2),
                        "degraded": result.degraded,
                    }
                )

            # 未指定会话：退化为跨作用域关键词匹配（只有 LIKE，没有打分）
            rows = await memory.list_all(offset=0, limit=limit or 10, keyword=query)
            return _ok(
                {
                    "items": [
                        {**_memory_payload(item), "score": None, "breakdown": {}} for item in rows
                    ],
                    "total": len(rows),
                    "routes": "like",
                    "elapsed_ms": 0,
                    "degraded": "未指定会话 UMO，已退化为跨作用域关键词匹配（无打分）",
                }
            )
        except Exception as exc:  # noqa: BLE001
            return error_response(f"检索失败：{exc}")

    return handler


# --------------------------------------------------------------------------- #
# 周记
# --------------------------------------------------------------------------- #


def _journals(app: Any) -> Handler:
    async def handler() -> Any:
        memory = app.memory
        if memory is None:
            return _not_ready()
        limit = _int_param("limit", 20, low=1, high=100)
        offset = _int_param("offset", 0, low=0, high=1_000_000)
        try:
            rows = await memory.list_all_journals(offset=offset, limit=limit)
            total = await memory.count_all_journals()
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取周记失败：{exc}")

        return _ok(
            {
                "items": [
                    {
                        "id": row.get("id"),
                        "content": row.get("content"),
                        "tags": row.get("tags"),
                        "emotion": row.get("emotion"),
                        "event_time": row.get("event_time"),
                        "created_at": row.get("created_at"),
                        "scope": f"{row.get('scope_type')}:{row.get('scope_id')}",
                    }
                    for row in rows
                ],
                "total": total,
                "offset": offset,
                "limit": limit,
            }
        )

    return handler


# --------------------------------------------------------------------------- #
# 待审与审批
# --------------------------------------------------------------------------- #


def _reviews(app: Any) -> Handler:
    async def handler() -> Any:
        umo = _str_param("umo").strip()
        limit = _int_param("limit", 20, low=1, high=100)
        try:
            if umo:
                rows = await app.pending_reviews(MemoryScope.for_session(umo), limit=limit)
            else:
                rows = await app.pending_reviews_all(limit=limit)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取待审队列失败：{exc}")

        origins = sorted({str(row.get("origin") or "") for row in rows})
        return _ok(
            {
                "items": rows,
                "total": len(rows),
                "origins": origins,
                "umo": umo,
            }
        )

    return handler


def _review_action(app: Any) -> Handler:
    async def handler() -> Any:
        payload = await request.json(default={}) or {}
        try:
            review_id = int(payload.get("id"))
        except (TypeError, ValueError):
            return error_response("id 必须为整数")
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"approve", "reject"}:
            return error_response("action 必须是 approve 或 reject")

        try:
            if action == "approve":
                handled, message = await app.approve_review(review_id)
                if not handled:
                    return error_response("该记录不存在或已处理", status_code=404)
                return _ok({"message": message})
            rejected = await app.reject_review(review_id)
            if not rejected:
                return error_response("该记录不存在或已处理", status_code=404)
            return _ok({"rejected": True})
        except Exception as exc:  # noqa: BLE001
            return error_response(f"处理失败：{exc}")

    return handler


# --------------------------------------------------------------------------- #
# 拟人化学习
# --------------------------------------------------------------------------- #


def _persona(app: Any) -> Handler:
    async def handler() -> Any:
        service = app.persona_service
        if service is None:
            return _not_ready()

        limit = _int_param("limit", 20, low=1, high=100)
        umo = _str_param("umo").strip()
        try:
            snapshot = service.snapshot()
            counts = await service.stats()
            if umo:
                scope = MemoryScope.for_session(umo)
                styles = await service.style_patterns(scope, limit=limit)
                jargons = await service.jargon_entries(scope, limit=limit)
                affinity = await service.affinity_rows(scope, limit=limit)
                pending = len(await app.pending_reviews(scope, limit=100))
            else:
                styles = await service.all_style_patterns(limit=limit)
                jargons = await service.all_jargons(limit=limit)
                affinity = await service.all_affinity(limit=limit)
                pending = len(await app.pending_reviews_all(limit=100))
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取学习数据失败：{exc}")

        style = snapshot.get("style") or {}
        jargon = snapshot.get("jargon") or {}
        affin = snapshot.get("affinity") or {}
        return _ok(
            {
                "umo": umo,
                "enabled": {
                    "style": bool(style.get("enabled")),
                    "jargon": bool(jargon.get("enabled")),
                    "affinity": bool(affin.get("enabled")),
                },
                "approval_required": {
                    "style": bool(style.get("approval_required")),
                    "jargon": bool(jargon.get("approval_required")),
                },
                "counts": {
                    "style": counts.style,
                    "jargon": counts.jargon,
                    "affinity": counts.affinity,
                },
                "pending": pending,
                "style": [
                    {
                        "id": row.get("id"),
                        "scope": f"{row.get('scope_type')}:{row.get('scope_id')}",
                        "situation": row.get("situation"),
                        "expression": row.get("expression"),
                        "weight": round(float(row.get("weight") or 0.0), 4),
                        "hits": row.get("hits"),
                        "created_at": row.get("created_at"),
                    }
                    for row in styles
                ],
                "jargon": [
                    {
                        "id": row.get("id"),
                        "scope": f"{row.get('scope_type')}:{row.get('scope_id')}",
                        "term": row.get("term"),
                        "meaning": row.get("meaning"),
                        "confidence": round(float(row.get("confidence") or 0.0), 4),
                        "evidence": row.get("evidence"),
                    }
                    for row in jargons
                ],
                "affinity": [
                    {
                        "scope": f"{row.get('scope_type')}:{row.get('scope_id')}",
                        "target_id": row.get("target_id"),
                        "score": round(float(row.get("score") or 0.0), 4),
                        "mood": row.get("mood"),
                        "interactions": row.get("interactions"),
                        "last_interaction": row.get("last_interaction"),
                    }
                    for row in affinity
                ],
            }
        )

    return handler


# --------------------------------------------------------------------------- #
# 知识图谱与运行监控
# --------------------------------------------------------------------------- #


def _graph(app: Any) -> Handler:
    async def handler() -> Any:
        service = app.graph_service
        if service is None:
            return _not_ready()

        umo = _str_param("umo").strip()
        limit_nodes = _int_param("limit_nodes", 120, low=10, high=300)
        limit_edges = _int_param("limit_edges", 240, low=10, high=600)
        raw_memory_id = request.query.get("memory_id", None, type=int)

        try:
            if raw_memory_id is not None:
                data = await service.memory_subgraph(
                    int(raw_memory_id), limit_nodes=limit_nodes, limit_edges=limit_edges
                )
            else:
                scope = MemoryScope.for_session(umo) if umo else None
                data = await service.snapshot(
                    scope=scope, limit_nodes=limit_nodes, limit_edges=limit_edges
                )
            stats = await service.stats()
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取图谱失败：{exc}")

        nodes = [
            {
                "id": row.get("id"),
                "label": row.get("name") or row.get("canonical_name") or "",
                "name": row.get("name"),
                "canonical_name": row.get("canonical_name"),
                "entity_type": row.get("entity_type"),
                "scope": f"{row.get('scope_type')}:{row.get('scope_id')}",
                "weight": round(float(row.get("weight") or 0.0), 4),
                "evidence": row.get("evidence"),
                "degree": row.get("degree", 0),
            }
            for row in data.get("nodes") or []
        ]
        edges = [
            {
                "id": row.get("id"),
                "src_entity_id": row.get("src_entity_id"),
                "dst_entity_id": row.get("dst_entity_id"),
                "relation": row.get("relation"),
                "weight": round(float(row.get("weight") or 0.0), 4),
                "confidence": round(float(row.get("confidence") or 0.0), 4),
            }
            for row in data.get("edges") or []
        ]
        return _ok(
            {
                "enabled": app.capabilities.get("graph.enabled", False),
                "stats": stats,
                "nodes": nodes,
                "edges": edges,
                "truncated": bool(data.get("truncated")),
                "umo": umo,
            }
        )

    return handler


def _monitor(app: Any) -> Handler:
    async def handler() -> Any:
        service = app.monitor_service
        if service is None:
            return _not_ready()

        range_hours = _int_param("range_hours", 24, low=1, high=MAX_RANGE_HOURS)
        raw_bucket = request.query.get("bucket_seconds", None, type=int)
        # 只提供小时/天两种粒度：更细的粒度由内存桶实时值承担。
        bucket = 86400 if (raw_bucket or 3600) >= 86400 else 3600
        raw_metrics = _str_param("metrics")
        metrics = [item.strip() for item in raw_metrics.split(",") if item.strip()] or list(
            CORE_METRICS
        )

        try:
            window = await service.overview(hours=range_hours)
            series = await service.trends(
                metrics=metrics, range_hours=range_hours, bucket_seconds=bucket
            )
            snapshot = await service.snapshot()
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取监控数据失败：{exc}")

        review: dict[str, Any] = {}
        auto = app.auto_review_service
        if auto is not None:
            try:
                review = await auto.stats()
                review["enabled"] = bool(app.capabilities.get("review.auto", False))
            except Exception as exc:  # noqa: BLE001 - 监控页不应因单个区块失败而整体报错
                review = {"error": str(exc)}

        graph: dict[str, Any] = {}
        graph_service = app.graph_service
        if graph_service is not None:
            try:
                graph = await graph_service.stats()
            except Exception as exc:  # noqa: BLE001
                graph = {"error": str(exc)}

        return _ok(
            {
                "window_hours": range_hours,
                "bucket_seconds": bucket,
                "live": window.get("live") or {},
                "totals": window.get("totals") or {},
                "metrics": window.get("metrics") or [],
                "series": series.get("series") or {},
                "pending": snapshot.get("pending") or 0,
                "retention_days": snapshot.get("retention_days"),
                "review": review,
                "graph": graph,
            }
        )

    return handler


# --------------------------------------------------------------------------- #
# 维护
# --------------------------------------------------------------------------- #


def _maintenance(app: Any) -> Handler:
    async def handler() -> Any:
        memory = app.memory
        if memory is None:
            return _not_ready()
        payload = await request.json(default={}) or {}
        action = str(payload.get("action") or "").strip().lower()
        if action != "reindex":
            return error_response("当前仅支持 action=reindex")
        try:
            stats = await memory.reindex()
        except Exception as exc:  # noqa: BLE001
            return error_response(f"重建索引失败：{exc}")
        return _ok({"stats": stats})

    return handler
