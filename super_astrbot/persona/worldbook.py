"""世界书 / Lorebook（AMBRACE 功能点进程内重写）。

来源与改写：AMBRACE 的 Lorebook 提供「触发词 → 注入事实卡」机制。本项目按
实施总纲 §0.5 只移植该功能点：表结构落在本插件自己的 SQLite（``worldbook_entries``），
命中与注入全部在进程内完成，不引入其 FastAPI 服务与数据库。

命中规则（刻意保守，避免上下文被撑爆）：

1. 条目 **启用中**，且触发词在**当前这条用户消息**里出现（大小写不敏感的子串匹配）；
2. 作用域过滤：``global``（默认）对所有会话生效；``user`` 仅对指定发送者生效；
   ``session`` 仅对指定会话生效；
3. 触发词为空的条目视为「手动条目」，只存档不自动注入；
4. 按优先级从高到低取，总字符受 ``max_injected_chars`` 约束，超出即截断。
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Mapping

from ..harness.protocols import EventView
from ..support import truncate

SCOPE_GLOBAL = "global"
SCOPE_USER = "user"
SCOPE_SESSION = "session"
_SCOPES = (SCOPE_GLOBAL, SCOPE_USER, SCOPE_SESSION)


def _loads_triggers(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        data = [part.strip() for part in text.replace("，", ",").split(",")]
    if not isinstance(data, list):
        return []
    return [str(item).strip() for item in data if str(item).strip()]


def _dumps_triggers(value: Any) -> str:
    if isinstance(value, str):
        items = [part.strip() for part in value.replace("，", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        items = [str(item).strip() for item in value]
    else:
        items = []
    return json.dumps([item for item in items if item], ensure_ascii=False)


class WorldbookService:
    """世界书条目的 CRUD 与命中注入。"""

    def __init__(
        self,
        *,
        db: Any | None = None,
        injector: Any | None = None,
        max_entries: int = 200,
        max_injected_chars: int = 600,
        clock: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._db = db
        self._injector = injector
        self._max_entries = int(max_entries)
        self._max_injected_chars = int(max_injected_chars)
        self._clock = clock or time.time
        self._logger = logger

    def enabled(self) -> bool:
        return self._db is not None

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    async def list_entries(self, *, limit: int | None = None, umo: str = "") -> dict[str, Any]:
        if self._db is None:
            return {"items": [], "total": 0}
        cap = max(1, min(1000, int(limit or self._max_entries)))
        if umo:
            rows = await self._db.query(
                "SELECT * FROM worldbook_entries"
                " WHERE scope_type='global' OR (scope_type='session' AND scope_id=?)"
                " ORDER BY priority DESC, updated_at DESC LIMIT ?",
                (umo, cap),
            )
        else:
            rows = await self._db.query(
                "SELECT * FROM worldbook_entries ORDER BY priority DESC, updated_at DESC LIMIT ?",
                (cap,),
            )
        total = await self._db.scalar("SELECT COUNT(*) FROM worldbook_entries", (), 0)
        return {"items": [self._format(row) for row in rows], "total": int(total or 0)}

    async def add(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self._db is None:
            return {"ok": False, "message": "持久层未就绪"}
        content = str(payload.get("content") or "").strip()
        if not content:
            return {"ok": False, "message": "注入内容不能为空"}
        triggers = _dumps_triggers(payload.get("triggers"))
        scope_type, scope_id = self._normalize_scope(payload)
        priority = _int(payload.get("priority"), 5, low=1, high=100)
        now = self._clock()
        cursor = await self._db.execute(
            "INSERT INTO worldbook_entries(triggers, content, priority, scope_type, scope_id,"
            " enabled, hits, created_at, updated_at) VALUES (?,?,?,?,?,?,0,?,?)",
            (
                triggers,
                truncate(content, 2000),
                priority,
                scope_type,
                scope_id,
                1 if payload.get("enabled", True) is not False else 0,
                now,
                now,
            ),
        )
        return {
            "ok": True,
            "id": int(getattr(cursor, "lastrowid", 0) or 0),
            "message": "条目已创建",
        }

    async def update(self, entry_id: int, patch: Mapping[str, Any]) -> dict[str, Any]:
        if self._db is None:
            return {"ok": False, "message": "持久层未就绪"}
        row = await self._db.query_one(
            "SELECT * FROM worldbook_entries WHERE id=?", (int(entry_id),)
        )
        if row is None:
            return {"ok": False, "message": "条目不存在"}

        content = patch.get("content")
        content = str(content).strip() if content is not None else str(row["content"] or "")
        if not content:
            return {"ok": False, "message": "注入内容不能为空"}
        triggers = (
            _dumps_triggers(patch.get("triggers"))
            if "triggers" in patch
            else str(row["triggers"] or "[]")
        )
        scope_type = str(patch.get("scope_type") or row["scope_type"] or SCOPE_GLOBAL)
        scope_id = str(patch.get("scope_id") if "scope_id" in patch else row["scope_id"] or "")
        if scope_type not in _SCOPES:
            scope_type = SCOPE_GLOBAL
        if scope_type == SCOPE_GLOBAL:
            scope_id = ""
        priority = _int(patch.get("priority"), int(row["priority"] or 5), low=1, high=100)
        enabled = patch.get("enabled")
        enabled_flag = int(row["enabled"] or 0) if enabled is None else (1 if enabled else 0)
        await self._db.execute(
            "UPDATE worldbook_entries SET triggers=?, content=?, priority=?, scope_type=?,"
            " scope_id=?, enabled=?, updated_at=? WHERE id=?",
            (
                triggers,
                truncate(content, 2000),
                priority,
                scope_type,
                scope_id,
                enabled_flag,
                self._clock(),
                int(entry_id),
            ),
        )
        return {"ok": True, "id": int(entry_id), "message": "条目已保存"}

    async def delete(self, entry_id: int) -> dict[str, Any]:
        if self._db is None:
            return {"ok": False, "message": "持久层未就绪"}
        cursor = await self._db.execute(
            "DELETE FROM worldbook_entries WHERE id=?", (int(entry_id),)
        )
        deleted = int(getattr(cursor, "rowcount", 0) or 0)
        if not deleted:
            return {"ok": False, "message": "条目不存在"}
        return {"ok": True, "id": int(entry_id), "message": "条目已删除"}

    # ------------------------------------------------------------------ #
    # 命中与注入
    # ------------------------------------------------------------------ #

    async def match(self, view: EventView) -> list[dict[str, Any]]:
        """返回命中的条目（按优先级降序），并做字符预算裁剪。"""
        if self._db is None:
            return []
        text = str(getattr(view, "text", "") or "")
        if not text.strip():
            return []
        rows = await self._db.query(
            "SELECT * FROM worldbook_entries WHERE enabled=1"
            " ORDER BY priority DESC, updated_at DESC LIMIT ?",
            (self._max_entries,),
        )
        lowered = text.lower()
        sender_id = str(getattr(view, "sender_id", "") or "")
        umo = str(getattr(view, "umo", "") or "")
        hits: list[dict[str, Any]] = []
        budget = self._max_injected_chars
        for row in rows:
            entry = self._format(row)
            triggers = entry["triggers"]
            if not triggers:
                continue
            if not any(trigger.lower() in lowered for trigger in triggers):
                continue
            if not self._scope_allows(entry, sender_id=sender_id, umo=umo):
                continue
            content = str(entry["content"])
            if len(content) > budget:
                if budget < 40:  # 剩余预算太小，后面的条目一律不注入
                    break
                content = truncate(content, budget)
            budget -= len(content)
            entry["content"] = content
            hits.append(entry)
            if budget <= 0:
                break
        return hits

    @staticmethod
    def _scope_allows(entry: Mapping[str, Any], *, sender_id: str, umo: str) -> bool:
        scope_type = str(entry.get("scope_type") or SCOPE_GLOBAL)
        scope_id = str(entry.get("scope_id") or "")
        if scope_type == SCOPE_GLOBAL:
            return True
        if scope_type == SCOPE_USER:
            return bool(sender_id) and scope_id == sender_id
        if scope_type == SCOPE_SESSION:
            return bool(umo) and scope_id == umo
        return False

    async def inject(self, view: EventView, request: Any) -> dict[str, Any]:
        """命中即注入；无命中返回 ``applied=False``（正常路径，不是错误）。"""
        if self._injector is None:
            return {"applied": False, "reason": "注入器不可用"}
        hits = await self.match(view)
        if not hits:
            return {"applied": False, "reason": "无条目命中"}
        body = "【世界书】\n" + "\n".join(f"- {item['content']}" for item in hits)
        try:
            result = self._injector.inject(request, [body], prefer="auto")
        except Exception as exc:  # noqa: BLE001
            self._warn("世界书注入失败：%s", exc)
            return {"applied": False, "reason": f"注入异常：{exc}"}
        applied = bool(getattr(result, "applied", False))
        if applied:
            ids = [int(item["id"]) for item in hits]
            try:
                placeholders = ",".join("?" for _ in ids)
                await self._db.execute(
                    f"UPDATE worldbook_entries SET hits = hits + 1 WHERE id IN ({placeholders})",
                    tuple(ids),
                )
            except Exception as exc:  # noqa: BLE001  命中计数失败不影响注入结果
                self._debug("世界书命中计数失败：%s", exc)
        return {
            "applied": applied,
            "reason": str(getattr(result, "reason", "") or ""),
            "ids": [int(item["id"]) for item in hits],
            "titles": [item["content"][:24] for item in hits],
            "chars": int(getattr(result, "chars", 0) or 0),
        }

    # ------------------------------------------------------------------ #

    async def stats(self) -> dict[str, Any]:
        if self._db is None:
            return {"entries": 0, "enabled": 0, "hits": 0}
        entries = await self._db.scalar("SELECT COUNT(*) FROM worldbook_entries", (), 0)
        enabled = await self._db.scalar(
            "SELECT COUNT(*) FROM worldbook_entries WHERE enabled=1", (), 0
        )
        hits = await self._db.scalar(
            "SELECT COALESCE(SUM(hits), 0) FROM worldbook_entries", (), 0
        )
        return {
            "entries": int(entries or 0),
            "enabled": int(enabled or 0),
            "hits": int(hits or 0),
        }

    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_scope(payload: Mapping[str, Any]) -> tuple[str, str]:
        scope_type = str(payload.get("scope_type") or SCOPE_GLOBAL).strip() or SCOPE_GLOBAL
        if scope_type not in _SCOPES:
            scope_type = SCOPE_GLOBAL
        scope_id = str(payload.get("scope_id") or "").strip()
        if scope_type == SCOPE_GLOBAL:
            scope_id = ""
        return scope_type, scope_id

    def _format(self, row: Any) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "triggers": _loads_triggers(row["triggers"]),
            "content": str(row["content"] or ""),
            "priority": int(row["priority"] or 5),
            "scope_type": str(row["scope_type"] or SCOPE_GLOBAL),
            "scope_id": str(row["scope_id"] or ""),
            "scope": (
                SCOPE_GLOBAL
                if str(row["scope_type"] or SCOPE_GLOBAL) == SCOPE_GLOBAL
                else f"{row['scope_type']}:{row['scope_id']}"
            ),
            "enabled": bool(row["enabled"]),
            "hits": int(row["hits"] or 0),
            "created_at": float(row["created_at"] or 0.0),
            "updated_at": float(row["updated_at"] or 0.0),
        }

    def _debug(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            try:
                self._logger.debug(message, *args)
            except Exception:  # noqa: BLE001
                pass

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            try:
                self._logger.warning(message, *args)
            except Exception:  # noqa: BLE001
                pass


def _int(value: Any, default: int, *, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


__all__ = ["SCOPE_GLOBAL", "SCOPE_SESSION", "SCOPE_USER", "WorldbookService"]
