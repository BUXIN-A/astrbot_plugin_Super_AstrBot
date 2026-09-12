"""Super_AstrBot · AstrBot 长期记忆与自我学习增强插件（入口）。

本文件只做三件事，业务逻辑一律下沉到 ``super_astrbot`` 包：

1. 注册插件类与生命周期（``initialize`` / ``terminate``）；
2. 注册事件钩子（LLM 请求前注入记忆、消息发送后记录回复）；
3. 注册**单一顶层指令** ``sab``（别名 ``superastrbot``），参数解析交给
   ``super_astrbot.commands.parser``，执行交给 ``CommandService``。

为什么只有一个顶层指令：AstrBot 的指令冲突检测以指令「完整名」为键。把状态、检索、
周记、待审等全部作为子指令注册，会在指令列表中产生十余条注册项；收敛为单入口后
冲突面只剩 ``sab`` 一个名字，跨插件撞名概率最低，也不依赖框架的参数推导行为。

注意：``Star.__init__`` 不会保存 config，必须自行保存（见 AstrBot 源码
``astrbot/core/star/base.py:Star.__init__``）。
"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .super_astrbot.app import SuperAstrBotApp
from .super_astrbot.commands import CommandService, resolve_action, split_command_args
from .super_astrbot.harness import (
    GROUP_FILTER_AVAILABLE,
    GroupMessageFilter,
    to_event_view,
)
from .super_astrbot.spec.errors import safe_detail

COMMAND_NAME = "sab"
COMMAND_ALIASES = {"superastrbot"}

_GROUP_HANDLER_READY = (
    GROUP_FILTER_AVAILABLE
    and hasattr(filter, "custom_filter")
    and hasattr(filter, "event_message_type")
    and hasattr(filter, "EventMessageType")
)
"""框架是否支持注册「群消息 handler」。

缺符号时该 handler 不注册：群聊语义能力整体降级为不可用，插件其余部分不受影响。
"""


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
        except Exception as exc:  # 初始化失败不应阻断 AstrBot 启动
            self.logger.error("Super_AstrBot 初始化失败：%s", safe_detail(exc))

        try:
            from .super_astrbot.web import register_web_apis

            register_web_apis(self.context, self._app)
        except Exception as exc:  # 面板注册失败不影响核心能力
            self.logger.warning(
                "注册 Web API 失败（面板不可用，核心功能不受影响）：%s", safe_detail(exc)
            )

    async def terminate(self) -> None:
        """插件卸载/停用时收敛资源。"""
        try:
            await self._app.shutdown()
        except Exception as exc:
            self.logger.warning("Super_AstrBot 卸载清理失败：%s", safe_detail(exc))

    # ------------------------------------------------------------------ #
    # 事件钩子
    # ------------------------------------------------------------------ #

    @filter.on_llm_request()
    async def _hook_llm_request(self, event: AstrMessageEvent, request: Any) -> None:
        """请求 LLM 前：召回并注入长期记忆。"""
        try:
            await self._app.on_llm_request(event, request)
        except Exception as exc:  # 钩子绝不可打断对话
            self.logger.warning("记忆注入钩子异常：%s", safe_detail(exc))

    @filter.after_message_sent()
    async def _hook_after_message_sent(self, event: AstrMessageEvent) -> None:
        """消息发送后：把回复放入对话缓冲，作为反思原料。"""
        try:
            await self._app.on_after_message_sent(event)
        except Exception as exc:
            self.logger.debug("回复采集钩子异常：%s", safe_detail(exc))

    if _GROUP_HANDLER_READY:

        @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
        @filter.custom_filter(GroupMessageFilter, False)
        async def _hook_group_message(self, event: AstrMessageEvent) -> None:
            """群消息钩子：读空气决定是否主动参与群聊。

            门控 filter 只在 ``group.enabled`` 开启时通过；关闭时本 handler 不参与
            AstrBot 的唤醒判定，因此对群聊行为零影响。
            """
            try:
                await self._app.on_group_message(event)
            except Exception as exc:  # 群聊钩子绝不可打断消息链路
                self.logger.warning("群聊语义钩子异常：%s", safe_detail(exc))

    @filter.on_astrbot_loaded()
    async def _hook_astrbot_loaded(self) -> None:
        """框架完全加载后复检能力。

        AstrBot 先加载插件、后初始化 ProviderManager，因此插件启动阶段探测不到
        嵌入提供商；这个钩子在框架就绪后重新探测，让向量检索自动启用。
        """
        try:
            await self._app.on_astrbot_loaded()
        except Exception as exc:
            self.logger.warning("框架加载后的能力复检异常：%s", safe_detail(exc))

    # ------------------------------------------------------------------ #
    # 唯一顶层指令
    # ------------------------------------------------------------------ #

    @filter.command(
        COMMAND_NAME,
        alias=COMMAND_ALIASES,
        desc="Super_AstrBot 管理指令：状态 / 记忆检索 / 周记 / 待审 / 重置 / 重建索引",
    )
    async def sab(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """Super_AstrBot 统一入口，用法见 ``/sab help``。"""
        yield event.plain_result(await self._dispatch(event))

    async def _dispatch(self, event: AstrMessageEvent) -> str:
        """解析并执行子命令；所有异常都在此兜底。"""
        try:
            args = split_command_args(
                getattr(event, "message_str", "") or "",
                (COMMAND_NAME, *COMMAND_ALIASES),
            )
            action, rest = resolve_action(args)
            view = to_event_view(event)
            return await self._commands.dispatch(action, view, rest)
        except Exception as exc:  # 命令层兜底，绝不把异常抛回框架
            self.logger.warning("指令执行异常：%s", safe_detail(exc))
            return f"指令执行异常：{safe_detail(exc)}"
