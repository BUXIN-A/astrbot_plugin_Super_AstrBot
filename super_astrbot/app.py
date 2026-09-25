"""应用容器：依赖装配、生命周期编排与钩子动作。

职责边界：

- **只做装配与编排**，不实现业务规则（规则在各自领域服务里）；
- 对 ``main.py`` 暴露少量稳定方法（``start`` / ``shutdown`` / ``on_llm_request`` /
  ``on_after_message_sent`` / ``status``），使插件入口保持极薄；
- 所有后台工作都经 ``TaskScope`` / ``Scheduler``，保证 ``shutdown`` 能完全收敛。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Mapping

from . import __version__
from .backup import (
    EXCLUDED_TABLES as BACKUP_EXCLUDED_TABLES,
    RESTORE_MERGE,
    RESTORE_REPLACE,
    BackupService,
    normalize_mode,
)
from .context import ContextConfig, ContextGovernor
from .graph import GraphConfig, GraphService
from .group import GroupChatService, GroupConfig
from .harness import (
    GROUP_FILTER_AVAILABLE,
    MEMORY_TOOL_NAMES,
    Harness,
    apply_group_decision,
    clear_options,
    compat,
    create_harness,
    inject_string_options,
    register_memory_tools,
    release_group_gate,
    set_group_gate,
    to_event_view,
    to_group_signals,
    unregister_tools,
)
from .harness.protocols import EventView
from .journal import (
    DEFAULT_ENTRY_TYPE,
    JournalConfig,
    JournalService,
    normalize_entry_type,
)
from .learning import ReflectionConfig, ReflectionService
from .loop import ConcurrencyGate, LLMBudget, Scheduler, TaskScope
from .maibot import MaiBotConfig, MaiBotService
from .memory import (
    AgentMemoryBackend,
    HybridRetriever,
    KeywordRetriever,
    MemoryConfig,
    MemoryIdentity,
    MemoryLifecycle,
    MemoryService,
    ResolvedIdentity,
    Reranker,
    VectorRetriever,
    DEFAULT_IDENTITY_STRATEGY,
    SOURCE_JOURNAL,
    SOURCE_WEEKLY,
    resolve_identity,
)
from .memory.retriever import GraphRetriever
from .monitor import (
    METRIC_GRAPH_ENTITIES,
    METRIC_GRAPH_INDEXED,
    METRIC_GROUP_INTERJECT,
    METRIC_INJECT_BLOCKS,
    METRIC_INJECT_CHARS,
    METRIC_INJECT_FALLBACKS,
    METRIC_LLM_BUDGET_BLOCKED,
    METRIC_LLM_CALLS,
    METRIC_LLM_ERRORS,
    METRIC_LLM_LATENCY_MS,
    METRIC_LLM_TOKENS,
    METRIC_MEMORY_TOTAL,
    METRIC_MEMORY_WRITES,
    METRIC_PERSONA_LEARNED,
    METRIC_PROACTIVE_SENT,
    METRIC_PROACTIVE_SKIPPED,
    METRIC_RERANK_CALLS,
    METRIC_RERANK_CANDIDATES,
    METRIC_RERANK_FAILURES,
    METRIC_RERANK_LATENCY_MS,
    METRIC_RETRIEVAL_CALLS,
    METRIC_RETRIEVAL_HITS,
    METRIC_RETRIEVAL_LATENCY_MS,
    METRIC_REVIEW_AUTO_APPROVED,
    METRIC_REVIEW_AUTO_REJECTED,
    METRIC_REVIEW_PENDING,
    METRIC_SCHEDULER_DURATION_MS,
    METRIC_SCHEDULER_FAILURES,
    METRIC_SCHEDULER_RUNS,
    MonitorService,
    observe,
    record,
)
from .persona import PersonaConfig, PersonaService, summarize_reviews
from .proactive import (
    TRACK_DAILY,
    TRACK_IDLE,
    MemoryMaterialSource,
    ProactiveConfig,
    ProactiveService,
)
from .review import AutoReviewService, ReviewConfig
from .spec.capabilities import explain_disabled, resolve_capabilities
from .spec.errors import StorageError, safe_detail
from .spec.scopes import MemoryScope, ScopeType, retrieval_scopes
from .storage import (
    CURRENT_VERSION,
    AffinityRepository,
    Database,
    GraphRepository,
    JargonRepository,
    IdentityRepository,
    JournalRepository,
    MemoryRepository,
    MetricSeriesRepository,
    ReflectionRepository,
    ReviewRepository,
    SqliteStateStore,
    StyleRepository,
    VectorRepository,
)
from .support import PromptOverlay, PromptOverrides, PromptStore, missing_placeholders, truncate

_DB_FILENAME = "super_astrbot.db"
_SKIP_CAPTURE_PREFIXES = ("/", "!", "#", ".")
"""命令类消息不进入记忆缓冲，避免把指令当成语料。"""

def _optional_float(value: Any) -> float | None:
    """导入用：能转成数字就转，否则返回 ``None``（语义为「按写入时刻算」）。

    ``0`` 原样返回：``last_access_at=0`` 表示「从未访问过」，必须保留；
    而 ``created_at=0`` 到了生命周期层会被当作「未提供」回退到当前时刻，
    不会写出 1970 年的时间。
    """
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


SCOPE_MIGRATION_TARGETS: tuple[str, ...] = (
    "user",
    "global",
    "archive",
    "user_else_archive",
)
"""作用域迁移目标（面板下拉与后端校验共用一份）。"""

_REFLECTION_SCAN_INTERVAL = 300.0
"""反思扫描间隔（秒）：只查条件是否满足，满足才真正调用模型。"""

_REFLECTION_SKIP_LOG_INTERVAL = 1800.0
"""「全部跳过」时反思原因日志的最小间隔（秒）。

