"""``RerankGateway`` 的 AstrBot 实现（重排序 / Rerank）。

事实与取舍（对照 AstrBot 4.x 与同类插件的既有实现）：

- 重排序提供商由 ``context.get_all_rerank_providers()`` 枚举；未启用的候选可从
  ``provider_manager.providers_config``（``provider_type == "rerank"``）读到，
  用于配置页提前展示；
- ``provider.rerank(query, documents, top_n)`` 返回带 ``index`` / ``relevance_score``
  的对象列表。本网关用**鸭子类型**读取（并兼容 ``{index, relevance_score}`` 字典），
  不硬依赖 ``astrbot.core.provider.entities``，避免框架内部模块改名时直接崩掉；
- 多数重排序 API 对 query 有长度上限（常见 256~512 tokens），调用前先截断；
- Provider 实例可能被框架重建（旧实例的 httpx 客户端已关闭）。因此**调用失败即失效缓存**，
  下次调用重新解析——与嵌入网关「失败不缓存结论」同一思路。

**本网关永不抛异常**：不可用、调用失败、返回结构异常都返回空列表，
由检索层决定回退策略（见 ``memory/retriever/rerank.py``）。
"""

from __future__ import annotations

from typing import Any, Sequence

from ..spec.errors import safe_detail
from ..support import truncate
from . import astrbot_compat as compat
from .protocols import ProviderInfo, RerankHit

_MAX_QUERY_CHARS = 512
"""查询截断长度：多数重排序接口对 query 长度有硬限制。"""

_MAX_DOC_CHARS = 1024
"""单条候选文档截断长度：避免超长记忆把请求体撑爆。"""


def parse_hits(raw: Any, count: int) -> list[RerankHit]:
    """把框架返回的重排序结果规整为 ``RerankHit`` 列表（按分数降序）。

    兼容对象与字典两种形态；下标越界、分数非数字的条目直接丢弃——
    面对不可信的提供商输出，宁可少用几条评分，也不要让脏数据影响排序。
    """
    if not isinstance(raw, (list, tuple)):
        return []

    hits: list[RerankHit] = []
    for item in raw:
        index: Any = getattr(item, "index", None)
        score: Any = getattr(item, "relevance_score", None)
        if index is None or score is None:
            if isinstance(item, dict):
                index = item.get("index")
                score = item.get("relevance_score", item.get("score"))
        try:
            position = int(index)
            value = float(score)
        except (TypeError, ValueError):
            continue
        if not 0 <= position < count:
            continue
        hits.append(RerankHit(index=position, score=value))

    hits.sort(key=lambda hit: hit.score, reverse=True)
    return hits


class AstrBotRerankGateway:
    """基于 AstrBot 提供商的可选重排序能力。"""

    def __init__(self, context: Any, host: Any, *, provider_id: str = "") -> None:
        self._context = context
        self._host = host
        self._preferred_id = (provider_id or "").strip()
        self._provider: Any | None = None
        self._resolved = False

    # ------------------------------------------------------------------ #
    # 解析
    # ------------------------------------------------------------------ #

    def _resolve(self) -> None:
        """解析重排序提供商；失败不缓存结论（ProviderManager 可能尚未就绪）。"""
        if self._resolved and self._provider is not None:
            return
        try:
            providers = self._loaded_providers()
            if self._preferred_id:
                for candidate in providers:
                    if compat.provider_meta(candidate)["id"] == self._preferred_id:
                        self._provider = candidate
                        self._resolved = True
                        return
                # 枚举列表里找不到时再按 ID 直取：兼容未暴露列表但可查实例的框架版本。
                getter = getattr(self._context, "get_provider_by_id", None)
                if callable(getter):
                    candidate = getter(self._preferred_id)
                    if candidate is not None and hasattr(candidate, "rerank"):
                        self._provider = candidate
                        self._resolved = True
                        return
            # 未配置首选 ID，或首选 ID 已不可用：回退到第一个可用提供商。
            # 与嵌入网关保持一致——用户填错 ID 时能力仍可用，而不是静默失效。
            if providers:
                self._provider = providers[0]
                self._resolved = True
                return
        except Exception as exc:  # 探测失败按不可用处理
            self._host.log().debug("解析 Rerank 提供商失败：%s", safe_detail(exc))
        self._provider = None
        self._resolved = False

    def refresh(self, provider_id: str | None = None) -> None:
        """清除解析缓存并允许重新解析；``provider_id`` 非空时同时更新首选 ID。"""
        if provider_id is not None:
            self._preferred_id = (provider_id or "").strip()
        self._resolved = False
        self._provider = None

    def _loaded_providers(self) -> list[Any]:
        getter = getattr(self._context, "get_all_rerank_providers", None)
        if not callable(getter):
            return []
        try:
            return [item for item in (getter() or []) if hasattr(item, "rerank")]
        except Exception as exc:
            self._host.log().debug("枚举 Rerank 提供商失败：%s", safe_detail(exc))
            return []

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #

    @property
    def available(self) -> bool:
        self._resolve()
        return self._provider is not None

    def model(self) -> str:
        """当前提供商的模型名；不可用时为空串。"""
        self._resolve()
        if self._provider is None:
            return ""
        getter = getattr(self._provider, "get_model", None)
        if callable(getter):
            try:
                return str(getter() or "")
            except Exception:
                return ""
        return compat.provider_meta(self._provider)["model"]

    def list_providers(self) -> list[ProviderInfo]:
        """列出可选的重排序提供商（已加载实例 + 已配置但未启用的条目）。"""
        result: list[ProviderInfo] = []
        seen: set[str] = set()

        for provider in self._loaded_providers():
            meta = compat.provider_meta(provider)
            provider_id = meta["id"]
            if provider_id and provider_id not in seen:
                seen.add(provider_id)
                result.append(ProviderInfo(id=provider_id, type="rerank", model=meta["model"]))

        for entry in compat.configured_provider_entries(self._context, "rerank"):
            provider_id = str(entry.get("id") or "")
            if provider_id and provider_id not in seen:
                seen.add(provider_id)
                result.append(
                    ProviderInfo(id=provider_id, type="rerank", model=str(entry.get("model") or ""))
                )
        return result

    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int | None = None,
    ) -> list[RerankHit]:
        """重排序候选；任何失败都返回空列表（调用方须回退）。"""
        self._resolve()
        if self._provider is None or not documents:
            return []
        text = (query or "").strip()
        if not text:
            return []

        call = getattr(self._provider, "rerank", None)
        if not callable(call):
            return []

        docs = [truncate(str(document or ""), _MAX_DOC_CHARS) for document in documents]
        try:
            raw = await call(text[:_MAX_QUERY_CHARS], docs, top_n)
        except Exception as exc:
            # 实例可能已被框架重建（旧连接已关闭），失效缓存以便下次重新解析。
            self._host.log().debug("Rerank 调用失败：%s", safe_detail(exc))
            self.refresh()
            return []
        return parse_hits(raw, len(docs))


__all__ = ["AstrBotRerankGateway", "parse_hits"]
