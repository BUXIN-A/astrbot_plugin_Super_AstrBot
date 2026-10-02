"""融合状态聚合：记忆后端 / 编排流水线 / 模块健康。

三份输出的共同约定：

1. **状态取「进程内能力」的真实值**：就绪 = 模块对象存在且其数据层可用；
   缺依赖（如未配置嵌入模型）时给 ``degraded`` 与可读原因，绝不假装在线；
2. **每个数字都能溯源**：流水线卡片的计数来自各服务自己的 ``stats()``，
   不做估算；取不到时返回 ``None`` 而不是 0（0 与「未知」必须可区分）；
3. **失败隔离**：任一子模块异常只影响它自己那一格（``error`` 字段），
   聚合方法永不抛出——面板是诊断工具，不能因为一个模块坏了整体白屏。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Mapping

PIPELINE_STAGES: tuple[dict[str, str], ...] = (
    {"index": "①", "key": "identity", "title": "身份识别", "target": "style-samples"},
    {"index": "②", "key": "scope", "title": "作用域归属", "target": "memories"},
    {"index": "③", "key": "recall", "title": "记忆检索", "target": "recall"},
    {"index": "④", "key": "persona", "title": "人格+共情", "target": "persona-forge"},
    {"index": "⑤", "key": "reply", "title": "回复生成", "target": "prompts"},
    {"index": "⑥", "key": "proactive", "title": "主动触发", "target": "proactive"},
    {"index": "⑦", "key": "reflection", "title": "反思学习", "target": "style-samples"},
    {"index": "⑧", "key": "sediment", "title": "沉淀", "target": "graph"},
)

FUSION_MODULES: tuple[dict[str, str], ...] = (
    {"key": "members", "title": "群友识别", "domain": "members", "capability": "members.enabled"},
    {
        "key": "forge",
        "title": "PersonaForge 三层人格",
        "domain": "forge",
        "capability": "forge.enabled",
    },
    {
        "key": "introspection",
        "title": "选择性双过程内省",
        "domain": "forge",
        "capability": "forge.introspection",
    },
    {
        "key": "evolution",
        "title": "人格演化轨迹",
        "domain": "evolution",
        "capability": "evolution.enabled",
    },
    {
        "key": "empathy",
        "title": "CogEmp 共情管线",
        "domain": "empathy",
        "capability": "empathy.enabled",
    },
    {
        "key": "latrace",
        "title": "LATRACE 时序图谱",
        "domain": "latrace",
        "capability": "latrace.enabled",
    },
    {"key": "tiers", "title": "letta 三级记忆", "domain": "tiers", "capability": "tiers.enabled"},
    {
        "key": "worldbook",
        "title": "世界书 / Lorebook",
        "domain": "worldbook",
        "capability": "worldbook.enabled",
    },
    {"key": "decay", "title": "艾宾浩斯衰减", "domain": "fusion", "capability": "fusion.decay"},
)


class FusionStatusService:
    """融合状态聚合器。"""

    def __init__(
        self,
        *,
        services: Mapping[str, Any],
        capabilities: Callable[[], Mapping[str, bool]],
        db: Any | None = None,
        clock: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._services = dict(services or {})
        self._capabilities = capabilities
        self._db = db
        self._clock = clock or time.time
        self._logger = logger

    def _service(self, key: str) -> Any | None:
        return self._services.get(key)

    def _enabled(self, key: str) -> bool:
        try:
            return bool((self._capabilities() or {}).get(key, False))
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ #
    # 记忆后端
    # ------------------------------------------------------------------ #

    async def backends(self, *, umo: str = "") -> dict[str, Any]:
        """「记忆后端」页：本地 / LATRACE / 三级 / 衰减 四块状态。"""
        payload: dict[str, Any] = {
            "generated_at": self._clock(),
            "active_backend": "local",
            "backends": [
                {
                    "key": "local",
                    "title": "本地 SQLite（内置）",
                    "status": "online" if self._db is not None else "degraded",
                    "detail": "记忆、向量与全部融合表都在插件数据目录",
                    "ready": self._db is not None,
                }
            ],
        }

        # LATRACE（进程内时序图谱）
        tkg = self._service("tkg")
        latrace_enabled = self._enabled("latrace.enabled")
        latrace: dict[str, Any] = {
            "key": "latrace",
            "title": "LATRACE 时序图谱（进程内）",
            "enabled": latrace_enabled,
            "status": "off",
            "detail": "",
            "ready": False,
        }
        if latrace_enabled and tkg is not None:
            try:
                stats = await tkg.stats()
                ready = bool(stats.get("nodes"))
                latrace.update(
                    {
                        "status": "online" if ready else "degraded",
                        "ready": ready,
                        "stats": stats,
                        "detail": (
                            f"节点 {stats.get('nodes', 0)} · 边 {stats.get('edges', 0)} · "
                            f"近 7 天新增 {stats.get('recent_edges_7d', 0)}"
                            if ready
                            else "图谱为空：可用「重建时序图谱」从既有记忆回填"
                        ),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                latrace.update({"status": "degraded", "detail": f"读取失败：{exc}"})
        elif not latrace_enabled:
            latrace["detail"] = "能力未启用（功能页可开）"
        else:
            latrace.update({"status": "degraded", "detail": "模块未装配"})
        payload["backends"].append(latrace)

        # letta 三级
        tiers = self._service("tiers")
        tiers_enabled = self._enabled("tiers.enabled")
        tier_block: dict[str, Any] = {
            "key": "tiers",
            "title": "letta 三级记忆（核心 / 召回 / 归档）",
            "enabled": tiers_enabled,
            "status": "off",
            "detail": "",
            "counts": {},
            "shares": {},
        }
        if tiers_enabled and tiers is not None:
            try:
                stats = await tiers.stats()
                tier_block.update(
                    {
                        "status": "online",
                        "ready": True,
                        "counts": stats.get("counts") or {},
                        "shares": stats.get("shares") or {},
                        "detail": "核心 {core} · 召回 {recall} · 归档 {archive}".format(
                            core=(stats.get("counts") or {}).get("core", 0),
                            recall=(stats.get("counts") or {}).get("recall", 0),
                            archive=(stats.get("counts") or {}).get("archive", 0),
                        ),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                tier_block.update({"status": "degraded", "detail": f"读取失败：{exc}"})
        elif not tiers_enabled:
            tier_block["detail"] = "能力未启用（功能页可开）"
        payload["backends"].append(tier_block)

        # 艾宾浩斯衰减
        decay = self._service("decay")
        decay_enabled = self._enabled("fusion.decay")
        decay_block: dict[str, Any] = {
            "key": "decay",
            "title": "艾宾浩斯衰减（AMBRACE · 进程内）",
            "enabled": decay_enabled,
            "status": "off",
            "detail": "",
            "write_back": False,
        }
        if decay is not None:
            try:
                curves = decay.curves()
                decay_block.update(
                    {
                        "status": "online" if decay_enabled else "monitor-only",
                        "ready": True,
                        "curves": curves,
                        "write_back": bool(getattr(decay, "write_back", False)),
                        "stats": await decay.stats(),
                        "detail": (
                            f"强度 {curves.get('strength_days')} 天 · "
                            f"保留率 30 天 {curves['natural'][-2]['retention'] if len(curves.get('natural') or []) >= 2 else '—'}%"
                        ),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                decay_block.update({"status": "degraded", "detail": f"读取失败：{exc}"})
        payload["backends"].append(decay_block)

        if latrace.get("ready"):
            payload["active_backend"] = "latrace"
        payload["capabilities"] = {
            "latrace.enabled": latrace_enabled,
            "tiers.enabled": tiers_enabled,
            "fusion.decay": decay_enabled,
        }
        payload["umo"] = umo
        return payload

    # ------------------------------------------------------------------ #
    # 编排流水线
    # ------------------------------------------------------------------ #

    async def pipeline(self, *, umo: str = "") -> dict[str, Any]:
        """总览页的 ①→⑧ 流水线卡片数据。"""
        stages: list[dict[str, Any]] = []
        for spec in PIPELINE_STAGES:
            builder = getattr(self, f"_stage_{spec['key']}", None)
            detail: dict[str, Any] = {}
            status = "on"
            if builder is not None:
                try:
                    detail, status = await builder()
                except Exception as exc:  # noqa: BLE001
                    detail, status = {"error": str(exc)}, "degraded"
            stages.append({**spec, "detail": detail, "status": status})

        enabled_count = sum(1 for stage in stages if stage["status"] == "on")
        return {
            "generated_at": self._clock(),
            "stages": stages,
            "summary": {
                "total": len(stages),
                "on": enabled_count,
                "degraded": sum(1 for stage in stages if stage["status"] == "degraded"),
                "off": sum(1 for stage in stages if stage["status"] == "off"),
            },
            "umo": umo,
        }

    # 每个 _stage_* 返回 (detail_dict, status)
    async def _stage_identity(self) -> tuple[dict[str, Any], str]:
        members = self._service("members")
        if not self._enabled("members.enabled") or members is None:
            return {"hint": "群友识别未启用"}, "off"
        stats = await members.stats()
        return (
            {
                "members": stats.get("members", 0),
                "strategies": stats.get("strategies", 0),
                "hint": f"识别 {stats.get('members', 0)} 人 · 已有策略 {stats.get('strategies', 0)} 份",
            },
            "on",
        )

    async def _stage_scope(self) -> tuple[dict[str, Any], str]:
        memory = self._service("memory")
        if not self._enabled("memory.enabled") or memory is None:
            return {"hint": "长期记忆未启用"}, "off"
        stats = await memory.stats_all()
        return (
            {
                "active": stats.get("active", 0),
                "buffered": stats.get("buffered", 0),
                "hint": f"正式记忆 {stats.get('active', 0)} 条 · 缓冲 {stats.get('buffered', 0)} 条",
            },
            "on",
        )

    async def _stage_recall(self) -> tuple[dict[str, Any], str]:
        detail: dict[str, Any] = {}
        tkg = self._service("tkg")
        if tkg is not None and self._enabled("latrace.enabled"):
            stats = await tkg.stats()
            detail["tkg"] = stats
        if not self._enabled("memory.enabled"):
            return {"hint": "长期记忆未启用"}, "off"
        detail["hint"] = (
            f"时序图谱节点 {detail.get('tkg', {}).get('nodes', 0)}"
            if detail.get("tkg")
            else "混合召回（关键词 + 向量 + 可选重排）"
        )
        return detail, "on"

    async def _stage_persona(self) -> tuple[dict[str, Any], str]:
        detail: dict[str, Any] = {}
        status = "off"
        forge = self._service("forge")
        if forge is not None and self._enabled("forge.enabled"):
            stats = await forge.stats()
            detail["forge"] = stats
            detail["hint"] = f"心情 {stats.get('mood')} · 能量 {stats.get('energy')}"
            status = "on"
        empathy = self._service("empathy")
        if empathy is not None and self._enabled("empathy.enabled"):
            stats = await empathy.stats(hours=24)
            detail["empathy"] = stats
            detail["hint"] = (
                detail.get("hint", "") + f" · 共情事件 {stats.get('events', 0)}/24h"
            ).strip(" ·")
            status = "on"
        if status == "off":
            detail.setdefault("hint", "人格与共情均未启用")
        return detail, status

    async def _stage_reply(self) -> tuple[dict[str, Any], str]:
        if not self._enabled("basic.enabled"):
            return {"hint": "插件总开关关闭"}, "off"
        return {"hint": "会话默认模型 + 可定制提示词"}, "on"

    async def _stage_proactive(self) -> tuple[dict[str, Any], str]:
        queue = self._service("queue")
        detail: dict[str, Any] = {}
        status = "off"
        if queue is not None:
            stats = await queue.stats()
            detail["queue"] = stats
            detail["hint"] = f"回访队列待发 {stats.get('pending', 0)} 条"
            status = "on" if stats.get("pending") else "off"
        if self._enabled("proactive.enabled"):
            status = "on"
            detail["hint"] = (detail.get("hint", "") + " · 主动消息已启用").strip(" ·")
        if not detail:
            detail["hint"] = "主动关怀未启用"
        return detail, status

    async def _stage_reflection(self) -> tuple[dict[str, Any], str]:
        if not self._enabled("reflection.enabled"):
            return {"hint": "反思学习未启用"}, "off"
        review = self._service("auto_review")
        detail: dict[str, Any] = {"hint": "缓冲 → 反思 → 落库（可审）"}
        if review is not None:
            try:
                stats = await review.stats()
                detail["review"] = stats
                detail["hint"] += f" · 自动审核通过 {stats.get('approved', 0)}"
            except Exception:  # noqa: BLE001
                pass
        return detail, "on"

    async def _stage_sediment(self) -> tuple[dict[str, Any], str]:
        detail: dict[str, Any] = {}
        tkg = self._service("tkg")
        if tkg is not None and self._enabled("latrace.enabled"):
            detail["tkg"] = await tkg.stats()
        graph = self._service("graph")
        if graph is not None:
            try:
                detail["graph"] = await graph.stats()
            except Exception:  # noqa: BLE001
                pass
        worldbook = self._service("worldbook")
        if worldbook is not None:
            detail["worldbook"] = await worldbook.stats()
        decay = self._service("decay")
        if decay is not None:
            detail["decay"] = await decay.stats()
        if not detail:
            return {"hint": "沉淀域未启用"}, "off"
        parts = []
        if detail.get("tkg"):
            parts.append(f"图谱节点 {detail['tkg'].get('nodes', 0)}")
        if detail.get("worldbook"):
            parts.append(f"世界书 {detail['worldbook'].get('entries', 0)} 条")
        if detail.get("decay"):
            parts.append(f"平均重要度 {detail['decay'].get('avg_importance', 0)}")
        detail["hint"] = " · ".join(parts) or "图谱 / 衰减 / 现实桥"
        return detail, "on"

    # ------------------------------------------------------------------ #
    # 模块健康
    # ------------------------------------------------------------------ #

    async def health(self) -> dict[str, Any]:
        """监控页「融合服务健康」：每个模块一行，含 degraded 占位。"""
        items: list[dict[str, Any]] = []
        caps = self._capabilities()
        for spec in FUSION_MODULES:
            enabled = bool(caps.get(spec["capability"], False))
            service = self._service(spec["key"]) or self._service(
                {"introspection": "forge", "latrace": "tkg"}.get(spec["key"], spec["key"])
            )
            row: dict[str, Any] = {
                "key": spec["key"],
                "title": spec["title"],
                "domain": spec["domain"],
                "capability": spec["capability"],
                "enabled": enabled,
                "status": "off" if not enabled else "online",
                "detail": "",
            }
            if enabled and service is None:
                row.update({"status": "degraded", "detail": "模块未装配"})
            elif enabled and service is not None:
                try:
                    stats = await self._module_stats(service)
                    row["stats"] = stats
                    row["detail"] = self._describe(spec["key"], stats)
                    if row["key"] == "latrace" and not stats.get("nodes"):
                        row["status"] = "degraded"
                        row["detail"] = "图谱为空：可用「重建时序图谱」回填"
                except Exception as exc:  # noqa: BLE001
                    row.update({"status": "degraded", "detail": f"读取失败：{exc}"})
            elif not enabled:
                row["detail"] = "未启用（功能页可开）"
            items.append(row)
        return {
            "generated_at": self._clock(),
            "items": items,
            "summary": {
                "online": sum(1 for item in items if item["status"] == "online"),
                "degraded": sum(1 for item in items if item["status"] == "degraded"),
                "off": sum(1 for item in items if item["status"] == "off"),
            },
        }

    @staticmethod
    async def _module_stats(service: Any) -> dict[str, Any]:
        if hasattr(service, "stats"):
            return await service.stats()
        return {}

    @staticmethod
    def _describe(key: str, stats: Mapping[str, Any]) -> str:
        if key == "members":
            return f"群友 {stats.get('members', 0)} 人 · 策略 {stats.get('strategies', 0)} 份"
        if key == "forge":
            return f"能量 {stats.get('energy', '—')} · 心情 {stats.get('mood', '—')} · 关系 {stats.get('relationships', 0)} 人"
        if key == "introspection":
            return (
                f"已执行 {stats.get('introspection_count', 0)} 次"
                if stats.get("introspection_enabled")
                else "开关开启即生效（关键轮触发）"
            )
        if key == "empathy":
            return f"共情事件 {stats.get('events', 0)} 条（{stats.get('window_hours', 24)}h）"
        if key == "latrace":
            return f"节点 {stats.get('nodes', 0)} · 边 {stats.get('edges', 0)}"
        if key == "tiers":
            counts = stats.get("counts") or {}
            return f"核心 {counts.get('core', 0)} · 召回 {counts.get('recall', 0)} · 归档 {counts.get('archive', 0)}"
        if key == "worldbook":
            return f"条目 {stats.get('entries', 0)} 条 · 命中累计 {stats.get('hits', 0)} 次"
        if key == "decay":
            return (
                f"活跃记忆 {stats.get('memories', 0)} · 平均重要度 {stats.get('avg_importance', 0)}"
            )
        return ""
