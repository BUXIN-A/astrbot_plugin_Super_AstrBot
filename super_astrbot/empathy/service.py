"""共情服务：三阶段编排 + 事件留痕 + 请求注入。

与主链路的边界：
- 只在 ``on_llm_request`` 时读当前这条用户消息（不读历史），避免把旧情绪重复注入；
- 注入走临时内容块（``prefer="auto"``，宿主不支持时回退系统提示词），
  与记忆注入完全同一条通道，失败只降级、不打断对话；
- 事件落库是「留痕」而非「素材」：面板用它做三阶段可视化，不参与召回打分。
"""

from __future__ import annotations

import time
from typing import Any, Callable

from ..harness.protocols import EventView
from ..support import truncate
from . import cogemp
from .config import EmpathyConfig

_INJECT_METHOD_AUTO = "auto"


class EmpathyService:
    """CogEmp 三阶段的进程内编排。"""

    def __init__(
        self,
        *,
        config: EmpathyConfig,
        db: Any | None = None,
        injector: Any | None = None,
        clock: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._config = config
        self._db = db
        self._injector = injector
        self._clock = clock or time.time
        self._logger = logger

    # ------------------------------------------------------------------ #
    # 开关
    # ------------------------------------------------------------------ #

    def enabled(self) -> bool:
        return bool(self._config.enabled)

    # ------------------------------------------------------------------ #
    # 分析
    # ------------------------------------------------------------------ #

    async def observe(self, view: EventView) -> cogemp.EmpathyPlan | None:
        """对一条消息跑三阶段并按需留痕（不注入）。"""
        if not self.enabled():
            return None
        text = str(getattr(view, "text", "") or "")
        if not text.strip():
            return None
        plan = cogemp.plan(
            text,
            temperature=self._config.temperature,
            min_intensity=self._config.min_intensity,
            stages=self._config.stages(),
        )
        if plan is None:
            return None
        await self._record(view, plan)
        return plan

    async def inject(self, view: EventView, request: Any) -> dict[str, Any]:
        """分析 + 注入；返回结构化结果供日志与面板诊断。"""
        if not self.enabled():
            return {"applied": False, "reason": "共情管线未启用"}
        plan = await self.observe(view)
        if plan is None:
            return {"applied": False, "reason": "未识别到情绪信号"}
        if not plan.applied:
            return {
                "applied": False,
                "reason": plan.skipped_reason or "指引为空",
                "emotion": plan.hit.emotion,
                "intensity": round(plan.hit.intensity, 4),
            }
        if self._injector is None:
            return {"applied": False, "reason": "注入器不可用", "emotion": plan.hit.emotion}

        body = truncate(plan.guidance, self._config.max_injected_chars)
        try:
            result = self._injector.inject(request, [body], prefer=_INJECT_METHOD_AUTO)
        except Exception as exc:  # noqa: BLE001  注入失败不影响对话
            self._warn("共情注入失败：%s", exc)
            return {"applied": False, "reason": f"注入异常：{exc}", "emotion": plan.hit.emotion}
        if getattr(result, "applied", False):
            self._debug(
                "共情指引已注入（%s %.2f，%s 字）",
                plan.hit.emotion,
                plan.hit.intensity,
                getattr(result, "chars", 0),
            )
        return {
            "applied": bool(getattr(result, "applied", False)),
            "reason": str(getattr(result, "reason", "") or ""),
            "emotion": plan.hit.emotion,
            "intensity": round(plan.hit.intensity, 4),
            "causes": plan.causes,
            "method": str(getattr(result, "method", "") or ""),
        }

    # ------------------------------------------------------------------ #
    # 留痕
    # ------------------------------------------------------------------ #

    async def _record(self, view: EventView, plan: cogemp.EmpathyPlan) -> None:
        if self._db is None:
            return
        try:
            await self._db.execute(
                "INSERT INTO empathy_events(scope_type, scope_id, sender_id, sender_name,"
                " emotion, intensity, cause, stage_mask, guidance, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    _scope_type(getattr(view, "umo", "")),
                    str(getattr(view, "umo", "") or ""),
                    str(getattr(view, "sender_id", "") or ""),
                    str(getattr(view, "sender_name", "") or ""),
                    plan.hit.emotion,
                    float(plan.hit.intensity),
                    "、".join(plan.causes),
                    "+".join(plan.stage_mask),
                    truncate(plan.guidance, 200),
                    self._clock(),
                ),
            )
        except Exception as exc:  # noqa: BLE001  留痕失败不影响对话
            self._debug("共情事件落库失败：%s", exc)

    async def log(
        self, *, limit: int | None = None, umo: str = "", only_applied: bool = False
    ) -> dict[str, Any]:
        """共情事件日志（面板「最近共情事件」表）。"""
        if self._db is None:
            return {"items": [], "total": 0}
        cap = int(limit or self._config.log_limit)
        clauses: list[str] = []
        params: list[Any] = []
        if umo:
            clauses.append("scope_id = ?")
            params.append(str(umo))
        if only_applied:
            clauses.append("guidance != ''")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await self._db.query(
            f"SELECT * FROM empathy_events{where} ORDER BY created_at DESC LIMIT ?",
            (*params, cap),
        )
        total = await self._db.scalar(
            f"SELECT COUNT(*) FROM empathy_events{where}", tuple(params), 0
        )
        return {
            "items": [
                {
                    "id": int(row["id"]),
                    "umo": str(row["scope_id"] or ""),
                    "sender_id": str(row["sender_id"] or ""),
                    "sender_name": str(row["sender_name"] or ""),
                    "emotion": str(row["emotion"] or ""),
                    "intensity": round(float(row["intensity"] or 0.0), 4),
                    "causes": [part for part in str(row["cause"] or "").split("、") if part],
                    "stages": [part for part in str(row["stage_mask"] or "").split("+") if part],
                    "guidance": str(row["guidance"] or ""),
                    "created_at": float(row["created_at"] or 0.0),
                }
                for row in rows
            ],
            "total": int(total or 0),
        }

    async def stats(self, *, hours: int = 24) -> dict[str, Any]:
        """按情绪聚合的统计（监控 / 总览用）。"""
        if self._db is None:
            return {"window_hours": hours, "events": 0, "by_emotion": []}
        since = self._clock() - max(1, int(hours)) * 3600.0
        rows = await self._db.query(
            "SELECT emotion, COUNT(*) AS count, AVG(intensity) AS avg_intensity"
            " FROM empathy_events WHERE created_at >= ? GROUP BY emotion"
            " ORDER BY count DESC",
            (since,),
        )
        payload = [
            {
                "emotion": str(row["emotion"] or ""),
                "count": int(row["count"] or 0),
                "avg_intensity": round(float(row["avg_intensity"] or 0.0), 4),
            }
            for row in rows
        ]
        return {
            "window_hours": hours,
            "events": sum(item["count"] for item in payload),
            "by_emotion": payload,
            "temperature": self._config.temperature,
            "stages": list(self._config.stages()),
        }

    # ------------------------------------------------------------------ #

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


def _scope_type(umo: str) -> str:
    return "session" if umo else ""


__all__ = ["EmpathyService"]
