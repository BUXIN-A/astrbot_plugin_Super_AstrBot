"""命令层：把插件能力暴露为聊天指令。

设计要点：

- 本模块**不导入 AstrBot**，只接收 ``EventView``（纯数据）并返回字符串；
  指令的注册与参数解析由 ``main.py`` 负责，从而让命令逻辑可被单元测试直接驱动；
- 所有命令都做权限校验（是否管理员）与异常兜底，绝不让命令抛异常打断会话。
"""

from __future__ import annotations

import json
import time
from typing import Any, Sequence

from ..harness.protocols import EventView
from ..memory import KIND_FACT, SOURCE_MANUAL
from ..spec.capabilities import get_path
from ..spec.errors import safe_detail
from ..spec.scopes import MemoryScope, ScopeType

HELP_TEXT = """Super_AstrBot 指令（别名 /superastrbot，等价于 /sab）：
/sab status          查看运行状态、框架诊断与能力开关
/sab search <关键词>  检索记忆（含检索路、耗时与打分明细）
/sab why <关键词>     同 search，调参时查看打分构成
/sab remember <内容>  手动写入一条长期记忆
/sab journal <内容> [#标签]  写一条周记（现实记忆）
/sab journals        查看最近周记
/sab review          查看待审记忆
/sab approve <编号>   批准一条待审记忆
/sab reject <编号>    驳回一条待审记忆
/sab reset confirm   清空当前会话的记忆与缓冲（不可逆）
/sab reindex         重建检索索引
/sab help            显示本帮助

可视化管理：AstrBot 插件详情页 → Pages → dashboard"""


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
            data = await self._app.status()
        except Exception as exc:  # noqa: BLE001
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
                "- 记忆：正式 {active} 条／缓冲 {buffered} 条／周记 {journals} 条"
                "（检索路：{routes}）".format(
                    active=memory.get("active", 0),
                    buffered=memory.get("buffered", 0),
                    journals=memory.get("journals", 0),
                    routes="、".join(memory.get("routes") or []) or "无",
                )
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
        except Exception as exc:  # noqa: BLE001
            return f"检索失败：{safe_detail(exc)}"
        header = f"检索「{text}」：命中 {len(result.items)} 条（{result.route_summary}，{result.elapsed_ms:.0f}ms）"
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
            )
        except Exception as exc:  # noqa: BLE001
            return f"写入失败：{safe_detail(exc)}"
        return f"已记住（记忆 #{memory_id}）：{text}"

    async def journal_add(self, view: EventView, payload: str) -> str:
        journal = self._app.journal
        if journal is None:
            return self._not_ready()

        raw = (payload or "").strip()
        if not raw:
            return "用法：/sab journal <内容> [#标签1 #标签2]"

        tags = [token[1:] for token in raw.split() if token.startswith("#")]
        content = " ".join(token for token in raw.split() if not token.startswith("#")).strip()
        if not content:
            return "周记内容不能为空。"

        if not journal.config.can_write(is_admin=view.is_admin):
            return "当前配置不允许你写周记。"

        try:
            result = await journal.add(self._scope(view), content, tags=tags)
        except Exception as exc:  # noqa: BLE001
            return f"写入周记失败：{safe_detail(exc)}"
        if not result:
            return "周记内容为空，未写入。"
        tag_text = "、".join(result.get("tags") or []) or "无"
        return f"周记已记录（#{result['journal_id']}，标签：{tag_text}）：{content}"

    async def journal_list(self, view: EventView, limit: int = 5) -> str:
        journal = self._app.journal
        if journal is None:
            return self._not_ready()
        try:
            rows = await journal.list_recent(self._scope(view), limit=max(1, min(20, limit)))
        except Exception as exc:  # noqa: BLE001
            return f"读取周记失败：{safe_detail(exc)}"
        if not rows:
            return "还没有周记。可用 /sab journal <内容> 记录。"
        lines = ["最近周记："]
        for row in rows:
            date = time.strftime("%Y-%m-%d", time.localtime(float(row.get("event_time") or 0)))
            content = str(row.get("content") or "").replace("\n", " ")
            lines.append(f"- #{row.get('id')} [{date}] {content[:120]}")
        return "\n".join(lines)

    async def review_list(self, view: EventView) -> str:
        reflection = self._app.reflection
        if reflection is None:
            return self._not_ready()
        if not reflection.config.approval_required:
            return "当前未开启「反思结果需人工审批」，反思产出会直接写入记忆。"
        try:
            rows = await reflection.pending_reviews(self._scope(view), limit=10)
        except Exception as exc:  # noqa: BLE001
            return f"读取待审队列失败：{safe_detail(exc)}"
        if not rows:
            return "待审队列为空。"
        lines = ["待审记忆："]
        for row in rows:
            try:
                payload = json.loads(row.get("payload") or "{}")
            except (TypeError, ValueError):
                payload = {}
            lines.append(
                f"- #{row.get('id')} [{payload.get('kind', 'insight')}] {str(payload.get('content') or '')[:120]}"
            )
        lines.append("使用 /sab approve <编号> 或 /sab reject <编号> 处理。")
        return "\n".join(lines)

    async def review_approve(self, view: EventView, review_id: str) -> str:
        return await self._review_action(view, review_id, approve=True)

    async def review_reject(self, view: EventView, review_id: str) -> str:
        return await self._review_action(view, review_id, approve=False)

    async def _review_action(self, view: EventView, raw_id: str, *, approve: bool) -> str:
        reflection = self._app.reflection
        if reflection is None:
            return self._not_ready()
        try:
            review_id = int(str(raw_id).strip())
        except (TypeError, ValueError):
            return "请提供有效的待审编号，例如 /sab approve 3"
        try:
            if approve:
                memory_id = await reflection.approve(review_id)
                if memory_id is None:
                    return f"#{review_id} 不存在或已处理。"
                return f"已批准 #{review_id}，写入记忆 #{memory_id}。"
            rejected = await reflection.reject(review_id)
            if not rejected:
                return f"#{review_id} 不存在或已处理。"
            return f"已驳回 #{review_id}。"
        except Exception as exc:  # noqa: BLE001
            return f"处理失败：{safe_detail(exc)}"

    async def reset(self, view: EventView, confirm: str) -> str:
        memory = self._app.memory
        if memory is None:
            return self._not_ready()
        if (confirm or "").strip().lower() not in {"confirm", "确认"}:
            return "该操作不可逆。确认请执行：/sab reset confirm"
        try:
            stats = await memory.reset_scope(self._scope(view))
        except Exception as exc:  # noqa: BLE001
            return f"重置失败：{safe_detail(exc)}"
        return (
            f"已清空当前作用域：正式记忆 {stats.get('active', 0)} 条，"
            f"对话缓冲 {stats.get('buffered', 0)} 条。"
        )

    async def reindex(self, view: EventView) -> str:
        memory = self._app.memory
        if memory is None:
            return self._not_ready()
        try:
            stats = await memory.reindex(self._scope(view))
        except Exception as exc:  # noqa: BLE001
            return f"重建索引失败：{safe_detail(exc)}"
        return (
            f"索引重建完成：FTS {stats.get('indexed', 0)} 条，"
            f"向量 {stats.get('vectorized', 0)} 条，跳过 {stats.get('skipped', 0)} 条。"
        )

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
            "journal": lambda: self.journal_add(view, " ".join(args)),
            "journals": lambda: self.journal_list(view),
            "review": lambda: self.review_list(view),
            "approve": lambda: self.review_approve(view, args[0] if args else ""),
            "reject": lambda: self.review_reject(view, args[0] if args else ""),
            "reset": lambda: self.reset(view, args[0] if args else ""),
            "reindex": lambda: self.reindex(view),
        }
        handler = handlers.get(action)
        if handler is None:
            return f"未知子指令：{action}\n\n{HELP_TEXT}"
        try:
            return await handler()
        except Exception as exc:  # noqa: BLE001 - 命令层必须兜底
            return f"指令执行失败：{safe_detail(exc)}"
