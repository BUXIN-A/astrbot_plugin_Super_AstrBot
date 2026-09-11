"""Web API 适配层（框架相关）。

- 使用 ``context.register_web_api(route, handler, methods, desc)`` 注册；
- 路由必须带插件名前缀，而 Page 端 bridge 的 endpoint **不含**插件名
  （Dashboard 会转发到 ``/api/v1/plugins/extensions/<plugin_name>/<endpoint>``）；
- 同时注册「原始大小写」与「全小写」两套前缀：AstrBot 在部分路径会把插件名规范化，
  两套前缀可避免因大小写不一致导致面板 404（这是 AstrNa 踩过的坑）。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from astrbot.api.web import error_response, json_response, request

PLUGIN_NAME = "astrbot_plugin_Super_AstrBot"
PLUGIN_NAME_LOWER = PLUGIN_NAME.lower()

Handler = Callable[[], Awaitable[Any]]


def register_web_apis(context: Any, app: Any) -> None:
    """注册面板后端接口。"""
    for prefix in (f"/{PLUGIN_NAME}", f"/{PLUGIN_NAME_LOWER}"):
        context.register_web_api(
            f"{prefix}/overview", _overview(app), ["GET"], "Super_AstrBot 总览"
        )
        context.register_web_api(f"{prefix}/memories", _memories(app), ["GET"], "记忆列表")
        context.register_web_api(f"{prefix}/search", _search(app), ["POST"], "检索记忆")
        context.register_web_api(f"{prefix}/journals", _journals(app), ["GET"], "周记列表")
        context.register_web_api(f"{prefix}/reviews", _reviews(app), ["GET"], "待审队列")
        context.register_web_api(
            f"{prefix}/review-action", _review_action(app), ["POST"], "审批记忆"
        )


def _not_ready() -> Any:
    return error_response("插件尚未就绪（可能正在初始化或持久层不可用）", status_code=503)


def _int_param(name: str, default: int, *, low: int, high: int) -> int:
    try:
        value = int(request.query.get(name, default, type=int) or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _str_param(name: str, default: str = "") -> str:
    raw = request.query.get(name, default)
    return str(raw) if raw is not None else default


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

        return json_response(
            {
                "status": "ok",
                "data": {
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
                },
            }
        )

    return handler


# --------------------------------------------------------------------------- #
# 记忆
# --------------------------------------------------------------------------- #


def _memories(app: Any) -> Handler:
    async def handler() -> Any:
        if app.memory is None:
            return _not_ready()
        limit = _int_param("limit", 20, low=1, high=100)
        offset = _int_param("offset", 0, low=0, high=1_000_000)
        keyword = _str_param("keyword")
        umo = _str_param("umo")
        try:
            if umo:
                from ..spec.scopes import MemoryScope

                rows = await app.memory.list_memories(
                    MemoryScope.for_session(umo), offset=offset, limit=limit, keyword=keyword
                )
            else:
                rows = await app.memory.list_all(offset=offset, limit=limit, keyword=keyword)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取记忆失败：{exc}")

        return json_response(
            {
                "status": "ok",
                "data": [
                    {
                        "id": item.id,
                        "content": item.content,
                        "kind": item.kind,
                        "source": item.source,
                        "scope": f"{item.scope_type}:{item.scope_id}",
                        "importance": item.importance,
                        "confidence": item.confidence,
                        "tags": item.tags,
                        "created_at": item.created_at,
                        "last_access_at": item.last_access_at,
                        "access_count": item.access_count,
                    }
                    for item in rows
                ],
            }
        )

    return handler


def _search(app: Any) -> Handler:
    async def handler() -> Any:
        if app.memory is None:
            return _not_ready()
        payload = await request.json(default={}) or {}
        query = str(payload.get("query") or "").strip()
        if not query:
            return error_response("query 不能为空")
        limit = payload.get("limit")
        try:
            limit = max(1, min(20, int(limit))) if limit is not None else None
        except (TypeError, ValueError):
            limit = None

        umo = str(payload.get("umo") or "").strip()
        try:
            if umo:
                from ..spec.scopes import MemoryScope

                result = await app.memory.recall(MemoryScope.for_session(umo), query, limit=limit)
                items = [
                    {
                        "id": item.id,
                        "content": item.content,
                        "kind": item.kind,
                        "source": item.source,
                        "score": item.score,
                        "breakdown": item.score_breakdown,
                    }
                    for item in result.items
                ]
                return json_response(
                    {
                        "status": "ok",
                        "data": {
                            "items": items,
                            "routes": result.route_summary,
                            "elapsed_ms": round(result.elapsed_ms, 2),
                            "degraded": result.degraded,
                        },
                    }
                )

            # 未指定会话时退化为跨作用域关键词匹配（只做 LIKE，不打分）
            rows = await app.memory.list_all(offset=0, limit=limit or 10, keyword=query)
            return json_response(
                {
                    "status": "ok",
                    "data": {
                        "items": [
                            {
                                "id": item.id,
                                "content": item.content,
                                "kind": item.kind,
                                "source": item.source,
                                "score": None,
                                "breakdown": {},
                            }
                            for item in rows
                        ],
                        "routes": "like",
                        "elapsed_ms": 0,
                        "degraded": "未指定 umo，已退化为跨作用域关键词匹配",
                    },
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
        if app.memory is None:
            return _not_ready()
        limit = _int_param("limit", 20, low=1, high=100)
        offset = _int_param("offset", 0, low=0, high=1_000_000)
        try:
            rows = await app.memory.list_all_journals(offset=offset, limit=limit)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取周记失败：{exc}")
        return json_response(
            {
                "status": "ok",
                "data": [
                    {
                        "id": row.get("id"),
                        "content": row.get("content"),
                        "tags": row.get("tags"),
                        "emotion": row.get("emotion"),
                        "event_time": row.get("event_time"),
                        "scope": f"{row.get('scope_type')}:{row.get('scope_id')}",
                    }
                    for row in rows
                ],
            }
        )

    return handler


# --------------------------------------------------------------------------- #
# 待审与审批
# --------------------------------------------------------------------------- #


def _reviews(app: Any) -> Handler:
    async def handler() -> Any:
        reflection = app.reflection
        if reflection is None:
            return _not_ready()
        umo = _str_param("umo")
        limit = _int_param("limit", 10, low=1, high=50)
        try:
            from ..spec.scopes import MemoryScope

            scope = MemoryScope.for_session(umo) if umo else MemoryScope.global_scope()
            rows = await reflection.pending_reviews(scope, limit=limit)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取待审队列失败：{exc}")

        import json as _json

        data = []
        for row in rows:
            try:
                payload = _json.loads(row.get("payload") or "{}")
            except (TypeError, ValueError):
                payload = {}
            data.append(
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
        return json_response({"status": "ok", "data": data})

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
                return json_response({"status": "ok", "data": {"memory_id": memory_id}})
            ok = await reflection.reject(review_id)
            if not ok:
                return error_response("该记录不存在或已处理", status_code=404)
            return json_response({"status": "ok", "data": {"rejected": True}})
        except Exception as exc:  # noqa: BLE001
            return error_response(f"处理失败：{exc}")

    return handler
