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

import base64
import binascii
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from astrbot.api.web import error_response, json_response, request

try:  # 官方 Pages 文档的下载通道；老版本 AstrBot 无此符号时回退到内联 base64
    from astrbot.api.web import file_response
except ImportError:  # pragma: no cover - 取决于 AstrBot 版本
    file_response = None  # type: ignore[assignment]

from ..journal.service import to_export_item
from ..monitor import CORE_METRICS, MAX_RANGE_HOURS
from ..spec.entry_types import ENTRY_TYPES, normalize_entry_type
from ..spec.scopes import MemoryScope
from ..storage import (
    DEFAULT_JOURNAL_SORT,
    DEFAULT_MEMORY_SORT,
    JOURNAL_SORT_OPTIONS,
    MEMORY_SORT_OPTIONS,
)

PLUGIN_NAME = "astrbot_plugin_Super_AstrBot"
PLUGIN_NAME_LOWER = PLUGIN_NAME.lower()

Handler = Callable[[], Awaitable[Any]]

_ALLOWED_MEMORY_STATUS = {"active", "buffered", "pending", "archived", "forgotten"}

_INLINE_BACKUP_LIMIT = 24 * 1024 * 1024
"""回退通道（base64 内联）的体积上限：超过就只落盘并提示路径，避免把面板拖死。"""


def register_web_apis(context: Any, app: Any) -> None:
    """注册面板后端接口（大小写两套前缀）。"""
    routes: list[tuple[str, Handler, list[str], str]] = [
        ("overview", _overview(app), ["GET"], "Super_AstrBot 总览与系统诊断"),
        ("models", _models(app), ["GET"], "三类模型提供商与各功能所用模型"),
        ("features", _features(app), ["GET"], "功能清单与开关状态"),
        ("feature-toggle", _feature_toggle(app), ["POST"], "开启/关闭某个功能"),
        ("feature-setting", _feature_setting(app), ["POST"], "修改功能具体设置"),
        ("memories", _memories(app), ["GET"], "记忆列表（支持筛选与分页）"),
        ("memory", _memory_detail(app), ["GET"], "单条记忆详情"),
        ("memories/update", _memory_update(app), ["POST"], "面板编辑记忆内容"),
        ("memories/delete", _memory_delete(app), ["POST"], "面板删除记忆"),
        ("search", _search(app), ["POST"], "混合检索记忆"),
        ("journals", _journals(app), ["GET"], "现实桥记录列表（周记 / 日记 / 随笔）"),
        ("journals/add", _journal_add(app), ["POST"], "面板写入现实记录"),
        ("journals/update", _journal_update(app), ["POST"], "面板编辑现实记录"),
        ("journals/delete", _journal_delete(app), ["POST"], "面板删除现实记录"),
        ("journals/export", _journal_export(app), ["GET"], "导出全部现实记录 JSON"),
        ("journals/export-selected", _journal_export_selected(app), ["POST"], "按勾选导出所选记录 JSON"),
        ("journals/import", _journal_import(app), ["POST"], "导入现实记录 JSON"),
        ("weeklies", _weeklies(app), ["GET"], "每周总结列表"),
        ("weeklies/update", _weekly_update(app), ["POST"], "面板编辑每周总结"),
        ("weeklies/delete", _weekly_delete(app), ["POST"], "删除每周总结"),
        ("weeklies/export", _weekly_export(app), ["GET"], "导出每周总结 JSON"),
        ("weeklies/import", _weekly_import(app), ["POST"], "导入每周总结 JSON"),
        ("config/export", _config_export(app), ["GET"], "导出插件配置 JSON"),
        ("config/import", _config_import(app), ["POST"], "导入插件配置 JSON"),
        ("memories/export", _memories_export(app), ["GET"], "导出记忆 JSON"),
        ("memories/import", _memories_import(app), ["POST"], "导入记忆 JSON"),
        ("reviews", _reviews(app), ["GET"], "待审队列"),
        ("review-action", _review_action(app), ["POST"], "审批待审记录"),
        ("reviews/batch", _review_batch(app), ["POST"], "批量批准/驳回待审记录"),
        ("identities", _identities(app), ["GET"], "用户身份观测与稳定性判定"),
        ("identities/clear", _identities_clear(app), ["POST"], "清空身份观测"),
        ("scopes", _scopes(app), ["GET"], "记忆作用域分布"),
        ("scopes/migrate", _scopes_migrate(app), ["POST"], "作用域迁移（支持预览）"),
        ("backup/export", _backup_export(app), ["GET", "POST"], "导出备份包（配置+数据库+数据）"),
        ("backup/import", _backup_import(app), ["POST"], "从备份包恢复（merge/replace）"),
        ("backup/replace-database", _backup_replace_database(app), ["POST"], "整库恢复（用包内快照替换数据库）"),
        ("backup/list", _backup_list(app), ["GET"], "历史备份包列表"),
        ("persona", _persona(app), ["GET"], "拟人化学习数据（风格 / 黑话 / 好感度）"),
        ("graph", _graph(app), ["GET"], "知识图谱子图（可视化用）"),
        ("monitor", _monitor(app), ["GET"], "运行监控指标与时间序列"),
        ("prompts", _prompts(app), ["GET"], "提示词定制项与当前值"),
        ("prompt-save", _prompt_save(app), ["POST"], "保存单条提示词覆盖"),
        ("prompt-reset", _prompt_reset(app), ["POST"], "重置单条提示词为内置默认"),
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


def _bool_param(name: str, default: bool) -> bool:
    """查询参数里的布尔：``0/false/no/off`` 视为假，缺省或空串沿用默认值。"""
    raw = request.query.get(name, None)
    if raw is None:
        return default
    token = str(raw).strip().lower()
    if token == "":
        return default
    return token not in {"0", "false", "no", "off"}


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
        "sender_id": getattr(item, "sender_id", ""),
        "sender_name": getattr(item, "sender_name", ""),
        "origin_umo": getattr(item, "origin_umo", ""),
    }


