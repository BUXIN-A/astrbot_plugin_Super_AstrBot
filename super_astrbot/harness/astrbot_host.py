"""``Host`` 协议的 AstrBot 实现。

职责：路径、配置、日志、时间、键值存储、主动发送。
所有能力都提供兜底，缺失时降级而不是让插件加载失败。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping

from ..spec.errors import safe_detail
from . import astrbot_compat as compat


class AstrBotHost:
    """基于 ``Star`` + ``Context`` 的宿主适配。"""

    def __init__(
        self,
        star: Any,
        context: Any,
        config: Mapping[str, Any] | None = None,
        *,
        data_dir: str | Path | None = None,
    ) -> None:
        self._star = star
        self._context = context
        self._config: Mapping[str, Any] = config or {}
        self._logger = getattr(star, "logger", None) or compat.SYMBOLS.logger
        # 允许显式指定数据目录：便于测试隔离，也便于把数据放到自定义位置。
        self._data_dir: Path | None = Path(data_dir) if data_dir is not None else None
        self._kv_fallback: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # 基础
    # ------------------------------------------------------------------ #

    @property
    def context(self) -> Any:
        return self._context

    @property
    def star(self) -> Any:
        return self._star

    def data_dir(self) -> Path:
        """插件数据目录，优先级：StarTools → 数据路径常量 → 当前工作目录兜底。"""
        if self._data_dir is not None:
            return self._data_dir

        plugin_name = str(getattr(self._star, "name", "") or "super_astrbot")
        resolved: Path | None = None

        star_tools = compat.SYMBOLS.StarTools
        if star_tools is not None:
            try:
                resolved = Path(star_tools.get_data_dir(plugin_name))
            except Exception as exc:  # noqa: BLE001
                self.log().warning("通过 StarTools 解析数据目录失败：%s", safe_detail(exc))

        if resolved is None:
            try:
                from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

                resolved = Path(get_astrbot_plugin_data_path()) / plugin_name
            except Exception as exc:  # noqa: BLE001
                self.log().warning("解析 AstrBot 数据目录失败：%s", safe_detail(exc))

        if resolved is None:
            resolved = Path.cwd() / "data" / "plugin_data" / plugin_name
            self.log().warning("使用工作目录兜底作为数据目录：%s", resolved)

        try:
            resolved.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.log().error("创建数据目录失败：%s", safe_detail(exc))
        self._data_dir = resolved
        return resolved

    def app_config(self) -> Mapping[str, Any]:
        return self._config

    def log(self) -> Any:
        return self._logger

    def now(self) -> float:
        return time.time()

    # ------------------------------------------------------------------ #
    # 键值存储
    # ------------------------------------------------------------------ #

    async def kv_get(self, key: str, default: Any = None) -> Any:
        getter = getattr(self._star, "get_kv_data", None)
        if callable(getter):
            try:
                value = await getter(key, default)
                return default if value is None else value
            except Exception as exc:  # noqa: BLE001
                self.log().debug("KV 读取失败，转用内存兜底：%s", safe_detail(exc))
        return self._kv_fallback.get(key, default)

    async def kv_put(self, key: str, value: Any) -> None:
        setter = getattr(self._star, "put_kv_data", None)
        if callable(setter):
            try:
                await setter(key, value)
                return
            except Exception as exc:  # noqa: BLE001
                self.log().debug("KV 写入失败，转用内存兜底：%s", safe_detail(exc))
        self._kv_fallback[key] = value

    async def kv_delete(self, key: str) -> None:
        remover = getattr(self._star, "delete_kv_data", None)
        if callable(remover):
            try:
                await remover(key)
                return
            except Exception as exc:  # noqa: BLE001
                self.log().debug("KV 删除失败：%s", safe_detail(exc))
        self._kv_fallback.pop(key, None)

    # ------------------------------------------------------------------ #
    # 主动发送
    # ------------------------------------------------------------------ #

    async def send_message(self, umo: str, text: str) -> bool:
        if not umo or not text:
            return False
        try:
            chain = self._build_chain(text)
            if chain is None:
                return False
            result = await self._context.send_message(umo, chain)
            return bool(result)
        except Exception as exc:  # noqa: BLE001 - 主动发送失败不应影响主流程
            self.log().warning("主动发送消息失败：%s", safe_detail(exc))
            return False

    def _build_chain(self, text: str) -> Any | None:
        message_chain = compat.SYMBOLS.MessageChain
        if message_chain is not None:
            try:
                return message_chain().message(text)
            except Exception:  # noqa: BLE001
                pass
        plain = compat.SYMBOLS.Plain
        if plain is not None:
            try:
                return [plain(text=text)]
            except Exception:  # noqa: BLE001
                return None
        return None
