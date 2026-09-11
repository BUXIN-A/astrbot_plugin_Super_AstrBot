"""应用容器：依赖装配、生命周期编排与钩子动作。

职责边界：

- **只做装配与编排**，不实现业务规则（规则在各自领域服务里）；
- 对 ``main.py`` 暴露少量稳定方法（``start`` / ``shutdown`` / ``on_llm_request`` /
  ``on_after_message_sent`` / ``status``），使插件入口保持极薄；
- 所有后台工作都经 ``TaskScope`` / ``Scheduler``，保证 ``shutdown`` 能完全收敛。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping

from . import __version__
from .harness import Harness, compat, create_harness, to_event_view
from .harness.protocols import EventView
from .journal import JournalConfig, JournalService
from .learning import ReflectionConfig, ReflectionService
from .loop import ConcurrencyGate, LLMBudget, Scheduler, TaskScope
from .memory import (
    HybridRetriever,
    KeywordRetriever,
    MemoryConfig,
    MemoryLifecycle,
    MemoryService,
    VectorRetriever,
)
from .spec.capabilities import explain_disabled, resolve_capabilities
from .spec.errors import StorageError, safe_detail
from .spec.scopes import MemoryScope, ScopeType
from .storage import (
    CURRENT_VERSION,
    Database,
    JournalRepository,
    MemoryRepository,
    ReflectionRepository,
    ReviewRepository,
    SqliteStateStore,
    VectorRepository,
)
from .support import truncate

_DB_FILENAME = "super_astrbot.db"
_SKIP_CAPTURE_PREFIXES = ("/", "!", "#", ".")
"""命令类消息不进入记忆缓冲，避免把指令当成对话语料。"""

_REFLECTION_SCAN_INTERVAL = 300.0
"""反思扫描间隔（秒）：只查条件是否满足，满足才真正调用模型。"""

_MAINTENANCE_HOUR, _MAINTENANCE_MINUTE = 4, 30


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

        self._memories: MemoryRepository | None = None
        self._journals_repo: JournalRepository | None = None
        self._reflections_repo: ReflectionRepository | None = None
        self._reviews_repo: ReviewRepository | None = None

        self._memory_service: MemoryService | None = None
        self._journal_service: JournalService | None = None
        self._reflection_service: ReflectionService | None = None

        self._effective_capabilities: dict[str, bool] = {}

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
    def host(self) -> Any:
        return self._harness.host if self._harness is not None else None

    def _enabled(self, key: str) -> bool:
        return bool(self._effective_capabilities.get(key, False))

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

        self._ready = True
        self._report_startup()

    async def shutdown(self) -> None:
        """收敛全部资源：调度 → 任务 → 数据库。"""
        self._ready = False
        self._started = False

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
        from .spec.capabilities import get_path

        # 运行环境探测：决定向量能力是否可用（分层自适应的关键）。
        overrides: dict[str, bool] = {}
        embedding_provider_id = str(
            get_path(self._config, "memory.embedding_provider_id", "") or ""
        )
        if self._context is not None:
            try:
                from .harness.astrbot_host import AstrBotHost
                from .harness.astrbot_llm import AstrBotEmbeddingGateway

                probe_host = AstrBotHost(self._star, self._context, self._config)
                probe = AstrBotEmbeddingGateway(
                    self._context, probe_host, provider_id=embedding_provider_id
                )
                if not probe.available:
                    overrides["memory.vector_enabled"] = False
            except Exception as exc:  # noqa: BLE001 - 探测失败按不可用处理
                overrides["memory.vector_enabled"] = False
                self._warn("向量能力探测失败，按不可用处理：%s", safe_detail(exc))
        else:
            overrides["memory.vector_enabled"] = False

        self._effective_capabilities = resolve_capabilities(self._config, overrides)
        self._degraded_reasons = explain_disabled(self._effective_capabilities, overrides)

        self._memory_config = MemoryConfig.from_mapping(self._config)
        self._journal_config = JournalConfig.from_mapping(self._config)
        self._reflection_config = ReflectionConfig.from_mapping(self._config)

        # 让配置层的开关与能力解析结果保持一致（能力解析可能因环境降级）。
        self._memory_config.vector_enabled = self._enabled("memory.vector_enabled")
        self._memory_config.fts_enabled = self._enabled("memory.fts_enabled")

    async def _setup_storage(self) -> bool:
        if not self._enabled("memory.enabled") and not self._enabled("journal.enabled"):
            self._info("记忆与周记均未启用，跳过持久层初始化。")
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
                    self._memories,
                    max_scan=self._memory_config.vector_max_scan,
                    logger=self._logger,
                )
            )

        retriever = HybridRetriever(
            routes=routes,
            memories=self._memories,
            config=self._memory_config.retrieval_config(),
            logger=self._logger,
        )
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

        await self._scheduler.start()
        self._info(
            "后台调度已启动：%s", "、".join(job["key"] for job in self._scheduler.snapshot())
        )

    # ------------------------------------------------------------------ #
    # 钩子动作
    # ------------------------------------------------------------------ #

    async def on_llm_request(self, event: Any, request: Any) -> None:
        """LLM 请求钩子：召回并注入记忆。"""
        if not self._ready or self._memory_service is None or request is None:
            return
        if not self._enabled("memory.enabled"):
            return

        view = to_event_view(event)
        if view.stopped or not view.text.strip():
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

    async def on_after_message_sent(self, event: Any) -> None:
        """消息发送后钩子：把 Bot 回复放入对话缓冲，作为反思原料。"""
        if not self._ready or not self._enabled("memory.capture"):
            return
        from .harness.astrbot_event import extract_result_text

        view = to_event_view(event)
        text = extract_result_text(event)
        if not text.strip():
            return
        self._queue_buffer(view, f"助手：{truncate(text.strip(), 500)}")

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
        if self._memory_service is None:
            return
        async with self._gate.write("__maintenance__") if self._gate else _null_gate():
            stats = await self._memory_service.maintain()
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

    # ------------------------------------------------------------------ #
    # 对外状态
    # ------------------------------------------------------------------ #

    async def status(self) -> dict[str, Any]:
        """汇总运行状态，供命令与面板使用。"""
        memory_stats: dict[str, Any] = {}
        if self._memory_service is not None:
            try:
                # 面板/命令没有具体会话时，用全局视角统计。
                memory_stats = await self._memory_service.stats(MemoryScope.global_scope())
            except Exception as exc:  # noqa: BLE001
                memory_stats = {"error": safe_detail(exc)}

        scheduler = self._scheduler.snapshot() if self._scheduler is not None else []
        budget = self._budget.snapshot() if self._budget is not None else {}
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


class _null_gate:
    """``async with`` 占位：无门闸时保持调用形状一致。"""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False
