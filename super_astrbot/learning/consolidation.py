"""记忆整合：把零散的低价值记忆聚合为更精炼的记忆。

流程：候选查询 -> 分组 -> LLM 汇聚 -> 写入新记忆 + 归档旧记忆。

设计对齐 livingmemory 的 ``MemoryConsolidationManager``：反思每轮把对话切成
互相独立的小条目，此后没有任何环节回头合并，长期库里全是孤立碎片段。
整合层按周期把「足够旧 + 重要度低 + active」的记忆按作用域聚类，用 LLM
汇聚成一条更完整的记忆，原始条目归档。

可靠性约定：

- 整合失败绝不影响正常对话：所有异常被吞掉并记日志，只返回统计；
- 新能力默认关闭（``consolidation.enabled = false``）；
- 整合产物打上 ``source=consolidation`` 标记，避免被下一轮再次吞并。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..harness.protocols import LlmGateway
from ..memory import (
    KIND_INSIGHT,
    SOURCE_CONSOLIDATION,
    MemoryDraft,
    MemoryItem,
    MemoryService,
)
from ..spec.capabilities import as_bool, as_float, as_int, as_str, get_path
from ..spec.errors import LlmError, safe_detail
from ..support import PromptOverrides, extract_json_entries, truncate

MODE_SCOPE = "scope"
MODE_SEMANTIC = "semantic"

_CONSOLIDATION_SYSTEM = (
    "你是一个长期记忆整理助手。请把若干条零散记忆整合为一条更完整、更有信息量的记忆，"
    "不得编造记忆中没有的信息。"
)

_CONSOLIDATION_TEMPLATE = """以下是同一作用域内、围绕相近主题的若干条零散记忆（按时间顺序）：

<memories>
{material}
</memories>

请把它们整合为**一条**更完整的记忆，只输出 JSON 对象，不要输出任何解释文字：
{{
  "content": "整合后的记忆正文",
  "kind": "insight | fact | episode",
  "importance": 0.0 到 1.0 之间的小数,
  "tags": ["最多5个短标签"]
}}

要求：
1. 合并重复信息，保留各自独有的关键细节，不得丢信息；
2. 体现时间线（按发生先后组织），把「今天/昨天/上周」等相对时间换算为具体日期；
3. 若这批记忆围绕**一段人际关系或带有情绪分量的事**，允许用第一人称叙事
   （例如「这段时间和 XX 的几次私聊，我心里一直记着……」），此时 kind 用 episode；
   否则用第三人称客观陈述，kind 用 insight；