# --------------------------------------------------------------------------- #
# 总览
# --------------------------------------------------------------------------- #


def _overview(app: Any) -> Handler:
    async def handler() -> Any:
        try:
            status = await app.status()
        except Exception as exc:
            return error_response(f"读取状态失败：{exc}")

        stats: dict[str, Any] = {}
        if app.memory is not None:
            try:
                stats = await app.memory.stats_all()
            except Exception as exc:
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
                "rerank": status.get("rerank"),
                "embedding_providers": providers,
            }
        )

    return handler


# --------------------------------------------------------------------------- #
# 模型
# --------------------------------------------------------------------------- #


def _models(app: Any) -> Handler:
    async def handler() -> Any:
        try:
            return _ok(app.models_overview())
        except Exception as exc:
            return error_response(f"读取模型信息失败：{exc}")

    return handler


# --------------------------------------------------------------------------- #
# 功能开关
# --------------------------------------------------------------------------- #


def _features(app: Any) -> Handler:
    async def handler() -> Any:
        try:
            features = app.feature_catalog()
        except Exception as exc:
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
        except Exception as exc:
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


def _feature_setting(app: Any) -> Handler:
    async def handler() -> Any:
        payload = await request.json(default={}) or {}
        key = str(payload.get("key") or "").strip()
        if not key:
            return error_response("缺少参数 key")
        if "value" not in payload:
            return error_response("缺少参数 value")

        try:
            result = await app.set_feature_setting(key, payload["value"])
        except Exception as exc:  # noqa: BLE001
            return error_response(f"保存失败：{exc}")

        if not result.get("ok"):
            return error_response(str(result.get("message") or "保存失败"))
        return _ok(
            {
                "key": result.get("key"),
                "value": result.get("value"),
                "persisted": result.get("persisted"),
                "message": result.get("message"),
                "capabilities": dict(app.capabilities),
            }
        )

    return handler


# --------------------- 配置维护 + 记忆导入导出 --------------------- #


def _config_export(app: Any) -> Handler:
    async def handler() -> Any:
        try:
            return _ok(app.config_export())
        except Exception as exc:  # noqa: BLE001
            return error_response(f"导出配置失败：{exc}")

    return handler


