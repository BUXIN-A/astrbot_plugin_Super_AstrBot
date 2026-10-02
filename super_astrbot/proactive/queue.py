"""主动关怀：约定回访队列（AMBRACE 回访 + Sibyl 前瞻的进程内落地）。

两种来源，同一张队列表（``proactive_queue``）：

- ``callback``：**约定回访**——「答辩完问进展」「三天后提一下这事」这类
  明确约定的跟进，到期由调度器取出投递；
- ``forecast``：**前瞻推理**——按规律推出的关怀时机（例如「连续熬夜 →
  今晚早些问候」），由规则或人工登记，本质仍是队列项。

边界：本模块**只管理队列**（登记 / 到期 / 状态 / 留痕），实际发送复用既有
``ProactiveService`` 的单会话发送能力，避免出现第二条发送链路。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from ..support import truncate

KIND_CALLBACK = "callback"
KIND_FORECAST = "forecast"
KINDS = (KIND_CALLBACK, KIND_FORECAST)

STATUS_PENDING = "pending"
STATUS_SENT = "sent"
STATUS_SKIPPED = "skipped"
STATUS_CANCELED = "canceled"
STATUSES = (STATUS_PENDING, STATUS_SENT, STATUS_SKIPPED, STATUS_CANCELED)

KIND_LABELS = {KIND_CALLBACK: "约定回访", KIND_FORECAST: "前瞻关怀"}
STATUS_LABELS = {
    STATUS_PENDING: "待发",
    STATUS_SENT: "已发送",
    STATUS_SKIPPED: "已跳过",
    STATUS_CANCELED: "已取消",
}


class CallbackQueueService:
    """回访队列的 CRUD 与到期查询。"""

    def __init__(
        self,
        *,
        db: Any | None = None,
        max_pending: int = 200,
        clock: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._db = db
        self._max_pending = int(max_pending)
        self._clock = clock or time.time
        self._logger = logger

    def enabled(self) -> bool:
        return self._db is not None

    # ------------------------------------------------------------------ #
    # 读写
    # ------------------------------------------------------------------ #

    async def add(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if self._db is None:
            return {"ok": False, "message": "持久层未就绪"}
        content = str(payload.get("content") or "").strip()
        if not content:
            return {"ok": False, "message": "回访内容不能为空"}
        kind = str(payload.get("kind") or KIND_CALLBACK).strip()
        if kind not in KINDS:
            kind = KIND_CALLBACK
        due_at = _float(payload.get("due_at"), 0.0)
        if due_at <= 0:
            after_hours = _float(payload.get("due_in_hours"), 24.0)
            due_at = self._clock() + max(0.05, after_hours) * 3600.0
        pending = await self._db.scalar(
            "SELECT COUNT(*) FROM proactive_queue WHERE status='pending'", (), 0
        )
        if int(pending or 0) >= self._max_pending:
            return {"ok": False, "message": f"待发队列已满（{self._max_pending} 条），先清理再登记"}
        now = self._clock()
        cursor = await self._db.execute(
            "INSERT INTO proactive_queue(umo, target, due_at, content, kind, status, attempts,"
            " last_error, created_at, updated_at) VALUES (?,?,?,?,?,?,0,'',?,?)",
            (
                str(payload.get("umo") or "").strip(),
                str(payload.get("target") or "").strip(),
                due_at,
                truncate(content, 500),
                kind,
                STATUS_PENDING,
                now,
                now,
            ),
        )
        return {
            "ok": True,
            "id": int(getattr(cursor, "lastrowid", 0) or 0),
            "due_at": due_at,
            "message": "已登记回访",
        }

    async def update(self, item_id: int, patch: Mapping[str, Any]) -> dict[str, Any]:
        if self._db is None:
            return {"ok": False, "message": "持久层未就绪"}
        row = await self._db.query_one(
            "SELECT * FROM proactive_queue WHERE id=?", (int(item_id),)
        )
        if row is None:
            return {"ok": False, "message": "队列项不存在"}
        status = str(patch.get("status") or row["status"] or STATUS_PENDING)
        if status not in STATUSES:
            status = str(row["status"] or STATUS_PENDING)
        content = str(patch.get("content") or row["content"] or "").strip()
        if not content:
            return {"ok": False, "message": "回访内容不能为空"}
        await self._db.execute(
            "UPDATE proactive_queue SET content=?, due_at=?, status=?, target=?, umo=?, updated_at=?"
            " WHERE id=?",
            (
                truncate(content, 500),
                _float(patch.get("due_at"), float(row["due_at"] or 0.0)),
                status,
                str(patch.get("target") or row["target"] or ""),
                str(patch.get("umo") or row["umo"] or ""),
                self._clock(),
                int(item_id),
            ),
        )
        return {"ok": True, "id": int(item_id), "message": "已更新"}

    async def delete(self, item_id: int) -> dict[str, Any]:
        if self._db is None:
            return {"ok": False, "message": "持久层未就绪"}
        cursor = await self._db.execute(
            "DELETE FROM proactive_queue WHERE id=?", (int(item_id),)
        )
        deleted = int(getattr(cursor, "rowcount", 0) or 0)
        if not deleted:
            return {"ok": False, "message": "队列项不存在"}
        return {"ok": True, "id": int(item_id), "message": "已删除"}

    async def list_items(
        self, *, status: str = "", limit: int = 50, umo: str = ""
    ) -> dict[str, Any]:
        if self._db is None:
            return {"items": [], "total": 0, "kinds": list(KINDS), "statuses": list(STATUSES)}
        clauses: list[str] = []
        params: list[Any] = []
        if status and status in STATUSES:
            clauses.append("status=?")
            params.append(status)
        if umo:
            clauses.append("umo=?")
            params.append(umo)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._db.query(
            f"SELECT * FROM proactive_queue{where}"
            f" ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, due_at ASC LIMIT ?",
            (*params, max(1, min(200, int(limit)))),
        )
        total = await self._db.scalar(
            f"SELECT COUNT(*) FROM proactive_queue{where}", tuple(params), 0
        )
        return {
            "items": [self._format(row) for row in rows],
            "total": int(total or 0),
            "kinds": list(KINDS),
            "kind_labels": dict(KIND_LABELS),
            "statuses": list(STATUSES),
            "status_labels": dict(STATUS_LABELS),
        }

    async def due(self, *, limit: int = 10, now: float | None = None) -> list[dict[str, Any]]:
        """取出到期未发送的队列项（供调度器投递）。"""
        if self._db is None:
            return []
        moment = float(now if now is not None else self._clock())
        rows = await self._db.query(
            "SELECT * FROM proactive_queue WHERE status='pending' AND due_at <= ?"
            " ORDER BY due_at ASC LIMIT ?",
            (moment, max(1, int(limit))),
        )
        return [self._format(row) for row in rows]

    async def mark(
        self,
        item_id: int,
        status: str,
        *,
        error: str = "",
        bump_attempt: bool = False,
    ) -> None:
        if self._db is None:
            return
        if status not in STATUSES:
            status = STATUS_PENDING
        try:
            await self._db.execute(
                "UPDATE proactive_queue SET status=?, last_error=?,"
                " attempts = attempts + ?, updated_at=? WHERE id=?",
                (
                    status,
                    truncate(error or "", 200),
                    1 if bump_attempt else 0,
                    self._clock(),
                    int(item_id),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._debug("回访队列状态更新失败：%s", exc)

    # ------------------------------------------------------------------ #

    async def stats(self) -> dict[str, Any]:
        if self._db is None:
            return {"pending": 0, "total": 0, "due": 0}
        total = await self._db.scalar("SELECT COUNT(*) FROM proactive_queue", (), 0)
        pending = await self._db.scalar(
            "SELECT COUNT(*) FROM proactive_queue WHERE status='pending'", (), 0
        )
        due = await self._db.scalar(
            "SELECT COUNT(*) FROM proactive_queue WHERE status='pending' AND due_at <= ?",
            (self._clock(),),
            0,
        )
        sent = await self._db.scalar(
            "SELECT COUNT(*) FROM proactive_queue WHERE status='sent'", (), 0
        )
        return {
            "total": int(total or 0),
            "pending": int(pending or 0),
            "due": int(due or 0),
            "sent": int(sent or 0),
        }

    async def recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """近期状态变更（面板「主动关怀 · 日志」用）。"""
        if self._db is None:
            return []
        rows = await self._db.query(
            "SELECT * FROM proactive_queue WHERE status != 'pending'"
            " ORDER BY updated_at DESC LIMIT ?",
            (max(1, min(100, int(limit))),),
        )
        return [self._format(row) for row in rows]

    def _format(self, row: Any) -> dict[str, Any]:
        kind = str(row["kind"] or KIND_CALLBACK)
        status = str(row["status"] or STATUS_PENDING)
        return {
            "id": int(row["id"]),
            "umo": str(row["umo"] or ""),
            "target": str(row["target"] or ""),
            "due_at": float(row["due_at"] or 0.0),
            "content": str(row["content"] or ""),
            "kind": kind,
            "kind_label": KIND_LABELS.get(kind, kind),
            "status": status,
            "status_label": STATUS_LABELS.get(status, status),
            "attempts": int(row["attempts"] or 0),
            "last_error": str(row["last_error"] or ""),
            "created_at": float(row["created_at"] or 0.0),
            "updated_at": float(row["updated_at"] or 0.0),
        }

    def _debug(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            try:
                self._logger.debug(message, *args)
            except Exception:  # noqa: BLE001
                pass


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "KIND_CALLBACK",
    "KIND_FORECAST",
    "KIND_LABELS",
    "STATUS_CANCELED",
    "STATUS_LABELS",
    "STATUS_PENDING",
    "STATUS_SENT",
    "STATUS_SKIPPED",
    "STATUSES",
    "CallbackQueueService",
]
