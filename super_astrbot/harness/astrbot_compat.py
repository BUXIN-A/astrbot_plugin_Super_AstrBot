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
    except Exception:  # noqa: BLE001 - 兼容探测必须吞掉所有导入期异常
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
    AstrMessageEvent: Any = None

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
        AstrMessageEvent=_load("astrbot.api.event", "AstrMessageEvent"),
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
