"""群友识别服务：身份聚合 → 稳定档案 → 差异化对话策略。

设计约束（对应实施总纲 §0.2 / §8 / §10）：

1. **认人优先**：档案的第一性来源是身份观测（``identity_seen``）——
   同一昵称对应多个 ID 即为「标识不稳定」，档案上明确标出，提示先修身份策略；
2. **防串台**：所有个人素材（记忆摘要 / 好感度）都按 ``sender_id`` 过滤，
   不把 A 的事安到 B 头上；档案摘要只取自归属后的数据；
3. **单人格边界**：策略只微调语气 / 称呼 / 话题 / 禁忌，注入文本中明确
   「底层人格不变」，绝不切换人格；
4. **来源可溯**：档案带 ``source``（``distilly:offline`` 离线蒸馏产物 /
   ``online-sample`` 在线自写 / ``manual`` 手工编辑），面板据此展示。

本服务零外部依赖：所用数据全部来自插件自身的 SQLite 表，蒸馏产物以
数据目录下的 JSON 文件形式接入（离线产出、进程内读取）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from ..harness.protocols import EventView
from ..support import truncate
from .config import MembersConfig

SOURCE_DISTILLED = "distilly:offline"
SOURCE_ONLINE = "online-sample"
SOURCE_MANUAL = "manual"

_STABILITY_STABLE = "stable"
_STABILITY_UNSTABLE = "unstable"
_STABILITY_UNKNOWN = "unknown"

# 「常青」关系类型的默认语气建议：无档案时的兜底文案不再乱猜，只用中性表达。
_DEFAULT_TONE = "自然"


def _loads_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    try:
        data = json.loads(str(raw or "[]"))
    except (TypeError, ValueError):
        return []
    if isinstance(data, list):
        return [str(item) for item in data if str(item).strip()]
    return []


def _dumps_list(items: Any) -> str:
    if isinstance(items, str):
        items = [part.strip() for part in items.replace("，", ",").split(",") if part.strip()]
    if not isinstance(items, (list, tuple, set)):
        items = []
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    return json.dumps(cleaned, ensure_ascii=False)


class MembersService:
    """群友档案与差异化策略。"""

    def __init__(
        self,
        *,
        config: MembersConfig,
        db: Any,
        identities: Any | None = None,
        data_dir: Path | None = None,
        clock: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._config = config
        self._db = db
        self._identities = identities
        self._data_dir = data_dir
        self._clock = clock or time.time
        self._logger = logger

    # ------------------------------------------------------------------ #
    # 开关
    # ------------------------------------------------------------------ #

    def enabled(self) -> bool:
        return bool(self._config.enabled) and self._db is not None

    # ------------------------------------------------------------------ #
    # 档案聚合
    # ------------------------------------------------------------------ #

    async def roster(self, *, limit: int | None = None) -> dict[str, Any]:
        """聚合出群友名册：一位群友一份档案（身份 + 记忆 + 好感度 + 策略）。

        返回 ``{"members": [...], "stability": {...}, "totals": {...}}``。
        """
        if self._db is None:
            return {"members": [], "stability": {}, "totals": {"members": 0}}
        cap = int(limit or self._config.max_members)
        observations = await self._identity_rows(cap)
        strategies = await self._strategy_rows()
        memories = await self._memory_stats()
        affinities = await self._affinity_rows()

        # 昵称 → sender_id 集合：同一昵称对应多个 ID ⇒ 平台标识不稳定。
        by_sender: dict[str, dict[str, Any]] = {}
        name_to_ids: dict[str, set[str]] = {}
        for row in observations:
            sender_id = str(row.get("sender_id") or "").strip()
            name = str(row.get("sender_name") or "").strip()
            if not sender_id:
                continue
            entry = by_sender.setdefault(
                sender_id,
                {
                    "sender_id": sender_id,
                    "names": {},
                    "umos": set(),
                    "platform": str(row.get("platform") or ""),
                    "first_seen": float(row.get("first_seen") or 0.0),
                    "last_seen": float(row.get("last_seen") or 0.0),
                    "events": 0,
                },
            )
            if name:
                entry["names"][name] = entry["names"].get(name, 0) + int(row.get("events") or 1)
                name_to_ids.setdefault(name, set()).add(sender_id)
            entry["umos"].add(str(row.get("umo") or ""))
            entry["events"] += int(row.get("events") or 1)
            entry["last_seen"] = max(entry["last_seen"], float(row.get("last_seen") or 0.0))
            entry["first_seen"] = min(
                value
                for value in (entry["first_seen"], float(row.get("first_seen") or 0.0))
                if value is not None
            )

        now = self._clock()
        members: list[dict[str, Any]] = []
        for sender_id, entry in by_sender.items():
            stable_name = ""
            if entry["names"]:
                stable_name = max(entry["names"].items(), key=lambda kv: kv[1])[0]
            strategy = strategies.get(sender_id) or {}
            memory = memories.get(sender_id) or {}
            affinity = affinities.get(sender_id) or {}
            unstable = len(name_to_ids.get(stable_name, set())) > 1 if stable_name else False
            if not observations_total(entry):
                stability = _STABILITY_UNKNOWN
            else:
                stability = _STABILITY_UNSTABLE if unstable else _STABILITY_STABLE
            last_interact = max(
                float(entry.get("last_seen") or 0.0),
                float(memory.get("last_at") or 0.0),
                float(affinity.get("last_interaction") or 0.0),
            )
            members.append(
                {
                    "id": sender_id,
                    "sender_id": sender_id,
                    "stable_name": stable_name or sender_id,
                    "aliases": sorted(entry["names"].keys()),
                    "platform": entry["platform"],
                    "umos": sorted(umo for umo in entry["umos"] if umo),
                    "sessions": len(entry["umos"]),
                    "events": entry["events"],
                    "stability": stability,
                    "affinity": round(float(affinity.get("score") or 0.0), 4) if affinity else None,
                    "mood": affinity.get("mood") or "",
                    "interactions": int(affinity.get("interactions") or 0),
                    "memory_count": int(memory.get("count") or 0),
                    "memory_summary": memory.get("summary") or "",
                    "relation": str(strategy.get("relation") or ""),
                    "tone": str(strategy.get("tone") or ""),
                    "address_as": str(strategy.get("address_as") or ""),
                    "topics": _loads_list(strategy.get("topics")),
                    "taboo": _loads_list(strategy.get("taboo")),
                    "source": str(strategy.get("source") or ""),
                    "notes": str(strategy.get("notes") or ""),
                    "strategy_updated_at": float(strategy.get("updated_at") or 0.0),
                    "first_seen": float(entry.get("first_seen") or 0.0),
                    "last_interact": last_interact,
                    "stale": bool(
                        last_interact
                        and (now - last_interact) > self._config.profile_ttl_days * 86400.0
                    ),
                }
            )

        members.sort(
            key=lambda item: (
                item["affinity"] is not None,
                float(item["affinity"] or 0.0),
                float(item["last_interact"] or 0.0),
            ),
            reverse=True,
        )

        stable_count = sum(1 for item in members if item["stability"] == _STABILITY_STABLE)
        unstable_count = sum(1 for item in members if item["stability"] == _STABILITY_UNSTABLE)
        return {
            "members": members,
            "stability": {
                "total": len(members),
                "stable": stable_count,
                "unstable": unstable_count,
                "unknown": len(members) - stable_count - unstable_count,
                "verdict": ("unstable" if unstable_count else ("stable" if members else "unknown")),
                "hint": (
                    "同一昵称对应多个发送者标识，建议先修身份策略（改用昵称策略），"
                    "否则记忆仍会被会话切碎。"
                    if unstable_count
                    else "发送者标识跨会话稳定，可安全地按用户归属记忆。"
                ),
            },
            "totals": {
                "members": len(members),
                "with_strategy": sum(1 for item in members if item["source"]),
                "with_memory": sum(1 for item in members if item["memory_count"]),
            },
        }

    async def _identity_rows(self, limit: int) -> list[dict[str, Any]]:
        if self._identities is not None:
            try:
                return await self._identities.list_all(limit=limit)
            except Exception as exc:  # noqa: BLE001
                self._warn("读取身份观测失败：%s", exc)
                return []
        rows = await self._db.query(
            "SELECT * FROM identity_seen ORDER BY last_seen DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in rows]

    async def _strategy_rows(self) -> dict[str, dict[str, Any]]:
        rows = await self._db.query("SELECT * FROM member_profiles")
        return {str(row["sender_id"]): dict(row) for row in rows}

    async def _memory_stats(self) -> dict[str, dict[str, Any]]:
        """按发送者聚合记忆条数与最近时间；摘要素材单独取前 N 条。"""
        stats: dict[str, dict[str, Any]] = {}
        rows = await self._db.query(
            "SELECT sender_id, COUNT(*) AS count, MAX(created_at) AS last_at"
            " FROM memories WHERE sender_id != '' AND status = 'active'"
            " GROUP BY sender_id"
        )
        for row in rows:
            stats[str(row["sender_id"])] = {
                "count": int(row["count"] or 0),
                "last_at": float(row["last_at"] or 0.0),
                "summary": "",
            }
        for sender_id, item in stats.items():
            top = await self._db.query(
                "SELECT content FROM memories WHERE sender_id=? AND status='active'"
                " ORDER BY importance DESC, created_at DESC LIMIT ?",
                (sender_id, self._config.distill_top_memories),
            )
            item["summary"] = self._summarize([str(row["content"]) for row in top])
        return stats

    async def _affinity_rows(self) -> dict[str, dict[str, Any]]:
        """好感度按对象聚合（跨会话取最大分与累计交互次数）。"""
        rows = await self._db.query(
            "SELECT target_id, MAX(score) AS score, MAX(last_interaction) AS last_interaction,"
            " SUM(interactions) AS interactions FROM affinity_state GROUP BY target_id"
        )
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            target = str(row["target_id"] or "")
            if not target:
                continue
            result[target] = {
                "score": float(row["score"] or 0.0),
                "last_interaction": float(row["last_interaction"] or 0.0),
                "interactions": int(row["interactions"] or 0),
                "mood": "",
            }
        moods = await self._db.query(
            "SELECT target_id, mood FROM affinity_state WHERE mood != '' ORDER BY updated_at DESC"
        )
        for row in moods:
            target = str(row["target_id"] or "")
            if target in result and not result[target]["mood"]:
                result[target]["mood"] = str(row["mood"] or "")
        return result

    @staticmethod
    def _summarize(snippets: list[str]) -> str:
        parts: list[str] = []
        for text in snippets:
            cleaned = " ".join(str(text or "").split())
            if not cleaned:
                continue
            parts.append(truncate(cleaned, 32))
            if len(parts) >= 3:
                break
        return "；".join(parts)

    # ------------------------------------------------------------------ #
    # 策略读写
    # ------------------------------------------------------------------ #

    async def strategy(self, sender_id: str) -> dict[str, Any] | None:
        if self._db is None or not sender_id:
            return None
        row = await self._db.query_one(
            "SELECT * FROM member_profiles WHERE sender_id=?", (sender_id,)
        )
        if row is None:
            return None
        data = dict(row)
        data["topics"] = _loads_list(data.get("topics"))
        data["taboo"] = _loads_list(data.get("taboo"))
        return data

    async def save_strategy(
        self, sender_id: str, patch: Mapping[str, Any], *, source: str = SOURCE_MANUAL
    ) -> dict[str, Any]:
        """写入 / 合并一位群友的策略；只接受白名单字段。"""
        if self._db is None:
            return {"ok": False, "message": "持久层未就绪"}
        key = str(sender_id or "").strip()
        if not key:
            return {"ok": False, "message": "缺少 sender_id"}

        current = await self.strategy(key) or {}
        now = self._clock()
        fields = {
            "stable_name": _text(patch.get("stable_name"), current.get("stable_name", "")),
            "relation": _text(patch.get("relation"), current.get("relation", "")),
            "tone": _text(patch.get("tone"), current.get("tone", "")),
            "address_as": _text(patch.get("address_as"), current.get("address_as", "")),
            "topics": _dumps_list(patch["topics"])
            if "topics" in patch
            else _dumps_list(current.get("topics")),
            "taboo": _dumps_list(patch["taboo"])
            if "taboo" in patch
            else _dumps_list(current.get("taboo")),
            "notes": _text(patch.get("notes"), current.get("notes", "")),
        }
        next_source = _text(patch.get("source"), str(current.get("source") or source))
        await self._db.execute(
            "INSERT INTO member_profiles(sender_id, stable_name, relation, tone, address_as,"
            " topics, taboo, source, notes, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(sender_id) DO UPDATE SET"
            " stable_name=excluded.stable_name, relation=excluded.relation, tone=excluded.tone,"
            " address_as=excluded.address_as, topics=excluded.topics, taboo=excluded.taboo,"
            " source=excluded.source, notes=excluded.notes, updated_at=excluded.updated_at",
            (
                key,
                fields["stable_name"],
                fields["relation"],
                fields["tone"],
                fields["address_as"],
                fields["topics"],
                fields["taboo"],
                next_source,
                fields["notes"],
                float(current.get("created_at") or now),
                now,
            ),
        )
        return {"ok": True, "sender_id": key, "source": next_source, "updated_at": now}

    async def delete_strategy(self, sender_id: str) -> dict[str, Any]:
        if self._db is None:
            return {"ok": False, "message": "持久层未就绪"}
        cursor = await self._db.execute(
            "DELETE FROM member_profiles WHERE sender_id=?", (str(sender_id or ""),)
        )
        return {"ok": True, "deleted": int(getattr(cursor, "rowcount", 0) or 0)}

    # ------------------------------------------------------------------ #
    # 蒸馏（离线产物优先，缺失时用在线样本自写）
    # ------------------------------------------------------------------ #

    async def distill(self, sender_id: str) -> dict[str, Any]:
        """为一位群友生成 / 刷新档案。

        数据来源优先级：

        1. ``distilly:offline``：数据目录 ``distilly/<sender_id>.json`` 的离线蒸馏产物
           （由 distilly skill 离线产出，本服务只读取，符合「训练不进运行时」约束）；
        2. ``online-sample``：无离线产物时的在线自写 fallback —— 用该群友的
           高权重记忆 + 好感度 + 已有风格样本拼出档案骨架。
        """
        key = str(sender_id or "").strip()
        if not key:
            return {"ok": False, "message": "缺少 sender_id"}

        offline = self._load_distilled(key)
        if offline is not None:
            patch = {
                "stable_name": offline.get("stable_name") or "",
                "relation": offline.get("relation") or "",
                "tone": offline.get("tone") or "",
                "address_as": offline.get("address_as") or "",
                "topics": offline.get("topics") or [],
                "taboo": offline.get("taboo") or [],
                "notes": offline.get("notes") or "",
                "source": SOURCE_DISTILLED,
            }
            result = await self.save_strategy(key, patch, source=SOURCE_DISTILLED)
            result["origin"] = SOURCE_DISTILLED
            return result

        roster = await self.roster()
        member = next((item for item in roster["members"] if item["sender_id"] == key), None)
        if member is None:
            return {"ok": False, "message": "未观测到该群友，先让 TA 在群里说句话"}

        topics = list(member.get("topics") or [])
        if not topics and member.get("memory_summary"):
            topics = [member["memory_summary"]]
        patch = {
            "stable_name": member.get("stable_name") or "",
            "relation": member.get("relation") or _relation_hint(member),
            "tone": member.get("tone") or _DEFAULT_TONE,
            "address_as": member.get("address_as") or (member.get("stable_name") or ""),
            "topics": topics,
            "taboo": member.get("taboo") or [],
            "source": SOURCE_ONLINE,
        }
        result = await self.save_strategy(key, patch, source=SOURCE_ONLINE)
        result["origin"] = SOURCE_ONLINE
        result["summary"] = member.get("memory_summary") or ""
        return result

    def _load_distilled(self, sender_id: str) -> dict[str, Any] | None:
        if self._data_dir is None:
            return None
        candidates = [
            Path(self._data_dir) / "distilly" / f"{sender_id}.json",
            Path(self._data_dir) / "distilly" / f"{sender_id}.persona.json",
        ]
        for path in candidates:
            try:
                if not path.is_file():
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:  # noqa: BLE001
                self._warn("读取离线蒸馏产物失败（%s）：%s", path, exc)
                continue
            if isinstance(data, dict):
                return data
        return None

    # ------------------------------------------------------------------ #
    # 注入
    # ------------------------------------------------------------------ #

    async def inject_block(self, view: EventView) -> str:
        """生成给当前说话群友的差异化策略块；无档案时返回空串（零影响）。"""
        if not self.enabled() or not self._config.inject_strategy:
            return ""
        sender_id = str(getattr(view, "sender_id", "") or "").strip()
        if not sender_id:
            return ""
        row = await self.strategy(sender_id)
        if not row:
            return ""
        topics = _loads_list(row.get("topics"))
        taboo = _loads_list(row.get("taboo"))
        parts: list[str] = []
        name = str(row.get("stable_name") or "").strip()
        if name:
            parts.append(f"对方是{name}")
        if row.get("relation"):
            parts.append(f"关系：{row['relation']}")
        if row.get("address_as"):
            parts.append(f"称呼「{row['address_as']}」")
        if row.get("tone"):
            parts.append(f"语气：{row['tone']}")
        if topics:
            parts.append("可聊：" + "、".join(topics[:4]))
        if taboo:
            parts.append("禁忌：" + "、".join(taboo[:3]))
        if not parts:
            return ""
        body = "【群友档案】" + "；".join(parts) + "。底层人格不变，只调整表层表达。"
        return truncate(body, self._config.max_injected_chars)

    # ------------------------------------------------------------------ #
    # 统计
    # ------------------------------------------------------------------ #

    async def stats(self) -> dict[str, Any]:
        if self._db is None:
            return {"members": 0, "strategies": 0}
        members = await self._db.scalar(
            "SELECT COUNT(DISTINCT sender_id) FROM identity_seen WHERE sender_id != ''",
            default=0,
        )
        strategies = await self._db.scalar("SELECT COUNT(*) FROM member_profiles", default=0)
        return {"members": int(members or 0), "strategies": int(strategies or 0)}

    # ------------------------------------------------------------------ #

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            try:
                self._logger.warning(message, *args)
            except Exception:  # noqa: BLE001
                pass


def observations_total(entry: Mapping[str, Any]) -> int:
    """身份观测条数（用于判定稳定性是否有据可依）。"""
    try:
        return int(entry.get("events") or 0)
    except (TypeError, ValueError):
        return 0


def _text(value: Any, fallback: Any = "") -> str:
    if value is None:
        return str(fallback or "")
    text = str(value).strip()
    return text if text else str(fallback or "")


def _relation_hint(member: Mapping[str, Any]) -> str:
    """无策略时的关系推断：只看可解释信号（好感度档位），不编造细节。"""
    score = member.get("affinity")
    if score is None:
        return ""
    if score >= 0.8:
        return "熟络"
    if score >= 0.5:
        return "常聊"
    return "初识"


__all__ = [
    "MembersConfig",
    "MembersService",
    "SOURCE_DISTILLED",
    "SOURCE_MANUAL",
    "SOURCE_ONLINE",
]
