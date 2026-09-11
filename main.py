"""Super_AstrBot · AstrBot 长期记忆与自我学习增强插件（入口）。

本文件只做三件事，业务逻辑一律下沉到 ``super_astrbot`` 包：

1. 注册插件类与生命周期（``initialize`` / ``terminate``）；
2. 注册事件钩子（LLM 请求前注入记忆、消息发送后记录回复）；
3. 注册 ``/sab`` 指令组（参数解析与回显），并把动作交给 ``CommandService``。

注意：``Star.__init__`` 不会保存 config，必须自行保存（见 AstrBot 源码
``astrbot/core/star/base.py:Star.__init__``）。
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .super_astrbot.app import SuperAstrBotApp
from .super_astrbot.commands import CommandService
from .super_astrbot.harness import to_event_view
from .super_astrbot.spec.errors import safe_detail

try:  # pragma: no cover - 依赖框架版本
    from astrbot.core.star.filter.command import GreedyStr
except Exception:  # noqa: BLE001 - 极老版本无此类时退化为普通字符串

    class GreedyStr(str):  # type: ignore[no-redef]
        """降级实现：多词参数会被截断为第一个词。"""


class SuperAstrBot(Star):
    """插件主类。"""

    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context)
        self.config = config if config is not None else {}
        self._app = SuperAstrBotApp(star=self, context=context, config=self.config)
        self._commands = CommandService(app=self._app, config=self.config)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    async def initialize(self) -> None:
        """插件激活时装配运行时。"""
        try:
            await self._app.start()
        except Exception as exc:  # noqa: BLE001 - 初始化失败不应阻断 AstrBot 启动
            self.logger.error("Super_AstrBot 初始化失败：%s", safe_detail(exc))

        try:
            from .super_astrbot.web import register_web_apis

            register_web_apis(self.context, self._app)
        except Exception as exc:  # noqa: BLE001 - 面板注册失败不影响核心能力
            self.logger.warning(
                "注册 Web API 失败（面板不可用，核心功能不受影响）：%s", safe_detail(exc)
            )

    async def terminate(self) -> None:
        """插件卸载/停用时收敛资源。"""
        try:
            await self._app.shutdown()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Super_AstrBot 卸载清理失败：%s", safe_detail(exc))

    # ------------------------------------------------------------------ #
    # 事件钩子
    # ------------------------------------------------------------------ #

    @filter.on_llm_request()
    async def _hook_llm_request(self, event: AstrMessageEvent, request: Any) -> None:
        """请求 LLM 前：召回并注入长期记忆。"""
        try:
            await self._app.on_llm_request(event, request)
        except Exception as exc:  # noqa: BLE001 - 钩子绝不可打断对话
            self.logger.warning("记忆注入钩子异常：%s", safe_detail(exc))

    @filter.after_message_sent()
    async def _hook_after_message_sent(self, event: AstrMessageEvent) -> None:
        """消息发送后：把回复放入对话缓冲，作为反思原料。"""
        try:
            await self._app.on_after_message_sent(event)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("回复采集钩子异常：%s", safe_detail(exc))

    # ------------------------------------------------------------------ #
    # 指令组
    # ------------------------------------------------------------------ #

    @filter.command_group("sab")
    async def sab(self) -> None:
        """Super_AstrBot 管理指令组。"""

    async def _run_command(self, action: str, event: AstrMessageEvent, args: list[str]) -> str:
        """统一执行入口：把事件转成视图，再交给命令层。"""
        try:
            view = to_event_view(event)
            return await self._commands.dispatch(action, view, args)
        except Exception as exc:  # noqa: BLE001 - 命令层兜底
            self.logger.warning("指令 %s 执行异常：%s", action, safe_detail(exc))
            return f"指令执行异常：{safe_detail(exc)}"

    @sab.command("help")
    async def sab_help(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        yield event.plain_result(await self._run_command("help", event, []))

    @sab.command("status")
    async def sab_status(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        yield event.plain_result(await self._run_command("status", event, []))

    @sab.command("search")
    async def sab_search(
        self, event: AstrMessageEvent, query: GreedyStr
    ) -> AsyncGenerator[Any, None]:
        yield event.plain_result(await self._run_command("search", event, [str(query)]))

    @sab.command("why")
    async def sab_why(self, event: AstrMessageEvent, query: GreedyStr) -> AsyncGenerator[Any, None]:
        yield event.plain_result(await self._run_command("why", event, [str(query)]))

    @sab.command("remember")
    async def sab_remember(
        self, event: AstrMessageEvent, content: GreedyStr
    ) -> AsyncGenerator[Any, None]:
        yield event.plain_result(await self._run_command("remember", event, [str(content)]))

    @sab.command("journal")
    async def sab_journal(
        self, event: AstrMessageEvent, content: GreedyStr
    ) -> AsyncGenerator[Any, None]:
        yield event.plain_result(await self._run_command("journal", event, [str(content)]))

    @sab.command("journals")
    async def sab_journals(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        yield event.plain_result(await self._run_command("journals", event, []))

    @sab.command("review")
    async def sab_review(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        yield event.plain_result(await self._run_command("review", event, []))

    @sab.command("approve")
    async def sab_approve(
        self, event: AstrMessageEvent, review_id: str
    ) -> AsyncGenerator[Any, None]:
        yield event.plain_result(await self._run_command("approve", event, [str(review_id)]))

    @sab.command("reject")
    async def sab_reject(
        self, event: AstrMessageEvent, review_id: str
    ) -> AsyncGenerator[Any, None]:
        yield event.plain_result(await self._run_command("reject", event, [str(review_id)]))

    @sab.command("reset")
    async def sab_reset(
        self, event: AstrMessageEvent, confirm: str = ""
    ) -> AsyncGenerator[Any, None]:
        yield event.plain_result(await self._run_command("reset", event, [str(confirm)]))

    @sab.command("reindex")
    async def sab_reindex(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        yield event.plain_result(await self._run_command("reindex", event, []))
