"""面板编排层：把「一次页面请求」需要的多个服务调用拼成一个响应。

为什么单独一层：

- ``api.py`` 只负责 HTTP 语义（参数解析、响应封装、错误码），不应知道
  「检索页要顺带查证据链」这类业务拼装；
- 面板需要的组合（召回 + 证据链 + 图扩展、后端状态 + 三级占比 + 衰减曲线）
  与命令侧/主链路无关，放在 ``app`` 上会让 app 继续膨胀。

约定：本模块的函数都接收 ``app``（``SuperAstrBotApp``）与已解析的参数，
返回「直接可 JSON 序列化」的 dict；服务缺失时返回可读的 ``degraded`` 说明，
而不是抛异常——面板必须能在任何降级组合下打开。
"""

from __future__ import annotations

from typing import Any, Mapping

from ..spec.scopes import MemoryScope, ScopeType

_NOT_READY = "该模块未装配（能力未启用或持久层不可用）"


# --------------------------------------------------------------------------- #
# 记忆：召回（含证据链与图扩展）
# --------------------------------------------------------------------------- #


async def recall(app: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    """混合召回 + 时序图谱证据链 + 图上相邻记忆补充。

    ``sender_id`` 过滤在召回之后执行：拟人化防串台的关键是「不要把 A 的事
    安到 B 头上」，因此过滤必须**在结果可见之前**完成，且要在响应里说明
    过滤前后的条数，避免用户误以为检索漏了。
    """
    memory = app.memory
    if memory is None:
        return {"items": [], "total": 0, "degraded": _NOT_READY}

    query = str(payload.get("query") or "").strip()
    if not query:
        return {"items": [], "total": 0, "degraded": "query 不能为空"}

    umo = str(payload.get("umo") or "").strip()
    sender_id = str(payload.get("sender_id") or "").strip()
    try:
        limit = int(payload.get("limit") or 5)
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(20, limit))

    scope = MemoryScope.for_session(umo) if umo else MemoryScope.global_scope()
    result = await memory.recall(scope, query, limit=limit)
    items = list(result.items)

    # 证据链：用召回结果反查 TKG，得到实体 / 关系 / 来源引用
    provenance: dict[int, dict[str, Any]] = {}
    expanded: list[dict[str, Any]] = []
    tkg = app.tkg_service
    if tkg is not None:
        try:
            provenance = await tkg.provenance(memory_ids=[item.id for item in items], scope=scope)
            extra = await tkg.expand(memory_ids=[item.id for item in items], scope=scope)
            for memory_id in extra.get("extra_ids") or []:
                extra_item = await memory.get_memory(int(memory_id))
                if extra_item is None:
                    continue
                record = _memory_item(extra_item)
                record["via"] = str((extra.get("via") or {}).get(int(memory_id), ""))
                record["source"] = "tkg-expand"
                expanded.append(record)
        except Exception as exc:  # noqa: BLE001  证据链失败不影响召回本身
            provenance = {}
            expanded = []
            if app.logger is not None:
                app.logger.debug("证据链查询失败：%s", exc)

    rows = []
    for item in items:
        record = _memory_item(item)
        chain = provenance.get(item.id) or {}
        record["provenance"] = chain
        record["match"] = _match_summary(item)
        rows.append(record)

    filtered_out = 0
    if sender_id:
        before = len(rows)
        rows = [row for row in rows if str(row.get("sender_id") or "") == sender_id]
        expanded = [row for row in expanded if str(row.get("sender_id") or "") == sender_id]
        filtered_out = before - len(rows)

    return {
        "items": rows,
        "expanded": expanded,
        "total": len(rows) + len(expanded),
        "umo": umo,
        "sender_id": sender_id,
        "filtered_out": filtered_out,
        "routes": getattr(result, "route_summary", ""),
        "rerank": getattr(result, "rerank_summary", ""),
        "elapsed_ms": round(float(getattr(result, "elapsed_ms", 0.0) or 0.0), 2),
        "degraded": getattr(result, "degraded", ""),
        "tkg": bool(provenance),
    }