def _config_import(app: Any) -> Handler:
    async def handler() -> Any:
        files = await request.files()
        upload = files.get("file")
        if upload is None:
            return error_response("缺少文件字段 file")
        try:
            raw = (await upload.read()).decode("utf-8")
        except UnicodeDecodeError:
            return error_response("文件必须是 UTF-8 编码的 JSON")
        try:
            data = json.loads(raw)
        except ValueError:
            return error_response("JSON 解析失败")
        if isinstance(data, dict) and isinstance(data.get("config"), dict):
            data = data["config"]  # 兼容本插件导出的完整信封
        try:
            result = await app.config_import(data)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"导入配置失败：{exc}")
        if not result.get("ok"):
            return error_response(str(result.get("message") or "导入失败"))
        return _ok(result)

    return handler


def _memories_export(app: Any) -> Handler:
    async def handler() -> Any:
        try:
            items = await app.panel_memory_export()
        except Exception as exc:  # noqa: BLE001
            return error_response(f"导出记忆失败：{exc}")
        return _ok(
            {
                "kind": "super_astrbot.memories",
                "exported_at": time.time(),
                "count": len(items),
                "items": items,
            }
        )

    return handler


def _memories_import(app: Any) -> Handler:
    async def handler() -> Any:
        files = await request.files()
        upload = files.get("file")
        if upload is None:
            return error_response("缺少文件字段 file")
        try:
            raw = (await upload.read()).decode("utf-8")
        except UnicodeDecodeError:
            return error_response("文件必须是 UTF-8 编码的 JSON")
        try:
            data = json.loads(raw)
        except ValueError:
            return error_response("JSON 解析失败")
        if isinstance(data, dict):
            data = data.get("items")
        if not isinstance(data, list):
            return error_response("JSON 顶层必须是数组或含 items 数组")
        try:
            result = await app.panel_memory_import(data)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"导入记忆失败：{exc}")
        return _ok(result)

    return handler


# --------------------- 现实桥记录管理（参照 admin-diary-proxy 模式） --------------------- #


def _journal_add(app: Any) -> Handler:
    async def handler() -> Any:
        payload = await request.json(default={}) or {}
        try:
            result = await app.panel_journal_add(payload)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"写入记录失败：{exc}")
        if not result.get("ok"):
            return error_response(str(result.get("message") or "写入失败"))
        return _ok(result)

    return handler


def _journal_update(app: Any) -> Handler:
    async def handler() -> Any:
        payload = await request.json(default={}) or {}
        journal_id = payload.get("id")
        if not isinstance(journal_id, int) or journal_id <= 0:
            return error_response("id 不合法")
        if not str(payload.get("content") or "").strip():
            return error_response("内容不能为空")
        try:
            result = await app.panel_journal_update(journal_id, payload)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"保存记录失败：{exc}")
        if not result.get("ok"):
            return error_response(str(result.get("message") or "保存失败"))
        return _ok(result)

    return handler


def _journal_delete(app: Any) -> Handler:
    async def handler() -> Any:
        payload = await request.json(default={}) or {}
        journal_id = payload.get("id")
        if not isinstance(journal_id, int) or journal_id <= 0:
            return error_response("id 不合法")
        try:
            result = await app.panel_journal_delete(journal_id)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"删除记录失败：{exc}")
        if not result.get("ok"):
            return error_response(str(result.get("message") or "删除失败"))
        return _ok(result)

    return handler


def _journal_export(app: Any) -> Handler:
    async def handler() -> Any:
        try:
            items = await app.panel_journal_export()
        except Exception as exc:  # noqa: BLE001
            return error_response(f"导出记录失败：{exc}")
        return _ok(
            {
                "kind": "super_astrbot.journals",
                "exported_at": time.time(),
                "count": len(items),
                "items": items,
            }
        )

    return handler


def _journal_export_selected(app: Any) -> Handler:
    """按勾选导出：``ids`` 为勾选集合；``all=true`` 表示「全选当前筛选」。"""

    async def handler() -> Any:
        payload = await request.json(default={}) or {}
        try:
            result = await app.panel_journal_export_selected(payload)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"导出所选记录失败：{exc}")
        if not result.get("ok"):
            return error_response(str(result.get("message") or "导出失败"))
        return _ok(
            {
                "kind": "super_astrbot.journals",
                "exported_at": time.time(),
                "mode": result.get("mode"),
                "count": result.get("count", 0),
                "items": result.get("items") or [],
            }
        )

    return handler


