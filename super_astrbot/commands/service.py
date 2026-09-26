"""命令层：把插件能力暴露为聊天指令。

设计要点：

- 本模块**不导入 AstrBot**，只接收 ``EventView``（纯数据）并返回字符串；
  指令的注册与参数解析由 ``main.py`` 负责，从而让命令逻辑可被单元测试直接驱动；
- 所有命令都做权限校验（是否管理员）与异常兜底，绝不让命令抛异常打断会话。
"""

from __future__ import annotations

import time
from typing import Any, Sequence

from ..harness.protocols import EventView
from ..memory import KIND_FACT, SOURCE_MANUAL, MemoryIdentity
from ..spec.capabilities import get_path
from ..spec.entry_types import (
    ENTRY_TYPE_DIARY,
    ENTRY_TYPE_ESSAY,
    ENTRY_TYPE_WEEKLY,
    entry_type_label,
    is_entry_type,
    normalize_entry_type,
)
from ..spec.errors import safe_detail
from ..spec.scopes import MemoryScope, ScopeType
from .parser import parse_journal_payload

HELP_TEXT = """Super_AstrBot 指令（别名 /superastrbot，等价于 /sab）：
/sab status          查看运行状态、框架诊断与能力开关
/sab search <关键词>  检索记忆（含检索路、耗时与打分明细）
/sab why <关键词>     同 search，调参时查看打分构成
/sab remember <内容>  手动写入一条长期记忆
现实桥（现实记忆，三类文本共用一条写入链路）：
/sab journal <内容>   写一条周记（别名：周记 / 现实桥 / 桥）
/sab diary <内容>     写一条日记（别名：日记）
/sab essay <内容>     写一条随笔（别名：随笔 / 随记）
  以上三类的完整写法：/sab 日记 <标题> | <正文> [#标签]
  标题留空时自动使用当天日期时间（例 2015061517:00）
/sab journals [周记|日记|随笔] [条数]  查看最近记录（默认 5 条）
/sab edit <编号> [周记|日记|随笔] [标题 | ] 正文 [#标签]  修改一条记录
/sab review          查看待审记忆
/sab approve <编号>   批准一条待审记录
/sab reject <编号>    驳回一条待审记录
/sab persona [类型]   查看拟人化学习概况；类型可选 style / jargon / affinity
/sab graph [UMO]     查看记忆知识图谱概况；可选指定会话
/sab reset confirm   清空当前会话的记忆与缓冲（不可逆）
/sab reindex         重建检索索引
/sab quiet [on|off]  暂停/恢复本会话的主动消息（免打扰）
/sab help            显示本帮助

可视化管理：AstrBot 插件详情页 → Pages → dashboard（「现实桥」页）"""