def _memory_item(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "content": item.content,
        "kind": item.kind,
        "source": getattr(item, "source", ""),
        "scope": f"{item.scope_type}:{item.scope_id}",
        "scope_type": item.scope_type,
        "scope_id": item.scope_id,
        "importance": round(float(item.importance), 4),
        "confidence": round(float(item.confidence), 4),
        "status": item.status,
        "tags": list(getattr(item, "tags", []) or []),
        "score": round(float(getattr(item, "score", 0.0) or 0.0), 4),
        "breakdown": dict(getattr(item, "score_breakdown", {}) or {}),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "last_access_at": item.last_access_at,
        "access_count": item.access_count,
        "sender_id": getattr(item, "sender_id", ""),
        "sender_name": getattr(item, "sender_name", ""),
        "origin_umo": getattr(item, "origin_umo", ""),
    }


def _match_summary(item: Any) -> str:
    breakdown = dict(getattr(item, "score_breakdown", {}) or {})
    if not breakdown:
        return "关键词（未指定会话时无打分构成）"
    parts = [f"{key} {float(value):.2f}" for key, value in breakdown.items() if value]
    return " + ".join(parts) or "—"


async def memory_facets(app: Any) -> dict[str, Any]:
    """记忆页的筛选项：发送者清单（带昵称）与层级清单。"""
    senders: list[dict[str, Any]] = []
    db = app.db
    if db is not None:
        rows = await db.query(
            "SELECT sender_id, MAX(sender_name) AS sender_name, COUNT(*) AS count"
            " FROM memories WHERE sender_id != '' GROUP BY sender_id"
            " ORDER BY count DESC LIMIT 200"
        )
        senders = [
            {
                "sender_id": str(row["sender_id"] or ""),
                "sender_name": str(row["sender_name"] or ""),
                "count": int(row["count"] or 0),
            }
            for row in rows
        ]
    tiers = ["core", "recall", "archive"]
    return {"senders": senders, "tiers": tiers}


# --------------------------------------------------------------------------- #
# 人格：三层画像 / 演化
# --------------------------------------------------------------------------- #


async def forge_snapshot(app: Any) -> dict[str, Any]:
    service = app.forge_service
    if service is None:
        return {"degraded": _NOT_READY, "profile": None}
    return await service.snapshot()