def _journal_import(app: Any) -> Handler:
    async def handler() -> Any:
        files = await request.files()
        upload = files.get("file")
        if upload is None:
            return error_response("缺少文件字段 file")
        try:
            raw = (await upload.read()).decode("utf-8")
        except UnicodeDecodeError:
            return error_response("文件必须是 UTF-8 编码的 JSON")
        try:
            data = json.loads(raw)
        except ValueError:
            return error_response("JSON 解析失败")
        if isinstance(data, dict):
            data = data.get("items")
        if not isinstance(data, list):
            return error_response("JSON 顶层必须是数组或含 items 数组")
        try:
            result = await app.panel_journal_import(data)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"导入记录失败：{exc}")
        return _ok(result)

    return handler


# ----------------------------- 每周总结 ----------------------------- #


def _weeklies(app: Any) -> Handler:
    async def handler() -> Any:
        memory = app.memory
        if memory is None:
            return _not_ready()
        limit = _int_param("limit", 20, low=1, high=100)
        offset = _int_param("offset", 0, low=0, high=1_000_000)
        keyword = _str_param("keyword").strip()
        try:
            data = await app.weeklies_page(offset=offset, limit=limit, keyword=keyword)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取每周总结失败：{exc}")
        data["keyword"] = keyword
        return _ok(data)

    return handler


def _weekly_delete(app: Any) -> Handler:
    async def handler() -> Any:
        payload = await request.json(default={}) or {}
        memory_id = payload.get("id")
        if not isinstance(memory_id, int) or memory_id <= 0:
            return error_response("id 不合法")
        try:
            result = await app.panel_weekly_delete(memory_id)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"删除每周总结失败：{exc}")
        if not result.get("ok"):
            return error_response(str(result.get("message") or "删除失败"))
        return _ok(result)

    return handler


def _weekly_update(app: Any) -> Handler:
    """面板编辑每周总结（与编辑记忆共用写入路径）。"""

    async def handler() -> Any:
        payload = await request.json(default={}) or {}
        memory_id = payload.get("id")
        content = str(payload.get("content") or "").strip()
        if not isinstance(memory_id, int) or memory_id <= 0:
            return error_response("id 不合法")
        if not content:
            return error_response("内容不能为空")
        try:
            result = await app.panel_weekly_update(memory_id, content)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"保存每周总结失败：{exc}")
        if not result.get("ok"):
            return error_response(str(result.get("message") or "保存失败"))
        return _ok(result)

    return handler


def _weekly_export(app: Any) -> Handler:
    async def handler() -> Any:
        try:
            items = await app.panel_weekly_export()
        except Exception as exc:  # noqa: BLE001
            return error_response(f"导出每周总结失败：{exc}")
        return _ok(
            {
                "kind": "super_astrbot.weeklies",
                "exported_at": time.time(),
                "count": len(items),
                "items": items,
            }
        )

    return handler


def _weekly_import(app: Any) -> Handler:
    async def handler() -> Any:
        files = await request.files()
        upload = files.get("file")
        if upload is None:
            return error_response("缺少文件字段 file")
        try:
            raw = (await upload.read()).decode("utf-8")
        except UnicodeDecodeError:
            return error_response("文件必须是 UTF-8 编码的 JSON")
        try:
            data = json.loads(raw)
        except ValueError:
            return error_response("JSON 解析失败")
        if isinstance(data, dict):
            data = data.get("items")
        if not isinstance(data, list):
            return error_response("JSON 顶层必须是数组或含 items 数组")
        try:
            result = await app.panel_weekly_import(data)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"导入每周总结失败：{exc}")
        return _ok(result)

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
        sort = _str_param("sort", DEFAULT_MEMORY_SORT).strip()
        if sort not in MEMORY_SORT_OPTIONS:
            sort = DEFAULT_MEMORY_SORT

        try:
            items = await memory.list_all(
                offset=offset,
                limit=limit,
                keyword=keyword,
                status=status,
                kind=kind,
                sort=sort,
            )
            total = await memory.count_filtered(status=status, kind=kind, keyword=keyword)
        except Exception as exc:
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
                "sort": sort,
                "sorts": list(MEMORY_SORT_OPTIONS),
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
        except Exception as exc:
            return error_response(f"读取记忆失败：{exc}")
        if item is None:
            return error_response("记忆不存在", status_code=404)

        return _ok(_memory_payload(item))

    return handler


