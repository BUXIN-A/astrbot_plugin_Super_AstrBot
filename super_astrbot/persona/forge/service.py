"""PersonaForge 服务：三层人格的进程内管理 + 选择性双过程内省。

职责：
- **画像管理**：单人格画像的读取 / 合并更新 / 重置（持久化在 ``fusion_state``）；
- **状态推进**：每次互动推进动态状态（能量、关系亲密度），按日回归；
- **选择性内省**：仅在关键轮先产出「内心独白」再交给主模型（默认关闭，省调用）；
- **注入**：产出「人格摘要（+ 内心独白）」文本块，走与记忆相同的注入通道。

来源与改写：PersonaForge ``dual_process_agent.py`` 的 ``is_critical_interaction``
与两段式提示词结构被保留；LLM 调用改为经本插件 Harness（统一超时 / 预算 / 观测），
不外联、不起服务。
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from typing import Any, Callable, Mapping

from ...harness.protocols import EventView
from ...support import truncate
from .model import (
    BIG_FIVE_AXES,
    BIG_FIVE_LABELS,
    DEFENSE_LABELS,
    EMOJI_LABELS,
    PUNCTUATION_LABELS,
    SENTENCE_LABELS,
    VOCABULARY_LABELS,
    PersonalityProfile,
    default_profile,
)

_STATE_KEY = "persona.forge"

# 关键交互的情绪信号（PersonaForge 原关键词表的中文裁剪版 + 压力场景词）
_CRITICAL_KEYWORDS: tuple[str, ...] = (
    "喜欢", "讨厌", "生气", "开心", "难过", "愤怒", "失望", "惊喜", "爱你", "恨",
    "紧张", "担心", "委屈", "质疑", "威胁", "危机", "紧急", "重大", "关键", "离开", "别走",
    "生病", "住院", "分手", "吵架", "离职", "裁员", "考试", "答辩", "上线",
)

_ENERGY_DRIFT_PER_DAY = 18
"""连续无互动时每天回落的能量（向基线回归），避免能量长期钉死在两端。"""

_ENERGY_BASELINE = 60
_ENERGY_STEP = 2
"""每次互动恢复的能量。"""

_INTIMACY_STEP = 1.2
"""每次互动对该对象关系亲密度的增量（上限 100）。"""


class ForgeService:
    """三层人格（PersonaForge）服务。"""

    def __init__(
        self,
        *,
        db: Any | None = None,
        llm: Any | None = None,
        injector: Any | None = None,
        introspection: bool = False,
        provider_id: str = "",
        timeout_seconds: float = 45.0,
        max_monologue_chars: int = 400,
        max_injected_chars: int = 900,
        clock: Callable[[], float] | None = None,
        logger: Any | None = None,
    ) -> None:
        self._db = db
        self._llm = llm
        self._injector = injector
        self._introspection = bool(introspection)
        self._provider_id = str(provider_id or "")
        self._timeout = float(timeout_seconds or 45.0)
        self._max_monologue_chars = int(max_monologue_chars or 400)
        self._max_injected_chars = int(max_injected_chars or 900)
        self._clock = clock or time.time
        self._logger = logger

        self._profile: PersonalityProfile | None = None
        self._updated_at: float = 0.0
        self._last_state_at: float = 0.0
        self._last_introspection_at: float = 0.0
        self._introspection_count: int = 0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # 画像读写
    # ------------------------------------------------------------------ #

    async def profile(self) -> PersonalityProfile:
        async with self._lock:
            if self._profile is None:
                await self._load_locked()
            assert self._profile is not None
            return self._profile

    async def snapshot(self) -> dict[str, Any]:
        """面板用快照：画像 + 元信息 + 标签字典（前端不再维护映射表）。"""
        profile = await self.profile()
        return {
            "profile": profile.to_dict(),
            "profile_text": profile.to_profile_text(),
            "meta": {
                "introspection_enabled": self._introspection,
                "introspection_available": self._llm is not None,
                "provider_id": self._provider_id,
                "updated_at": self._updated_at,
                "last_state_at": self._last_state_at,
                "last_introspection_at": self._last_introspection_at,
                "introspection_count": self._introspection_count,
                "relationships": len(profile.dynamic_state.relationship_map),
            },
            "labels": {
                "big_five": dict(BIG_FIVE_LABELS),
                "defense": dict(DEFENSE_LABELS),
                "sentence": dict(SENTENCE_LABELS),
                "vocabulary": dict(VOCABULARY_LABELS),
                "punctuation": dict(PUNCTUATION_LABELS),
                "emoji": dict(EMOJI_LABELS),
            },
            "axes": list(BIG_FIVE_AXES),
        }

    async def update(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        """按补丁合并画像并落盘；返回最新快照。"""
        async with self._lock:
            if self._profile is None:
                await self._load_locked()
            assert self._profile is not None
            self._profile = self._profile.merge(patch or {})
            await self._save_locked(reason="panel-update")
        return await self.snapshot()

    async def reset(self) -> dict[str, Any]:
        async with self._lock:
            self._profile = default_profile()
            await self._save_locked(reason="reset")
        return await self.snapshot()

    async def apply_shift(
        self,
        deltas: Mapping[str, float],
        *,
        experience: str,
        summary: str = "",
        confidence: float = 0.0,
    ) -> dict[str, float]:
        """把演化域算出的特质增量写回内核层（大五人格轴，钳制 0~1）。

        返回实际生效的增量（越界部分被截断），供演化事件留痕。
        """
        applied: dict[str, float] = {}
        async with self._lock:
            if self._profile is None:
                await self._load_locked()
            assert self._profile is not None
            big_five = self._profile.core_traits.big_five
            for axis, delta in (deltas or {}).items():
                if axis not in BIG_FIVE_AXES:
                    continue
                try:
                    step = float(delta)
                except (TypeError, ValueError):
                    continue
                before = float(big_five.get(axis, 0.5))
                after = max(0.0, min(1.0, before + step))
                if abs(after - before) < 1e-6:
                    continue
                big_five[axis] = after
                applied[axis] = round(after - before, 6)
            if applied:
                await self._save_locked(reason="trait-shift")
        if applied:
            await self._record_event(
                kind="shift",
                experience=experience,
                summary=summary,
                deltas=applied,
                confidence=confidence,
            )
        return applied

    # ------------------------------------------------------------------ #
    # 动态状态推进
    # ------------------------------------------------------------------ #

    async def touch(self, view: EventView) -> None:
        """推进一次互动：能量恢复、按日回归、关系亲密度累积。"""
        now = self._clock()
        async with self._lock:
            if self._profile is None:
                await self._load_locked()
            assert self._profile is not None
            state = self._profile.dynamic_state
            # 按日回归：距上次推进超过一天的部分先回落，再叠加本次恢复
            elapsed_days = 0.0
            if self._last_state_at:
                elapsed_days = max(0.0, (now - self._last_state_at) / 86400.0)
            if elapsed_days > 0:
                drop = int(elapsed_days * _ENERGY_DRIFT_PER_DAY)
                if drop:
                    if state.energy_level > _ENERGY_BASELINE:
                        state.update_energy(-min(drop, state.energy_level - _ENERGY_BASELINE))
                    elif state.energy_level < _ENERGY_BASELINE:
                        state.update_energy(min(drop // 2, _ENERGY_BASELINE - state.energy_level))
            state.update_energy(_ENERGY_STEP)
            sender_id = str(getattr(view, "sender_id", "") or "")
            if sender_id:
                info = state.relationship(sender_id)
                name = str(getattr(view, "sender_name", "") or "")
                info.intimacy = min(100.0, float(info.intimacy) + _INTIMACY_STEP)
                if name and not info.history_summary:
                    info.history_summary = f"最近一次互动来自{name}"
            self._last_state_at = now
            await self._save_locked(reason="touch")

    # ------------------------------------------------------------------ #
    # 选择性双过程内省
    # ------------------------------------------------------------------ #

    async def should_introspect(self, view: EventView) -> tuple[bool, str]:
        """关键轮判定（PersonaForge 四标准的进程内版，纯规则、零成本）。"""
        if not self._introspection or self._llm is None:
            return False, "内省未启用"
        text = str(getattr(view, "text", "") or "")
        if not text.strip():
            return False, "空消息"
        profile = await self.profile()
        sender_id = str(getattr(view, "sender_id", "") or "")
        if sender_id and sender_id not in profile.dynamic_state.relationship_map:
            return True, "首次对话"
        for keyword in _CRITICAL_KEYWORDS:
            if keyword in text:
                return True, f"情绪/压力信号「{keyword}」"
        lowered = text.lower()
        for interest in profile.interests:
            if interest and interest.lower() in lowered:
                return True, f"触及核心兴趣「{interest}」"
        if len(text) >= 200:
            return True, "长文本倾诉"
        return False, "非关键轮"

    async def introspect(self, view: EventView) -> str:
        """产出内心独白并留痕；失败返回空串（绝不打断主链路）。"""
        if self._llm is None:
            return ""
        should, reason = await self.should_introspect(view)
        if not should:
            return ""
        profile = await self.profile()
        prompt = self._monologue_prompt(profile, view)
        try:
            result = await self._llm.chat(
                prompt=prompt,
                system_prompt=(
                    "你在为角色扮演生成内心独白。只输出独白本身，不要解释、不要分点、"
                    "不要出现心理学术语或数值。"
                ),
                provider_id=self._provider_id or None,
                timeout=self._timeout,
                purpose="forge-introspection",
            )
            text = truncate(str(getattr(result, "text", "") or "").strip(), self._max_monologue_chars)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001  内省失败不降级主链路
            self._warn("双过程内省失败：%s", exc)
            return ""
        if not text:
            return ""
        self._last_introspection_at = self._clock()
        self._introspection_count += 1
        await self._record_event(
            kind="introspection",
            experience="monologue",
            summary=f"{reason}｜{truncate(text, 80)}",
            deltas={},
            confidence=0.0,
        )
        self._debug("双过程内省已生成（%s）", reason)
        return text

    def _monologue_prompt(self, profile: PersonalityProfile, view: EventView) -> str:
        core = profile.core_traits
        state = profile.dynamic_state
        big_five = "、".join(
            f"{BIG_FIVE_LABELS[axis]} {core.big_five.get(axis, 0.5):.2f}" for axis in BIG_FIVE_AXES
        )
        speaker = str(getattr(view, "sender_name", "") or getattr(view, "sender_id", "") or "对方")
        return (
            f"你的大五人格：{big_five}；价值观：{'、'.join(core.values) or '—'}；"
            f"防御机制：{DEFENSE_LABELS.get(core.defense_mechanism, core.defense_mechanism)}。\n"
            f"你当前能量 {state.energy_level}/100，心情「{state.current_mood}」。\n"
            f"{speaker}对你说：{truncate(str(getattr(view, 'text', '') or ''), 400)}\n\n"
            "规则：神经质高时多想风险与不安；宜人性低时内心可以吐槽；外向性高时想法更主动；"
            "能量低时想法更短更消极。只输出这一段的内心独白。"
        )

    # ------------------------------------------------------------------ #
    # 注入
    # ------------------------------------------------------------------ #

    async def inject_block(self, view: EventView, *, monologue: str = "") -> str:
        """人格摘要 + （可选）内心独白，拼成一个注入块。"""
        if self._injector is None:
            return ""
        profile = await self.profile()
        body = profile.to_profile_text()
        if monologue:
            body += f"\n【本轮内心独白（仅你可见，不要照抄）】{monologue}"
        return truncate(body, self._max_injected_chars)

    async def inject(self, view: EventView, request: Any) -> dict[str, Any]:
        """完整注入流程：内省（可关）→ 拼块 → 注入。"""
        if self._injector is None:
            return {"applied": False, "reason": "注入器不可用"}
        monologue = ""
        if self._introspection and self._llm is not None:
            monologue = await self.introspect(view)
        body = await self.inject_block(view, monologue=monologue)
        if not body:
            return {"applied": False, "reason": "人格文本为空"}
        try:
            result = self._injector.inject(request, [body], prefer="auto")
        except Exception as exc:  # noqa: BLE001
            self._warn("人格注入失败：%s", exc)
            return {"applied": False, "reason": f"注入异常：{exc}"}
        return {
            "applied": bool(getattr(result, "applied", False)),
            "reason": str(getattr(result, "reason", "") or ""),
            "chars": int(getattr(result, "chars", 0) or 0),
            "introspected": bool(monologue),
            "method": str(getattr(result, "method", "") or ""),
        }

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #

    async def _load_locked(self) -> None:
        payload: dict[str, Any] = {}
        if self._db is not None:
            try:
                row = await self._db.query_one(
                    "SELECT value, updated_at FROM fusion_state WHERE key=?", (_STATE_KEY,)
                )
            except Exception as exc:  # noqa: BLE001  读取失败用出厂画像兜底
                self._warn("读取三层人格失败：%s", exc)
                row = None
            if row is not None:
                try:
                    payload = json.loads(str(row["value"] or "{}"))
                except (TypeError, ValueError):
                    payload = {}
                self._updated_at = float(row["updated_at"] or 0.0)
        if not payload:
            profile = default_profile()
            self._profile = profile
            self._updated_at = self._clock()
            if self._db is not None:
                await self._save_locked(reason="init")
            return
        self._profile = PersonalityProfile.from_dict(payload)
        if not self._updated_at:
            self._updated_at = self._clock()

    async def _save_locked(self, *, reason: str) -> None:
        if self._profile is None:
            return
        self._updated_at = self._clock()
        if self._db is None:
            return
        try:
            await self._db.execute(
                "INSERT INTO fusion_state(key, value, updated_at) VALUES (?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                " updated_at=excluded.updated_at",
                (
                    _STATE_KEY,
                    json.dumps(self._profile.to_dict(), ensure_ascii=False),
                    self._updated_at,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._warn("保存三层人格失败（%s）：%s", reason, exc)

    async def _record_event(
        self,
        *,
        kind: str,
        experience: str,
        summary: str,
        deltas: Mapping[str, float],
        confidence: float,
    ) -> None:
        if self._db is None:
            return
        try:
            await self._db.execute(
                "INSERT INTO persona_events(kind, experience, sender_id, scope_type, scope_id,"
                " summary, deltas, confidence, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    kind,
                    experience,
                    "",
                    "user",
                    "self",
                    truncate(summary, 200),
                    json.dumps(dict(deltas or {}), ensure_ascii=False),
                    float(confidence or 0.0),
                    self._clock(),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._debug("人格事件落库失败：%s", exc)

    # ------------------------------------------------------------------ #

    async def stats(self) -> dict[str, Any]:
        profile = await self.profile()
        return {
            "energy": profile.dynamic_state.energy_level,
            "mood": profile.dynamic_state.current_mood,
            "relationships": len(profile.dynamic_state.relationship_map),
            "introspection_enabled": self._introspection,
            "introspection_count": self._introspection_count,
            "last_introspection_at": self._last_introspection_at,
            "updated_at": self._updated_at,
        }

    def profile_sync(self) -> PersonalityProfile:
        """同步读取当前缓存画像（未加载时返回出厂画像，不触发 IO）。"""
        return self._profile if self._profile is not None else default_profile()

    def copy_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.profile_sync().to_dict())

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


__all__ = ["ForgeService"]