跳过原因必须能在后台看见（``basic.debug_log`` 默认关闭，debug 级日志等于黑盒），
但每 5 分钟原样输出一次既吵又没用，因此只在「有进展」时逐次记录，
纯粹的「全都跳过」按半小时节流。
"""

_REFLECTION_SKIP_DETAIL_MAX = 5
"""单行日志里最多列出几个跳过的作用域，避免把一行日志写成一张表。"""

_MAINTENANCE_HOUR, _MAINTENANCE_MINUTE = 4, 30

_SCHEMA_SYNC_START_DELAY = 5.0
"""启动后延迟再同步 schema：给 ProviderManager 留出加载提供商的时间。"""

_SCHEMA_SYNC_INTERVAL = 600.0
"""schema 同步间隔（秒）。保存插件配置会触发重载并重建 AstrBotConfig，
因此运行时注入的下拉选项会丢失，必须周期性重新注入。"""

_EMBEDDING_FIELD_PATH = ("memory", "embedding_provider_id")
"""需要动态注入选项的配置字段路径（嵌入模型）。"""

_RERANK_FIELD_PATH = ("memory", "rerank_provider_id")
"""需要动态注入选项的配置字段路径（重排序模型）。"""

_JOB_PROACTIVE_DAILY = "proactive-daily"
_JOB_PROACTIVE_IDLE = "proactive-idle"
_PROACTIVE_JOB_TIMEOUT = 300.0
"""主动交互任务超时（秒）：逐个会话生成 + 发送，给足余量但不无限占用。"""
_JOB_JARGON_SCAN = "persona-jargon-scan"
_JARGON_JOB_TIMEOUT = 300.0
"""黑话扫描任务超时（秒）：一次批量推断 + 若干次写入。"""

_JOB_REVIEW_AUTO = "review-auto"
_REVIEW_JOB_TIMEOUT = 300.0
"""自动审核任务超时（秒）：一批规则判定 + 可能的模型兜底。"""

_JOB_MONITOR_FLUSH = "monitor-flush"
_MONITOR_FLUSH_INTERVAL = 60.0
"""指标落盘间隔（秒）：内存聚合按小时桶，只需分钟级批量写入。"""


_STYLE_PAIR_TTL = 300.0
"""风格配对的有效期（秒）：用户消息与 Bot 回复超过该间隔就不再配对成样本。"""

_STYLE_PAIR_CACHE = 200
"""风格配对缓存的最大会话数，防止长期运行后无限增长。"""


class SuperAstrBotApp:
    """插件运行时容器。"""

    def __init__(
        self,
        *,
        star: Any,
        context: Any,
        config: Mapping[str, Any] | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self._star = star
        self._context = context
        # 注意不要用 `config or {}`：空但真实的配置实体（如新建插件）会被误换成普通 dict，丢掉 .schema 引用
        self._config: Mapping[str, Any] = config if config is not None else {}
        # 允许显式指定数据目录（测试隔离 / 自定义部署位置）；None 时交由 Host 解析。
        self._data_dir_override = data_dir
        self._logger = getattr(star, "logger", None)

        self._started = False
        self._ready = False
        self._degraded_reasons: list[tuple[str, str]] = []

        # --- 运行时组件（在 start 中装配） ---
        self._db: Database | None = None
        self._state_store: SqliteStateStore | None = None
        self._harness: Harness | None = None
        self._scope: TaskScope | None = None
        self._scheduler: Scheduler | None = None
        self._gate: ConcurrencyGate | None = None
        self._budget: LLMBudget | None = None

        self._memory_config: MemoryConfig | None = None
        self._journal_config: JournalConfig | None = None
        self._reflection_config: ReflectionConfig | None = None
        self._context_config: ContextConfig | None = None
        self._group_config: GroupConfig | None = None
        self._proactive_config: ProactiveConfig | None = None
        self._persona_config: PersonaConfig | None = None
        self._graph_config: GraphConfig | None = None
        self._review_config: ReviewConfig | None = None
        self._maibot_config: MaiBotConfig | None = None

        self._memories: MemoryRepository | None = None
        self._journals_repo: JournalRepository | None = None
        self._identities_repo: IdentityRepository | None = None
        self._backup_service: BackupService | None = None
        self._reflections_repo: ReflectionRepository | None = None
        self._reviews_repo: ReviewRepository | None = None
        self._style_repo: StyleRepository | None = None
        self._jargon_repo: JargonRepository | None = None
        self._affinity_repo: AffinityRepository | None = None
        self._graph_repo: GraphRepository | None = None
        self._metrics_repo: MetricSeriesRepository | None = None

        self._memory_service: MemoryService | None = None
        self._journal_service: JournalService | None = None
        self._reflection_service: ReflectionService | None = None
        self._context_governor: ContextGovernor | None = None
        self._group_service: GroupChatService | None = None
        self._proactive_service: ProactiveService | None = None
        self._persona_service: PersonaService | None = None
        self._graph_service: GraphService | None = None
        self._auto_review_service: AutoReviewService | None = None
        self._maibot_service: MaiBotService | None = None
        self._monitor_service: MonitorService | None = None
        self._group_gate: Any | None = None
        """当前注入给 harness 的门控闭包（卸载时按对象身份清除，避免误清新实例）。"""

        self._style_pairs: dict[str, tuple[str, float]] = {}
        """会话最近一条用户消息（umo → 文本/时间），用于与 Bot 回复配对成风格样本。"""

        self._injection_methods: set[str] = set()
        """已经用 info 记录过的注入方式：同一方式只报一次，之后降为 debug。"""

        self._reflection_skip_logged_at = 0.0
        """上次输出「反思全部跳过」日志的时间戳（见 ``_REFLECTION_SKIP_LOG_INTERVAL``）。"""

        self._effective_capabilities: dict[str, bool] = {}
        self._retriever: Any | None = None

        self._prompt_store: PromptStore | None = None
        self._prompt_values: dict[str, str] = {}
        """页面保存的提示词覆盖（``{key: 文本}``，空值不入表即视为用内置默认）。"""
        self._prompt_handles: list[PromptOverrides] = []
        """各域配置里共享的覆盖解析器；热更新时就地换源，保证消费方立刻生效。"""
        self._data_dir: Path | None = None

    # ------------------------------------------------------------------ #
    # 属性
    # ------------------------------------------------------------------ #

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def capabilities(self) -> Mapping[str, bool]:
        return dict(self._effective_capabilities)

    @property
    def memory(self) -> MemoryService | None:
        return self._memory_service

    @property
    def journal(self) -> JournalService | None:
        return self._journal_service

    @property
    def reflection(self) -> ReflectionService | None:
        return self._reflection_service

    @property
    def memory_config(self) -> MemoryConfig | None:
        return self._memory_config

    @property
    def journal_config(self) -> JournalConfig | None:
        return self._journal_config

    @property
    def reflection_config(self) -> ReflectionConfig | None:
        return self._reflection_config

    @property
    def proactive_service(self) -> ProactiveService | None:
        return self._proactive_service

    @property
    def persona_service(self) -> PersonaService | None:
        return self._persona_service

    @property
    def graph_service(self) -> GraphService | None:
        return self._graph_service

    @property
    def auto_review_service(self) -> AutoReviewService | None:
        return self._auto_review_service

    @property
    def monitor_service(self) -> MonitorService | None:
        return self._monitor_service

    @property
    def host(self) -> Any:
        return self._harness.host if self._harness is not None else None

    def _enabled(self, key: str) -> bool:
        return bool(self._effective_capabilities.get(key, False))

    def _persona_any(self) -> bool:
        return any(
            self._enabled(key) for key in ("persona.style", "persona.jargon", "persona.affinity")
        )

    def _needs_storage(self) -> bool:
        """是否需要初始化持久层。

        运行监控的指标要落盘才有趋势可看，因此只要总开关开着就初始化数据库——
        数据库位于本插件自己的数据目录，未启用其它能力时也只是建一张空表。
        """
        if self._enabled("basic.enabled"):
            return True
        return any(
            self._enabled(key)
            for key in (
                "memory.enabled",
                "journal.enabled",
                "graph.enabled",
                "review.auto",
                "maibot.enabled",
            )
        )

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """装配全部组件并启动调度。任何子系统失败都降级而非抛出。"""
        if self._started:
            return
        self._started = True

        self._prepare_prompts()
        self._build_configs()
        if not self._enabled("basic.enabled"):
            self._info("插件总开关为关闭状态，仅注册命令与面板。")
            return

        if not await self._setup_storage():
            return

        if not await self._setup_harness():
            return

        self._setup_services()
        await self._start_background()
        self._setup_agent_tools()

        self._ready = True
        self._report_startup()

    async def shutdown(self) -> None:
        """收敛全部资源：Agent 工具 → 调度 → 任务 → 数据库。"""
        self._ready = False
        self._started = False

        self._teardown_agent_tools()
        # 摘掉门控闭包：卸载后不得再被框架的唤醒判定回调到本实例
        release_group_gate(self._group_gate)
        self._group_gate = None

        if self._scheduler is not None:
            try:
                await self._scheduler.shutdown()
            except Exception as exc:
                self._warn("关闭调度器失败：%s", safe_detail(exc))
            self._scheduler = None

        if self._scope is not None:
            try:
                cancelled = await self._scope.cancel_all(timeout=5.0)
                if cancelled:
                    self._info("已收敛 %s 个后台任务", cancelled)
            except Exception as exc:
                self._warn("收敛后台任务失败：%s", safe_detail(exc))
            self._scope = None

        if self._persona_service is not None:
            # 候选词计数保存在内存里，卸载前必须落盘，否则重载即归零。
            try:
                await self._persona_service.flush()
            except Exception as exc:
                self._warn("拟人化学习进度落盘失败：%s", safe_detail(exc))
            self._persona_service = None

        if self._monitor_service is not None:
            # 指标在内存里按小时桶聚合，卸载前落盘，否则最后一个窗口的数据会丢。
            try:
                await self._monitor_service.flush()
            except Exception as exc:
                self._warn("运行指标落盘失败：%s", safe_detail(exc))
            self._monitor_service = None

        if self._db is not None:
            try:
                await self._db.close()
            except Exception as exc:
                self._warn("关闭数据库失败：%s", safe_detail(exc))
            self._db = None

    # ------------------------------------------------------------------ #
    # 装配
    # ------------------------------------------------------------------ #

    def _build_configs(self) -> None:
        overrides = self._probe_runtime_overrides()
        self._capability_overrides = overrides
        self._effective_capabilities = resolve_capabilities(self._config, overrides)
        self._degraded_reasons = explain_disabled(self._effective_capabilities, overrides)

        self._memory_config = MemoryConfig.from_mapping(self._config)
        self._journal_config = JournalConfig.from_mapping(self._config)
        self._reflection_config = ReflectionConfig.from_mapping(self._config)
        self._context_config = ContextConfig.from_mapping(self._config)
        self._group_config = GroupConfig.from_mapping(self._config)
        self._proactive_config = ProactiveConfig.from_mapping(self._config)
        self._persona_config = PersonaConfig.from_mapping(self._config)
        self._graph_config = GraphConfig.from_mapping(self._config)
        self._review_config = ReviewConfig.from_mapping(self._config)
        self._maibot_config = MaiBotConfig.from_mapping(self._config)
        self._sync_derived_configs()
        self._bind_prompt_overrides()

    # ------------------------------------------------------------------ #
    # 提示词定制（面板）
    # ------------------------------------------------------------------ #

    def prompt_catalog(self) -> list[dict[str, Any]]:
        """列出全部可定制提示词及其当前取值，供面板渲染。"""
        from .prompt_catalog import PROMPT_SPECS

        items: list[dict[str, Any]] = []
        for spec in PROMPT_SPECS:
            custom = bool(self._prompt_values.get(spec.key))
            items.append(
                {
                    "key": spec.key,
                    "title": spec.title,
                    "group": spec.group,
                    "hint": spec.hint,
                    "required": list(spec.required),
                    "default": spec.default,
                    # 面板看到的一律是「当前生效值」：未自定义即内置默认。
                    "value": self._prompt_values.get(spec.key) or spec.default,
                    "custom": custom,
                }
            )
        return items

    async def set_prompt(self, key: str, value: str) -> dict[str, Any]:
        """保存一项提示词覆盖；与内置默认一致时等价于重置。"""
        from .prompt_catalog import BY_KEY

        spec = BY_KEY.get(key)
        if spec is None:
            return {"ok": False, "message": f"未知提示词：{key}"}

        text = (value or "").strip()
        if not text or text == spec.default.strip():
            return await self.reset_prompt(key, message=f"「{spec.title}」未修改，已使用内置默认")

        missing = missing_placeholders(text, spec.required)
        if missing:
            names = "、".join(f"{{{name}}}" for name in missing)
            return {"ok": False, "message": f"模板缺少必填占位符：{names}"}

        updated = dict(self._prompt_values)
        updated[key] = text
        if not await self._persist_prompts(updated):
            return {"ok": False, "message": "保存失败：提示词文件不可写"}
        self._prompt_values = updated
        self._bind_prompt_overrides()
        return {
            "ok": True,
            "key": key,
            "value": text,
            "custom": True,
            "message": f"「{spec.title}」已保存",
        }

    async def reset_prompt(self, key: str, *, message: str = "") -> dict[str, Any]:
        """把一项提示词重置为内置默认（删除覆盖）。"""
        from .prompt_catalog import BY_KEY

        spec = BY_KEY.get(key)
        if spec is None:
            return {"ok": False, "message": f"未知提示词：{key}"}

        updated = {k: v for k, v in self._prompt_values.items() if k != key}
        if updated != self._prompt_values and not await self._persist_prompts(updated):
            return {"ok": False, "message": "重置失败：提示词文件不可写"}
        self._prompt_values = updated
        self._bind_prompt_overrides()
        return {
            "ok": True,
            "key": key,
            "value": spec.default,
            "custom": False,
            "message": message or f"「{spec.title}」已重置为内置默认",
        }

    def _prepare_prompts(self) -> None:
        """定位提示词文件并载入覆盖；数据目录不可用时降级为「全部用内置默认」。"""
        self._data_dir = self._resolve_data_dir()
        self._prompt_store = PromptStore(
            None if self._data_dir is None else self._data_dir / PromptStore.FILENAME
        )
        self._prompt_values = self._prompt_store.load()
        if not self._prompt_store.available:
            self._warn("无法解析数据目录，提示词定制将无法保存。")
        elif self._prompt_values:
            self._info("已载入 %s 条自定义提示词。", len(self._prompt_values))

    async def _persist_prompts(self, values: dict[str, str]) -> bool:
        if self._prompt_store is None:
            return False
        try:
            return await asyncio.to_thread(self._prompt_store.save, values)
        except Exception as exc:
            self._warn("保存提示词失败：%s", safe_detail(exc))
            return False

    def _bind_prompt_overrides(self) -> None:
        """把当前覆盖绑定到各域配置的 ``PromptOverrides``（同实例热更新）。"""
        handles: list[PromptOverrides] = []
        for config in (
            self._reflection_config,
            self._context_config,
            self._proactive_config,
            self._persona_config,
            self._graph_config,
            self._review_config,
        ):
            prompts = getattr(config, "prompts", None)
            if isinstance(prompts, PromptOverrides):
                handles.append(prompts)
        self._prompt_handles = handles

        overlay = PromptOverlay(self._config, self._prompt_values)
        for handle in handles:
            handle.bind(overlay)

    def _resolve_data_dir(self) -> Path | None:
        """解析插件数据目录；失败返回 ``None``（调用方决定降级策略）。"""
        try:
            from .harness.astrbot_host import AstrBotHost

            host = AstrBotHost(
                self._star, self._context, self._config, data_dir=self._data_dir_override
            )
            return Path(host.data_dir())
        except Exception as exc:
            self._debug("解析数据目录失败：%s", safe_detail(exc))
            return None

    def _probe_runtime_overrides(self) -> dict[str, bool]:
        """探测运行环境对能力的支持情况。

        注意时序：AstrBot 的生命周期是「先加载插件、后初始化 ProviderManager」，
        所以插件启动时这里多半探测不到嵌入提供商。因此：

        - 探测结果只是**当次**结论，不缓存到 embedding 网关里（网关失败可重试）；
        - 另注册 ``on_astrbot_loaded`` 钩子在框架完全就绪后重新探测并热应用。
        """
        from .spec.capabilities import get_path

        overrides: dict[str, bool] = {}
        embedding_provider_id = str(
            get_path(self._config, "memory.embedding_provider_id", "") or ""
        )

        if self._context is None:
            overrides["memory.vector_enabled"] = False
            return overrides

        try:
            gateway = self._harness.embedding if self._harness is not None else None
            if gateway is None:
                # 装配早期（_build_configs 阶段）尚无 harness，只能临时构造探测网关。
                from .harness.astrbot_host import AstrBotHost
                from .harness.astrbot_llm import AstrBotEmbeddingGateway

                gateway = AstrBotEmbeddingGateway(
                    self._context,
                    AstrBotHost(self._star, self._context, self._config),
                    provider_id=embedding_provider_id,
                )
            if not gateway.available:
                overrides["memory.vector_enabled"] = False
        except Exception as exc:  # 探测失败按不可用处理
            overrides["memory.vector_enabled"] = False
            self._debug("向量能力探测失败，按不可用处理：%s", safe_detail(exc))
        return overrides

    def _sync_derived_configs(self) -> None:
        """让各领域配置对象与能力解析结果保持一致。"""
        memory = self._memory_config
        if memory is not None:
            memory.vector_enabled = self._enabled("memory.vector_enabled")
            memory.fts_enabled = self._enabled("memory.fts_enabled")
            memory.capture = self._enabled("memory.capture")
            memory.capture_groups = self._enabled("memory.capture_groups")
            memory.capture_private = self._enabled("memory.capture_private")
            memory.rerank_enabled = self._enabled("memory.rerank_enabled")
        if self._journal_config is not None:
            self._journal_config.enabled = self._enabled("journal.enabled")
        if self._reflection_config is not None:
            self._reflection_config.enabled = self._enabled("reflection.enabled")
        if self._context_config is not None:
            self._context_config.enabled = self._enabled("context.enabled")
        if self._group_config is not None:
            self._group_config.enabled = self._enabled("group.enabled")
        if self._proactive_config is not None:
            self._proactive_config.enabled = self._enabled("proactive.enabled")
        if self._persona_config is not None:
            self._persona_config.style.enabled = self._enabled("persona.style")
            self._persona_config.jargon.enabled = self._enabled("persona.jargon")
            self._persona_config.affinity.enabled = self._enabled("persona.affinity")
        if self._graph_config is not None:
            self._graph_config.enabled = self._enabled("graph.enabled")
        if self._review_config is not None:
            self._review_config.enabled = self._enabled("review.auto")
        if self._maibot_config is not None:
            self._maibot_config.enabled = self._enabled("maibot.enabled")
        self._sync_group_gate()

    def _sync_group_gate(self) -> None:
        """把「群聊语义是否生效」注入 harness 的 filter 门控。

        门控必须跟着能力开关走：关闭时群消息 handler 的 filter 不通过，
        插件就完全不参与 AstrBot 的唤醒判定（等价于没装插件）。
        """

        def _gate() -> bool:
            return (
                self._enabled("group.enabled")
                and self._group_service is not None
                and self._group_service.can_activate()
            )

        self._group_gate = _gate
        set_group_gate(_gate)

    def _sync_retriever_routes(self) -> None:
        """按当前能力开关增删检索路（热切换）。"""
        retriever = self._retriever
        if retriever is None or self._harness is None:
            return
        memories = self._memories
        if memories is None:
            return

        if self._enabled("memory.fts_enabled"):
            retriever.add_route(KeywordRetriever(memories, logger=self._logger))
        else:
            retriever.remove_route(KeywordRetriever.name)

        if self._harness.embedding.available and self._enabled("memory.vector_enabled"):
            assert self._db is not None
            retriever.add_route(
                VectorRetriever(
                    self._harness.embedding,
                    VectorRepository(self._db),
                    max_scan=(self._memory_config.vector_max_scan if self._memory_config else 5000),
                )
            )
        else:
            retriever.remove_route(VectorRetriever.name)

        if self._graph_service is not None and self._enabled("graph.enabled"):
            retriever.add_route(GraphRetriever(self._graph_service, logger=self._logger))
        else:
            retriever.remove_route(GraphRetriever.name)

    def _sync_rerank(self) -> None:
        """把重排序配置与网关推给检索器（热切换，无需重建检索路）。

        每次都会 ``refresh()`` 网关：ProviderManager 可能在插件启动之后才加载，
        或框架重建了 Provider 实例（旧实例的连接已关闭）。
        """
        retriever = self._retriever
        if retriever is None or self._memory_config is None:
            return
        gateway = self._harness.rerank if self._harness is not None else None
        if gateway is not None:
            try:
                gateway.refresh(self._memory_config.rerank_provider_id)
            except Exception as exc:  # 重新探测失败不影响流程
                self._debug("重排序提供商重新探测失败：%s", safe_detail(exc))
        try:
            retriever.configure_rerank(self._memory_config.retrieval_config(), gateway)
        except Exception as exc:
            self._debug("重排序热切换失败：%s", safe_detail(exc))

    def _sync_scheduler_jobs(self) -> None:
        """按能力开关启停定时任务。"""
        if self._scheduler is None:
            return
        self._scheduler.set_enabled(
            "memory-maintenance",
            self._enabled("memory.enabled")
            or self._enabled("persona.style")
            or self._enabled("graph.enabled")
            or self._enabled("maibot.enabled"),
        )
        self._scheduler.set_enabled("reflection-scan", self._enabled("reflection.enabled"))
        self._scheduler.set_enabled("weekly-insight", self._enabled("journal.weekly_reflection"))
        self._sync_proactive_jobs()
        self._sync_persona_jobs()
        self._sync_review_jobs()
        self._sync_monitor_jobs()

    def _sync_review_jobs(self) -> None:
        """增删自动审核任务（支持热切换与周期调整）。"""
        scheduler = self._scheduler
        config = self._review_config
        if scheduler is None or config is None or self._auto_review_service is None:
            return

        interval = max(60.0, float(config.interval_minutes) * 60.0)
        job = scheduler.get(_JOB_REVIEW_AUTO)
        if not self._enabled("review.auto"):
            scheduler.remove(_JOB_REVIEW_AUTO)
        elif job is None or job.interval != interval:
            scheduler.every(
                interval,
                self._job_review_auto,
                key=_JOB_REVIEW_AUTO,
                timeout=_REVIEW_JOB_TIMEOUT,
            )
        else:
            scheduler.set_enabled(_JOB_REVIEW_AUTO, True)

    def _sync_monitor_jobs(self) -> None:
        """指标落盘任务：只要持久层可用就常驻（监控不是可选能力）。

        与其它任务一样保持「已存在则不动」：重新 ``every`` 会新建 JobSpec 并把统计清零。
        末尾无条件 ``set_enabled(True)``：该任务不归能力开关管，任何一次热切换都不该把它关掉
        （曾被误关后表现为「指标内存里在涨、库里却不再新增」，难以自查）。
        """
        scheduler = self._scheduler
        if scheduler is None or self._monitor_service is None:
            return
        if scheduler.get(_JOB_MONITOR_FLUSH) is None:
            scheduler.every(
                _MONITOR_FLUSH_INTERVAL,
                self._job_monitor_flush,
                key=_JOB_MONITOR_FLUSH,
                timeout=60.0,
                run_immediately=False,
            )
        scheduler.set_enabled(_JOB_MONITOR_FLUSH, True)

    def _sync_persona_jobs(self) -> None:
        """增删黑话扫描任务（支持热切换）。"""
        scheduler = self._scheduler
        config = self._persona_config
        if scheduler is None or config is None or self._persona_service is None:
            return

        interval = max(600.0, float(config.jargon.scan_interval_minutes) * 60.0)
        job = scheduler.get(_JOB_JARGON_SCAN)
        if not self._enabled("persona.jargon"):
            scheduler.remove(_JOB_JARGON_SCAN)
        elif job is None or job.interval != interval:
            scheduler.every(
                interval,
                self._job_jargon_scan,
                key=_JOB_JARGON_SCAN,
                timeout=_JARGON_JOB_TIMEOUT,
            )
        else:
            scheduler.set_enabled(_JOB_JARGON_SCAN, True)

    def _sync_proactive_jobs(self) -> None:
        """增删主动交互的两条轨道（支持热切换）。

        只在「轨道开关」或「调度参数」变化时重建任务：``Scheduler`` 重建 ``daily``
        任务会清空 ``last_date``，导致当天重复发送，因此参数未变时保持原任务不动。
        """
        scheduler = self._scheduler
        config = self._proactive_config
        if scheduler is None or config is None or self._proactive_service is None:
            return

        active = self._enabled("proactive.enabled")

        daily = scheduler.get(_JOB_PROACTIVE_DAILY)
        if not active or not config.daily_enabled:
            scheduler.remove(_JOB_PROACTIVE_DAILY)
        elif daily is None or (daily.hour, daily.minute) != (
            config.daily_hour,
            config.daily_minute,
        ):
            scheduler.daily_at(
                config.daily_hour,
                config.daily_minute,
                self._job_proactive_daily,
                key=_JOB_PROACTIVE_DAILY,
                timeout=_PROACTIVE_JOB_TIMEOUT,
            )
        else:
            scheduler.set_enabled(_JOB_PROACTIVE_DAILY, True)

        idle = scheduler.get(_JOB_PROACTIVE_IDLE)
        interval = max(60.0, float(config.idle_check_minutes) * 60.0)
        if not active or not config.idle_enabled:
            scheduler.remove(_JOB_PROACTIVE_IDLE)
        elif idle is None or idle.interval != interval:
            scheduler.every(
                interval,
                self._job_proactive_idle,
                key=_JOB_PROACTIVE_IDLE,
                timeout=_PROACTIVE_JOB_TIMEOUT,
            )
        else:
            scheduler.set_enabled(_JOB_PROACTIVE_IDLE, True)

    def refresh_capabilities(self) -> dict[str, bool]:
        """重新探测运行时能力并热应用到各子系统。

        适用场景：ProviderManager 就绪后向量能力变为可用、用户在控制台切换功能开关。
        返回最新的生效状态。
        """
        previous = dict(self._effective_capabilities)
        if self._harness is not None:
            embedding_id = (
                self._memory_config.embedding_provider_id if self._memory_config else None
            )
            self._harness.embedding.refresh(embedding_id)

        overrides = self._probe_runtime_overrides()
        self._capability_overrides = overrides
        self._effective_capabilities = resolve_capabilities(self._config, overrides)
        self._degraded_reasons = explain_disabled(self._effective_capabilities, overrides)

        self._sync_derived_configs()
        self._sync_retriever_routes()
        self._sync_rerank()
        self._sync_scheduler_jobs()

        changed = {
            key: value
            for key, value in self._effective_capabilities.items()
            if previous.get(key) != value
        }
        if changed:
            self._info(
                "能力状态已更新：%s",
                "、".join(f"{key}={'开' if value else '关'}" for key, value in changed.items()),
            )
            if self._retriever is not None:
                self._info("当前检索路：%s", "、".join(self._retriever.route_names) or "无")
        return dict(self._effective_capabilities)

    async def on_astrbot_loaded(self) -> None:
        """框架完全加载完成：重新探测能力。

        AstrBot 先加载插件、后初始化 ProviderManager，因此插件启动阶段探测不到
        嵌入提供商；这里补一次探测，让向量检索自动启用。
        """
        if not self._started:
            return
        try:
            self.refresh_capabilities()
            self.sync_schema_options()
        except Exception as exc:  # 复检失败不影响主流程
            self._debug("框架加载后的能力复检失败：%s", safe_detail(exc))

    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # 配置维护（面板）：导出/导入插件配置 JSON，导入后热应用
    # ------------------------------------------------------------------ #

    def _schema_defaults(self) -> dict[str, Any]:
        """从 schema 的 ``default`` 字段构建参考默认配置（供导入校验）。"""
        schema = getattr(self._config, "schema", None)
        if not isinstance(schema, dict):
            return {}

        def defaults_of(node: dict[str, Any]) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for key, field in node.items():
                if not isinstance(field, dict) or "type" not in field:
                    continue
                ftype = field.get("type")
                if ftype == "object":
                    out[key] = defaults_of(field.get("items") or {})
                elif "default" in field:
                    out[key] = field["default"]
                else:
                    out[key] = {"int": 0, "float": 0.0, "bool": False, "string": "", "text": ""}.get(ftype, "")
            return out

        return defaults_of(schema)

    def config_export(self) -> dict[str, Any]:
        """导出当前插件配置（与官方配置页同一份数据）。"""
        return {
            "kind": "super_astrbot.config",
            "exported_at": time.time(),
            "config": dict(self._config),
        }

    @staticmethod
    def _coerce_config_value(value: Any, ftype: str, default: Any, field: dict[str, Any]) -> Any:
        """按 schema 类型收敛单个配置值；无法解析时回退默认值。"""
        from .spec.capabilities import as_bool, as_float, as_int, as_str

        try:
            if ftype == "bool":
                return value if isinstance(value, bool) else as_bool(value, as_bool(default, False))
            if ftype == "int":
                return as_int(value, as_int(default, 0))
            if ftype == "float":
                return as_float(value, as_float(default, 0.0))
            if ftype in ("string", "text"):
                result = as_str(value)
                options = field.get("options")
                if options and result and result not in options:
                    return as_str(default)
                return result
            if ftype == "list":
                if isinstance(value, list):
                    return [str(item) for item in value]
                if isinstance(value, str):
                    return [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
                return list(default or [])
            return value  # dict / file / template_list 等保持原样
        except (TypeError, ValueError):
            return default

    def _coerce_by_schema(self, node: dict[str, Any], schema: dict[str, Any]) -> None:
        """递归按 schema 收敛配置值类型（就地修改）。"""
        for key, field in schema.items():
            if not isinstance(field, dict) or "type" not in field or key not in node:
                continue
            ftype = field.get("type")
            if ftype == "object" and isinstance(node[key], dict):
                self._coerce_by_schema(node[key], field.get("items") or {})
            else:
                node[key] = self._coerce_config_value(
                    node[key], ftype, field.get("default"), field
                )

    async def config_import(self, payload: Any) -> dict[str, Any]:
        """导入插件配置：按 schema 剔除未知键/补齐缺失默认值/收敛类型 → 合并进配置实体 → 落盘 → 热应用。"""
        if not isinstance(payload, dict):
            return {"ok": False, "message": "配置必须是 JSON 对象"}
        if not payload:
            return {"ok": False, "message": "配置为空"}

        incoming = json.loads(json.dumps(payload, ensure_ascii=False))  # 深拷贝，避免污染调用方
        schema = getattr(self._config, "schema", None)
        checker = getattr(self._config, "check_config_integrity", None)
        if callable(checker):
            defaults = self._schema_defaults()
            if defaults:
                # 规范化：未知键剔除、缺失补默认、类型不符回退默认（核心同款逻辑）
                checker(defaults, incoming)
        else:
            defaults = self._schema_defaults()
            incoming = {key: value for key, value in incoming.items() if key in defaults}

        if not incoming:
            return {"ok": False, "message": "没有可识别的配置项"}

        # 类型收敛：check_config_integrity 不处理「"13"→13」这类标量转型
        if isinstance(schema, dict):
            self._coerce_by_schema(incoming, schema)

        known = sum(1 for key in incoming if key in self._config)
        self._config.clear()
        self._config.update(incoming)
        persisted = await self._persist_config()

        # 热应用：重建全部配置包装 + 重解析能力开关 + 重注入嵌入模型选项
        self._build_configs()
        self.refresh_capabilities()
        self.sync_schema_options()
        self._info(
            "导入插件配置：%s 个分节（落盘%s）",
            len(incoming),
            "成功" if persisted else "失败",
        )
        message = "已导入并热应用" + ("" if persisted else "；配置落盘失败，重启后可能回到原值")
        return {
            "ok": True,
            "sections": len(incoming),
            "known": known,
            "persisted": persisted,
            "message": message,
        }

    # ------------------------------------------------------------------ #
    # 记忆编辑 / 导入导出（面板）
    # ------------------------------------------------------------------ #

    async def panel_memory_export(self) -> list[dict[str, Any]]:
        memory = self._memory_or_error()
        return await memory.export_all_memories()

    async def panel_memory_update(self, memory_id: int, content: str) -> dict[str, Any]:
        """面板编辑记忆正文：写入新内容并重建该条的关键词/向量索引。

        重建而不是「只改字」：编辑过的记忆若沿用旧索引，关键词路会因未分词而搜不到它，
        向量路则会继续按旧语义被召回——两种都是静默的错配。
        """
        memory = self._memory_or_error()
        ok = await memory.update_content(int(memory_id), content)
        return {"ok": ok, "message": "已保存" if ok else "记录不存在或内容为空"}

    async def panel_memory_delete(self, memory_id: int) -> dict[str, Any]:
        """面板删除单条记忆（软删除：状态置为已遗忘，索引与图谱关联一并清理）。"""
        memory = self._memory_or_error()
        deleted = await memory.delete([int(memory_id)])
        return {"ok": deleted > 0, "message": "已删除" if deleted else "条目不存在"}

    async def panel_memory_import(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """导入记忆：保留类型/重要度/来源/标签/状态/作用域；跳过已遗忘与空内容。"""
        from .memory.models import STATUS_FORGOTTEN

        memory = self._memory_or_error()
        imported, skipped = 0, 0
        for item in items[:10_000]:
            if not isinstance(item, dict):
                skipped += 1
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                skipped += 1
                continue
            status = str(item.get("status") or "active")
            if status == STATUS_FORGOTTEN:
                skipped += 1
                continue
            scope = self._parse_scope_key(str(item.get("scope") or f"{item.get('scope_type') or 'global'}:{item.get('scope_id') or '*'}"))
            tags = item.get("tags")
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except (TypeError, ValueError):
                    tags = [part.strip() for part in tags.replace("，", ",").split(",") if part.strip()]
            try:
                importance = float(item.get("importance") or 0.5)
            except (TypeError, ValueError):
                importance = 0.5
            try:
                confidence = float(item.get("confidence") or 0.8)
            except (TypeError, ValueError):
                confidence = 0.8
            await memory.remember_text(
                scope,
                content,
                kind=str(item.get("kind") or "fact"),
                importance=max(0.0, min(1.0, importance)),
                confidence=max(0.0, min(1.0, confidence)),
                source=str(item.get("source") or "manual"),
                tags=tags if isinstance(tags, list) else None,
                status=status,
                identity=MemoryIdentity(
                    sender_id=str(item.get("sender_id") or ""),
                    sender_name=str(item.get("sender_name") or ""),
                    origin_umo=str(item.get("origin_umo") or ""),
                ),
                # 保留原始时间线：不回填的话，导入的历史记忆会全部显示成导入时刻
                created_at=_optional_float(item.get("created_at")),
                updated_at=_optional_float(item.get("updated_at")),
                last_access_at=_optional_float(item.get("last_access_at")),
                access_count=_optional_int(item.get("access_count")),
            )
            imported += 1
        return {"ok": True, "imported": imported, "skipped": skipped}

    # ------------------------------------------------------------------ #
    # 身份诊断与作用域迁移（跨会话识别用户的两件配套工具）
    # ------------------------------------------------------------------ #

    async def panel_identity_report(self) -> dict[str, Any]:
        """身份观测报告：谁在哪个会话说过话，以及平台标识是否稳定。"""
        memory = self.memory
        if memory is None:
            return {
                "items": [],
                "total": 0,
                "analysis": {"verdict": "empty", "hint": "记忆服务未就绪"},
                "strategy": DEFAULT_IDENTITY_STRATEGY,
                "scope_type": "",
                "tracking": False,
            }
        report = await memory.identity_observations(limit=200)
        config = self._memory_config
        report["strategy"] = config.identity_strategy if config else DEFAULT_IDENTITY_STRATEGY
        report["scope_type"] = config.default_scope.value if config else "session"
        report["tracking"] = self._identity_tracking_enabled()
        return report

    async def panel_identity_clear(self) -> dict[str, Any]:
        """清空身份观测（用户换平台或想重新取样时用）。"""
        memory = self.memory
        if memory is None:
            return {"ok": False, "message": "记忆服务未就绪"}
        removed = await memory.clear_identity_observations()
        return {"ok": True, "removed": removed, "message": f"已清空 {removed} 条身份观测"}

    async def panel_scope_report(self) -> dict[str, Any]:
        """作用域分布 + 当前作用域策略（迁移前必看）。"""
        memory = self.memory
        if memory is None:
            return {"types": {}, "scopes": [], "strategy": DEFAULT_IDENTITY_STRATEGY}
        data = await memory.scope_distribution(limit=50)
        config = self._memory_config
        data["strategy"] = config.identity_strategy if config else DEFAULT_IDENTITY_STRATEGY
        data["scope_type"] = config.default_scope.value if config else "session"
        data["targets"] = list(SCOPE_MIGRATION_TARGETS)
        return data

    async def panel_scope_migrate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """作用域迁移：``dry_run`` 只预览，落库前自动做一次数据库备份。

        支持的目标：``user``（按发送者归属）/ ``global``（整体提升）/
        ``archive``（只归档）/ ``user_else_archive``（能归属的归用户，其余归档）。
        """
        memory = self._memory_or_error()
        to = str(payload.get("to") or "").strip().lower()
        if to not in SCOPE_MIGRATION_TARGETS:
            return {"ok": False, "message": f"不支持的目标：{to or '(空)'}"}
        from_scope = str(payload.get("from_scope_type") or "session").strip()
        dry_run = payload.get("dry_run", True) is not False
        only_attributed = payload.get("only_attributed", True) is not False

        stats = await memory.migrate_scope(
            to=to,
            from_scope_type=from_scope,
            only_attributed=only_attributed,
            dry_run=dry_run,
        )
        result: dict[str, Any] = {
            "ok": True,
            "to": to,
            "from_scope_type": from_scope,
            "dry_run": dry_run,
            **stats,
        }
        if dry_run:
            result["message"] = (
                f"预览：符合条件 {stats.get('matched', 0)} 条（其中带身份 {stats.get('attributed', 0)} 条）——"
                f"将归属用户 {stats.get('moved', 0)} 条、归档 {stats.get('archived', 0)} 条、"
                f"跳过 {stats.get('skipped', 0)} 条；点「执行迁移」后才会真正写入。"
            )
            return result

        # 真实写入前先留一份库备份：作用域迁移是批量 UPDATE，必须可回滚。
        backup_path = await self._backup_database_file()
        result["backup"] = str(backup_path) if backup_path else ""

        # 记忆迁移完成后，配套迁移现实桥记录与图谱。
        # 注意：这两张表都没有发送者字段，无法像记忆那样逐条归属到用户，
        # 因此只在「整体提升为 global」时同步搬迁；其余目标会在结果里说明。
        if to == "global" and from_scope:
            result["journals"] = await memory.migrate_journal_scope(
                scope_type=from_scope, to_scope_type="global", to_scope_id="*", dry_run=False
            )
            if self._graph_repo is not None:
                try:
                    result["graph"] = await self._graph_repo.migrate_scope(
                        scope_type=from_scope, to_scope_type="global", to_scope_id="*", dry_run=False
                    )
                except Exception as exc:  # 图谱迁移失败不影响记忆迁移结果
                    result["graph"] = {"error": safe_detail(exc)}
        elif to in {"user", "user_else_archive"}:
            result["note"] = (
                "现实桥记录与图谱没有发送者字段，无法按用户归属，本次未改动它们；"
                "如需一并收敛，可先选「整体提升为全局」或单独归档。"
            )

        await self._record_migration(result)
        result["message"] = (
            f"迁移完成：处理 {stats.get('matched', 0)} 条（归属用户 {stats.get('moved', 0)}，"
            f"归档 {stats.get('archived', 0)}）。"
        )
        return result

    async def _backup_database_file(self) -> Path | None:
        """迁移前的库文件备份（走在线快照，WAL 下也一致）。"""
        db = self._db
        if db is None or self._data_dir is None:
            return None

        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self._data_dir / f"{db.path.name}.pre-scope-migration-{stamp}"
        try:
            return await db.snapshot_to(target)
        except Exception as exc:
            self._warn("迁移前备份失败：%s", safe_detail(exc))
            return None

    async def _record_migration(self, payload: dict[str, Any]) -> None:
        """把迁移动作记进 kv_state，便于事后追溯「什么时候动过作用域」。"""
        if self._db is None:
            return
        try:
            await SqliteStateStore(self._db).set(
                "scope_migration:last",
                {
                    "at": time.time(),
                    "to": payload.get("to"),
                    "matched": payload.get("matched"),
                    "moved": payload.get("moved"),
                    "archived": payload.get("archived"),
                    "backup": payload.get("backup"),
                },
            )
        except Exception as exc:
            self._debug("迁移记录写入失败：%s", safe_detail(exc))

    # ------------------------------------------------------------------ #
    # 周记管理（面板）—— 参照 admin-diary-proxy 模式：面板可直接增/编/删/导入导出
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_scope_key(key: str) -> MemoryScope:
        """把 ``global:*`` / ``user:123`` / ``group:456`` / ``session:umo`` 解析成作用域。"""
        from .spec.scopes import GLOBAL_SCOPE_ID, ScopeType

        raw = (key or "").strip()
        scope_type, _, scope_id = raw.partition(":")
        try:
            stype = ScopeType(scope_type.strip().lower())
        except ValueError:
            stype = ScopeType.GLOBAL
        if stype is ScopeType.GLOBAL:
            return MemoryScope(stype, GLOBAL_SCOPE_ID)
        return MemoryScope(stype, scope_id.strip() or GLOBAL_SCOPE_ID)

    def _journal_service_or_error(self) -> JournalService:
        service = self.journal
        if service is None:
            raise RuntimeError("现实桥服务未就绪")
        return service

    @staticmethod
    def _journal_entry_type(payload: dict[str, Any]) -> str | None:
        """从面板/导入载荷里取类型；``type`` 与 ``entry_type`` 两个键都接受。"""
        raw = payload.get("entry_type")
        if raw is None or str(raw).strip() == "":
            raw = payload.get("type")
        if raw is None or str(raw).strip() == "":
            return None
        return normalize_entry_type(raw)

    def _default_entry_type(self) -> str:
        """面板新增未选类型时的默认类型（配置项 ``journal.default_entry_type``）。"""
        config = self._journal_config
        return config.default_entry_type if config is not None else DEFAULT_ENTRY_TYPE

    async def panel_journal_add(self, payload: dict[str, Any]) -> dict[str, Any]:
        """面板写入现实记录（含记忆联动），与聊天 ``/sab journal`` 同一落库路径。

        标题留空时由服务层补「当天日期时间」；类型默认周记。
        """
        service = self._journal_service_or_error()
        content = str(payload.get("content") or "").strip()
        if not content:
            return {"ok": False, "message": "内容不能为空"}
        tags = payload.get("tags")
        if isinstance(tags, str):
            tags = [part.strip() for part in tags.replace("，", ",").split(",") if part.strip()]
        emotion = payload.get("emotion")
        event_time = payload.get("event_time")
        scope = self._parse_scope_key(str(payload.get("scope") or "global:*"))
        result = await service.add(
            scope,
            content,
            title=payload.get("title"),
            entry_type=self._journal_entry_type(payload) or self._default_entry_type(),
            tags=tags if isinstance(tags, list) else None,
            emotion=emotion,
            event_time=float(event_time) if event_time else None,
        )
        if result is None:
            return {"ok": False, "message": "写入失败（内容为空）"}
        return {"ok": True, **result}

    async def panel_journal_update(self, journal_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """面板编辑记录：标题/类型缺省表示保持不变，与导入同构。"""
        service = self._journal_service_or_error()
        tags = payload.get("tags")
        if isinstance(tags, str):
            tags = [part.strip() for part in tags.replace("，", ",").split(",") if part.strip()]
        has_title = "title" in payload
        has_type = payload.get("entry_type") is not None or payload.get("type") is not None
        ok = await service.update(
            int(journal_id),
            content=str(payload.get("content") or ""),
            title=payload.get("title") if has_title else None,
            entry_type=self._journal_entry_type(payload) if has_type else None,
            tags=tags if isinstance(tags, list) else None,
            emotion=payload.get("emotion"),
        )
        return {"ok": bool(ok), "message": "已保存" if ok else "记录不存在或内容为空"}

    async def panel_journal_delete(self, journal_id: int) -> dict[str, Any]:
        service = self._journal_service_or_error()
        ok = await service.delete(int(journal_id))
        return {"ok": bool(ok), "message": "已删除" if ok else "记录不存在"}

    async def panel_journal_export(
        self,
        *,
        ids: list[int] | None = None,
        entry_type: str = "",
        keyword: str = "",
    ) -> list[dict[str, Any]]:
        """导出记录：``ids`` 非空为「导出所选」，否则按类型/关键词筛选导出。"""
        service = self._journal_service_or_error()
        return await service.export_items(ids=ids, entry_type=entry_type, keyword=keyword)

    async def panel_journal_export_selected(self, payload: dict[str, Any]) -> dict[str, Any]:
        """面板勾选导出：解析勾选集合（或「全选当前筛选」）后走同一导出路径。"""
        select_all = bool(payload.get("all"))
        entry_type = self._journal_entry_type(payload) or ""
        keyword = str(payload.get("keyword") or "").strip()
        raw_ids = payload.get("ids")
        ids: list[int] = []
        if not select_all:
            if not isinstance(raw_ids, (list, tuple)):
                return {"ok": False, "message": "缺少勾选列表 ids"}
            for item in raw_ids:
                try:
                    value = int(item)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    ids.append(value)
            if not ids:
                return {"ok": False, "message": "请先勾选要导出的记录"}
        items = await self.panel_journal_export(
            ids=ids or None,
            entry_type=entry_type,
            keyword=keyword,
        )
        return {
            "ok": True,
            "mode": "filter" if select_all else "ids",
            "count": len(items),
            "items": items,
        }

    async def panel_journal_types(self) -> dict[str, int]:
        """各类型条目数（面板筛选下拉计数）。"""
        service = self._journal_service_or_error()
        return await service.count_by_type()

    # ------------------------------------------------------------------ #
    # 待审批量处理（44 条 pending 只有落地了，学习结果才真正生效）
    # ------------------------------------------------------------------ #

    async def panel_review_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        """批量批准/驳回待审记录。

        ``ids`` 为空且 ``all=true`` 时按当前筛选（``origin`` / ``umo``）取全部待审，
        逐条复用单条审批逻辑，保证副作用（写入风格/术语、生成记忆）完全一致。
        """
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"approve", "reject"}:
            return {"ok": False, "message": "action 必须是 approve 或 reject"}

        ids: list[int] = []
        raw_ids = payload.get("ids")
        if isinstance(raw_ids, (list, tuple)):
            for item in raw_ids:
                try:
                    value = int(item)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    ids.append(value)

        if not ids:
            try:
                rows = await self._pending_review_rows(payload)
            except Exception as exc:
                return {"ok": False, "message": f"读取待审队列失败：{safe_detail(exc)}"}
            ids = [int(row["id"]) for row in rows]
        if not ids:
            return {"ok": False, "message": "当前筛选下没有待审记录"}

        done, failed = 0, 0
        for review_id in ids[:1000]:
            try:
                if action == "approve":
                    handled, _ = await self.approve_review(review_id)
                else:
                    handled = await self.reject_review(review_id)
            except Exception as exc:
                self._warn("批量处理待审 #%s 失败：%s", review_id, safe_detail(exc))
                failed += 1
                continue
            if handled:
                done += 1
            else:
                failed += 1
        verb = "批准" if action == "approve" else "驳回"
        return {
            "ok": True,
            "action": action,
            "handled": done,
            "failed": failed,
            "message": f"已{verb} {done} 条" + (f"，{failed} 条未处理" if failed else ""),
        }

    async def _pending_review_rows(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """按面板筛选条件取待审行（供批量处理用）。"""
        origin = str(payload.get("origin") or "").strip()
        umo = str(payload.get("umo") or "").strip()
        if umo:
            scope = MemoryScope.for_session(umo)
            return await self.pending_reviews(scope, limit=1000, offset=0, origin=origin)
        return await self.pending_reviews_all(limit=1000, offset=0, origin=origin)

    # ------------------------------------------------------------------ #
    # 备份导出（配置 + 数据库 + 各类数据 → zip）
    # ------------------------------------------------------------------ #

    @property
    def backup(self) -> BackupService | None:
        return self._backup_service

    def _backup_or_error(self) -> BackupService:
        service = self._backup_service
        if service is None:
            raise RuntimeError("备份服务未就绪")
        return service

    async def panel_backup_build(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """生成备份包并返回可下载信息。"""
        service = self._backup_or_error()
        options = payload or {}
        artifact = await service.build(
            include_config=options.get("include_config", True) is not False,
            include_database=options.get("include_database", True) is not False,
            include_data=options.get("include_data", True) is not False,
            notes=str(options.get("notes") or ""),
        )
        return {"ok": True, **artifact.as_dict(), "message": f"已生成备份包：{artifact.filename}"}

    async def panel_backup_list(self) -> dict[str, Any]:
        """列出历史备份包与本次备份会覆盖的范围。"""
        service = self._backup_service
        if service is None:
            return {"items": [], "tables": [], "excluded": {}, "views": [], "dir": ""}
        return {
            "items": service.list_backups(limit=20),
            "tables": list(service.tables),
            "excluded": dict(BACKUP_EXCLUDED_TABLES),
            "views": service.view_names(),
            "dir": str(service.backup_dir),
            "keep": service.keep,
        }

    async def panel_backup_import(
        self, raw: bytes, *, mode: str = RESTORE_MERGE, dry_run: bool = False
    ) -> dict[str, Any]:
        """从全局备份包恢复。

        两种模式：

        - ``merge``（默认）：逐表 ``INSERT OR REPLACE``，备份优先——备份里有的行一定恢复，
          备份之后新产生的数据不受影响；
        - ``replace``：逐表「先清后灌」，恢复后与备份逐表一致（备份里没有的行必然不存在）。

        执行顺序（不可颠倒，见 SPEC 21.6）：**数据库快照 → 逐表写入（单事务，整体原子）
        → 重建关键词索引**。快照先于任何 DELETE 落盘；写入失败整体回滚并向用户报告快照路径；
        索引只重建关键词，向量表随包回灌、不重跑嵌入接口。
        """
        service = self._backup_service
        if service is None:
            return {"ok": False, "message": "备份服务未就绪"}
        selected = normalize_mode(mode)

        try:
            manifest = service.read_manifest(raw)
        except Exception as exc:
            return {"ok": False, "message": f"备份包不可用：{safe_detail(exc)}"}

        try:
            preview = await service.restore(raw, dry_run=True, mode=selected)
        except Exception as exc:
            return {"ok": False, "message": f"备份包不可用：{safe_detail(exc)}"}

        result: dict[str, Any] = {
            "ok": True,
            "mode": selected,
            "manifest": manifest,
            "planned": preview.tables,
            "planned_rows": preview.rows_written,
            "existing": preview.existing,
            "estimated_deleted": preview.estimated_deleted,
            "estimated_deleted_total": sum(preview.estimated_deleted.values()),
            "warnings": list(preview.notes),
        }
        backup_schema = int(manifest.get("schema_version") or 0)
        if backup_schema > CURRENT_VERSION:
            result["warnings"].append(
                f"备份包的 schema 版本(v{backup_schema})高于当前插件(v{CURRENT_VERSION})："
                "本版本不认识的字段无法恢复，建议先升级插件再恢复"
            )

        # 旧格式（format 1）包没有 tables/：走按视图恢复 + 整库恢复引导
        if preview.rows_written == 0 and preview.legacy_views:
            if dry_run:
                result["legacy"] = True
                result["planned_rows"] = sum(len(items) for items in preview.legacy_views.values())
                result["message"] = "旧格式包预览：将按视图恢复记忆 / 现实桥 / 每周总结"
                return result
            return await self._restore_legacy_backup(raw, preview, result)
        if preview.rows_written == 0:
            result["ok"] = False
            result["message"] = (
                "备份包里没有可恢复的数据"
                "（既没有 tables/ 全表导出，也没有可用的 data/ 视图或数据库快照）"
            )
            return result
        if dry_run:
            # 预览与真实执行返回同一套字段（tables / rows_written），
            # 面板才能用同一段渲染逻辑展示「将写入 N 行 / 预计删除 M 行」
            result["tables"] = dict(preview.tables)
            result["rows_written"] = preview.rows_written
            result["message"] = (
                f"{'完全覆盖' if selected == RESTORE_REPLACE else '合并'}预览："
                f"将写入 {preview.rows_written} 行 / {len(preview.tables)} 张表"
                + (
                    f"，预计删除约 {result['estimated_deleted_total']} 行（估计值）"
                    if selected == RESTORE_REPLACE
                    else ""
                )
            )
            return result

        # 1) 快照先于任何 DELETE 落盘
        snapshot = await self._backup_database_file()
        result["backup"] = str(snapshot) if snapshot else ""

        # 2) 逐表写入（单事务，整体原子）
        try:
            outcome = await service.restore(raw, dry_run=False, mode=selected)
        except Exception as exc:
            self._warn("备份恢复失败：%s", safe_detail(exc))
            return {
                "ok": False,
                "mode": selected,
                "message": f"恢复失败：{safe_detail(exc)}",
                "backup": result["backup"],
                "hint": (
                    "本次恢复已整体回滚，数据保持恢复前状态；"
                    f"如需整库回退可用快照：{result['backup'] or '（未生成，请检查数据目录权限）'}"
                ),
            }
        result["tables"] = outcome.tables
        result["rows_written"] = outcome.rows_written
        result["skipped_tables"] = outcome.skipped_tables
        result["warnings"] = [*result["warnings"], *outcome.notes]

        # 3) 索引：覆盖模式必须先清空 FTS（旧 token 会挂到同 id 的新行上），只重建关键词
        reindex_note = ""
        if self._memory_service is not None:
            try:
                stats = await self._memory_service.reindex_keywords(
                    clear=selected == RESTORE_REPLACE
                )
                cleared = int(stats.get("cleared") or 0)
                reindex_note = (
                    f"关键词索引重建 {stats.get('indexed', 0)} 条"
                    + (f"（先清空 {cleared} 条旧索引）" if cleared else "")
                )
            except Exception as exc:
                self._warn("恢复后重建关键词索引失败：%s", safe_detail(exc))
                reindex_note = "关键词索引重建失败（可用「重建检索索引」重试）"
        result["reindex"] = reindex_note

        result["config"] = await self._restore_config_from_backup(raw)
        result["database"] = await self._stash_database_from_backup(raw)
        result["can_replace_database"] = bool(
            str(result["database"].get("saved_to") or "").strip()
        )

        verb = "完全覆盖" if selected == RESTORE_REPLACE else "逐表合并"
        parts = [f"{verb}恢复 {outcome.rows_written} 行 / {len(outcome.tables)} 张表"]
        if result["config"].get("ok"):
            parts.append("配置已热应用")
        if reindex_note:
            parts.append(reindex_note)
        tail = "备份里没有的行已删除" if selected == RESTORE_REPLACE else "备份之后的新数据未受影响"
        result["message"] = "导入完成：" + "，".join(parts) + f"（{tail}）"
        self._info("全局备份恢复完成：%s", result["message"])
        return result

    async def panel_backup_replace_database(self, source: str) -> dict[str, Any]:
        """整库恢复：用快照文件替换当前数据库（唯一能连 kv_state 等非导出表一起还原的方式）。

        只接受**由本插件自己落在 backups/ 下的快照**（``restored-*.db``），避免把任意路径
        交给接口变成文件覆盖漏洞。流程与失败回滚由 ``Database.replace_file_from`` 保证。
        """
        db = self._db
        service = self._backup_service
        if db is None or service is None or self._data_dir is None:
            return {"ok": False, "message": "数据库或备份服务未就绪"}

        candidate = self._resolve_snapshot_path(source)
        if candidate is None:
            return {"ok": False, "message": "只允许使用备份目录下的快照文件（restored-*.db）"}

        try:
            info = await db.replace_file_from(candidate)
        except Exception as exc:
            self._warn("整库恢复失败：%s", safe_detail(exc))
            return {
                "ok": False,
                "message": f"整库恢复失败：{safe_detail(exc)}",
                "backup": "",
            }
        self._info("整库恢复完成：%s", info)
        return {
            "ok": True,
            "restored_from": info.get("restored_from", ""),
            "backup": info.get("backup", ""),
            "schema_version": info.get("schema_version"),
            "message": (
                "整库恢复完成：已用快照覆盖当前数据库并重新连接、跑完迁移。"
                "原库已另存为 pre-restore 备份，如发现不对可整库回退。"
            ),
        }

    def _resolve_snapshot_path(self, source: str) -> Path | None:
        """校验快照路径：必须落在 ``backups/`` 目录内且是 ``.db`` 文件。"""
        service = self._backup_service
        if service is None or self._data_dir is None:
            return None
        raw = str(source or "").strip()
        if not raw:
            # 未指定时取最近一次导入落盘的快照
            candidates = sorted(
                service.backup_dir.glob("restored-*.db"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            return candidates[0] if candidates else None
        candidate = Path(raw)
        try:
            resolved = candidate.resolve()
            root = service.backup_dir.resolve()
        except OSError:
            return None
        if resolved.parent != root or resolved.suffix != ".db" or not resolved.is_file():
            return None
        return resolved

    async def _restore_legacy_backup(
        self, raw: bytes, preview: Any, result: dict[str, Any]
    ) -> dict[str, Any]:
        """旧格式（format 1）备份包：按 ``data/*.json`` 尽力恢复。

        旧包是按类挑着导的（记忆 / 现实桥 / 每周总结有导入管线，其余只有可读视图），
        因此这里只回灌这三类，并把「剩下的请用包内数据库快照整库恢复」说清楚——
        不假装整包恢复成功。
        """
        snapshot = await self._backup_database_file()
        result["backup"] = str(snapshot) if snapshot else ""
        result["legacy"] = True

        handlers: dict[str, Any] = {
            "memories": self.panel_memory_import,
            "journals": self.panel_journal_import,
            "weeklies": self.panel_weekly_import,
        }
        imported: dict[str, Any] = {}
        rows = 0
        for key, items in preview.legacy_views.items():
            handler = handlers.get(key)
            if handler is None:
                continue
            try:
                outcome = await handler(items)
            except Exception as exc:
                imported[key] = {"ok": False, "message": safe_detail(exc)}
                continue
            imported[key] = {"ok": True, **outcome}
            rows += int(outcome.get("imported") or 0)
        result["tables"] = imported
        result["rows_written"] = rows
        result["warnings"] = [*result["warnings"], *preview.notes]

        reindex_note = ""
        if self._memory_service is not None:
            try:
                stats = await self._memory_service.reindex_keywords()
                reindex_note = f"关键词索引重建 {stats.get('indexed', 0)} 条"
            except Exception as exc:
                self._warn("恢复后重建关键词索引失败：%s", safe_detail(exc))
        result["reindex"] = reindex_note
        result["config"] = await self._restore_config_from_backup(raw)
        result["database"] = await self._stash_database_from_backup(raw)

        head = (
            f"导入完成（旧格式 format 1 包）：按视图恢复 {rows} 条"
            + ("，配置已热应用" if result["config"].get("ok") else "")
        )
        snapshot_ready = bool(str(result["database"].get("saved_to") or "").strip())
        if snapshot_ready:
            # 有库快照：包内数据本身没导出风格/图谱/待审，但整库替换能把它们一起还原
            result["can_replace_database"] = True
            result["message"] = (
                head
                + "。该备份未导出风格 / 图谱 / 待审等表；包内**有**数据库快照，"
                "完整恢复请用「整库恢复」（用快照替换当前数据库，含这些未导出的表与运行态）"
            )
        else:
            result["can_replace_database"] = False
            result["message"] = (
                head
                + "。该备份未导出风格 / 图谱 / 待审等表，且包内无数据库快照，"
                "这些数据无法从此备份恢复；请用新版重新导出一份全量备份（format 2）"
            )
        self._info("旧格式备份包恢复完成：%s", result["message"])
        return result

    async def _restore_config_from_backup(self, raw: bytes) -> dict[str, Any]:
        """从包内 ``config/plugin_config.json`` 恢复配置（Schema 校验后热应用）。"""
        import io
        import zipfile

        if self._backup_service is None:
            return {"ok": False, "message": "备份服务未就绪"}
        entry = "config/plugin_config.json"
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if entry not in set(archive.namelist()):
                    return {"ok": False, "message": "备份包内没有配置"}
                payload = json.loads(archive.read(entry).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
            return {"ok": False, "message": f"配置解析失败：{safe_detail(exc)}"}
        if isinstance(payload, dict) and isinstance(payload.get("config"), dict):
            payload = payload["config"]  # 兼容 config_export 的完整信封
        try:
            return await self.config_import(payload)
        except Exception as exc:  # 配置导入失败不应阻断数据恢复
            self._warn("备份包配置导入失败：%s", safe_detail(exc))
            return {"ok": False, "message": f"配置导入失败：{safe_detail(exc)}"}

    async def _stash_database_from_backup(self, raw: bytes) -> dict[str, Any]:
        """把包内数据库另存到 ``backups/``，供停用插件后手动整库替换。"""
        import io
        import zipfile

        service = self._backup_service
        if service is None or self._data_dir is None:
            return {"saved_to": "", "message": "数据目录未知，未保存数据库快照"}
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self._data_dir / service.BACKUP_DIR_NAME / f"restored-{stamp}.db"
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                if service.DB_ENTRY not in set(archive.namelist()):
                    return {"saved_to": "", "message": "备份包内没有数据库快照"}
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(service.DB_ENTRY))
        except (OSError, zipfile.BadZipFile) as exc:
            return {"saved_to": "", "message": f"数据库快照保存失败：{safe_detail(exc)}"}
        live = self._db.path.name if self._db is not None else "super_astrbot.db"
        return {
            "saved_to": str(target),
            "message": f"数据库快照已另存（未覆盖运行中的库）。整库恢复：停用插件 → 用它覆盖 {live} → 启用插件。",
        }

    async def panel_journal_import(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """导入记录：走同一写入路径（记录 + 记忆联动），保留标题、类型、时间与标签。"""
        service = self._journal_service_or_error()
        imported, skipped = 0, 0
        for item in items[:10_000]:
            if not isinstance(item, dict):
                skipped += 1
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                skipped += 1
                continue
            scope = self._parse_scope_key(
                str(item.get("scope") or f"{item.get('scope_type') or 'global'}:{item.get('scope_id') or '*'}")
            )
            tags = item.get("tags")
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except (TypeError, ValueError):
                    tags = [part.strip() for part in tags.replace("，", ",").split(",") if part.strip()]
            result = await service.add(
                scope,
                content,
                title=item.get("title"),
                entry_type=self._journal_entry_type(item),
                tags=tags if isinstance(tags, list) else None,
                emotion=item.get("emotion"),
                event_time=float(item["event_time"]) if item.get("event_time") else None,
                created_at=_optional_float(item.get("created_at")),
                identity=MemoryIdentity(
                    sender_id=str(item.get("sender_id") or ""),
                    sender_name=str(item.get("sender_name") or ""),
                    origin_umo=str(item.get("origin_umo") or ""),
                ),
            )
            if result is None:
                skipped += 1
            else:
                imported += 1
        return {"ok": True, "imported": imported, "skipped": skipped}

    # ------------------------------------------------------------------ #
    # 每周总结（周度洞察产出，独立于周记管理）
    # ------------------------------------------------------------------ #

    def _memory_or_error(self) -> MemoryService:
        service = self.memory
        if service is None:
            raise RuntimeError("记忆服务未就绪")
        return service

    async def weeklies_page(
        self, *, offset: int = 0, limit: int = 20, keyword: str = ""
    ) -> dict[str, Any]:
        memory = self._memory_or_error()
        items = await memory.list_weeklies(offset=offset, limit=limit, keyword=keyword)
        total = await memory.count_weeklies(keyword=keyword)
        return {
            "items": [
                {
                    "id": item.id,
                    "content": item.content,
                    "kind": item.kind,
                    "importance": item.importance,
                    "scope": f"{item.scope_type}:{item.scope_id}",
                    "created_at": item.created_at,
                }
                for item in items
            ],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    async def panel_weekly_delete(self, memory_id: int) -> dict[str, Any]:
        memory = self._memory_or_error()
        deleted = await memory.delete([int(memory_id)])
        return {"ok": deleted > 0, "message": "已删除" if deleted else "条目不存在"}

    async def panel_weekly_update(self, memory_id: int, content: str) -> dict[str, Any]:
        """面板编辑每周总结。

        每周总结就是 ``memories`` 里 ``source='weekly_reflection'`` 的行，
        因此与编辑记忆共用同一条写入路径（含索引重建），不另立分支。
        """
        memory = self._memory_or_error()
        ok = await memory.update_content(int(memory_id), content)
        return {"ok": ok, "message": "已保存" if ok else "记录不存在或内容为空"}

    async def panel_weekly_export(self) -> list[dict[str, Any]]:
        memory = self._memory_or_error()
        return await memory.export_weeklies()

    async def panel_weekly_import(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """导入每周总结：以 ``source=weekly_reflection`` 写回记忆，保留作用域/重要度/标签。"""
        memory = self._memory_or_error()
        imported, skipped = 0, 0
        for item in items[:10_000]:
            if not isinstance(item, dict):
                skipped += 1
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                skipped += 1
                continue
            scope = self._parse_scope_key(str(item.get("scope") or "global:*"))
            tags = item.get("tags")
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except (TypeError, ValueError):
                    tags = [part.strip() for part in tags.replace("，", ",").split(",") if part.strip()]
            try:
                importance = float(item.get("importance") or 0.75)
            except (TypeError, ValueError):
                importance = 0.75
            await memory.remember_text(
                scope,
                content,
                kind=str(item.get("kind") or "insight"),
                importance=max(0.0, min(1.0, importance)),
                confidence=0.95,
                source=SOURCE_WEEKLY,
                tags=tags if isinstance(tags, list) else None,
                created_at=_optional_float(item.get("created_at")),
                updated_at=_optional_float(item.get("updated_at")),
            )
            imported += 1
        return {"ok": True, "imported": imported, "skipped": skipped}

    # 功能开关（控制台）
    # ------------------------------------------------------------------ #

    def feature_catalog(self) -> list[dict[str, Any]]:
        """列出全部功能及其状态，供控制台渲染开关。"""
        from .spec.capabilities import CAPABILITIES, as_bool, get_path

        try:
            settings_map = self.feature_setting_specs()
        except Exception as exc:  # noqa: BLE001 - 设置派生失败只降级为纯开关，不能拖垮功能清单
            from .spec.errors import safe_detail

            self._warn("功能设置清单派生失败，已降级为纯开关：%s", safe_detail(exc))
            settings_map = {}
        catalog: list[dict[str, Any]] = []
        for item in CAPABILITIES:
            runtime_blocked = self._capability_overrides.get(item.key) is False
            catalog.append(
                {
                    "key": item.key,
                    "title": item.title,
                    "domain": item.domain,
                    "description": item.description,
                    "enabled": bool(self._effective_capabilities.get(item.key)),
                    "configured": as_bool(
                        get_path(self._config, item.key, item.default), item.default
                    ),
                    "depends_on": list(item.depends_on),
                    "hot_reloadable": item.hot_reloadable,
                    "runtime_dependent": item.runtime_dependent,
                    "runtime_blocked": runtime_blocked,
                    "blocked_by": [
                        dep for dep in item.depends_on if not self._effective_capabilities.get(dep)
                    ],
                    "status": self._capability_status(
                        item.key, runtime_blocked, item.hot_reloadable
                    ),
                    "settings": settings_map.get(item.key, []),
                }
            )
        return catalog

    def _schema_fields(self) -> dict[str, dict[str, Any]]:
        """摊平运行时 schema（``AstrBotConfig.schema``，含热注入的 options）。

        Returns:
            ``{点号路径: 字段声明}``；schema 不可用时返回空 dict（功能页退化为纯开关）。
        """
        schema = getattr(self._config, "schema", None)
        if not isinstance(schema, dict):
            return {}
        fields: dict[str, dict[str, Any]] = {}

        def walk(node: Any, prefix: str) -> None:
            for key, field in node.items():
                if not isinstance(field, dict) or "type" not in field:
                    continue
                path = f"{prefix}{key}"
                fields[path] = field
                if field.get("type") == "object":
                    walk(field.get("items") or {}, path + ".")

        walk(schema, "")
        return fields

    def feature_setting_specs(self) -> dict[str, list[dict[str, Any]]]:
        """把每个功能开关的「具体设置」归到该开关名下（与插件配置页同一份 schema）。

        归属规则：
        - 路径以 ``开关key + "_"`` 开头 → 归该开关（如 ``persona.style_*`` → ``persona.style``）；
        - 其余同分节的非开关项 → 归该分节的主能力（分节里第一个能力，通常是 ``*.enabled``）；
        - 无能力分节（``prompts`` / ``runtime``）不进功能页，仍由插件配置页管理。

        每次实时读取配置值，因此官方配置页改动后，下一次拉取即反映（双向同步的读取向）。
        """
        from .spec.capabilities import CAPABILITIES, get_path

        fields = self._schema_fields()
        switches = {item.key for item in CAPABILITIES}
        primary: dict[str, str] = {}
        for item in CAPABILITIES:
            primary.setdefault(item.key.split(".")[0], item.key)

        result: dict[str, list[dict[str, Any]]] = {item.key: [] for item in CAPABILITIES}
        for path, field in fields.items():
            if path in switches or "." not in path:
                continue
            section = path.split(".", 1)[0]
            if section not in primary:
                continue
            target = next(
                (
                    item.key
                    for item in CAPABILITIES
                    if path.startswith(item.key + "_")
                ),
                None,
            )
            if target is None:
                target = primary[section]
            value = get_path(self._config, path, field.get("default"))
            result[target].append(
                {
                    "key": path,
                    "type": field.get("type", "string"),
                    "default": field.get("default"),
                    "description": field.get("description", ""),
                    "hint": field.get("hint", ""),
                    "options": field.get("options"),
                    "labels": field.get("labels"),
                    "value": value,
                }
            )
        return result

    async def set_feature_setting(self, key: str, value: Any) -> dict[str, Any]:
        """修改某个功能的具体设置：按 schema 类型收敛取值 → 写配置 → 落盘。

        只允许写入 ``feature_setting_specs`` 派生出来的白名单路径，防止任意路径注入。
        """
        from .spec.capabilities import (
            as_bool,
            as_float,
            as_int,
            as_str,
            set_path,
        )

        spec = next(
            (
                item
                for items in self.feature_setting_specs().values()
                for item in items
                if item["key"] == key
            ),
            None,
        )
        if spec is None:
            return {"ok": False, "message": f"未知配置项：{key}"}

        ftype = spec.get("type")
        if ftype == "bool":
            coerced = as_bool(value, as_bool(spec.get("default"), False))
        elif ftype == "int":
            coerced = as_int(value, as_int(spec.get("default"), 0))
        elif ftype == "float":
            coerced = as_float(value, as_float(spec.get("default"), 0.0))
        elif ftype == "string":
            coerced = as_str(value)
            options = spec.get("options")
            if options and coerced and coerced not in options:
                return {"ok": False, "message": "取值不在可选项中"}
        elif ftype == "list":
            if isinstance(value, str):
                coerced = [
                    part.strip()
                    for part in str(value).replace("，", ",").split(",")
                    if part.strip()
                ]
            elif isinstance(value, list):
                coerced = [str(item) for item in value]
            else:
                return {"ok": False, "message": "列表取值不合法"}
        else:
            return {"ok": False, "message": f"面板暂不支持编辑 {ftype} 类型"}

        if not set_path(self._config, key, coerced):
            return {"ok": False, "message": "写入配置失败（配置结构异常）"}

        persisted = await self._persist_config()
        message = "已保存" + ("" if persisted else "；配置落盘失败，重启后可能回到原值")
        return {
            "ok": True,
            "key": key,
            "value": coerced,
            "persisted": persisted,
            "message": message,
        }

    def _capability_status(self, key: str, runtime_blocked: bool, hot: bool) -> str:
        if not hot:
            return "needs_reload"
        if runtime_blocked:
            return "runtime_unsupported"
        return "on" if self._effective_capabilities.get(key) else "off"

    async def set_capability(self, key: str, enabled: bool) -> dict[str, Any]:
        """切换功能开关：写配置 → 落盘 → 热应用。

        返回结构化结果（``ok`` / ``message`` / 最新状态），供控制台提示。
        """
        from .spec.capabilities import capability, set_path

        try:
            cap = capability(key)
        except KeyError:
            return {"ok": False, "message": f"未知功能：{key}"}

        if not cap.hot_reloadable:
            return {
                "ok": False,
                "needs_reload": True,
                "message": f"「{cap.title}」需要重载插件才能生效：请在插件配置页修改后重载。",
            }

        if enabled:
            blocked = [dep for dep in cap.depends_on if not self._effective_capabilities.get(dep)]
            if blocked:
                return {"ok": False, "message": "前置功能未开启：" + "、".join(blocked)}
            if cap.runtime_dependent and self._capability_overrides.get(cap.key) is False:
                return {
                    "ok": False,
                    "message": "运行环境不支持该功能（例如未配置嵌入模型提供商）",
                }

        if not set_path(self._config, key, bool(enabled)):
            return {"ok": False, "message": "写入配置失败（配置结构异常）"}

        persisted = await self._persist_config()
        effective = self.refresh_capabilities()
        actual = bool(effective.get(key))
        message = f"「{cap.title}」已{'开启' if actual else '关闭'}"
        if enabled and not actual:
            message += "（受前置条件或运行环境限制，实际未生效）"
        if not persisted:
            message += "；配置落盘失败，重启后可能回到原值"
        return {
            "ok": True,
            "key": key,
            "enabled": actual,
            "configured": bool(enabled),
            "persisted": persisted,
            "message": message,
        }

    async def _persist_config(self) -> bool:
        """把当前配置写入磁盘；普通 dict（测试 / 降级）时返回 False。"""
        async_saver = getattr(self._config, "save_config_async", None)
        if callable(async_saver):
            try:
                return bool(await async_saver())
            except Exception as exc:
                self._warn("保存配置失败：%s", safe_detail(exc))
                return False
        sync_saver = getattr(self._config, "save_config", None)
        if callable(sync_saver):
            try:
                await asyncio.to_thread(sync_saver)
                return True
            except Exception as exc:
                self._warn("保存配置失败：%s", safe_detail(exc))
                return False
        return False

    async def _setup_storage(self) -> bool:
        if not self._needs_storage():
            self._info("所有依赖持久层的功能均未启用，跳过持久层初始化。")
            return False

        data_dir = self._data_dir or self._resolve_data_dir()
        if data_dir is None:
            self._error("解析数据目录失败，持久化能力不可用。")
            return False
        self._data_dir = data_dir

        db = Database(data_dir / _DB_FILENAME, logger=self._logger)
        try:
            await db.connect()
        except StorageError as exc:
            self._error("数据库初始化失败，持久化能力不可用：%s", safe_detail(exc))
            return False

        self._db = db
        self._state_store = SqliteStateStore(db)
        self._memories = MemoryRepository(db)
        self._journals_repo = JournalRepository(db)
        self._identities_repo = IdentityRepository(db)
        self._reflections_repo = ReflectionRepository(db)
        self._reviews_repo = ReviewRepository(db)
        self._style_repo = StyleRepository(db)
        self._jargon_repo = JargonRepository(db)
        self._affinity_repo = AffinityRepository(db)
        self._graph_repo = GraphRepository(db)
        self._metrics_repo = MetricSeriesRepository(db)
        self._info("数据库就绪（schema v%s，FTS=%s）", CURRENT_VERSION, db.fts_available)
        return True

    async def _setup_harness(self) -> bool:
        from .spec.capabilities import get_path

        timeout = 45.0
        try:
            timeout = float(get_path(self._config, "runtime.llm_timeout_seconds", 45) or 45)
        except (TypeError, ValueError):
            timeout = 45.0

        try:
            self._budget = LLMBudget(
                daily_limit=int(get_path(self._config, "runtime.daily_llm_budget", 0) or 0),
                max_concurrent=int(get_path(self._config, "runtime.max_concurrent_llm", 2) or 2),
                logger=self._logger,
            )
            self._harness = create_harness(
                self._star,
                self._context,
                self._config,
                budget=self._budget,
                llm_timeout=timeout,
                embedding_provider_id=str(
                    get_path(self._config, "memory.embedding_provider_id", "") or ""
                ),
                rerank_provider_id=str(
                    get_path(self._config, "memory.rerank_provider_id", "") or ""
                ),
                llm_observer=self._on_llm_call,
            )
        except Exception as exc:  # Harness 失败则整体不可用
            self._error("Harness 初始化失败：%s", safe_detail(exc))
            return False

        self._info("Harness 就绪：%s", self._harness.describe())
        return True

    def _build_backup_service(self) -> BackupService:
        """装配备份服务：数据走「全表导出」，面板已有的导出视图作为可读副本一并打包。

        职责划分：**表导出**（完整字段，恢复读它）由 ``Database`` 提供；**视图**
        （语义化 JSON，供人看与跨插件同步）复用面板已有的导出方法，避免另写一套 SQL
        造成两处漂移。
        """
        data_dir = self._data_dir
        assert data_dir is not None

        async def _persona() -> dict[str, Any]:
            service = self.persona_service
            if service is None:
                return {}
            snapshot = service.snapshot()
            limit = 500
            return {
                "enabled": snapshot,
                "style": await service.all_style_patterns(limit=limit),
                "jargon": await service.all_jargons(limit=limit),
                "affinity": await service.all_affinity(limit=limit),
            }

        async def _graph() -> dict[str, Any]:
            service = self.graph_service
            if service is None:
                return {}
            data = await service.snapshot(limit_nodes=1000, limit_edges=2000)
            data["stats"] = await service.stats()
            return data

        async def _reviews() -> list[dict[str, Any]]:
            return await self.pending_reviews_all(limit=5000)

        async def _identities() -> dict[str, Any]:
            memory = self.memory
            if memory is None:
                return {}
            return await memory.identity_observations(limit=1000)

        views: dict[str, Any] = {
            "memories": self.panel_memory_export,
            "journals": self.panel_journal_export,
            "weeklies": self.panel_weekly_export,
            "reviews": _reviews,
            "identities": _identities,
            "persona": _persona,
            "graph": _graph,
        }
        return BackupService(
            data_dir=data_dir,
            db=self._db,
            views=views,
            config_provider=self.config_export,
            plugin_version=str(__version__ or ""),
            schema_version=CURRENT_VERSION,
            logger=self._logger,
        )

    def _setup_services(self) -> None:
        assert self._db is not None and self._harness is not None
        assert self._memory_config is not None and self._journal_config is not None
        assert self._reflection_config is not None
        assert self._memories is not None and self._journals_repo is not None
        assert self._reflections_repo is not None and self._reviews_repo is not None

        poll_ms = 50
        try:
            from .spec.capabilities import get_path

            poll_ms = int(get_path(self._config, "runtime.task_stop_poll_ms", 50) or 50)
        except (TypeError, ValueError):
            poll_ms = 50

        self._scope = TaskScope(
            "super-astrbot", poll_interval=max(0.01, poll_ms / 1000.0), logger=self._logger
        )
        assert self._state_store is not None
        self._scheduler = Scheduler(
            self._scope, store=self._state_store, logger=self._logger, observer=self._on_job
        )
        self._gate = ConcurrencyGate(logger=self._logger)

        # --- 检索路（分层自适应） ---
        routes: list[Any] = []
        if self._memory_config.fts_enabled:
            routes.append(KeywordRetriever(self._memories, logger=self._logger))
        if self._memory_config.vector_enabled:
            routes.append(
                VectorRetriever(
                    self._harness.embedding,
                    VectorRepository(self._db),
                    max_scan=self._memory_config.vector_max_scan,
                )
            )

        retrieval_config = self._memory_config.retrieval_config()
        retriever = HybridRetriever(
            routes=routes,
            memories=self._memories,
            config=retrieval_config,
            reranker=Reranker(
                config=retrieval_config.rerank,
                gateway=self._harness.rerank,
                observer=self._on_rerank,
                logger=self._logger,
            ),
            logger=self._logger,
        )
        # 保存引用：功能开关热切换时需要增删检索路
        self._retriever = retriever
        # 图谱服务先于记忆生命周期创建：生命周期只依赖 GraphIndexer 协议，
        # 由这里注入具体实现，memory 域因此无需 import graph 域。
        assert self._graph_repo is not None
        assert self._graph_config is not None
        self._graph_service = GraphService(
            config=self._graph_config,
            entities=self._graph_repo,
            llm=self._harness.llm,
            observer=self._on_graph_indexed,
            logger=self._logger,
        )

        lifecycle = MemoryLifecycle(
            db=self._db,
            memories=self._memories,
            vectors=VectorRepository(self._db),
            embedding=self._harness.embedding,
            config=self._memory_config,
            graph_indexer=self._graph_service,
            logger=self._logger,
        )

        self._memory_service = MemoryService(
            config=self._memory_config,
            lifecycle=lifecycle,
            retriever=retriever,
            memories=self._memories,
            journals=self._journals_repo,
            injector=self._harness.injector,
            embedding=self._harness.embedding,
            identities=self._identities_repo,
            logger=self._logger,
        )
        self._journal_service = JournalService(
            config=self._journal_config,
            journals=self._journals_repo,
            memory_service=self._memory_service,
            logger=self._logger,
        )
        self._backup_service = self._build_backup_service()
        self._reflection_service = ReflectionService(
            config=self._reflection_config,
            memory_service=self._memory_service,
            journals=self._journal_service,
            reflections=self._reflections_repo,
            reviews=self._reviews_repo,
            llm=self._harness.llm,
            logger=self._logger,
        )
        assert self._context_config is not None
        self._context_governor = ContextGovernor(
            config=self._context_config,
            llm=self._harness.llm,
            store=self._state_store,
            logger=self._logger,
        )

        assert self._group_config is not None
        self._group_service = GroupChatService(config=self._group_config)

        assert self._proactive_config is not None
        self._proactive_service = ProactiveService(
            config=self._proactive_config,
            host=self._harness.host,
            llm=self._harness.llm,
            materials=MemoryMaterialSource(memory=self._memory_service, logger=self._logger),
            store=self._state_store,
            logger=self._logger,
        )

        assert self._persona_config is not None
        assert self._style_repo is not None and self._jargon_repo is not None
        assert self._affinity_repo is not None and self._reviews_repo is not None
        # MaiBot 增强：表达样本的「按发送者个性化」由它提供额外作用域。
        assert self._maibot_config is not None
        self._maibot_service = MaiBotService(
            config=self._maibot_config,
            styles=self._style_repo,
            graph=self._graph_repo,
            clock=self._harness.host.now,
            logger=self._logger,
        )
        self._persona_service = PersonaService(
            config=self._persona_config,
            patterns=self._style_repo,
            jargons=self._jargon_repo,
            affinities=self._affinity_repo,
            reviews=self._reviews_repo,
            llm=self._harness.llm,
            injector=self._harness.persona_injector,
            store=self._state_store,
            extra_scope=self._maibot_service.user_scope,
            clock=self._harness.host.now,
            logger=self._logger,
        )
        assert self._review_config is not None
        self._auto_review_service = AutoReviewService(
            config=self._review_config,
            reviews=self._reviews_repo,
            approve=self._auto_approve,
            reject=self._auto_reject,
            llm=self._harness.llm,
            clock=self._harness.host.now,
            logger=self._logger,
        )
        assert self._metrics_repo is not None
        self._monitor_service = MonitorService(
            metrics=self._metrics_repo,
            logger=self._logger,
        )
        # 检索路在能力解析之后统一增删：图谱路依赖上面刚创建的服务实例。
        self._sync_retriever_routes()
        self._sync_group_gate()

    async def _start_background(self) -> None:
        assert self._scheduler is not None and self._memory_service is not None

        # 1) 修复中断写入（补齐索引/向量）
        try:
            repaired = await self._memory_service.repair()
            if repaired:
                self._info("启动修复完成：%s 条", repaired)
        except Exception as exc:
            self._warn("启动修复失败：%s", safe_detail(exc))

        # 2) 每日维护：衰减 + 清理缓冲
        self._scheduler.daily_at(
            _MAINTENANCE_HOUR,
            _MAINTENANCE_MINUTE,
            self._job_maintenance,
            key="memory-maintenance",
            timeout=180.0,
        )

        # 3) 反思扫描
        if self._enabled("reflection.enabled"):
            self._scheduler.every(
                _REFLECTION_SCAN_INTERVAL,
                self._job_reflection_scan,
                key="reflection-scan",
                timeout=300.0,
            )

        # 4) 周度洞察
        if self._enabled("journal.weekly_reflection") and self._journal_config is not None:
            self._scheduler.daily_at(
                self._journal_config.weekly_hour,
                0,
                self._job_weekly_insight,
                key="weekly-insight",
                weekday=self._journal_config.weekly_weekday,
                timeout=300.0,
            )

        # 5) 按能力开关统一启停（主动交互 / 黑话扫描 / 自动审核 / 指标落盘）
        self._sync_scheduler_jobs()

        await self._scheduler.start()
        self._info(
            "后台调度已启动：%s", "、".join(job["key"] for job in self._scheduler.snapshot())
        )

        # 7) 配置页动态下拉：把真实的嵌入模型列表注入到 schema
        if self._enabled("memory.enabled"):
            self._scope.spawn(self._schema_sync_loop(), name="schema-sync")

    # ------------------------------------------------------------------ #
    # Agent 函数工具
    # ------------------------------------------------------------------ #

    def _setup_agent_tools(self) -> int:
        """把记忆函数工具注册到框架；框架不支持时降级为「不注册」。"""
        if not self._enabled("agent.memory_tools"):
            return 0
        if self._memory_service is None:
            self._warn("记忆服务未就绪，跳过 Agent 记忆工具注册。")
            return 0

        backend = AgentMemoryBackend(
            service=self._memory_service, config=self._config, logger=self._logger
        )
        try:
            registered = register_memory_tools(self._context, backend, logger=self._logger)
        except Exception as exc:  # 注册失败不影响核心能力
            self._warn("注册 Agent 记忆工具失败：%s", safe_detail(exc))
            return 0
        if not registered:
            self._warn(
                "Agent 记忆工具未注册（框架未提供 FunctionTool 或注册接口不可用），"
                "记忆功能不受影响。"
            )
            return 0
        self._info("Agent 记忆工具已注册：%s", "、".join(MEMORY_TOOL_NAMES))
        return registered

    def _teardown_agent_tools(self) -> None:
        """注销本插件注册的函数工具，避免插件卸载/重载后残留。"""
        try:
            unregister_tools(self._context, logger=self._logger)
        except Exception as exc:
            self._warn("注销 Agent 记忆工具失败：%s", safe_detail(exc))

    # ------------------------------------------------------------------ #
    # 配置页动态选项
    # ------------------------------------------------------------------ #

    def embedding_providers(self) -> list[Any]:
        """当前可选的嵌入模型提供商。"""
        if self._harness is None:
            return []
        try:
            return list(self._harness.embedding.list_providers())
        except Exception as exc:
            self._debug("枚举嵌入提供商失败：%s", safe_detail(exc))
            return []

    def rerank_providers(self) -> list[Any]:
        """当前可选的重排序模型提供商。"""
        if self._harness is None:
            return []
        try:
            return list(self._harness.rerank.list_providers())
        except Exception as exc:
            self._debug("枚举重排序提供商失败：%s", safe_detail(exc))
            return []

    def chat_providers(self) -> list[Any]:
        """当前可选的对话模型提供商。"""
        if self._harness is None:
            return []
        try:
            return list(self._harness.llm.list_providers())
        except Exception as exc:
            self._debug("枚举对话提供商失败：%s", safe_detail(exc))
            return []

    def auxiliary_models(self) -> list[dict[str, Any]]:
        """列出各「辅助调用」所配置的对话模型。

        面板「模型」分区据此说明「哪个功能用哪一类模型」：这里的每一项都必须是
        对话模型（由 ``AstrBotLlmGateway.valid_provider_id`` 在调用前校验）；
        留空表示跟随会话默认模型。记忆的向量化用嵌入模型、重排序用重排序模型，
        二者不走此表。
        """
        sources: list[tuple[str, str, str, str]] = [
            (
                "reflection",
                "反思学习 / 周度洞察",
                "reflection.provider_id",
                self._reflection_config.provider_id if self._reflection_config else "",
            ),
            (
                "context",
                "上下文摘要",
                "context.summary_provider_id",
                self._context_config.summary_provider_id if self._context_config else "",
            ),
            (
                "graph",
                "知识图谱抽取",
                "graph.provider_id",
                self._graph_config.provider_id if self._graph_config else "",
            ),
            (
                "review",
                "自动审核兜底",
                "review.auto_provider_id",
                self._review_config.provider_id if self._review_config else "",
            ),
            (
                "proactive",
                "主动消息生成",
                "proactive.provider_id",
                self._proactive_config.provider_id if self._proactive_config else "",
            ),
            (
                "jargon",
                "群内用语推断",
                "persona.jargon_provider_id",
                self._persona_config.jargon.provider_id if self._persona_config else "",
            ),
            (
                "affinity",
                "好感度兜底",
                "persona.affinity_provider_id",
                self._persona_config.affinity.provider_id if self._persona_config else "",
            ),
        ]
        return [
            {
                "key": key,
                "title": title,
                "config_key": config_key,
                "provider_id": provider_id,
                "model_type": "chat",
            }
            for key, title, config_key, provider_id in sources
        ]

    def models_overview(self) -> dict[str, Any]:
        """三类模型提供商的选项与当前使用情况（面板「模型」分区）。"""
        embedding = self._harness.embedding if self._harness is not None else None
        return {
            "chat": {
                "providers": [
                    {"id": info.id, "model": info.model} for info in self.chat_providers()
                ],
                "auxiliary": self.auxiliary_models(),
            },
            "embedding": {
                "providers": [
                    {"id": info.id, "model": info.model} for info in self.embedding_providers()
                ],
                "selected": self._memory_config.embedding_provider_id
                if self._memory_config
                else "",
                "available": bool(embedding.available) if embedding is not None else False,
                "dimension": embedding.dimension() if embedding is not None else 0,
            },
            "rerank": {
                "providers": [
                    {"id": info.id, "model": info.model} for info in self.rerank_providers()
                ],
                **self._rerank_status(),
            },
        }

    def sync_schema_options(self) -> bool:
        """把真实的嵌入 / 重排序模型列表注入插件配置 schema 的下拉选项。

        AstrBot 的 ``_special: "select_provider"`` 只列对话模型且无法按类型过滤，
        因此这里改用运行时注入。三种情形：

        - 有提供商 → 注入 ``options``，字段渲染为下拉框；
        - 无提供商 → **移除** ``options``，字段退回文本框，用户可手填 ID；
        - 注入失败（如 schema 结构不符）→ 静默返回 ``False``，不影响功能。

        第 2 条很关键：如果注入一个只有「自动选择」的空列表，字段会变成无法输入的下拉框，
        反而把用户锁死。返回值为「是否至少有一个字段成功注入」。
        """
        embedding_ok = self._sync_provider_options(
            _EMBEDDING_FIELD_PATH,
            self.embedding_providers(),
            config_key="memory.embedding_provider_id",
            auto_label="（自动选择第一个可用的嵌入模型）",
        )
        rerank_ok = self._sync_provider_options(
            _RERANK_FIELD_PATH,
            self.rerank_providers(),
            config_key="memory.rerank_provider_id",
            auto_label="（自动选择第一个可用的重排序模型）",
        )
        return embedding_ok or rerank_ok

    def _sync_provider_options(
        self,
        path: tuple[str, ...],
        providers: list[Any],
        *,
        config_key: str,
        auto_label: str,
    ) -> bool:
        """把某一类提供商列表注入到指定字段的下拉选项。"""
        from .spec.capabilities import get_path

        if not providers:
            clear_options(self._config, path)
            return False

        options = [""]
        labels = [auto_label]
        for info in providers:
            options.append(info.id)
            labels.append(f"{info.id} · {info.model}" if info.model else info.id)

        current = str(get_path(self._config, config_key, "") or "")
        if current and current not in options:
            # 保留「已配置但当前不可用」的值，避免下拉框把用户设置清空
            options.append(current)
            labels.append(f"{current}（当前不可用）")

        try:
            injected = inject_string_options(self._config, path, options, labels)
        except Exception as exc:
            self._debug("注入配置下拉选项失败（%s）：%s", config_key, safe_detail(exc))
            return False
        if injected:
            self._debug("已为 %s 注入 %s 个选项", config_key, len(providers))
        return injected

    async def _schema_sync_loop(self) -> None:
        """周期性重新注入：保存插件配置会触发插件重载并重建 AstrBotConfig。"""
        await asyncio.sleep(_SCHEMA_SYNC_START_DELAY)
        while self._scope is not None and not self._scope.is_stopped():
            try:
                self.sync_schema_options()
            except Exception as exc:
                self._debug("schema 同步异常：%s", safe_detail(exc))
            await asyncio.sleep(_SCHEMA_SYNC_INTERVAL)

    # ------------------------------------------------------------------ #
    # 钩子动作
    # ------------------------------------------------------------------ #

    async def on_llm_request(self, event: Any, request: Any) -> None:
        """LLM 请求钩子：注入记忆，再做请求级上下文治理。

        两条链路互相独立：记忆走 ``extra_user_content_parts``，上下文治理走 ``contexts``，
        任一条失败都不影响另一条，也不影响对话本身。
        """
        if not self._ready or request is None:
            return

        view = to_event_view(event)
        if view.stopped or not view.text.strip():
            return

        self._note_activity(view)
        self._remember_user_text(view)
        self._observe_persona(view)
        await self._inject_memory(view, request)
        await self._govern_context(view, request)
        await self._inject_persona(view, request)

    async def _inject_memory(self, view: EventView, request: Any) -> None:
        """召回并注入长期记忆；失败只降级。"""
        if self._memory_service is None or not self._enabled("memory.enabled"):
            return

        if self._enabled("memory.capture"):
            self._capture_user_message(view)

        scope = self.memory_scope_for(view)
        try:
            result = await self._memory_service.recall(scope, view.text)
        except Exception as exc:  # 召回失败不影响对话
            self._warn("记忆召回失败：%s", safe_detail(exc))
            return

        record(METRIC_RETRIEVAL_CALLS)
        observe(METRIC_RETRIEVAL_LATENCY_MS, result.elapsed_ms)
        if result.items:
            record(METRIC_RETRIEVAL_HITS, total=float(len(result.items)))

        if not result.items:
            self._debug("未召回记忆（%s）", result.route_summary)
            return

        try:
            inject_result = await self._memory_service.inject(request, result)
        except Exception as exc:
            self._warn("记忆注入失败：%s", safe_detail(exc))
            return

        if inject_result.applied:
            record(METRIC_INJECT_BLOCKS, count=max(1, int(inject_result.parts or 1)))
            record(METRIC_INJECT_CHARS, total=float(inject_result.chars or 0))
            if inject_result.fallback:
                record(METRIC_INJECT_FALLBACKS)
            self._log_injection(
                method=inject_result.method,
                items=len(result.items),
                chars=inject_result.chars,
                route=result.route_summary,
                fallback=inject_result.fallback,
            )
        else:
            self._warn("记忆未注入：%s", inject_result.reason)

    def _log_injection(
        self, *, method: str, items: int, chars: int, route: str, fallback: bool
    ) -> None:
        """记录一次记忆注入；同一方式只报一次（info/warning），之后降为 debug。

        注入是每轮对话都会发生的事，逐次 info 会淹没日志；但完全不报又会让
        「注入有没有生效、走的哪条路」无从核对。折中：方式首次出现时显式记录，
        后续同类降到 debug，持续计数交给 ``inject.*`` 指标。
        """
        if method in self._injection_methods:
            self._debug("注入 %s 条记忆（%s，%s 字符，%s）", items, method, chars, route)
            return
        self._injection_methods.add(method)
        if fallback:
            self._warn(
                "记忆注入（%s，已降级）：%s 条 / %s 字符；检索路 %s。"
                "临时内容块不可用时回退系统提示词，记忆仍生效但会占用系统提示词位。",
                method,
                items,
                chars,
                route,
            )
            return
        self._info("记忆注入（%s）：%s 条 / %s 字符；检索路 %s", method, items, chars, route)

    async def _govern_context(self, view: EventView, request: Any) -> None:
        """请求级上下文治理：仅在估算超过阈值时动手。"""
        governor = self._context_governor
        if governor is None or not self._enabled("context.enabled"):
            return
        try:
            result = await governor.govern(request, session_key=view.umo)
        except Exception as exc:  # 治理失败不影响对话
            self._warn("上下文治理异常：%s", safe_detail(exc))
            return
        if result.error:
            self._warn("上下文治理降级（%s）：%s", result.reason, result.error)
        elif result.applied:
            self._debug("上下文治理：%s", result.summary())

    # ------------------------------------------------------------------ #
    # 拟人化学习
    # ------------------------------------------------------------------ #

    def _remember_user_text(self, view: EventView) -> None:
        """记住最近一条用户消息，供 Bot 回复送达后配对成风格样本。"""
        if self._persona_service is None or not self._enabled("persona.style"):
            return
        text = view.text.strip()
        if not text:
            return
        self._style_pairs[view.umo] = (text, view.timestamp or time.time())
        if len(self._style_pairs) > _STYLE_PAIR_CACHE:
            self._style_pairs.pop(next(iter(self._style_pairs)), None)

    def _observe_persona(self, view: EventView) -> None:
        """黑话候选统计与好感度更新都放到后台，不阻塞对话主链路。"""
        service = self._persona_service
        if service is None or self._scope is None or not self._persona_any():
            return
        text = view.text

        async def _task() -> None:
            try:
                await service.observe_user(view, text)
            except Exception as exc:  # 学习失败不影响对话
                self._warn("拟人化学习观测失败：%s", safe_detail(exc))

        self._scope.spawn(_task(), name="persona-observe")

    async def _inject_persona(self, view: EventView, request: Any) -> None:
        """把风格示例 / 黑话含义 / 关系语气写成本次请求的临时内容块。"""
        service = self._persona_service
        if service is None or not self._persona_any():
            return
        try:
            detail = await service.inject(request, view)
        except Exception as exc:  # 注入失败不影响对话
            self._warn("拟人化学习注入失败：%s", safe_detail(exc))
            return
        if detail:
            record(METRIC_INJECT_BLOCKS)
            self._debug("拟人化学习注入：%s", detail)

    async def _learn_style(self, view: EventView, reply_text: str) -> None:
        """把「用户上一条消息 → 本次回复」配对成风格样本。"""
        service = self._persona_service
        if service is None:
            return
        pair = self._style_pairs.pop(view.umo, None)
        if pair is None:
            return
        user_text, recorded_at = pair
        if time.time() - recorded_at > _STYLE_PAIR_TTL:
            return
        try:
            outcome = await service.learn_style(view, user_text=user_text, reply_text=reply_text)
        except Exception as exc:
            self._warn("风格学习失败：%s", safe_detail(exc))
            return
        if outcome.stored or outcome.pending:
            record(METRIC_PERSONA_LEARNED)
            self._debug("风格学习（%s）：%s", view.umo, outcome.reason)

    async def on_group_message(self, event: Any) -> None:
        """群消息钩子：读空气决定本次是否需要 Bot 参与。

        只有在 ``group.enabled`` 开启时该 handler 才会被激活（见 harness 的 filter 门控），
        因此能力关闭时本方法不会被调用，插件对群聊完全没有影响。
        """
        service = self._group_service
        if service is None or not self._enabled("group.enabled"):
            return

        view = to_event_view(event)
        signals = to_group_signals(event)
        try:
            decision = await service.decide(view, signals)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 决策失败按框架原判定处理
            self._warn("群聊语义决策异常：%s", safe_detail(exc))
            return

        if decision.action == "silent":
            self._debug("群聊静默（%s）：%s", view.umo, decision.reason)
        elif decision.action == "interject":
            record(METRIC_GROUP_INTERJECT)
            self._debug(
                "群聊插话（%s）：%s，合并 %s 条", view.umo, decision.reason, decision.merged
            )
        apply_group_decision(event, decision)

    async def on_after_message_sent(self, event: Any) -> None:
        """消息发送后钩子：记录会话活动，并把 Bot 回复放入对话缓冲。"""
        if not self._ready:
            return

        view = to_event_view(event)
        from .harness.astrbot_event import extract_result_text

        text = extract_result_text(event)
        reply = text.strip()
        self._note_activity(view, reply_text=reply)
        if self._enabled("persona.style") and reply:
            await self._learn_style(view, reply)
        if not self._enabled("memory.capture") or not reply:
            return
        self._queue_buffer(view, f"我：{truncate(reply, 500)}")

    def _note_activity(self, view: EventView, *, reply_text: str = "") -> None:
        """记录会话活动（供主动交互判断静默）与 Bot 发言（供群聊话题延续）。"""
        if self._proactive_service is not None:
            self._proactive_service.note_activity(view.umo)
        if reply_text and self._group_service is not None:
            self._group_service.record_bot_reply(view.umo, reply_text)

    # ------------------------------------------------------------------ #
    # 记忆采集
    # ------------------------------------------------------------------ #

    def _capture_user_message(self, view: EventView) -> None:
        text = view.text.strip()
        if not text or len(text) < 2:
            return
        if text.startswith(_SKIP_CAPTURE_PREFIXES):
            return
        if view.is_group and not (self._memory_config and self._memory_config.capture_groups):
            return
        if view.is_private and not (self._memory_config and self._memory_config.capture_private):
            return
        self._queue_buffer(view, f"用户({view.display_user})：{truncate(text, 500)}")

    def _queue_buffer(self, view: EventView, line: str) -> None:
        """把缓冲写入放到后台，避免阻塞对话主链路。

        显式传入事件时间戳，保证即使执行顺序有抖动，缓冲顺序仍然正确。
        顺带记录一次身份观测（同一后台任务，不额外增加主链路耗时）。
        """
        if self._scope is None or self._memory_service is None:
            return
        scope = self.memory_scope_for(view)
        identity = MemoryIdentity.from_view(view)
        timestamp = view.timestamp or time.time()

        async def _task() -> None:
            try:
                await self._memory_service.buffer_episode(
                    scope, line, now=timestamp, identity=identity
                )
                record(METRIC_MEMORY_WRITES)
            except Exception as exc:
                self._warn("写入对话缓冲失败：%s", safe_detail(exc))
            await self._observe_identity(view, scope)

        self._scope.spawn(_task(), name="buffer")

    async def _observe_identity(self, view: EventView, scope: MemoryScope) -> None:
        """记录「这条会话上出现过哪个发送者」，用于判定平台标识是否稳定。

        观测是纯旁路：失败只记 debug 日志，绝不冒泡到对话链路。
        """
        if self._memory_service is None or not self._enabled("memory.enabled"):
            return
        if not self._identity_tracking_enabled():
            return
        await self._memory_service.observe_identity(
            umo=view.umo,
            platform=view.platform,
            sender_id=view.sender_id,
            sender_name=view.sender_name,
            scope=scope,
            user_key=scope.scope_id,
            now=view.timestamp or time.time(),
        )

    def _identity_tracking_enabled(self) -> bool:
        """身份观测开关（默认开：观测是旁路写入，用于判定平台标识是否稳定）。"""
        from .spec.capabilities import get_path

        return bool(get_path(self._config, "basic.identity_tracking", True))

    def memory_scope_for(self, view: EventView) -> MemoryScope:
        """按配置的作用域类型与身份策略解析作用域。

        这是「跨会话识别用户」的落点：``default_scope=user`` 时，作用域键取自
        ``identity_strategy`` 指定的稳定标识（平台 ID / 昵称 / 自动回退），
        而不是会随连接变化的会话 ``umo``。
        """
        config = self._memory_config
        scope_type = config.default_scope if config else ScopeType.SESSION
        if scope_type is not ScopeType.USER:
            return MemoryScope.from_event(scope_type, umo=view.umo, user_id=view.sender_id or "")
        resolved = self.resolve_user_identity(view)
        return MemoryScope.for_user(resolved.user_key)

    def resolve_user_identity(self, view: EventView) -> ResolvedIdentity:
        """按身份策略解析用户键（诊断与面板共用同一份逻辑）。"""
        config = self._memory_config
        strategy = config.identity_strategy if config else DEFAULT_IDENTITY_STRATEGY
        return resolve_identity(
            sender_id=view.sender_id,
            sender_name=view.sender_name,
            strategy=strategy,
        )

    # ------------------------------------------------------------------ #
    # 调度任务
    # ------------------------------------------------------------------ #

    async def _job_maintenance(self) -> None:
        stats: dict[str, Any] = {}
        # MaiBot 增强开启时由它统一承担「风格 + 图谱」的衰减，各域只做容量淘汰，
        # 否则同一份权重会被衰减两次。
        unified_decay = (
            self._maibot_service is not None
            and self._enabled("maibot.enabled")
            and self._maibot_service.decay_enabled()
        )
        if self._memory_service is not None and self._enabled("memory.enabled"):
            async with self._gate.write("__maintenance__") if self._gate else _NullGate():
                stats = await self._memory_service.maintain()
        if self._persona_service is not None and self._enabled("persona.style"):
            stats["persona"] = await self._persona_service.maintain(with_decay=not unified_decay)
        if self._graph_service is not None and self._enabled("graph.enabled"):
            stats["graph"] = await self._graph_service.maintain(with_decay=not unified_decay)
        if unified_decay and self._maibot_service is not None:
            stats["maibot"] = await self._maibot_service.maintain()
        if self._monitor_service is not None:
            stats["metrics_purged"] = await self._monitor_service.purge()
        self._info("每日维护完成：%s", stats)

    async def _job_reflection_scan(self) -> None:
        if self._memory_service is None or self._reflection_service is None:
            return
        if self._memories is None:
            return

        try:
            scopes = await self._memories.all_scopes(status="buffered")
        except Exception as exc:
            self._warn("枚举待反思作用域失败：%s", safe_detail(exc))
            return

        if not scopes:
            self._debug("没有待反思的作用域")
            return

        eligible = 0
        skipped: list[str] = []
        for scope_type, scope_id in scopes:
            scope = MemoryScope(ScopeType.parse(scope_type), scope_id)
            try:
                should, reason = await self._reflection_service.should_run(scope)
            except Exception as exc:
                self._warn("反思条件判定失败（%s）：%s", scope.key, safe_detail(exc))
                continue
            if not should:
                skipped.append(f"{scope.key}（{reason}）")
                continue
            eligible += 1
            await self._run_reflection(scope, reason)

        self._log_reflection_scan(eligible, skipped)

    def _log_reflection_scan(self, eligible: int, skipped: list[str]) -> None:
        """输出本次反思扫描的结论（含跳过原因）。

        「为什么没反思」必须是后台可见的事实，而不是只能靠开 ``basic.debug_log`` 猜：
        有作用域达标时逐次记录（反思本身低频），全部跳过时按 ``_REFLECTION_SKIP_LOG_INTERVAL``
        节流，避免每轮扫描都刷同样一行。
        """
        if not skipped:
            self._info("反思扫描：%s 个作用域达标，全部执行", eligible)
            return

        detail = "；".join(skipped[:_REFLECTION_SKIP_DETAIL_MAX])
        if len(skipped) > _REFLECTION_SKIP_DETAIL_MAX:
            detail += f"；…另有 {len(skipped) - _REFLECTION_SKIP_DETAIL_MAX} 个"
        if eligible:
            self._info("反思扫描：%s 个作用域达标、%s 个跳过 → %s", eligible, len(skipped), detail)
            self._reflection_skip_logged_at = time.time()
            return

        now = time.time()
        if now - self._reflection_skip_logged_at < _REFLECTION_SKIP_LOG_INTERVAL:
            self._debug("反思扫描：%s 个作用域全部跳过 → %s", len(skipped), detail)
            return
        self._reflection_skip_logged_at = now
        self._info("反思扫描：%s 个作用域全部跳过 → %s", len(skipped), detail)

    async def _run_reflection(self, scope: MemoryScope, reason: str) -> None:
        assert self._reflection_service is not None
        gate_key = f"reflect:{scope.key}"
        try:
            if self._gate is not None:
                async with self._gate.write(gate_key):
                    outcome = await self._reflection_service.reflect(scope, reason=reason)
            else:
                outcome = await self._reflection_service.reflect(scope, reason=reason)
        except Exception as exc:
            self._warn("反思执行异常（%s）：%s", scope.key, safe_detail(exc))
            return
        if outcome.error:
            self._warn("反思（%s）：%s", scope.key, outcome.summary())
        else:
            self._info("反思（%s）：%s", scope.key, outcome.summary())
        if outcome.produced:
            record(METRIC_MEMORY_WRITES, count=int(outcome.produced))

    async def _job_weekly_insight(self) -> None:
        if self._reflection_service is None or self._journals_repo is None:
            return
        try:
            scopes = await self._journals_repo.all_scopes()
        except Exception as exc:
            self._warn("枚举周记作用域失败：%s", safe_detail(exc))
            return

        for scope_type, scope_id in scopes:
            scope = MemoryScope(ScopeType.parse(scope_type), scope_id)
            try:
                outcome = await self._reflection_service.weekly_reflect(scope)
            except Exception as exc:
                self._warn("周度洞察异常（%s）：%s", scope.key, safe_detail(exc))
                continue
            self._info("周度洞察（%s）：%s", scope.key, outcome.summary())

    async def _job_proactive_daily(self) -> None:
        """计划轨：每天固定时间尝试发起一次主动消息。"""
        await self._run_proactive(TRACK_DAILY)

    async def _job_proactive_idle(self) -> None:
        """空闲轨：会话静默足够久时尝试发起一次主动消息。"""
        await self._run_proactive(TRACK_IDLE)

    async def _run_proactive(self, kind: str) -> None:
        service = self._proactive_service
        if service is None or not self._enabled("proactive.enabled"):
            return
        attempts = await service.run_track(kind)
        sent = [item for item in attempts if item.sent]
        record(METRIC_PROACTIVE_SENT, count=len(sent))
        record(METRIC_PROACTIVE_SKIPPED, count=max(0, len(attempts) - len(sent)))
        if sent:
            self._info("主动交互（%s）：成功发送 %s 个会话", kind, len(sent))

    async def _job_jargon_scan(self) -> None:
        """黑话扫描：逐个「有候选词统计」的会话推断词义。"""
        service = self._persona_service
        if service is None or not self._enabled("persona.jargon"):
            return
        for scope in service.jargon.tracked_scopes():
            try:
                if self._gate is not None:
                    async with self._gate.write(f"jargon:{scope.key}"):
                        outcome = await service.scan_jargon(scope)
                else:
                    outcome = await service.scan_jargon(scope)
            except Exception as exc:  # 单会话失败不影响其它会话
                self._warn("黑话扫描异常（%s）：%s", scope.key, safe_detail(exc))
                continue
            if outcome.ran:
                self._info("黑话扫描（%s）：%s", scope.key, outcome.summary())

    # ------------------------------------------------------------------ #
    # 运行监控埋点
    # ------------------------------------------------------------------ #

    def _on_llm_call(
        self, purpose: str, ok: bool, duration_ms: float, usage: Any, blocked: bool
    ) -> None:
        """LLM 网关回调：调用次数/耗时/token/错误/预算拒绝。"""
        record(METRIC_LLM_CALLS)
        record(METRIC_LLM_LATENCY_MS, total=duration_ms)
        if blocked:
            record(METRIC_LLM_BUDGET_BLOCKED)
        elif not ok:
            record(METRIC_LLM_ERRORS)
        tokens = int(getattr(usage, "total", 0) or 0) if usage is not None else 0
        if tokens:
            record(METRIC_LLM_TOKENS, total=float(tokens))

    def _on_rerank(self, source: str, ok: bool, duration_ms: float, candidates: int) -> None:
        """重排序回调：调用次数/耗时/候选数/失败（失败后已由检索层回退）。"""
        record(METRIC_RERANK_CALLS)
        record(METRIC_RERANK_LATENCY_MS, total=duration_ms)
        if ok:
            record(METRIC_RERANK_CANDIDATES, total=float(candidates))
        else:
            record(METRIC_RERANK_FAILURES)

    def _on_job(self, key: str, ok: bool, duration_ms: float) -> None:
        """调度器回调：任务运行次数/失败次数/耗时。"""
        record(METRIC_SCHEDULER_RUNS)
        record(METRIC_SCHEDULER_DURATION_MS, total=duration_ms)
        if not ok:
            record(METRIC_SCHEDULER_FAILURES)

    def _on_graph_indexed(self, entities: int) -> None:
        """图谱索引回调。"""
        record(METRIC_GRAPH_INDEXED, count=max(0, int(entities)))

    async def _refresh_gauges(self) -> None:
        """刷新 gauge 型指标（当前值，而非累计值）。"""
        if self._memories is not None:
            try:
                total = await self._memories.count_all(status="active")
                record(METRIC_MEMORY_TOTAL, gauge=float(total))
            except Exception as exc:  # 指标刷新失败不影响主流程
                self._debug("刷新记忆总量指标失败：%s", safe_detail(exc))
        if self._reviews_repo is not None:
            try:
                pending = await self._reviews_repo.count_all_pending()
                record(METRIC_REVIEW_PENDING, gauge=float(pending))
            except Exception as exc:
                self._debug("刷新待审指标失败：%s", safe_detail(exc))
        if self._graph_service is not None and self._enabled("graph.enabled"):
            try:
                stats = await self._graph_service.stats()
                record(METRIC_GRAPH_ENTITIES, gauge=float(stats.get("entities") or 0))
            except Exception as exc:
                self._debug("刷新图谱指标失败：%s", safe_detail(exc))

    async def _job_monitor_flush(self) -> None:
        """把内存指标批量落盘（小时桶，分钟级刷新足够）。"""
        service = self._monitor_service
        if service is None:
            return
        await self._refresh_gauges()
        written = await service.flush()
        if written:
            self._debug("运行指标落盘：%s 行", written)

    async def _job_review_auto(self) -> None:
        """自动审核：规则先审，必要时模型兜底。"""
        service = self._auto_review_service
        if service is None or not self._enabled("review.auto"):
            return
        try:
            outcome = await service.run_once()
        except Exception as exc:  # 审核失败不影响其它任务
            self._warn("自动审核异常：%s", safe_detail(exc))
            return
        if outcome.approved:
            record(METRIC_REVIEW_AUTO_APPROVED, count=outcome.approved)
        if outcome.rejected:
            record(METRIC_REVIEW_AUTO_REJECTED, count=outcome.rejected)
        if outcome.scanned:
            self._info("自动审核：%s", outcome.summary())

    # ------------------------------------------------------------------ #
    # 待审队列（统一入口）
    # ------------------------------------------------------------------ #

    async def pending_reviews(
        self,
        scope: MemoryScope,
        *,
        limit: int = 20,
        offset: int = 0,
        origin: str = "",
    ) -> list[dict[str, Any]]:
        """读取待审队列（含反思与拟人化学习两类来源）。"""
        if self._reviews_repo is None:
            return []
        rows = await self._reviews_repo.list_pending(
            retrieval_scopes(scope), limit=limit, offset=offset, origin=origin
        )
        return summarize_reviews(rows)

    async def pending_reviews_all(
        self, *, limit: int = 50, offset: int = 0, origin: str = ""
    ) -> list[dict[str, Any]]:
        """跨作用域读取待审队列（面板默认视角）。"""
        if self._reviews_repo is None:
            return []
        rows = await self._reviews_repo.list_all_pending(limit=limit, offset=offset, origin=origin)
        return summarize_reviews(rows)

    async def pending_count(self, scope: MemoryScope | None = None, *, origin: str = "") -> int:
        """待审总数（面板分页与统计用）。"""
        if self._reviews_repo is None:
            return 0
        if scope is None:
            return await self._reviews_repo.count_all_pending(origin=origin)
        return await self._reviews_repo.count_pending(retrieval_scopes(scope), origin=origin)

    async def pending_origins(self) -> list[str]:
        """待审队列出现过的来源（供面板筛选）。"""
        if self._reviews_repo is None:
            return []
        return await self._reviews_repo.distinct_origins()

    async def approve_review(self, review_id: int) -> tuple[bool, str]:
        """审批一条待审记录：拟人化学习来源由本域落地，其余交给反思域。"""
        service = self._persona_service
        if service is not None:
            handled, message = await service.approve(review_id)
            if handled:
                return True, message
        reflection = self._reflection_service
        if reflection is None:
            return False, ""
        memory_id = await reflection.approve(review_id)
        if memory_id is None:
            return False, ""
        return True, f"已写入记忆 #{memory_id}"

    async def reject_review(self, review_id: int) -> bool:
        """驳回一条待审记录（来源无关：只改状态，不落地）。"""
        if self._reviews_repo is None:
            return False
        return await self._reviews_repo.set_status(review_id, "rejected", at=time.time())

    async def _auto_approve(self, review_id: int) -> tuple[bool, str]:
        """自动审核的批准回调：复用统一审批入口，并补写判定者留痕。"""
        handled, message = await self.approve_review(review_id)
        if handled:
            await self._mark_decided_by(review_id)
        return handled, message

    async def _auto_reject(self, review_id: int) -> bool:
        """自动审核的驳回回调：复用统一驳回入口，并补写判定者留痕。"""
        rejected = await self.reject_review(review_id)
        if rejected:
            await self._mark_decided_by(review_id)
        return rejected

    async def _mark_decided_by(self, review_id: int) -> None:
        if self._reviews_repo is None:
            return
        try:
            await self._reviews_repo.mark_decided_by(review_id, "auto")
        except Exception as exc:  # 留痕失败不影响审批结果
            self._debug("写入自动审核留痕失败：%s", safe_detail(exc))

    # ------------------------------------------------------------------ #
    # 对外状态
    # ------------------------------------------------------------------ #

    def _rerank_status(self) -> dict[str, Any]:
        """重排序能力概览（供命令与面板展示）。"""
        config = self._memory_config
        if config is None:
            return {}
        gateway = self._harness.rerank if self._harness is not None else None
        available = False
        model = ""
        if gateway is not None:
            try:
                available = bool(gateway.available)
                model = gateway.model() if available else ""
            except Exception as exc:  # 状态查询失败不应影响 status
                self._debug("读取重排序状态失败：%s", safe_detail(exc))
        return {
            "enabled": self._enabled("memory.rerank_enabled"),
            "available": available,
            "model": model,
            "provider_id": config.rerank_provider_id,
            "fallback": config.rerank_fallback,
            "candidates": config.rerank_candidates,
            "weight": config.rerank_weight,
            "state": self._retriever.rerank_note if self._retriever is not None else "未启用",
        }

    async def _identity_status(self) -> dict[str, Any]:
        """身份与作用域的现状摘要（``/sab status`` 与面板共用）。"""
        config = self._memory_config
        summary: dict[str, Any] = {
            "scope_type": config.default_scope.value if config else "session",
            "strategy": config.identity_strategy if config else DEFAULT_IDENTITY_STRATEGY,
            "tracking": self._identity_tracking_enabled(),
            "umo_count": 0,
            "hint": "",
        }
        if self._memory_service is None:
            return summary
        try:
            report = await self._memory_service.identity_observations(limit=200)
        except Exception as exc:
            summary["hint"] = f"身份观测读取失败：{safe_detail(exc)}"
            return summary
        analysis = report.get("analysis") or {}
        summary["umo_count"] = len(report.get("items") or [])
        summary["verdict"] = analysis.get("verdict", "empty")
        summary["hint"] = analysis.get("hint", "")
        return summary

    async def status(self, *, umo: str = "") -> dict[str, Any]:
        """汇总运行状态，供命令与面板使用。

        ``umo`` 非空时附带该会话的群聊冷却/配额与主动交互状态（命令侧使用）。
        """
        memory_stats: dict[str, Any] = {}
        if self._memory_service is not None:
            try:
                # 面板/命令没有具体会话时，用全局视角统计。
                memory_stats = await self._memory_service.stats(MemoryScope.global_scope())
            except Exception as exc:
                memory_stats = {"error": safe_detail(exc)}

        scheduler = self._scheduler.snapshot() if self._scheduler is not None else []
        budget = self._budget.snapshot() if self._budget is not None else {}
        group = self._group_service.snapshot() if self._group_service is not None else {}
        proactive = (
            self._proactive_service.snapshot() if self._proactive_service is not None else {}
        )
        persona: dict[str, Any] = {}
        if self._persona_service is not None:
            persona = self._persona_service.snapshot()
            try:
                counts = await self._persona_service.stats()
                persona["counts"] = {
                    "style": counts.style,
                    "jargon": counts.jargon,
                    "affinity": counts.affinity,
                }
            except Exception as exc:
                persona["counts"] = {"error": safe_detail(exc)}
        graph: dict[str, Any] = {}
        if self._graph_service is not None:
            graph = {"enabled": self._enabled("graph.enabled")}
            try:
                graph.update(await self._graph_service.stats())
            except Exception as exc:
                graph["error"] = safe_detail(exc)
        review: dict[str, Any] = {}
        if self._auto_review_service is not None:
            review = {
                "enabled": self._enabled("review.auto"),
                "use_llm": bool(self._review_config and self._review_config.use_llm),
            }
            try:
                review.update(await self._auto_review_service.stats())
            except Exception as exc:
                review["error"] = safe_detail(exc)
        monitor: dict[str, Any] = {}
        if self._monitor_service is not None:
            monitor = await self._monitor_service.snapshot()
        maibot = self._maibot_service.snapshot() if self._maibot_service is not None else {}
        rerank = self._rerank_status()
        identity = await self._identity_status()
        if umo:
            if self._group_service is not None:
                group = {**group, "session": self._group_service.session_snapshot(umo)}
            if self._proactive_service is not None:
                proactive = {
                    **proactive,
                    "session": await self._proactive_service.session_snapshot(umo),
                }
            if self._persona_service is not None:
                persona = {
                    **persona,
                    "session": await self._persona_service.session_counts(umo),
                }
        return {
            "ready": self._ready,
            "plugin_version": __version__,
            "framework": compat.diagnostics(),
            "capabilities": dict(self._effective_capabilities),
            "degraded": [
                {"capability": key, "reason": reason} for key, reason in self._degraded_reasons
            ],
            "memory": memory_stats,
            "scheduler": scheduler,
            "budget": budget,
            "context": (
                self._context_governor.snapshot() if self._context_governor is not None else {}
            ),
            "group": group,
            "proactive": proactive,
            "persona": persona,
            "graph": graph,
            "review": review,
            "monitor": monitor,
            "maibot": maibot,
            "rerank": rerank,
            "identity": identity,
            "pending_tasks": self._scope.pending_count() if self._scope is not None else 0,
            "database": str(self._db.path) if self._db is not None else "",
            "fts": bool(self._db.fts_available) if self._db is not None else False,
        }

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #

    def _report_startup(self) -> None:
        enabled = [key for key, value in self._effective_capabilities.items() if value]
        self._info("Super_AstrBot 已就绪；生效能力：%s", "、".join(enabled) or "无")
        for key, reason in self._degraded_reasons:
            self._info("能力未生效：%s（%s）", key, reason)
        if PromptOverrides.REJECTED:
            self._warn(
                "以下自定义提示词因缺少必填占位符被忽略，已回退内置默认：%s",
                "、".join(sorted(PromptOverrides.REJECTED)),
            )
        if self._enabled("group.enabled") and not GROUP_FILTER_AVAILABLE:
            self._warn(
                "群聊语义已开启，但当前 AstrBot 缺少 custom_filter/EventMessageType 符号，"
                "该能力不会生效（其余功能不受影响）。"
            )

    def _info(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.info(message, *args)

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)

    def _error(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.error(message, *args)

    def _debug(self, message: str, *args: Any) -> None:
        if self._logger is None:
            return
        from .spec.capabilities import get_path

        if not get_path(self._config, "basic.debug_log", False):
            return
        self._logger.debug(message, *args)


class _NullGate:
    """``async with`` 占位：无门闸时保持调用形状一致。"""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False