def _memory_update(app: Any) -> Handler:
    """面板编辑记忆内容（详情侧滑面板的「保存」）。"""

    async def handler() -> Any:
        payload = await request.json(default={}) or {}
        memory_id = payload.get("id")
        content = str(payload.get("content") or "").strip()
        if not isinstance(memory_id, int) or memory_id <= 0:
            return error_response("id 不合法")
        if not content:
            return error_response("内容不能为空")
        try:
            result = await app.panel_memory_update(memory_id, content)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"保存记忆失败：{exc}")
        if not result.get("ok"):
            return error_response(str(result.get("message") or "保存失败"))
        return _ok(result)

    return handler


def _memory_delete(app: Any) -> Handler:
    """面板删除单条记忆（详情侧滑面板的「删除」）。"""

    async def handler() -> Any:
        payload = await request.json(default={}) or {}
        memory_id = payload.get("id")
        if not isinstance(memory_id, int) or memory_id <= 0:
            return error_response("id 不合法")
        try:
            result = await app.panel_memory_delete(memory_id)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"删除记忆失败：{exc}")
        if not result.get("ok"):
            return error_response(str(result.get("message") or "删除失败"))
        return _ok(result)

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
                        "rerank": result.rerank_summary,
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
        except Exception as exc:
            return error_response(f"检索失败：{exc}")

    return handler


# --------------------------------------------------------------------------- #
# 现实桥记录
# --------------------------------------------------------------------------- #


def _journals(app: Any) -> Handler:
    async def handler() -> Any:
        memory = app.memory
        if memory is None:
            return _not_ready()
        limit = _int_param("limit", 20, low=1, high=100)
        offset = _int_param("offset", 0, low=0, high=1_000_000)
        keyword = _str_param("keyword").strip()
        entry_type = _str_param("entry_type").strip().lower()
        if entry_type not in ENTRY_TYPES:
            entry_type = ""
        sort = _str_param("sort", DEFAULT_JOURNAL_SORT).strip()
        if sort not in JOURNAL_SORT_OPTIONS:
            sort = DEFAULT_JOURNAL_SORT
        try:
            rows = await memory.list_all_journals(
                offset=offset, limit=limit, keyword=keyword, sort=sort, entry_type=entry_type
            )
            total = await memory.count_all_journals(keyword=keyword, entry_type=entry_type)
            type_counts = await app.panel_journal_types()
        except Exception as exc:
            return error_response(f"读取记录失败：{exc}")

        return _ok(
            {
                # 与导出共用同一序列化：字段顺序即面板表头顺序，标签还原成数组，
                # 历史空标题按条目时间补默认标题。
                "items": [to_export_item(row) for row in rows],
                "total": total,
                "offset": offset,
                "limit": limit,
                "keyword": keyword,
                "entry_type": entry_type,
                "type_counts": type_counts,
                "types": list(ENTRY_TYPES),
                "default_type": _default_entry_type(app),
                "sort": sort,
                "sorts": list(JOURNAL_SORT_OPTIONS),
            }
        )

    return handler


def _default_entry_type(app: Any) -> str:
    """面板「新增记录」的默认类型（来自 ``journal.default_entry_type`` 配置）。"""
    config = getattr(app, "journal_config", None)
    return normalize_entry_type(getattr(config, "default_entry_type", ""))


# --------------------------------------------------------------------------- #
# 待审与审批
# --------------------------------------------------------------------------- #


