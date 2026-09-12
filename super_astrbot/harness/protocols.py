"""Harness 层对外暴露的抽象协议。

业务域只依赖本模块中的 ``Protocol`` 与数据类，不感知 AstrBot 的存在。
这是本项目与 AstrNa 最重要的差异之一：AstrNa 直接把 AstrBot 内部符号散布在各模块，
导致版本升级时大面积静默降级；我们把这类耦合收敛到 ``harness`` 内。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

# --------------------------------------------------------------------------- #
# 日志与宿主
# --------------------------------------------------------------------------- #


@runtime_checkable
class LoggerLike(Protocol):
    """与标准库 ``logging.Logger`` 兼容的最小日志接口。"""

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None: ...

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None: ...

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None: ...

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None: ...

    def exception(self, msg: str, *args: Any, **kwargs: Any) -> None: ...


@runtime_checkable
class Host(Protocol):
    """宿主环境能力：路径、配置、日志、时间、键值存储、主动发送。"""

    def data_dir(self) -> Path:
        """插件数据目录（位于 AstrBot ``data`` 下，保证升级/重装不丢数据）。"""

    def app_config(self) -> Mapping[str, Any]:
        """插件配置对象。"""

    def log(self) -> LoggerLike:
        """插件专属日志器。"""

    def now(self) -> float:
        """当前 Unix 时间戳（秒）。"""

    async def kv_get(self, key: str, default: Any = None) -> Any:
        """读取宿主键值存储（用于轻量运行态，不承载业务数据）。"""

    async def kv_put(self, key: str, value: Any) -> None:
        """写入宿主键值存储。"""

    async def kv_delete(self, key: str) -> None:
        """删除宿主键值存储。"""

    async def send_message(self, umo: str, text: str) -> bool:
        """向指定会话主动发送纯文本；失败返回 ``False`` 而不抛异常。"""


# --------------------------------------------------------------------------- #
# 事件视图
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EventView:
    """``AstrMessageEvent`` 的纯数据快照。

    业务层只消费本对象，从而与框架解耦，并且可被单元测试直接构造。
    """

    umo: str
    platform: str = ""
    session_id: str = ""
    is_group: bool = False
    group_id: str = ""
    sender_id: str = ""
    sender_name: str = ""
    text: str = ""
    timestamp: float = 0.0
    is_admin: bool = False
    stopped: bool = False

    @property
    def is_private(self) -> bool:
        return not self.is_group

    @property
    def display_user(self) -> str:
        return self.sender_name or self.sender_id or "未知用户"


# --------------------------------------------------------------------------- #
# LLM
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ChatMessage:
    """与 OpenAI 风格对齐的简化消息结构。"""

    role: str
    content: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
        )


@dataclass(frozen=True)
class LlmResult:
    """一次 LLM 调用的结果。

    注意：AstrBot 的 ``LLMResponse`` 没有 ``content`` 字段，文本用 ``completion_text``；
    这里统一成 ``text``，避免业务层感知框架细节。
    """

    text: str
    reasoning: str = ""
    usage: TokenUsage | None = None
    provider_id: str = ""
    model: str = ""
    raw: Any = None


@dataclass(frozen=True)
class ProviderInfo:
    """可供选择的模型提供商信息。"""

    id: str
    type: str = "chat_completion"
    model: str = ""


@runtime_checkable
class LlmGateway(Protocol):
    """统一的 LLM 调用入口（含预算、超时、停止感知）。"""

    def list_providers(self) -> Sequence[ProviderInfo]:
        """列出可用的对话模型提供商。"""

    async def chat(
        self,
        *,
        prompt: str,
        system_prompt: str | None = None,
        contexts: Sequence[ChatMessage] | None = None,
        provider_id: str | None = None,
        session_key: str | None = None,
        timeout: float | None = None,
        purpose: str = "general",
    ) -> LlmResult:
        """发起一次对话补全。

        Raises:
            LlmError: 调用失败（网络、鉴权、空输出等）。
            BudgetExhaustedError: 超出预算。
        """


@runtime_checkable
class BudgetGuard(Protocol):
    """成本/并发预算守卫。

    由 ``loop`` 层实现并注入给 ``LlmGateway``，从而避免 harness 反向依赖 loop
    （依赖倒置）。
    """

    async def try_acquire(self, purpose: str) -> bool:
        """尝试获取一次调用配额；超限返回 ``False``。"""

    def release(self, purpose: str) -> None:
        """释放并发占用（配额的「今日计数」不回退）。"""

    def snapshot(self) -> Mapping[str, Any]:
        """返回统计快照，供面板/命令展示。"""


@runtime_checkable
class EmbeddingGateway(Protocol):
    """可选向量能力。不可用时 ``available`` 为 ``False``，调用方须降级。"""

    @property
    def available(self) -> bool: ...

    def fingerprint(self) -> str:
        """模型指纹；变化时需重建向量索引。"""

    def dimension(self) -> int: ...

    async def embed(self, text: str) -> list[float] | None:
        """返回向量；失败或不可用返回 ``None``。"""


@dataclass(frozen=True)
class RerankHit:
    """一条重排序结果。

    ``score`` 的口径由提供商决定（可能带负值、可能已被归一化），
    调用方需自行做 min-max 归一化后再使用。
    """

    index: int
    """候选在传入 ``documents`` 中的下标。"""

    score: float


@runtime_checkable
class RerankGateway(Protocol):
    """可选重排序能力。不可用或调用失败时返回空列表，调用方须降级。"""

    @property
    def available(self) -> bool: ...

    def model(self) -> str:
        """当前提供商的模型名；不可用返回空串。"""

    def list_providers(self) -> Sequence[ProviderInfo]:
        """列出可用的重排序提供商（供配置页动态下拉）。"""

    def refresh(self) -> None:
        """清除解析缓存（Provider 实例被框架重建后重新探测）。"""

    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int | None = None,
    ) -> list[RerankHit]:
        """按查询相关性重排序候选，返回按分数降序的结果；失败返回空列表。"""


# --------------------------------------------------------------------------- #
# 注入
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class InjectResult:
    """注入结果，便于上层记录与面板展示。"""

    applied: bool
    method: str = ""
    parts: int = 0
    chars: int = 0
    reason: str = ""


@runtime_checkable
class Injector(Protocol):
    """把记忆块注入到平台的请求对象中。"""

    def compose(self, blocks: Sequence[str]) -> str:
        """把多个文本块拼成带边界标记的注入正文。"""

    def inject(self, target: Any, blocks: Sequence[str], *, prefer: str = "auto") -> InjectResult:
        """注入；``target`` 通常为 ``ProviderRequest``。

        ``prefer`` 取 ``auto`` / ``extra_user_content`` / ``system_prompt``，
        表示调用方期望的注入方式，实现在宿主不支持时可降级。
        """

    def clear(self, target: Any) -> int:
        """清理本插件历史注入的块，返回清理数量。"""


# --------------------------------------------------------------------------- #
# Agent 函数工具
# --------------------------------------------------------------------------- #


@runtime_checkable
class MemoryToolBackend(Protocol):
    """Agent 记忆工具的业务回调。

    harness 只负责把工具接线到框架；检索/写入的规则与校验由 ``memory`` 域实现。
    因此这里只声明「一次工具调用需要什么、产出什么」，不引入任何业务类型。
    """

    async def memory_search(self, *, view: EventView, query: str, limit: int | None = None) -> str:
        """检索长期记忆，返回可直接回给模型的文本。"""

    async def memory_write(
        self,
        *,
        view: EventView,
        content: str,
        kind: str = "fact",
        importance: float = 0.6,
    ) -> str:
        """写入一条长期记忆；参数不合法或权限不足时返回可读的说明文本。"""


# --------------------------------------------------------------------------- #
# 群聊语义
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GroupSignals:
    """群消息的额外信号（「读空气」决策的输入）。

    ``EventView`` 只承载通用字段；被 @、被引用这类判断需要消息段，
    因此单独抽出，避免让所有调用方都去解析消息链。
    """

    self_id: str = ""
    """Bot 自身 ID。"""

    mentioned: bool = False
    """消息是否 @ 了 Bot、或引用了 Bot 发送的消息。"""

    wake: bool = False
    """框架是否已判定该消息应唤醒 Bot（wake 前缀 / @ / 引用）。"""


@dataclass(frozen=True)
class GroupDecision:
    """一次群消息处理决策，由业务域给出、由 harness 落地到事件对象。"""

    action: str = "reply"
    """``interject``（主动插话）/ ``reply``（按既有链路正常回复）/ ``silent``（静默）。"""

    reason: str = ""
    """决策原因（日志与状态展示用）。"""

    attention: float = 0.0
    """注意力得分（0~1），仅用于观测。"""

    text: str = ""
    """插话时要写回事件的消息文本（并发合并结果）；为空表示不改写原文。"""

    merged: int = 0
    """本次合并的消息条数（含首条）。"""
