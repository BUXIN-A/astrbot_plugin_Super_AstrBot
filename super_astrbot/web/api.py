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

PLUGIN_NAME = "astrbot_plugin_Super_AstrBot"
PLUGIN_NAME_LOWER = PLUGIN_NAME.lower()

Handler = Callable[[], Awaitable[Any]]

_ALLOWED_MEMORY_STATUS = {"active", "buffered", "pending", "archived", "forgotten"}


def register_web_apis(context: Any, app: Any) -> None:
    """注册面板后端接口（大小写两套前缀）。"""
    routes: list[tuple[str, Handler, list[str], str]] = [
        ("overview", _overview(app), ["GET"], "Super_AstrBot 总览与系统诊断"),
        ("memories", _memories(app), ["GET"], "记忆列表（支持筛选与分页）"),
        ("memory", _memory_detail(app), ["GET"], "单条记忆详情"),
        ("search", _search(app), ["POST"], "混合检索记忆"),
        ("journals", _journals(app), ["GET"], "周记列表"),
        ("reviews", _reviews(app), ["GET"], "待审记忆队列"),
        ("review-action", _review_action(app), ["POST"], "审批记忆"),
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
            record = await memory._memories.get(int(raw_id))  # noqa: SLF001 - 面板只读详情
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取记忆失败：{exc}")
        if record is None:
            return error_response("记忆不存在", status_code=404)

        from ..memory import MemoryItem

        return _ok(_memory_payload(MemoryItem.from_row(record)))

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
                from ..spec.scopes import MemoryScope

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
            total = await memory._journals.count_all()  # noqa: SLF001 - 面板只读统计
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


def _review_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import json as _json

    items: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = _json.loads(row.get("payload") or "{}")
        except (TypeError, ValueError):
            payload = {}
        items.append(
            {
                "id": row.get("id"),
                "origin": row.get("origin"),
                "scope": f"{row.get('scope_type')}:{row.get('scope_id')}",
                "created_at": row.get("created_at"),
                "content": payload.get("content"),
                "kind": payload.get("kind"),
                "importance": payload.get("importance"),
            }
        )
    return items


def _reviews(app: Any) -> Handler:
    async def handler() -> Any:
        reflection = app.reflection
        if reflection is None:
            return _not_ready()
        umo = _str_param("umo")
        limit = _int_param("limit", 20, low=1, high=100)
        try:
            from ..spec.scopes import MemoryScope

            scope = MemoryScope.for_session(umo) if umo else MemoryScope.global_scope()
            rows = await reflection.pending_reviews(scope, limit=limit)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取待审队列失败：{exc}")

        items = _review_items(rows)
        return _ok(
            {
                "items": items,
                "total": len(items),
                "approval_required": bool(reflection.config.approval_required),
            }
        )

    return handler


def _review_action(app: Any) -> Handler:
    async def handler() -> Any:
        reflection = app.reflection
        if reflection is None:
            return _not_ready()
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
                memory_id = await reflection.approve(review_id)
                if memory_id is None:
                    return error_response("该记录不存在或已处理", status_code=404)
                return _ok({"memory_id": memory_id})
            ok = await reflection.reject(review_id)
            if not ok:
                return error_response("该记录不存在或已处理", status_code=404)
            return _ok({"rejected": True})
        except Exception as exc:  # noqa: BLE001
            return error_response(f"处理失败：{exc}")

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