def _reviews(app: Any) -> Handler:
    async def handler() -> Any:
        umo = _str_param("umo").strip()
        origin = _str_param("origin").strip()
        limit = _int_param("limit", 20, low=1, high=100)
        offset = _int_param("offset", 0, low=0, high=1_000_000)
        scope = MemoryScope.for_session(umo) if umo else None
        try:
            if scope is not None:
                rows = await app.pending_reviews(scope, limit=limit, offset=offset, origin=origin)
            else:
                rows = await app.pending_reviews_all(limit=limit, offset=offset, origin=origin)
            total = await app.pending_count(scope, origin=origin)
            origins = await app.pending_origins()
        except Exception as exc:
            return error_response(f"读取待审队列失败：{exc}")

        return _ok(
            {
                "items": rows,
                "total": total,
                "offset": offset,
                "limit": limit,
                "origins": origins,
                "origin": origin,
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
        except Exception as exc:
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
        except Exception as exc:
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
        except Exception as exc:
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
        except Exception as exc:
            return error_response(f"读取监控数据失败：{exc}")

        review: dict[str, Any] = {}
        auto = app.auto_review_service
        if auto is not None:
            try:
                review = await auto.stats()
                review["enabled"] = bool(app.capabilities.get("review.auto", False))
            except Exception as exc:  # 监控页不应因单个区块失败而整体报错
                review = {"error": str(exc)}

        graph: dict[str, Any] = {}
        graph_service = app.graph_service
        if graph_service is not None:
            try:
                graph = await graph_service.stats()
            except Exception as exc:
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
# 提示词定制
# --------------------------------------------------------------------------- #


def _prompts(app: Any) -> Handler:
    async def handler() -> Any:
        try:
            items = app.prompt_catalog()
        except Exception as exc:
            return error_response(f"读取提示词失败：{exc}")

        groups: list[str] = []
        for item in items:
            group = str(item.get("group") or "")
            if group and group not in groups:
                groups.append(group)
        return _ok({"items": items, "groups": groups})

    return handler


def _prompt_save(app: Any) -> Handler:
    async def handler() -> Any:
        payload = await request.json(default={}) or {}
        key = str(payload.get("key") or "").strip()
        if not key:
            return error_response("缺少参数 key")
        if payload.get("value") is None:
            return error_response("缺少参数 value")

        try:
            result = await app.set_prompt(key, str(payload["value"]))
        except Exception as exc:
            return error_response(f"保存失败：{exc}")
        if not result.get("ok"):
            return error_response(str(result.get("message") or "保存失败"))
        return _ok(result)

    return handler


def _prompt_reset(app: Any) -> Handler:
    async def handler() -> Any:
        payload = await request.json(default={}) or {}
        key = str(payload.get("key") or "").strip()
        if not key:
            return error_response("缺少参数 key")

        try:
            result = await app.reset_prompt(key)
        except Exception as exc:
            return error_response(f"重置失败：{exc}")
        if not result.get("ok"):
            return error_response(str(result.get("message") or "重置失败"))
        return _ok(result)

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
        except Exception as exc:
            return error_response(f"重建索引失败：{exc}")
        # 带上「为什么没建向量」：面板直接把 note 展示给运维，省去猜配置的时间。
        return _ok({"stats": stats, "note": stats.get("note", "")})

    return handler


# --------------------------------------------------------------------------- #
# 批量审批 / 身份诊断 / 作用域迁移
# --------------------------------------------------------------------------- #


def _review_batch(app: Any) -> Handler:
    async def handler() -> Any:
        payload = await request.json(default={}) or {}
        try:
            result = await app.panel_review_batch(payload)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"批量处理失败：{exc}")
        if not result.get("ok"):
            return error_response(str(result.get("message") or "批量处理失败"))
        return _ok(result)

    return handler


def _identities(app: Any) -> Handler:
    async def handler() -> Any:
        try:
            return _ok(await app.panel_identity_report())
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取身份观测失败：{exc}")

    return handler


def _identities_clear(app: Any) -> Handler:
    async def handler() -> Any:
        try:
            result = await app.panel_identity_clear()
        except Exception as exc:  # noqa: BLE001
            return error_response(f"清空失败：{exc}")
        if not result.get("ok"):
            return error_response(str(result.get("message") or "清空失败"))
        return _ok(result)

    return handler


def _scopes(app: Any) -> Handler:
    async def handler() -> Any:
        try:
            return _ok(await app.panel_scope_report())
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取作用域分布失败：{exc}")

    return handler


def _scopes_migrate(app: Any) -> Handler:
    async def handler() -> Any:
        payload = await request.json(default={}) or {}
        if not str(payload.get("to") or "").strip():
            return error_response("缺少参数 to（user / global / archive / user_else_archive）")
        try:
            result = await app.panel_scope_migrate(payload)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"迁移失败：{exc}")
        if not result.get("ok"):
            return error_response(str(result.get("message") or "迁移失败"))
        return _ok(result)

    return handler


# --------------------------------------------------------------------------- #
# 备份导出（配置 + 数据库 + 各类数据 → zip）
# --------------------------------------------------------------------------- #