class CommandService:
    """指令门面。"""

    def __init__(self, *, app: Any, config: Any) -> None:
        self._app = app
        self._config = config or {}

    # ------------------------------------------------------------------ #
    # 公共辅助
    # ------------------------------------------------------------------ #

    def admin_only(self) -> bool:
        return bool(get_path(self._config, "basic.admin_only_commands", True))

    def _scope(self, view: EventView) -> MemoryScope:
        """与对话链路共用同一份作用域解析（含身份策略）。

        这里曾直接用 ``sender_id`` 兜底，而对话链路走 ``identity_strategy``：
        一旦把身份策略切成 auto / sender_name（ID 不稳定的平台），指令就会落到
        另一个作用域，表现为「明明记过，指令却查不到」。
        """
        resolver = getattr(self._app, "memory_scope_for", None)
        if callable(resolver):
            return resolver(view)
        config = self._app.memory_config
        scope_type = config.default_scope if config is not None else ScopeType.SESSION
        return MemoryScope.from_event(scope_type, umo=view.umo, user_id=view.sender_id or "unknown")

    def _permission_error(self) -> str:
        return "该指令仅管理员可用。可在插件配置中关闭「管理命令仅管理员可用」。"

    def _not_ready(self) -> str:
        return "插件尚未就绪（可能正在初始化或持久层不可用），请稍后再试。"

    # ------------------------------------------------------------------ #
    # 指令实现
    # ------------------------------------------------------------------ #

    async def help_text(self) -> str:
        return HELP_TEXT

    async def status(self, view: EventView) -> str:
        if self._app.ready is False and self._app.host is None:
            return self._not_ready()
        try:
            data = await self._app.status(umo=view.umo)
        except Exception as exc:
            return f"获取状态失败：{safe_detail(exc)}"

        caps = data.get("capabilities") or {}
        enabled = [key for key, value in caps.items() if value]
        framework = data.get("framework") or {}
        lines = [
            "Super_AstrBot 状态",
            f"- 版本：插件 {data.get('plugin_version') or '未知'}"
            f"／AstrBot {framework.get('version') or '未知'}",
            f"- 就绪：{'是' if data.get('ready') else '否'}",
            f"- 生效能力：{('、'.join(enabled)) or '无'}",
            f"- 数据库：{data.get('database') or '未初始化'}（FTS={'可用' if data.get('fts') else '降级'}）",
            f"- 后台任务：{data.get('pending_tasks', 0)}",
        ]
        missing_symbols = framework.get("missing") or []
        if missing_symbols:
            lines.append(f"- 框架符号缺失（相关能力已降级）：{'、'.join(missing_symbols)}")

        memory = data.get("memory") or {}
        if memory:
            lines.append(
                "- 记忆：正式 {active} 条／缓冲 {buffered} 条／现实桥 {journals} 条"
                "（检索路：{routes}）".format(
                    active=memory.get("active", 0),
                    buffered=memory.get("buffered", 0),
                    journals=memory.get("journals", 0),
                    routes="、".join(memory.get("routes") or []) or "无",
                )
            )

        identity = data.get("identity") or {}
        if identity:
            lines.append(
                "- 身份：作用域 {scope}（策略 {strategy}），已观测会话 {umo_count} 个：{hint}".format(
                    scope=identity.get("scope_type") or "session",
                    strategy=identity.get("strategy") or "sender_id",
                    umo_count=identity.get("umo_count", 0),
                    hint=identity.get("hint") or "—",
                )
            )

        rerank = data.get("rerank") or {}
        if rerank:
            if not rerank.get("enabled"):
                lines.append("- 重排序：关闭")
            else:
                model = rerank.get("model") or "自动"
                state = rerank.get("state") or ""
                provider = rerank.get("provider_id") or "自动选择"
                lines.append(f"- 重排序：开启（提供商 {provider}／模型 {model}／状态 {state}）")

        graph = data.get("graph") or {}
        if graph:
            lines.append(
                f"- 知识图谱：{'开启' if graph.get('enabled') else '关闭'}"
                f"（实体 {graph.get('entities', 0)} 个／关系 {graph.get('relations', 0)} 条／"
                f"作用域 {graph.get('scopes', 0)} 个）"
            )

        review = data.get("review") or {}
        if review:
            lines.append(
                f"- 自动审核：{'开启' if review.get('enabled') else '关闭'}"
                + ("（含模型兜底）" if review.get("use_llm") else "（仅规则）")
                + f"，待审 {review.get('pending', 0)} 条"
                f"，已自动处理 {review.get('decided_by_auto', 0)} 条"
            )

        monitor = data.get("monitor") or {}
        if monitor:
            lines.append(
                f"- 运行指标：待落盘 {monitor.get('pending', 0)} 条，"
                f"保留 {monitor.get('retention_days', 0)} 天"
            )

        degraded = data.get("degraded") or []
        for item in degraded:
            lines.append(f"- 未生效：{item.get('capability')}（{item.get('reason')}）")

        budget = data.get("budget") or {}
        if budget:
            limit = budget.get("daily_limit") or 0
            lines.append(
                f"- 辅助调用：今日 {budget.get('used', 0)} 次"
                + (f"／上限 {limit} 次" if limit else "（未限制）")
                + f"，拒绝 {budget.get('rejected', 0)} 次"
            )

        context = data.get("context") or {}
        if context:
            state = "开启" if context.get("enabled") else "关闭"
            lines.append(
                f"- 上下文治理：{state}（上限 {context.get('max_tokens', 0)} token，"
                f"保留最近 {context.get('keep_recent', 0)} 条）"
            )
            last = context.get("last") or {}
            if last.get("applied"):
                lines.append(
                    f"  最近一次：{last.get('original_tokens', 0)}→"
                    f"{last.get('final_tokens', 0)} token（{last.get('reason', '')}）"
                )

        group = data.get("group") or {}
        if group:
            session = group.get("session") or {}
            detail = ""
            if session:
                detail = (
                    f"，本会话近 1 小时插话 {session.get('hourly', 0)}"
                    f"/{session.get('hourly_limit', 0)} 次，冷却剩余 "
                    f"{session.get('cooldown_remaining', 0)}s"
                )
            lines.append(
                f"- 群聊语义：{'开启' if group.get('enabled') else '关闭'}"
                f"（阈值 {group.get('attention_threshold', 0)}，"
                f"冷却 {group.get('cooldown_seconds', 0)}s{detail}）"
            )

        proactive = data.get("proactive") or {}
        if proactive:
            tracks = []
            if proactive.get("daily_enabled"):
                tracks.append(f"计划轨 {proactive.get('daily_time', '')}")
            if proactive.get("idle_enabled"):
                tracks.append(f"空闲轨 {proactive.get('idle_minutes', 0)} 分钟")
            lines.append(
                f"- 主动交互：{'开启' if proactive.get('enabled') else '关闭'}"
                f"（{'、'.join(tracks) or '无轨道'}，免打扰 {proactive.get('quiet_hours', '')}）"
            )
            session = proactive.get("session") or {}
            if session:
                lines.append(
                    f"  本会话：{'已暂停' if session.get('paused') else '正常'}，今日已发 "
                    f"{session.get('sent_today', 0)}/{session.get('daily_max', 0)} 条，"
                    f"静默 {session.get('idle_minutes', 0)} 分钟"
                )
            last_proactive = proactive.get("last") or {}
            if last_proactive:
                lines.append(
                    f"  最近一次：{'已发送' if last_proactive.get('sent') else '未发送'}"
                    f"（{last_proactive.get('reason', '')}）"
                )

        scheduler = data.get("scheduler") or []
        for job in scheduler:
            job_next = job.get("seconds_to_next")
            remain = f"{int(job_next)}s 后" if isinstance(job_next, (int, float)) else "待定"
            lines.append(
                f"- 任务 {job.get('key')}：运行 {job.get('runs', 0)} 次，"
                f"失败 {job.get('failures', 0)} 次，下次 {remain}"
            )
        return "\n".join(lines)

    async def search(self, view: EventView, query: str) -> str:
        memory = self._app.memory
        if memory is None:
            return self._not_ready()
        text = (query or "").strip()
        if not text:
            return "用法：/sab search <关键词>"
        try:
            result = await memory.recall(self._scope(view), text)
        except Exception as exc:
            return f"检索失败：{safe_detail(exc)}"
        header = f"检索「{text}」：命中 {len(result.items)} 条（{result.route_summary}，{result.elapsed_ms:.0f}ms）"
        summary = result.rerank_summary
        if summary:
            header += f"\n{summary}"
        return f"{header}\n{memory.format_results(result, with_score=True)}"

    async def why(self, view: EventView, query: str) -> str:
        """与 search 相同，但重点展示打分明细（默认会展示分数）。"""
        return await self.search(view, query)

    async def remember(self, view: EventView, content: str) -> str:
        memory = self._app.memory
        if memory is None:
            return self._not_ready()
        text = (content or "").strip()
        if not text:
            return "用法：/sab remember <内容>"
        try:
            memory_id = await memory.remember_text(
                self._scope(view),
                text,
                kind=KIND_FACT,
                importance=0.7,
                confidence=0.9,
                source=SOURCE_MANUAL,
                identity=MemoryIdentity.from_view(view),
            )
        except Exception as exc:
            return f"写入失败：{safe_detail(exc)}"
        return f"已记住（记忆 #{memory_id}）：{text}"

    async def journal_add(
        self, view: EventView, payload: str, *, entry_type: str = ENTRY_TYPE_WEEKLY
    ) -> str:
        """写入一条现实记录；``entry_type`` 由动作名（周记 / 日记 / 随笔）决定。"""
        journal = self._app.journal
        if journal is None:
            return self._not_ready()

        label = entry_type_label(entry_type)
        raw = (payload or "").strip()
        if not raw:
            return (
                f"用法：/sab {label} <内容> [#标签]，或 /sab {label} 标题 | 内容 #标签\n"
                "不写标题时自动使用当天日期时间。"
            )
        if not journal.config.can_write(is_admin=view.is_admin):
            return "当前配置不允许你写现实记录。"

        title, content, tags = parse_journal_payload(raw)
        if not content:
            return f"{label}内容不能为空。"

        try:
            result = await journal.add(
                self._scope(view),
                content,
                title=title or None,
                entry_type=entry_type,
                tags=tags or None,
                identity=MemoryIdentity.from_view(view),
            )
        except Exception as exc:
            return f"写入{label}失败：{safe_detail(exc)}"
        if not result:
            return f"{label}内容为空，未写入。"
        tag_text = "、".join(result.get("tags") or []) or "无"
        return (
            f"{label}已记录（#{result['journal_id']}，标题：{result.get('title')}，"
            f"标签：{tag_text}）：{content}"
        )

    async def journal_edit(self, view: EventView, args: Sequence[str]) -> str:
        """修改一条现实记录：``/sab edit <编号> [类型] [标题 | ] 正文 [#标签]``。

        未写标题时保持原标题不变（标题已在写入时按当天日期时间生成过）。
        """
        journal = self._app.journal
        if journal is None:
            return self._not_ready()
        usage = "用法：/sab edit <编号> [周记|日记|随笔] [标题 | ] 正文 [#标签]"
        if not args:
            return usage
        try:
            journal_id = int(str(args[0]).strip().lstrip("#"))
        except (TypeError, ValueError):
            return f"请提供有效的记录编号，例如 /sab edit 12 新的正文\n{usage}"

        rest = [str(item) for item in args[1:]]
        entry_type: str | None = None
        if rest and is_entry_type(rest[0]):
            entry_type = normalize_entry_type(rest[0])
            rest = rest[1:]
        if not rest:
            return usage
        if not journal.config.can_write(is_admin=view.is_admin):
            return "当前配置不允许你修改现实记录。"

        title, content, tags = parse_journal_payload(" ".join(rest))
        if not content:
            return "新的正文不能为空。"
        try:
            ok = await journal.update(
                journal_id,
                content=content,
                title=title or None,
                entry_type=entry_type,
                tags=tags or None,
            )
        except Exception as exc:
            return f"修改失败：{safe_detail(exc)}"
        if not ok:
            return f"#{journal_id} 不存在或内容为空。"
        changes = []
        if title:
            changes.append(f"标题：{title}")
        if entry_type:
            changes.append(f"类型：{entry_type_label(entry_type)}")
        suffix = f"（{'，'.join(changes)}）" if changes else ""
        return f"已更新 #{journal_id}{suffix}：{content}"

    async def journal_list(self, view: EventView, arg: str = "") -> str:
        """查看最近记录：``/sab journals [周记|日记|随笔] [条数]``。"""
        journal = self._app.journal
        if journal is None:
            return self._not_ready()

        entry_type = ""
        limit = 5
        for token in str(arg or "").split():
            if not entry_type and is_entry_type(token):
                entry_type = normalize_entry_type(token)
                continue
            try:
                limit = int(token)
            except ValueError:
                return "用法：/sab journals [周记|日记|随笔] [条数]"

        try:
            rows = await journal.list_recent(
                self._scope(view),
                limit=max(1, min(20, limit)),
                entry_type=entry_type,
            )
        except Exception as exc:
            return f"读取记录失败：{safe_detail(exc)}"
        if not rows:
            scope_text = f"{entry_type_label(entry_type)}" if entry_type else "现实记录"
            return f"还没有{scope_text}。可用 /sab 日记 <内容> 记录。"

        head = f"最近{entry_type_label(entry_type)}：" if entry_type else "最近现实记录："
        lines = [head]
        for row in rows:
            date = time.strftime("%Y-%m-%d", time.localtime(float(row.get("event_time") or 0)))
            title = str(row.get("title") or "").strip()
            content = str(row.get("content") or "").replace("\n", " ")
            prefix = f"【{entry_type_label(row.get('entry_type'))}】"
            lines.append(
                f"- #{row.get('id')} {prefix}{date} {title}｜{content[:100]}"
            )
        return "\n".join(lines)

    async def review_list(self, view: EventView) -> str:
        try:
            rows = await self._app.pending_reviews(self._scope(view), limit=10)
        except Exception as exc:
            return f"读取待审队列失败：{safe_detail(exc)}"
        if not rows:
            return "待审队列为空。"
        lines = ["待审记录："]
        for row in rows:
            lines.append(
                f"- #{row.get('id')} [{row.get('origin')}] {str(row.get('summary') or '')[:120]}"
            )
        lines.append("使用 /sab approve <编号> 或 /sab reject <编号> 处理。")
        return "\n".join(lines)

    async def review_approve(self, view: EventView, review_id: str) -> str:
        return await self._review_action(view, review_id, approve=True)

    async def review_reject(self, view: EventView, review_id: str) -> str:
        return await self._review_action(view, review_id, approve=False)

    async def _review_action(self, view: EventView, raw_id: str, *, approve: bool) -> str:
        try:
            review_id = int(str(raw_id).strip())
        except (TypeError, ValueError):
            return "请提供有效的待审编号，例如 /sab approve 3"
        try:
            if approve:
                handled, message = await self._app.approve_review(review_id)
                if not handled:
                    return f"#{review_id} 不存在或已处理。"
                return f"已批准 #{review_id}：{message}"
            rejected = await self._app.reject_review(review_id)
            if not rejected:
                return f"#{review_id} 不存在或已处理。"
            return f"已驳回 #{review_id}。"
        except Exception as exc:
            return f"处理失败：{safe_detail(exc)}"

    async def reset(self, view: EventView, confirm: str) -> str:
        memory = self._app.memory
        service = self._app.persona_service
        if memory is None and service is None:
            return self._not_ready()
        if (confirm or "").strip().lower() not in {"confirm", "确认"}:
            return "该操作不可逆。确认请执行：/sab reset confirm"

        scope = self._scope(view)
        counts = {"active": 0, "buffered": 0}
        if memory is not None:
            try:
                counts = await memory.reset_scope(scope)
            except Exception as exc:
                return f"重置失败：{safe_detail(exc)}"

        extra = ""
        if service is not None:
            try:
                cleared = await service.clear(scope)
            except Exception as exc:
                extra = f"（拟人化学习清理失败：{safe_detail(exc)}）"
            else:
                extra = (
                    f"，风格样本 {cleared.get('style', 0)} 条、"
                    f"群内用语 {cleared.get('jargon', 0)} 条、"
                    f"好感度记录 {cleared.get('affinity', 0)} 条"
                )
        graph = self._app.graph_service
        if graph is not None:
            try:
                removed = await graph.clear(scope)
            except Exception as exc:
                extra += f"（图谱清理失败：{safe_detail(exc)}）"
            else:
                extra += (
                    f"，图谱实体 {removed.get('entities', 0)} 个、"
                    f"关系 {removed.get('relations', 0)} 条"
                )
        return (
            f"已清空当前作用域：正式记忆 {counts.get('active', 0)} 条，"
            f"对话缓冲 {counts.get('buffered', 0)} 条{extra}。"
        )

    async def reindex(self, view: EventView) -> str:
        memory = self._app.memory
        if memory is None:
            return self._not_ready()
        try:
            stats = await memory.reindex(self._scope(view))
        except Exception as exc:
            return f"重建索引失败：{safe_detail(exc)}"
        line = (
            f"索引重建完成：FTS {stats.get('indexed', 0)} 条，"
            f"向量 {stats.get('vectorized', 0)} 条，跳过 {stats.get('skipped', 0)} 条。"
        )
        note = str(stats.get("note") or "")
        return f"{line}\n{note}" if note else line

    async def quiet(self, view: EventView, arg: str) -> str:
        """暂停/恢复当前会话的主动消息（免打扰）。"""
        service = self._app.proactive_service
        if service is None:
            return self._not_ready()

        token = arg.strip().lower()
        if token in {"on", "1", "true", "开", "开启", "暂停", "静音"}:
            paused = True
        elif token in {"off", "0", "false", "关", "关闭", "恢复"}:
            paused = False
        elif not token:
            paused = not await service.is_paused(view.umo)
        else:
            return "用法：/sab quiet [on|off]；不带参数则在暂停与恢复之间切换。"

        try:
            await service.set_paused(view.umo, paused)
        except Exception as exc:
            return f"设置失败：{safe_detail(exc)}"
        return (
            "已暂停本会话的主动消息（再次发送 /sab quiet 可恢复）。"
            if paused
            else "已恢复本会话的主动消息。"
        )

    # ------------------------------------------------------------------ #
    # 拟人化学习
    # ------------------------------------------------------------------ #

    async def persona(self, view: EventView, arg: str) -> str:
        """查看拟人化学习概况；带类型参数时展示对应明细。"""
        service = self._app.persona_service
        if service is None:
            return self._not_ready()

        scope = self._scope(view)
        token = (arg or "").strip().lower()
        try:
            if token in {"style", "风格"}:
                return await self._persona_styles(service, scope)
            if token in {"jargon", "黑话", "用语"}:
                return await self._persona_jargons(service, scope)
            if token in {"affinity", "好感", "好感度"}:
                return await self._persona_affinity(service, scope)
            return await self._persona_overview(service, view, scope)
        except Exception as exc:
            return f"读取学习结果失败：{safe_detail(exc)}"

    async def graph(self, view: EventView, arg: str) -> str:
        """查看记忆知识图谱概况；可选指定会话 UMO。"""
        service = self._app.graph_service
        if service is None:
            return self._not_ready()
        umo = (arg or "").strip()
        scope = MemoryScope.for_session(umo) if umo else None
        try:
            stats = await service.stats()
            data = await service.snapshot(scope=scope, limit_nodes=15, limit_edges=20)
        except Exception as exc:
            return f"读取图谱失败：{safe_detail(exc)}"

        lines = [
            "记忆知识图谱",
            f"- 开关：{'开启' if self._app.capabilities.get('graph.enabled') else '关闭'}",
            f"- 实体 {stats.get('entities', 0)} 个、关系 {stats.get('relations', 0)} 条、"
            f"作用域 {stats.get('scopes', 0)} 个",
            f"- 范围：{umo or '全部会话'}",
        ]
        nodes = data.get("nodes") or []
        if not nodes:
            lines.append("- 还没有抽取到实体（写入正式记忆后自动建图）")
            return "\n".join(lines)

        lines.append("实体（按权重）：")
        for row in nodes[:10]:
            lines.append(
                f"- {row.get('name') or row.get('canonical_name')}"
                f"（{row.get('entity_type') or 'concept'}，"
                f"w={float(row.get('weight') or 0):.2f}，证据 {row.get('evidence')}）"
            )
        return "\n".join(lines)

    async def _persona_overview(self, service: Any, view: EventView, scope: MemoryScope) -> str:
        snapshot = service.snapshot()
        counts = await service.stats()
        session = await service.session_counts(view.umo)
        pending = await self._app.pending_reviews(scope, limit=100)
        style = snapshot.get("style") or {}
        jargon = snapshot.get("jargon") or {}
        affinity = snapshot.get("affinity") or {}
        return "\n".join(
            [
                "拟人化学习",
                f"- 风格模仿：{self._state(style)}，样本 {counts.style} 条"
                f"（本会话 {session.get('style', 0)} 条）"
                + ("，需审批" if style.get("approval_required") else ""),
                f"- 群内用语：{self._state(jargon)}，词条 {counts.jargon} 条"
                f"（本会话 {session.get('jargon', 0)} 条）"
                + ("，需审批" if jargon.get("approval_required") else ""),
                f"- 社交好感度：{self._state(affinity)}，记录 {counts.affinity} 条",
                f"- 待审记录：{len(pending)} 条（用 /sab review 查看）",
                "明细：/sab persona style｜jargon｜affinity",
            ]
        )

    async def _persona_styles(self, service: Any, scope: MemoryScope) -> str:
        rows = await service.style_patterns(scope, limit=10)
        if not rows:
            return "本会话还没有学到表达样本。"
        lines = ["表达样本（按权重排序）："]
        for row in rows:
            lines.append(
                f"- #{row.get('id')} [w={float(row.get('weight') or 0):.2f}] "
                f"{str(row.get('situation') or '')[:50]} → {str(row.get('expression') or '')[:80]}"
            )
        return "\n".join(lines)

    async def _persona_jargons(self, service: Any, scope: MemoryScope) -> str:
        rows = await service.jargon_entries(scope, limit=10)
        if not rows:
            return "本会话还没有收录群内用语。"
        lines = ["群内用语："]
        for row in rows:
            lines.append(
                f"- #{row.get('id')} 「{row.get('term')}」："
                f"{str(row.get('meaning') or '')[:60]}"
                f"（置信 {float(row.get('confidence') or 0):.2f}，证据 {row.get('evidence', 0)}）"
            )
        return "\n".join(lines)

    async def _persona_affinity(self, service: Any, scope: MemoryScope) -> str:
        rows = await service.affinity_rows(scope, limit=10)
        if not rows:
            return "本会话还没有好感度记录。"
        lines = ["本会话好感度："]
        for row in rows:
            lines.append(
                f"- {row.get('target_id')}：{float(row.get('score') or 0):.2f}"
                f"（{row.get('mood')}，交互 {row.get('interactions', 0)} 次）"
            )
        return "\n".join(lines)

    @staticmethod
    def _state(snapshot: dict[str, Any]) -> str:
        return "开启" if snapshot.get("enabled") else "关闭"

    # ------------------------------------------------------------------ #
    # 统一入口
    # ------------------------------------------------------------------ #

    async def dispatch(self, action: str, view: EventView, args: Sequence[str]) -> str:
        """按动作名分发；未知动作返回帮助。

        所有分支都在此兜底异常，保证命令永远不会把异常抛回框架。
        """
        if self.admin_only() and not view.is_admin and action not in {"help", "status"}:
            return self._permission_error()

        handlers = {
            "help": lambda: self.help_text(),
            "status": lambda: self.status(view),
            "search": lambda: self.search(view, " ".join(args)),
            "why": lambda: self.why(view, " ".join(args)),
            "remember": lambda: self.remember(view, " ".join(args)),
            # 现实桥：动作名携带文本类型
            "journal": lambda: self.journal_add(
                view, " ".join(args), entry_type=ENTRY_TYPE_WEEKLY
            ),
            "diary": lambda: self.journal_add(view, " ".join(args), entry_type=ENTRY_TYPE_DIARY),
            "essay": lambda: self.journal_add(view, " ".join(args), entry_type=ENTRY_TYPE_ESSAY),
            "journal-edit": lambda: self.journal_edit(view, args),
            "journals": lambda: self.journal_list(view, " ".join(args)),
            "review": lambda: self.review_list(view),
            "approve": lambda: self.review_approve(view, args[0] if args else ""),
            "reject": lambda: self.review_reject(view, args[0] if args else ""),
            "reset": lambda: self.reset(view, args[0] if args else ""),
            "reindex": lambda: self.reindex(view),
            "quiet": lambda: self.quiet(view, args[0] if args else ""),
            "persona": lambda: self.persona(view, args[0] if args else ""),
            "graph": lambda: self.graph(view, args[0] if args else ""),
        }
        handler = handlers.get(action)
        if handler is None:
            return f"未知子指令：{action}\n\n{HELP_TEXT}"
        try:
            return await handler()
        except Exception as exc:  # 命令层必须兜底
            return f"指令执行失败：{safe_detail(exc)}"
