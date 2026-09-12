"""运行时改写插件配置 schema，用于注入「动态下拉选项」。

背景：AstrBot 的 ``_special: "select_provider"`` 在框架里被**硬编码为对话模型**
（``ConfigItemRenderer.vue`` 传 ``provider-type="chat_completion"``），且不存在
嵌入模型专用的 special 值。因此「选择嵌入模型提供商」无法通过静态 schema 实现，
只能在运行时把真实的嵌入模型列表注入到字段的 ``options``/``labels`` 上。

要点（均已对照 AstrBot 源码确认）：

- schema 挂在**插件自己的 config 对象**上（``self.config.schema``），而不是
  ``context.get_config().schema``（后者是全局配置，schema 恒为 None）；
- 它是普通可变 dict，前端每次打开配置页都会重新读取，因此改了就生效；
- 保存插件配置会触发插件重载（重建 AstrBotConfig），注入会被重置，
  所以必须**周期性重新注入**（参考 livingmemory_ext 的做法）；
- 只有在字段**没有** ``options`` 时才是文本框，注入后才变下拉框 ——
  这意味着注入失败时用户仍可手填提供商 ID，是安全的降级。
"""

from __future__ import annotations

from typing import Any, Sequence


def _locate_field(schema: Any, path: Sequence[str]) -> dict[str, Any] | None:
    """按点号路径定位到字段定义节点；任一层缺失即返回 None。"""
    node: Any = schema
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
        if node is None:
            return None
    return node if isinstance(node, dict) else None


def inject_string_options(
    config: Any,
    path: Sequence[str],
    options: Sequence[str],
    labels: Sequence[str] | None = None,
) -> bool:
    """给某个 string 字段注入下拉选项。

    Args:
        config: 插件配置对象（需带 ``schema`` 属性，通常是 ``AstrBotConfig``）。
        path: 字段路径，例如 ``("memory", "embedding_provider_id")``。
        options: 保存值列表（顺序即展示顺序）。
        labels: 与 ``options`` 等长的展示文本；为空时用 options 自身。

    Returns:
        是否注入成功。任何结构不符都返回 ``False`` 而不抛异常（配置注入不应成为故障点）。
    """
    schema = getattr(config, "schema", None)
    if not isinstance(schema, dict):
        return False

    field = _locate_field(schema, path)
    if field is None:
        return False

    values = [str(item) for item in options]
    try:
        field["options"] = values
        field["labels"] = [str(item) for item in labels] if labels is not None else values
    except (TypeError, KeyError):
        return False
    return True


def clear_options(config: Any, path: Sequence[str]) -> bool:
    """移除运行时注入的选项，使字段退回文本框（用于关闭向量能力时）。"""
    schema = getattr(config, "schema", None)
    if not isinstance(schema, dict):
        return False
    field = _locate_field(schema, path)
    if field is None:
        return False
    field.pop("options", None)
    field.pop("labels", None)
    return True