def _backup_export(app: Any) -> Handler:
    async def handler() -> Any:
        # bridge.download 走 query 参数；面板按钮与自动化脚本可能用 POST body，两者都收。
        # 读 body 放在 try 里：部分框架版本对无 body 的 GET 解析会抛错，此时退回 query。
        options: dict[str, Any] = {}
        try:
            payload = await request.json(default=None)
        except Exception:  # noqa: BLE001
            payload = None
        if isinstance(payload, dict):
            options.update(payload)
        options.setdefault("include_config", _bool_param("include_config", True))
        options.setdefault("include_database", _bool_param("include_database", True))
        options.setdefault("include_data", _bool_param("include_data", True))
        options.setdefault("notes", _str_param("notes"))
        try:
            result = await app.panel_backup_build(options)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"生成备份包失败：{exc}")

        path = Path(str(result.get("path") or ""))
        if file_response is not None and path.is_file():
            # 首选官方下载通道：浏览器直接落盘，不经过 JSON 序列化。
            return file_response(
                str(path),
                filename=str(result.get("filename") or path.name),
                content_type="application/zip",
            )

        # 回退：把包内联成 base64，由前端拼 Blob 下载；过大时只给出落盘路径。
        try:
            raw = path.read_bytes()
        except OSError as exc:
            return error_response(f"备份包读取失败：{exc}")
        if len(raw) > _INLINE_BACKUP_LIMIT:
            return error_response(
                f"备份包 {len(raw) / 1048576:.1f}MB 超过内联上限，"
                f"请直接从服务器取用：{path}"
            )
        return _ok(
            {
                **result,
                "inline": True,
                "encoding": "base64",
                "content": base64.b64encode(raw).decode("ascii"),
            }
        )

    return handler


def _backup_import(app: Any) -> Handler:
    """从备份包恢复。

    两种投递方式都支持（``mode`` = ``merge`` / ``replace``，``dry_run`` 为预览）：

    1. **multipart**：字段名固定 ``file``（与 Pages 文档一致），``mode`` / ``dry_run`` 走查询参数；
    2. **JSON**：``{"mode": "...", "dry_run": false, "content_b64": "..."}``——
       面板走这条，因为 ``bridge.upload`` 不能带额外参数。

    两种方式共用同一段恢复逻辑，避免两条路径行为漂移。
    """

    async def handler() -> Any:
        raw: bytes | None = None
        mode = _str_param("mode", "merge")
        dry_run = _bool_param("dry_run", False)

        try:
            files = await request.files()
        except Exception:  # noqa: BLE001  非 multipart 请求
            files = None
        upload = files.get("file") if isinstance(files, dict) else None
        if upload is not None:
            try:
                raw = await upload.read()
            except Exception as exc:  # noqa: BLE001
                return error_response(f"读取上传内容失败：{exc}")
        else:
            try:
                payload = await request.json(default={}) or {}
            except Exception:  # noqa: BLE001
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            mode = str(payload.get("mode") or mode)
            if "dry_run" in payload:
                dry_run = bool(payload.get("dry_run"))
            encoded = payload.get("content_b64")
            if isinstance(encoded, str) and encoded.strip():
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error):
                    return error_response("content_b64 不是合法的 base64")
        if not raw:
            return error_response("缺少备份包内容（multipart 字段 file 或 JSON 字段 content_b64）")

        try:
            result = await app.panel_backup_import(raw, mode=mode, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"导入备份包失败：{exc}")
        if not result.get("ok"):
            return error_response(str(result.get("message") or "导入失败"))
        return _ok(result)

    return handler


def _backup_replace_database(app: Any) -> Handler:
    """整库恢复：用包内快照替换当前数据库（未指定路径时取最近一次导入落盘的快照）。"""

    async def handler() -> Any:
        payload = await request.json(default={}) or {}
        path = str(payload.get("path") or "").strip() if isinstance(payload, dict) else ""
        try:
            result = await app.panel_backup_replace_database(path)
        except Exception as exc:  # noqa: BLE001
            return error_response(f"整库恢复失败：{exc}")
        if not result.get("ok"):
            return error_response(str(result.get("message") or "整库恢复失败"))
        return _ok(result)

    return handler


def _backup_list(app: Any) -> Handler:
    async def handler() -> Any:
        try:
            return _ok(await app.panel_backup_list())
        except Exception as exc:  # noqa: BLE001
            return error_response(f"读取备份列表失败：{exc}")

    return handler
