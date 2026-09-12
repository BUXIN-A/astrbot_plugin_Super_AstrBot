"""应用容器：依赖装配、生命周期编排与钩子动作。

职责边界：

- **只做装配与编排**，不实现业务规则（规则在各自领域服务里）；
- 对 ``main.py`` 暴露少量稳定方法（``start`` / ``shutdown`` / ``on_llm_request`` /
  ``on_after_message_sent`` / ``status``），使插件入口保持极薄；
- 所有后台工作都经 ``TaskScope`` / ``Scheduler``，保证 ``shutdown`` 能完全收敛。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Mapping

from . import __version__
from .context import ContextConfig, ContextGovernor
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
from .journal import JournalConfig, JournalService
from .learning import ReflectionConfig, ReflectionService
from .loop import ConcurrencyGate, LLMBudget, Scheduler, TaskScope
from .memory import (
    AgentMemoryBackend,
    HybridRetriever,
    KeywordRetriever,
    MemoryConfig,
    MemoryLifecycle,
    MemoryService,
    VectorRetriever,
)
from .persona import PersonaConfig, PersonaService, summarize_reviews
from .proactive import (
    TRACK_DAILY,
    TRACK_IDLE,
    MemoryMaterialSource,
    ProactiveConfig,
    ProactiveService,
)
from .spec.capabilities import explain_disabled, resolve_capabilities
from .spec.errors import StorageError, safe_detail
from .spec.scopes import MemoryScope, ScopeType, retrieval_scopes
from .storage import (
    CURRENT_VERSION,
    AffinityRepository,
    Database,
    JargonRepository,
    JournalRepository,
    MemoryRepository,
    ReflectionRepository,
    ReviewRepository,
    SqliteStateStore,
    StyleRepository,
    VectorRepository,
)
from .support import truncate

_DB_FILENAME = "super_astrbot.db"
_SKIP_CAPTURE_PREFIXES = ("/", "!", "#", ".")
"""命令类消息不进入记忆缓冲，避免把指令当成对话语料。"""

_REFLECTION_SCAN_INTERVAL = 300.0
"""反思扫描间隔（秒）：只查条件是否满足，满足才真正调用模型。"""

_MAINTENANCE_HOUR, _MAINTENANCE_MINUTE = 4, 30

_SCHEMA_SYNC_START_DELAY = 5.0
"""启动后延迟再同步 schema：给 ProviderManager 留出加载提供商的时间。"""

_SCHEMA_SYNC_INTERVAL = 600.0
"""schema 同步间隔（秒）。保存插件配置会触发重载并重建 AstrBotConfig，
因此运行时注入的下拉选项会丢失，必须周期性重新注入。"""

_EMBEDDING_FIELD_PATH = ("memory", "embedding_provider_id")
"""需要动态注入选项的配置字段路径。"""

_JOB_PROACTIVE_DAILY = "proactive-daily"
_JOB_PROACTIVE_IDLE = "proactive-idle"
_PROACTIVE_JOB_TIMEOUT = 300.0
"""主动交互任务超时（秒）：逐个会话生成 + 发送，给足余量但不无限占用。"""

_JOB_JARGON_SCAN = "persona-jargon-scan"
_JARGON_JOB_TIMEOUT = 300.0
"""黑话扫描任务超时（秒）：一次批量推断 + 若干次写入。"""

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
        self._config: Mapping[str, Any] = config or {}
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

        self._memories: MemoryRepository | None = None
        self._journals_repo: JournalRepository | None = None
        self._reflections_repo: ReflectionRepository | None = None
        self._reviews_repo: ReviewRepository | None = None
        self._style_repo: StyleRepository | None = None
        self._jargon_repo: JargonRepository | None = None
        self._affinity_repo: AffinityRepository | None = None

        self._memory_service: MemoryService | None = None
        self._journal_service: JournalService | None = None
        self._reflection_service: ReflectionService | None = None
        self._context_governor: ContextGovernor | None = None
        self._group_service: GroupChatService | None = None
        self._proactive_service: ProactiveService | None = None
        self._persona_service: PersonaService | None = None
        self._group_gate: Any | None = None
        """当前注入给 harness 的门控闭包（卸载时按对象身份清除，避免误清新实例）。"""

        self._style_pairs: dict[str, tuple[str, float]] = {}
        """会话最近一条用户消息（umo → 文本/时间），用于与 Bot 回复配对成风格样本。"""

        self._effective_capabilities: dict[str, bool] = {}
        self._retriever: Any | None = None

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
    def context_config(self) -> ContextConfig | None:
        return self._context_config

    @property
    def group_service(self) -> GroupChatService | None:
        return self._group_service

    @property
    def proactive_service(self) -> ProactiveService | None:
        return self._proactive_service

    @property
    def persona_service(self) -> PersonaService | None:
        return self._persona_service

    @property
    def persona_config(self) -> PersonaConfig | None:
        return self._persona_config

    @property
    def host(self) -> Any:
        return self._harness.host if self._harness is not None else None

    def _enabled(self, key: str) -> bool:
        return bool(self._effective_capabilities.get(key, False))

    def _persona_any(self) -> bool:
        return any(
            self._enabled(key) for key in ("persona.style", "persona.jargon", "persona.affinity")
        )

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """装配全部组件并启动调度。任何子系统失败都降级而非抛出。"""
        if self._started:
            return
        self._started = True

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
            except Exception as exc:  # noqa: BLE001
                self._warn("关闭调度器失败：%s", safe_detail(exc))
            self._scheduler = None

        if self._scope is not None:
            try:
                cancelled = await self._scope.cancel_all(timeout=5.0)
                if cancelled:
                    self._info("已收敛 %s 个后台任务", cancelled)
            except Exception as exc:  # noqa: BLE001
                self._warn("收敛后台任务失败：%s", safe_detail(exc))
            self._scope = None

        if self._persona_service is not None:
            # 候选词计数保存在内存里，卸载前必须落盘，否则重载即归零。
            try:
                await self._persona_service.flush()
            except Exception as exc:  # noqa: BLE001
                self._warn("拟人化学习进度落盘失败：%s", safe_detail(exc))
            self._persona_service = None

        if self._db is not None:
            try:
                await self._db.close()
            except Exception as exc:  # noqa: BLE001
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
        self._sync_derived_configs()

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
        except Exception as exc:  # noqa: BLE001 - 探测失败按不可用处理
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
        if self._journal_config is not None:
            self._journal_config.enabled = self._enabled("journal.enabled")
        if self._reflection_config is not None:
            self._reflection_config.enabled = self._enabled("reflection.enabled")
        if self._context_config is not None:
            self._context_config.enabled = self._enabled("context.governance")
        if self._group_config is not None:
            self._group_config.enabled = self._enabled("group.enabled")
        if self._proactive_config is not None:
            self._proactive_config.enabled = self._enabled("proactive.enabled")
        if self._persona_config is not None:
            self._persona_config.style.enabled = self._enabled("persona.style")
            self._persona_config.jargon.enabled = self._enabled("persona.jargon")
            self._persona_config.affinity.enabled = self._enabled("persona.affinity")
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

    def _sync_scheduler_jobs(self) -> None:
        """按能力开关启停定时任务。"""
        if self._scheduler is None:
            return
        self._scheduler.set_enabled(
            "memory-maintenance",
            self._enabled("memory.enabled") or self._enabled("persona.style"),
        )
        self._scheduler.set_enabled("reflection-scan", self._enabled("reflection.enabled"))
        self._scheduler.set_enabled("weekly-insight", self._enabled("journal.weekly_reflection"))
        self._sync_proactive_jobs()
        self._sync_persona_jobs()

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
            self._harness.embedding.refresh()

        overrides = self._probe_runtime_overrides()
        self._capability_overrides = overrides
        self._effective_capabilities = resolve_capabilities(self._config, overrides)
        self._degraded_reasons = explain_disabled(self._effective_capabilities, overrides)

        self._sync_derived_configs()
        self._sync_retriever_routes()
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
        except Exception as exc:  # noqa: BLE001 - 复检失败不影响主流程
            self._debug("框架加载后的能力复检失败：%s", safe_detail(exc))

    # ------------------------------------------------------------------ #
    # 功能开关（控制台）
    # ------------------------------------------------------------------ #

    def feature_catalog(self) -> list[dict[str, Any]]:
        """列出全部功能及其状态，供控制台渲染开关。"""
        from .spec.capabilities import CAPABILITIES, as_bool, get_path

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
                }
            )
        return catalog

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
            except Exception as exc:  # noqa: BLE001
                self._warn("保存配置失败：%s", safe_detail(exc))
                return False
        sync_saver = getattr(self._config, "save_config", None)
        if callable(sync_saver):
            try:
                await asyncio.to_thread(sync_saver)
                return True
            except Exception as exc:  # noqa: BLE001
                self._warn("保存配置失败：%s", safe_detail(exc))
                return False
        return False

    async def _setup_storage(self) -> bool:
        if (
            not self._enabled("memory.enabled")
            and not self._enabled("journal.enabled")
            and not self._persona_any()
        ):
            self._info("记忆、周记与拟人化学习均未启用，跳过持久层初始化。")
            return False

        try:
            from .harness.astrbot_host import AstrBotHost

            host = AstrBotHost(
                self._star, self._context, self._config, data_dir=self._data_dir_override
            )
            data_dir: Path = host.data_dir()
        except Exception as exc:  # noqa: BLE001
            self._error("解析数据目录失败，持久化能力不可用：%s", safe_detail(exc))
            return False

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
        self._reflections_repo = ReflectionRepository(db)
        self._reviews_repo = ReviewRepository(db)
        self._style_repo = StyleRepository(db)
        self._jargon_repo = JargonRepository(db)
        self._affinity_repo = AffinityRepository(db)
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
            )
        except Exception as exc:  # noqa: BLE001 - Harness 失败则整体不可用
            self._error("Harness 初始化失败：%s", safe_detail(exc))
            return False

        self._info("Harness 就绪：%s", self._harness.describe())
        return True

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
        self._scheduler = Scheduler(self._scope, store=self._state_store, logger=self._logger)
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

        retriever = HybridRetriever(
            routes=routes,
            memories=self._memories,
            config=self._memory_config.retrieval_config(),
            logger=self._logger,
        )
        # 保存引用：功能开关热切换时需要增删检索路
        self._retriever = retriever
        lifecycle = MemoryLifecycle(
            db=self._db,
            memories=self._memories,
            vectors=VectorRepository(self._db),
            embedding=self._harness.embedding,
            config=self._memory_config,
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
            logger=self._logger,
        )
        self._journal_service = JournalService(
            config=self._journal_config,
            journals=self._journals_repo,
            memory_service=self._memory_service,
            logger=self._logger,
        )
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
        self._persona_service = PersonaService(
            config=self._persona_config,
            patterns=self._style_repo,
            jargons=self._jargon_repo,
            affinities=self._affinity_repo,
            reviews=self._reviews_repo,
            llm=self._harness.llm,
            injector=self._harness.persona_injector,
            store=self._state_store,
            clock=self._harness.host.now,
            logger=self._logger,
        )
        self._sync_group_gate()

    async def _start_background(self) -> None:
        assert self._scheduler is not None and self._memory_service is not None

        # 1) 修复中断写入（补齐索引/向量）
        try:
            repaired = await self._memory_service.repair()
            if repaired:
                self._info("启动修复完成：%s 条", repaired)
        except Exception as exc:  # noqa: BLE001
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

        # 5) 主动交互双轨（能力开启时才注册）
        self._sync_proactive_jobs()

        # 6) 黑话扫描（能力开启时才注册）
        self._sync_persona_jobs()

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
        except Exception as exc:  # noqa: BLE001 - 注册失败不影响核心能力
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
        except Exception as exc:  # noqa: BLE001
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
        except Exception as exc:  # noqa: BLE001
            self._debug("枚举嵌入提供商失败：%s", safe_detail(exc))
            return []

    def sync_schema_options(self) -> bool:
        """把真实嵌入模型列表注入插件配置 schema 的下拉选项。

        AstrBot 的 ``_special: "select_provider"`` 只列对话模型且无法按类型过滤，
        因此这里改用运行时注入。三种情形：

        - 有嵌入提供商 → 注入 ``options``，字段渲染为下拉框；
        - 无嵌入提供商 → **移除** ``options``，字段退回文本框，用户可手填 ID；
        - 注入失败（如 schema 结构不符）→ 静默返回 ``False``，不影响功能。

        第 2 条很关键：如果注入一个只有「自动选择」的空列表，字段会变成无法输入的下拉框，
        反而把用户锁死。
        """
        from .spec.capabilities import get_path

        providers = self.embedding_providers()
        if not providers:
            clear_options(self._config, _EMBEDDING_FIELD_PATH)
            return False

        options = [""]
        labels = ["（自动选择第一个可用的嵌入模型）"]
        for info in providers:
            options.append(info.id)
            labels.append(f"{info.id} · {info.model}" if info.model else info.id)

        current = str(get_path(self._config, "memory.embedding_provider_id", "") or "")
        if current and current not in options:
            # 保留「已配置但当前不可用」的值，避免下拉框把用户设置清空
            options.append(current)
            labels.append(f"{current}（当前不可用）")

        try:
            injected = inject_string_options(self._config, _EMBEDDING_FIELD_PATH, options, labels)
        except Exception as exc:  # noqa: BLE001
            self._debug("注入配置下拉选项失败：%s", safe_detail(exc))
            return False
        if injected:
            self._debug("已注入 %s 个嵌入模型选项", len(providers))
        return injected

    async def _schema_sync_loop(self) -> None:
        """周期性重新注入：保存插件配置会触发插件重载并重建 AstrBotConfig。"""
        await asyncio.sleep(_SCHEMA_SYNC_START_DELAY)
        while self._scope is not None and not self._scope.is_stopped():
            try:
                self.sync_schema_options()
            except Exception as exc:  # noqa: BLE001
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

        scope = self._scope_for(view)
        try:
            result = await self._memory_service.recall(scope, view.text)
        except Exception as exc:  # noqa: BLE001 - 召回失败不影响对话
            self._warn("记忆召回失败：%s", safe_detail(exc))
            return

        if not result.items:
            self._debug("未召回记忆（%s）", result.route_summary)
            return

        try:
            inject_result = await self._memory_service.inject(request, result)
        except Exception as exc:  # noqa: BLE001
            self._warn("记忆注入失败：%s", safe_detail(exc))
            return

        if inject_result.applied:
            self._debug(
                "注入 %s 条记忆（%s，%s 字符，%s）",
                len(result.items),
                inject_result.method,
                inject_result.chars,
                result.route_summary,
            )
        else:
            self._debug("记忆未注入：%s", inject_result.reason)

    async def _govern_context(self, view: EventView, request: Any) -> None:
        """请求级上下文治理：仅在估算超过阈值时动手。"""
        governor = self._context_governor
        if governor is None or not self._enabled("context.governance"):
            return
        try:
            result = await governor.govern(request, session_key=view.umo)
        except Exception as exc:  # noqa: BLE001 - 治理失败不影响对话
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
            except Exception as exc:  # noqa: BLE001 - 学习失败不影响对话
                self._warn("拟人化学习观测失败：%s", safe_detail(exc))

        self._scope.spawn(_task(), name="persona-observe")

    async def _inject_persona(self, view: EventView, request: Any) -> None:
        """把风格示例 / 黑话含义 / 关系语气写成本次请求的临时内容块。"""
        service = self._persona_service
        if service is None or not self._persona_any():
            return
        try:
            detail = await service.inject(request, view)
        except Exception as exc:  # noqa: BLE001 - 注入失败不影响对话
            self._warn("拟人化学习注入失败：%s", safe_detail(exc))
            return
        if detail:
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
        except Exception as exc:  # noqa: BLE001
            self._warn("风格学习失败：%s", safe_detail(exc))
            return
        if outcome.stored or outcome.pending:
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
        except Exception as exc:  # noqa: BLE001 - 决策失败按框架原判定处理
            self._warn("群聊语义决策异常：%s", safe_detail(exc))
            return

        if decision.action == "silent":
            self._debug("群聊静默（%s）：%s", view.umo, decision.reason)
        elif decision.action == "interject":
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
        self._queue_buffer(view, f"助手：{truncate(reply, 500)}")

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
        """
        if self._scope is None or self._memory_service is None:
            return
        scope = self._scope_for(view)
        timestamp = view.timestamp or time.time()

        async def _task() -> None:
            try:
                await self._memory_service.buffer_episode(scope, line, now=timestamp)
            except Exception as exc:  # noqa: BLE001
                self._warn("写入对话缓冲失败：%s", safe_detail(exc))

        self._scope.spawn(_task(), name="buffer")

    def _scope_for(self, view: EventView) -> MemoryScope:
        scope_type = self._memory_config.default_scope if self._memory_config else ScopeType.SESSION
        return MemoryScope.from_event(scope_type, umo=view.umo, user_id=view.sender_id or "unknown")

    # ------------------------------------------------------------------ #
    # 调度任务
    # ------------------------------------------------------------------ #

    async def _job_maintenance(self) -> None:
        stats: dict[str, Any] = {}
        if self._memory_service is not None and self._enabled("memory.enabled"):
            async with self._gate.write("__maintenance__") if self._gate else _NullGate():
                stats = await self._memory_service.maintain()
        if self._persona_service is not None and self._enabled("persona.style"):
            stats["persona"] = await self._persona_service.maintain()
        self._info("每日维护完成：%s", stats)

    async def _job_reflection_scan(self) -> None:
        if self._memory_service is None or self._reflection_service is None:
            return
        if self._memories is None:
            return

        try:
            scopes = await self._memories.all_scopes(status="buffered")
        except Exception as exc:  # noqa: BLE001
            self._warn("枚举待反思作用域失败：%s", safe_detail(exc))
            return

        for scope_type, scope_id in scopes:
            scope = MemoryScope(ScopeType.parse(scope_type), scope_id)
            try:
                should, reason = await self._reflection_service.should_run(scope)
            except Exception as exc:  # noqa: BLE001
                self._warn("反思条件判定失败（%s）：%s", scope.key, safe_detail(exc))
                continue
            if not should:
                self._debug("反思跳过（%s）：%s", scope.key, reason)
                continue
            await self._run_reflection(scope, reason)

    async def _run_reflection(self, scope: MemoryScope, reason: str) -> None:
        assert self._reflection_service is not None
        gate_key = f"reflect:{scope.key}"
        try:
            if self._gate is not None:
                async with self._gate.write(gate_key):
                    outcome = await self._reflection_service.reflect(scope, reason=reason)
            else:
                outcome = await self._reflection_service.reflect(scope, reason=reason)
        except Exception as exc:  # noqa: BLE001
            self._warn("反思执行异常（%s）：%s", scope.key, safe_detail(exc))
            return
        self._info("反思（%s）：%s", scope.key, outcome.summary())

    async def _job_weekly_insight(self) -> None:
        if self._reflection_service is None or self._journals_repo is None:
            return
        try:
            scopes = await self._journals_repo.all_scopes()
        except Exception as exc:  # noqa: BLE001
            self._warn("枚举周记作用域失败：%s", safe_detail(exc))
            return

        for scope_type, scope_id in scopes:
            scope = MemoryScope(ScopeType.parse(scope_type), scope_id)
            try:
                outcome = await self._reflection_service.weekly_reflect(scope)
            except Exception as exc:  # noqa: BLE001
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
            except Exception as exc:  # noqa: BLE001 - 单会话失败不影响其它会话
                self._warn("黑话扫描异常（%s）：%s", scope.key, safe_detail(exc))
                continue
            if outcome.ran:
                self._info("黑话扫描（%s）：%s", scope.key, outcome.summary())

    # ------------------------------------------------------------------ #
    # 待审队列（统一入口）
    # ------------------------------------------------------------------ #

    async def pending_reviews(self, scope: MemoryScope, *, limit: int = 20) -> list[dict[str, Any]]:
        """读取待审队列（含反思与拟人化学习两类来源）。"""
        if self._reviews_repo is None:
            return []
        rows = await self._reviews_repo.list_pending(retrieval_scopes(scope), limit=limit)
        return summarize_reviews(rows)

    async def pending_reviews_all(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """跨作用域读取待审队列（面板默认视角）。"""
        if self._reviews_repo is None:
            return []
        rows = await self._reviews_repo.list_all_pending(limit=limit)
        return summarize_reviews(rows)

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

    # ------------------------------------------------------------------ #
    # 对外状态
    # ------------------------------------------------------------------ #

    async def status(self, *, umo: str = "") -> dict[str, Any]:
        """汇总运行状态，供命令与面板使用。

        ``umo`` 非空时附带该会话的群聊冷却/配额与主动交互状态（命令侧使用）。
        """
        memory_stats: dict[str, Any] = {}
        if self._memory_service is not None:
            try:
                # 面板/命令没有具体会话时，用全局视角统计。
                memory_stats = await self._memory_service.stats(MemoryScope.global_scope())
            except Exception as exc:  # noqa: BLE001
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
            except Exception as exc:  # noqa: BLE001
                persona["counts"] = {"error": safe_detail(exc)}
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