4. 不要编造记忆里没有的信息；
5. 篇幅可长（数百字），不要自我截断。
"""


@dataclass
class ConsolidationConfig:
    """记忆整合参数。"""

    enabled: bool = False
    mode: str = MODE_SCOPE
    """scope：同作用域聚合；semantic：语义聚类（向量未就绪时回退 scope）。"""
    interval_minutes: int = 1440
    min_age_days: float = 7.0
    max_importance: float = 0.5
    min_memories_per_group: int = 3
    max_group_size: int = 25
    """单次模型调用最多带多少条原始记忆：作用域内的组按时间切成若干窗口分别整合，
    避免「一个作用域几百条」撑爆上下文。"""
    max_groups_per_run: int = 5
    max_candidates: int = 500
    keep_original: str = "archive"
    provider_id: str = ""
    timeout_seconds: float = 120.0
    prompts: PromptOverrides = field(default_factory=PromptOverrides)
    """用户自定义提示词（留空即用内置默认）。"""

    @classmethod
    def from_mapping(cls, config: Mapping[str, Any]) -> "ConsolidationConfig":
        base_timeout = as_float(
            get_path(config, "runtime.llm_timeout_seconds", 45), 45.0, low=5.0, high=300.0
        )
        keep = as_str(get_path(config, "consolidation.keep_original", "archive")).strip().lower()
        if keep not in {"archive", "delete"}:
            keep = "archive"
        mode = as_str(get_path(config, "consolidation.mode", MODE_SCOPE)).strip().lower()
        if mode not in {MODE_SCOPE, MODE_SEMANTIC}:
            mode = MODE_SCOPE
        result = cls(
            enabled=as_bool(get_path(config, "consolidation.enabled", False), False),
            mode=mode,
            interval_minutes=as_int(
                get_path(config, "consolidation.interval_minutes", 1440),
                1440,
                low=10,
                high=10080,
            ),
            min_age_days=as_float(
                get_path(config, "consolidation.min_age_days", 7.0), 7.0, low=0.0, high=365.0
            ),
            max_importance=as_float(
                get_path(config, "consolidation.max_importance", 0.5), 0.5, low=0.0, high=1.0
            ),
            min_memories_per_group=as_int(
                get_path(config, "consolidation.min_memories_per_group", 3), 3, low=2, high=50
            ),
            max_group_size=as_int(
                get_path(config, "consolidation.max_group_size", 25), 25, low=5, high=200
            ),
            max_groups_per_run=as_int(
                get_path(config, "consolidation.max_groups_per_run", 5), 5, low=1, high=100
            ),
            max_candidates=as_int(
                get_path(config, "consolidation.max_candidates", 500), 500, low=10, high=20000
            ),
            keep_original=keep,
            provider_id=as_str(get_path(config, "consolidation.provider_id", "")),
            timeout_seconds=float(min(300.0, max(20.0, base_timeout * 2))),
        )
        result.prompts = PromptOverrides(config)
        return result


class ConsolidationService:
    """按配置周期性整合记忆库。"""

    def __init__(
        self,
        *,
        config: ConsolidationConfig,
        memory_service: MemoryService,
        llm: LlmGateway,
        logger: Any | None = None,
    ) -> None:
        self._config = config
        self._memory = memory_service
        self._llm = llm
        self._logger = logger
        self._last_run_at = 0.0

    @property
    def config(self) -> ConsolidationConfig:
        return self._config

    async def run_once(self, *, force: bool = False, now: float | None = None) -> dict[str, int]:
        """执行一轮整合。返回统计；任何失败都返回带 ``error`` 的统计而非抛出。"""
        if not self._config.enabled:
            return {"skipped": 1}

        moment = now if now is not None else time.time()
        if not force and (moment - self._last_run_at) < self._config.interval_minutes * 60.0:
            return {"skipped": 1}
        self._last_run_at = moment

        stats = {"candidates": 0, "groups": 0, "merged": 0, "removed": 0, "failed": 0}
        try:
            candidates = await self._memory.consolidation_candidates(
                max_importance=self._config.max_importance,
                created_before=moment - self._config.min_age_days * 86400.0,
                limit=self._config.max_candidates,
            )
            stats["candidates"] = len(candidates)
            if not candidates:
                return stats

            groups = self._group(candidates)
            groups = [g for g in groups if len(g) >= self._config.min_memories_per_group]
            groups.sort(key=len, reverse=True)

            # 组内按时间顺序切块：单次模型调用只带 max_group_size 条原始记忆，
            # 避免「一个作用域几百条」形成巨型组撑爆上下文。
            scheduled: list[list[MemoryItem]] = []
            for group in groups:
                for start in range(0, len(group), max(1, self._config.max_group_size)):
                    scheduled.append(list(group[start : start + self._config.max_group_size]))
            scheduled.sort(key=len, reverse=True)

            for chunk in scheduled[: self._config.max_groups_per_run]:
                try:
                    summary = await self._summarize(chunk)
                    if not summary:
                        stats["failed"] += 1
                        continue
                    _, affected = await self._apply(chunk, summary, moment)
                    stats["groups"] += 1
                    stats["merged"] += len(chunk)
                    stats["removed"] += affected if self._config.keep_original == "archive" else 0
                except Exception as exc:  # 单组失败不影响其它组
                    stats["failed"] += 1
                    self._warn("记忆整合失败：%s", safe_detail(exc))

            self._info("记忆整合完成：%s", stats)
            return stats
        except Exception as exc:
            self._warn("记忆整合运行异常：%s", safe_detail(exc))
            stats["error"] = 1
            return stats

    # ------------------------------------------------------------------ #

    def _group(self, items: Sequence[MemoryItem]) -> list[list[MemoryItem]]:
        """按作用域分组；``semantic`` 模式在向量未就绪时回退为作用域分组。

        说明：语义聚类需要 embedding 可用；在向量未就绪时回退到作用域分组，
        保证功能可用（与检索层的降级思路一致）。
        """
        grouped: dict[str, list[MemoryItem]] = {}
        for item in items:
            key = f"{item.scope_type}:{item.scope_id}"
            grouped.setdefault(key, []).append(item)
        return list(grouped.values())

    async def _summarize(self, group: Sequence[MemoryItem]) -> str:
        material = "\n".join(
            f"- [{time.strftime('%Y-%m-%d', time.localtime(m.created_at or 0))}] "
            f"{truncate(str(m.content or '').replace(chr(10), ' '), 300)}"
            for m in group
        )
        # 模板中的 JSON 示例已写成双花括号，可直接用 format 渲染。
        prompt = _CONSOLIDATION_TEMPLATE.format(material=material)
        try:
            result = await self._llm.chat(
                prompt=prompt,
                system_prompt=_CONSOLIDATION_SYSTEM,
                provider_id=self._config.provider_id or None,
                timeout=self._config.timeout_seconds,
                purpose="consolidation",
            )
        except LlmError as exc:
            self._warn("整合模型调用失败：%s", safe_detail(exc))
            return ""
        except Exception as exc:
            self._warn("整合模型调用异常：%s", safe_detail(exc))
            return ""

        parsed = extract_json_entries(result.text or "")
        if not parsed:
            return ""
        return str(parsed[0].get("content") or "").strip()

    async def _apply(
        self, group: Sequence[MemoryItem], summary: str, now: float
    ) -> tuple[int, int]:
        """写入整合记忆并处理原始条目，返回 ``(新记忆 ID, 实际处理的原始条数)``。"""
        head = group[0]
        importance = max([float(m.importance or 0.0) for m in group] + [0.55])
        tags: list[str] = []
        for m in group:
            for tag in m.tags or []:
                if tag not in tags and len(tags) < 5:
                    tags.append(tag)

        new_id = await self._memory.remember(
            MemoryDraft(
                scope_type=head.scope_type,
                scope_id=head.scope_id,
                content=summary,
                kind=KIND_INSIGHT,
                importance=importance,
                confidence=0.75,
                source=SOURCE_CONSOLIDATION,
                tags=tags,
            )
        )

        # 写前查重若命中组内某条（例如摘要包含某条原文，触发包含判定），
        # 返回的就是那条记忆的 ID（其正文已被更新为整合结果）。
        # 该条此刻承载着整合产物，绝不能再归档/删除，否则产物随旧条目一起丢失。
        old_ids = [m.id for m in group if m.id != new_id]
        if self._config.keep_original == "archive":
            await self._memory.archive(old_ids)
        else:
            await self._memory.delete(old_ids)
        return new_id, len(old_ids)

    # ------------------------------------------------------------------ #

    def _info(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.info(message, *args)

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)
