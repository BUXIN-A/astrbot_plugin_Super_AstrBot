"""AstrBot 框架符号的集中导入与可用性探测。

**本模块是全项目唯一允许 import astrbot 的地方之一**（其余为 ``astrbot_*.py``）。
所有符号都做「拿不到就为 None」的宽容处理，配合 ``require`` 在真正需要时抛出
可读错误，从而把版本兼容风险收敛到一处。

参考事实（AstrBot 4.27.2 源码）：
- ``TextPart`` 位于 ``astrbot.core.agent.message``，不在 ``astrbot.api`` 命名空间；
- ``ProviderRequest`` / ``LLMResponse`` / ``ProviderType`` 为公开 API，位于 ``astrbot.api.provider``；
- ``MessageType`` / ``MessageChain`` 位于 ``astrbot.api.platform`` / ``astrbot.api.event``。
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, replace
from typing import Any

from ..spec.errors import RuntimeNotReadyError


def _load(module: str, name: str) -> Any | None:
    """宽容导入：模块或属性缺失时返回 None，不抛异常。"""
    try:
        mod = importlib.import_module(module)
    except Exception:  # 兼容探测必须吞掉所有导入期异常
        return None
    return getattr(mod, name, None)


@dataclass(frozen=True)
class FrameworkSymbols:
    """一次性解析出的框架符号集合。"""

    ok: bool = False
    version: str = "unknown"
    error: str = ""

    # --- 基础 ---
    logger: Any = None
    Star: Any = None
    StarTools: Any = None
    Context: Any = None
    AstrBotConfig: Any = None

    # --- 事件与消息 ---
    MessageType: Any = None
    MessageChain: Any = None
    Plain: Any = None
    At: Any = None
    Reply: Any = None
    AstrMessageEvent: Any = None

    # --- 事件过滤器 ---
    CustomFilter: Any = None
    EventMessageType: Any = None

    # --- LLM / 工具 ---
    ProviderType: Any = None
    ProviderRequest: Any = None
    LLMResponse: Any = None
    FunctionTool: Any = None
    ToolSet: Any = None
    TextPart: Any = None

    # --- Web ---
    web_request: Any = None
    json_response: Any = None
    error_response: Any = None


def _resolve() -> FrameworkSymbols:
    version = _load("astrbot", "__version__") or "unknown"
    symbols = FrameworkSymbols(
        version=str(version),
        logger=_load("astrbot.api", "logger"),
        Star=_load("astrbot.api.star", "Star"),
        StarTools=_load("astrbot.api.star", "StarTools"),
        Context=_load("astrbot.api.star", "Context"),
        AstrBotConfig=_load("astrbot.api", "AstrBotConfig"),
        MessageType=_load("astrbot.api.platform", "MessageType"),
        MessageChain=_load("astrbot.api.event", "MessageChain"),
        Plain=_load("astrbot.api.message_components", "Plain"),
        At=_load("astrbot.api.message_components", "At"),
        Reply=_load("astrbot.api.message_components", "Reply"),
        AstrMessageEvent=_load("astrbot.api.event", "AstrMessageEvent"),
        CustomFilter=_load("astrbot.api.event.filter", "CustomFilter"),
        EventMessageType=_load("astrbot.api.event.filter", "EventMessageType"),
        ProviderType=_load("astrbot.api.provider", "ProviderType"),
        ProviderRequest=_load("astrbot.api.provider", "ProviderRequest"),
        LLMResponse=_load("astrbot.api.provider", "LLMResponse"),
        FunctionTool=_load("astrbot.api", "FunctionTool"),
        ToolSet=_load("astrbot.api", "ToolSet"),
        TextPart=_load("astrbot.core.agent.message", "TextPart"),
        web_request=_load("astrbot.api.web", "request"),
        json_response=_load("astrbot.api.web", "json_response"),
        error_response=_load("astrbot.api.web", "error_response"),
    )
    ok = symbols.Star is not None and symbols.logger is not None
    if not ok:
        # 保留已解析出的 version：版本不匹配时，诊断信息里它最有价值。
        return replace(symbols, ok=False, error="关键符号缺失：Star/logger 不可用")
    return replace(symbols, ok=True)


SYMBOLS: FrameworkSymbols = _resolve()


def provider_meta(provider: Any) -> dict[str, str]:
    """从 Provider 提取 ``id`` / ``type`` / ``model``，全部防御式读取。

    放在 compat 而不是各网关里：Provider 的元信息形状由框架决定，
    「拿不到就为空」的兼容处理应与其它框架符号探测集中在一处，
    对话 / 嵌入 / 重排序三个网关共用同一份实现，避免口径漂移。
    """
    meta_obj = None
    meta_fn = getattr(provider, "meta", None)
    if callable(meta_fn):
        try:
            meta_obj = meta_fn()
        except Exception:
            meta_obj = None
    meta_obj = meta_obj or provider

    def _pick(name: str) -> str:
        value = getattr(meta_obj, name, None)
        if value is None:
            return ""
        value = getattr(value, "value", value)  # 处理 Enum
        return str(value)

    return {"id": _pick("id"), "type": _pick("type"), "model": _pick("model")}


def configured_provider_entries(context: Any, *kinds: str) -> list[dict[str, Any]]:
    """从 ``provider_manager.providers_config`` 读出指定类型的提供商条目。

    这里读的是**配置条目**而非已加载实例：它包含「已配置但尚未启用」的提供商，
    因此配置页能在用户启用之前就把可选项列出来。
    匹配规则：``provider_type`` 等于 ``kind``，或以 ``_{kind}`` 结尾
    （框架对嵌入类提供商存在 ``xxx_embedding`` 这类命名）。
    """
    try:
        manager = getattr(context, "provider_manager", None)
        configs = getattr(manager, "providers_config", None)
    except Exception:  # Manager 可能是会抛异常的代理对象
        return []
    if not isinstance(configs, (list, tuple)):
        return []

    entries: list[dict[str, Any]] = []
    for item in configs:
        if not isinstance(item, dict):
            continue
        provider_type = str(item.get("provider_type") or "").lower()
        if any(
            provider_type == kind or provider_type.endswith(f"_{kind}") for kind in kinds if kind
        ):
            entries.append(item)
    return entries


def has(name: str) -> bool:
    """判断某符号是否可用。"""
    return getattr(SYMBOLS, name, None) is not None


def require(name: str) -> Any:
    """取某符号，缺失时抛 ``RuntimeNotReadyError``（带清晰提示）。"""
    value = getattr(SYMBOLS, name, None)
    if value is None:
        raise RuntimeNotReadyError(
            f"AstrBot 符号 {name} 不可用（框架版本 {SYMBOLS.version}）。"
            "该功能将被跳过，不影响正常对话。"
        )
    return value


def framework_available() -> bool:
    return SYMBOLS.ok


_OPTIONAL_SYMBOLS: tuple[str, ...] = (
    "TextPart",
    "ProviderRequest",
    "ProviderType",
    "StarTools",
    "AstrMessageEvent",
    "MessageChain",
    "Plain",
    "At",
    "Reply",
    "CustomFilter",
    "EventMessageType",
    "FunctionTool",
    "ToolSet",
    "web_request",
    "json_response",
    "error_response",
)


def diagnostics() -> dict[str, Any]:
    """输出框架环境诊断信息。

    由于本插件主要在远端服务器上运行，把「哪个符号缺失」直接暴露到命令与面板，
    可以显著缩短排障链路（无需翻日志即可判断是否发生版本兼容降级）。
    """
    available = [name for name in _OPTIONAL_SYMBOLS if has(name)]
    missing = [name for name in _OPTIONAL_SYMBOLS if not has(name)]
    return {
        "version": SYMBOLS.version,
        "symbols_ok": SYMBOLS.ok,
        "error": SYMBOLS.error,
        "available": available,
        "missing": missing,
    }


def describe() -> str:
    """生成一行用于启动日志的框架信息。"""
    if not SYMBOLS.ok:
        return f"AstrBot 框架符号解析失败：{SYMBOLS.error}"
    optional = [
        name
        for name in ("TextPart", "ProviderRequest", "ProviderType", "StarTools", "web_request")
        if has(name)
    ]
    return f"AstrBot {SYMBOLS.version}；可用扩展符号：{', '.join(optional) or '无'}"