async def forge_update(app: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    service = app.forge_service
    if service is None:
        return {"ok": False, "message": _NOT_READY}
    action = str(payload.get("action") or "update").strip().lower()
    if action == "reset":
        snapshot = await service.reset()
        return {"ok": True, "message": "已重置为出厂人格", "snapshot": snapshot}
    patch = payload.get("patch")
    if not isinstance(patch, Mapping):
        patch = {key: value for key, value in payload.items() if key != "action"}
    snapshot = await service.update(patch)
    return {"ok": True, "message": "三层人格已保存", "snapshot": snapshot}


async def evolution_overview(app: Any, *, limit: int = 30, days: int = 30) -> dict[str, Any]:
    service = app.evolution_service
    if service is None:
        return {"degraded": _NOT_READY, "timeline": [], "radar": {"axes": [], "values": {}}}
    return await service.overview(limit=limit, days=days)


async def evolution_reset(app: Any) -> dict[str, Any]:
    service = app.evolution_service
    if service is None:
        return {"ok": False, "message": _NOT_READY}
    return await service.reset_events()


# --------------------------------------------------------------------------- #
# 群友识别
# --------------------------------------------------------------------------- #


async def members_roster(app: Any, *, limit: int | None = None) -> dict[str, Any]:
    service = app.members_service
    if service is None:
        return {"degraded": _NOT_READY, "members": [], "stability": {}}
    return await service.roster(limit=limit)


async def member_strategy(app: Any, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """群友策略的读 / 写 / 删 / 蒸馏（按 ``action`` 分派）。"""
    service = app.members_service
    if service is None:
        return {"ok": False, "message": _NOT_READY}
    payload = payload or {}
    action = str(payload.get("action") or "get").strip().lower()
    sender_id = str(payload.get("sender_id") or "").strip()
    if not sender_id:
        return {"ok": False, "message": "缺少 sender_id"}

    if action == "get":
        row = await service.strategy(sender_id)
        return {"ok": True, "sender_id": sender_id, "strategy": row}
    if action == "save":
        return await service.save_strategy(sender_id, payload)
    if action == "delete":
        return await service.delete_strategy(sender_id)
    if action == "distill":
        return await service.distill(sender_id)
    if action == "distill-all":
        roster = await service.roster()
        results = []
        for member in roster.get("members") or []:
            if not member.get("source"):
                results.append(await service.distill(str(member["sender_id"])))
        return {"ok": True, "distilled": len(results), "results": results[:20]}
    return {"ok": False, "message": f"未知 action：{action}"}


# --------------------------------------------------------------------------- #
# 世界书
# --------------------------------------------------------------------------- #


async def worldbook_list(app: Any, *, umo: str = "", limit: int | None = None) -> dict[str, Any]:
    service = app.worldbook_service
    if service is None:
        return {"degraded": _NOT_READY, "items": [], "total": 0}
    data = await service.list_entries(limit=limit, umo=umo)
    data["stats"] = await service.stats()
    return data


async def worldbook_save(app: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    service = app.worldbook_service
    if service is None:
        return {"ok": False, "message": _NOT_READY}
    entry_id = payload.get("id")
    if entry_id:
        try:
            return await service.update(int(entry_id), payload)
        except (TypeError, ValueError):
            return {"ok": False, "message": "id 不合法"}
    return await service.add(payload)


async def worldbook_delete(app: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    service = app.worldbook_service
    if service is None:
        return {"ok": False, "message": _NOT_READY}
    try:
        return await service.delete(int(payload.get("id")))
    except (TypeError, ValueError):
        return {"ok": False, "message": "id 不合法"}


# --------------------------------------------------------------------------- #
# 共情
# --------------------------------------------------------------------------- #


async def empathy_overview(app: Any, *, limit: int = 50) -> dict[str, Any]:
    service = app.empathy_service
    config = app.empathy_config
    config_view = (
        {
            "enabled": bool(getattr(config, "enabled", False)),
            "stage_identify": bool(getattr(config, "stage_identify", True)),
            "stage_understand": bool(getattr(config, "stage_understand", True)),
            "stage_empathize": bool(getattr(config, "stage_empathize", True)),
            "temperature": float(getattr(config, "temperature", 0.55)),
            "min_intensity": float(getattr(config, "min_intensity", 0.35)),
            "max_injected_chars": int(getattr(config, "max_injected_chars", 300)),
        }
        if config is not None
        else {}
    )
    if service is None:
        return {"degraded": _NOT_READY, "config": config_view, "items": [], "stats": {}}
    log = await service.log(limit=limit)
    stats = await service.stats(hours=24)
    return {"config": config_view, "stats": stats, **log}


async def empathy_save(app: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    """保存共情设置：逐项走 ``set_feature_setting``（白名单路径 + 落盘 + 热应用）。"""
    mapping = {
        "stage_identify": "empathy.stage_identify",
        "stage_understand": "empathy.stage_understand",
        "stage_empathize": "empathy.stage_empathize",
        "temperature": "empathy.temperature",
        "min_intensity": "empathy.min_intensity",
    }
    results: list[dict[str, Any]] = []
    for field, key in mapping.items():
        if field not in payload:
            continue
        if field == "temperature":
            try:
                value = round(max(0.0, min(1.0, float(payload[field]))), 2)
            except (TypeError, ValueError):
                value = 0.55
        elif field == "min_intensity":
            try:
                value = round(max(0.0, min(1.0, float(payload[field]))), 2)
            except (TypeError, ValueError):
                value = 0.35
        else:
            value = bool(payload[field])
        results.append(await app.set_feature_setting(key, value))
    failed = [item for item in results if not item.get("ok")]
    if failed:
        return {"ok": False, "message": failed[0].get("message") or "保存失败", "results": results}
    return {"ok": True, "message": "共情设置已保存", "results": results}


# --------------------------------------------------------------------------- #
# 主动关怀（回访队列 / 计划轨 / 日志）
# --------------------------------------------------------------------------- #


async def proactive_queue(
    app: Any, payload: Mapping[str, Any] | None = None, *, status: str = "", limit: int = 50
) -> dict[str, Any]:
    service = app.queue_service
    if service is None:
        return {"degraded": _NOT_READY, "items": [], "total": 0}
    payload = payload or {}
    action = str(payload.get("action") or "list").strip().lower()
    if action == "list":
        data = await service.list_items(status=status, limit=limit)
        data["stats"] = await service.stats()
        return data
    if action == "add":
        result = await service.add(payload)
    elif action == "update":
        try:
            result = await service.update(int(payload.get("id")), payload)
        except (TypeError, ValueError):
            result = {"ok": False, "message": "id 不合法"}
    elif action in {"cancel", "sent", "skip"}:
        try:
            status_map = {"cancel": "canceled", "sent": "sent", "skip": "skipped"}
            await service.mark(int(payload.get("id")), status_map[action])
            result = {"ok": True, "message": "状态已更新"}
        except (TypeError, ValueError):
            result = {"ok": False, "message": "id 不合法"}
    elif action == "delete":
        try:
            result = await service.delete(int(payload.get("id")))
        except (TypeError, ValueError):
            result = {"ok": False, "message": "id 不合法"}
    else:
        result = {"ok": False, "message": f"未知 action：{action}"}
    data = await service.list_items(status=status, limit=limit)
    data["stats"] = await service.stats()
    return {**result, "queue": data}


async def proactive_schedule(app: Any) -> dict[str, Any]:
    """计划轨 / 空闲轨的当前配置与最近一次发送结果（原「主动消息」视图）。"""
    config = app.proactive_config
    snapshot = app.proactive_service.snapshot() if app.proactive_service is not None else {}
    config_view = (
        {
            "enabled": bool(getattr(config, "enabled", False)),
            "targets": list(getattr(config, "targets", ()) or ()),
            "daily_enabled": bool(getattr(config, "daily_enabled", False)),
            "daily_time": str(getattr(config, "daily_time", "") or ""),
            "idle_enabled": bool(getattr(config, "idle_enabled", False)),
            "idle_minutes": int(getattr(config, "idle_minutes", 0) or 0),
            "quiet_hours": [
                int(getattr(config, "quiet_start", 0) or 0),
                int(getattr(config, "quiet_end", 0) or 0),
            ],
            "daily_max": int(getattr(config, "daily_max", 0) or 0),
        }
        if config is not None
        else {}
    )
    return {"config": config_view, "snapshot": snapshot}


async def proactive_log(app: Any, *, limit: int = 20) -> dict[str, Any]:
    service = app.queue_service
    items = await service.recent(limit=limit) if service is not None else []
    return {"items": items, "total": len(items)}


# --------------------------------------------------------------------------- #
# 群上下文
# --------------------------------------------------------------------------- #


async def group_context(app: Any) -> dict[str, Any]:
    """群聊语义与接管原则：配置 + 当前生效的名单判定。"""
    config = app.group_config
    if config is None:
        return {"degraded": _NOT_READY}
    snapshot = {}
    if app.group_service is not None:
        snapshot = app.group_service.snapshot()
    return {
        "enabled": bool(getattr(config, "enabled", False)),
        "attention_threshold": float(getattr(config, "attention_threshold", 0.0)),
        "cooldown_seconds": int(getattr(config, "cooldown_seconds", 0)),
        "max_per_hour": int(getattr(config, "max_per_hour", 0)),
        "merge_window_seconds": float(getattr(config, "merge_window_seconds", 0.0)),
        "bot_aliases": list(getattr(config, "bot_aliases", ()) or ()),
        "whitelist": list(getattr(config, "whitelist", ()) or ()),
        "blacklist": list(getattr(config, "blacklist", ()) or ()),
        "snapshot": snapshot,
    }


# --------------------------------------------------------------------------- #
# 融合状态
# --------------------------------------------------------------------------- #


async def fusion_backends(app: Any, *, umo: str = "") -> dict[str, Any]:
    status = app.fusion_status
    if status is None:
        return {"degraded": _NOT_READY, "backends": []}
    data = await status.backends(umo=umo)
    tiers = app.tiers_service
    if tiers is not None:
        data["tiers_detail"] = await tiers.overview(samples=5)
    return data


async def fusion_pipeline(app: Any, *, umo: str = "") -> dict[str, Any]:
    status = app.fusion_status
    if status is None:
        return {"degraded": _NOT_READY, "stages": []}
    return await status.pipeline(umo=umo)


async def fusion_health(app: Any) -> dict[str, Any]:
    status = app.fusion_status
    if status is None:
        return {"degraded": _NOT_READY, "items": []}
    return await status.health()


async def tkg_rebuild(app: Any, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    service = app.tkg_service
    if service is None:
        return {"ok": False, "message": _NOT_READY}
    payload = payload or {}
    try:
        limit = int(payload.get("limit") or 300)
    except (TypeError, ValueError):
        limit = 300
    result = await service.rebuild(limit=max(10, min(2000, limit)))
    return {**result, "stats": await service.stats()}


async def tkg_timeline(app: Any, *, limit: int = 20) -> dict[str, Any]:
    service = app.tkg_service
    if service is None:
        return {"degraded": _NOT_READY, "items": []}
    return {"items": await service.timeline(limit=limit)}


# --------------------------------------------------------------------------- #
# 好感度（面板直接读写）
# --------------------------------------------------------------------------- #


async def affinity_rows(app: Any, *, umo: str = "", limit: int = 50) -> dict[str, Any]:
    service = app.persona_service
    if service is None:
        return {"degraded": _NOT_READY, "items": []}
    scope = MemoryScope.for_session(umo) if umo else None
    if scope is not None:
        rows = await service.affinity_rows(scope, limit=limit)
    else:
        rows = await service.all_affinity(limit=limit)
    return {
        "items": [
            {
                "scope": f"{row.get('scope_type')}:{row.get('scope_id')}",
                "target_id": row.get("target_id"),
                "score": round(float(row.get("score") or 0.0), 4),
                "mood": row.get("mood"),
                "interactions": row.get("interactions"),
                "last_interaction": row.get("last_interaction"),
            }
            for row in rows
        ],
        "umo": umo,
    }


async def affinity_set(app: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    """人工校准好感度（面板滑块）：写入走既有 affinity 仓储，口径与学习一致。"""
    repo = app.affinity_repo
    if repo is None or app.clock is None:
        return {"ok": False, "message": _NOT_READY}
    target_id = str(payload.get("target_id") or "").strip()
    if not target_id:
        return {"ok": False, "message": "缺少 target_id"}
    umo = str(payload.get("umo") or "").strip()
    scope = MemoryScope.for_session(umo) if umo else MemoryScope(ScopeType.USER, target_id)
    try:
        score = float(payload.get("score"))
    except (TypeError, ValueError):
        return {"ok": False, "message": "score 必须是 0~1 的数字"}
    score = max(0.0, min(1.0, score))
    now = app.clock()
    current = await repo.get(scope.scope_type, scope.scope_id, target_id) or {}
    await repo.upsert(
        scope_type=scope.scope_type,
        scope_id=scope.scope_id,
        target_id=target_id,
        score=score,
        mood=str(current.get("mood") or "manual"),
        interactions=int(current.get("interactions") or 0),
        last_interaction=float(current.get("last_interaction") or now),
        updated_at=now,
    )
    return {"ok": True, "message": "好感度已更新", "score": score}


__all__ = [
    "affinity_rows",
    "affinity_set",
    "empathy_overview",
    "empathy_save",
    "evolution_overview",
    "evolution_reset",
    "forge_snapshot",
    "forge_update",
    "fusion_backends",
    "fusion_health",
    "fusion_pipeline",
    "group_context",
    "member_strategy",
    "members_roster",
    "memory_facets",
    "proactive_log",
    "proactive_queue",
    "proactive_schedule",
    "recall",
    "tkg_rebuild",
    "tkg_timeline",
    "worldbook_delete",
    "worldbook_list",
    "worldbook_save",
]
