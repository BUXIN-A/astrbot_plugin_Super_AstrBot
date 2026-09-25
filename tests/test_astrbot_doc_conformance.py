"""AstrBot 开发文档一致性检查（把「对照文档核一遍」固化成回归测试）。

依据官方文档：

- 插件基础：<https://docs.astrbot.app/dev/star/plugin.html>
  （``Star`` 子类 + ``initialize``/``terminate``、``_conf_schema.json`` 会被解析为
  ``data/config/<插件名>_config.json``、持久化数据必须落在 ``data`` 目录、``metadata.yaml``
  供插件市场展示、可用的 ``filter`` 装饰器清单）
- 插件页面：<https://docs.astrbot.app/dev/star/guides/plugin-pages.html>
  （``register_web_api(route, handler, methods, desc)``；route 带插件名前缀、Page 端 endpoint
  不带前缀且必须是不含 scheme/query/``..`` 的相对路径；响应契约 ``{status, data}``；
  ``bridge.download`` + 后端 ``file_response`` 的文件下载通道；上传字段名固定为 ``file``）

这些约束一旦漂移，表现是「面板整页 500 / 下载点了没反应 / 市场信息空白」，
因此值得用测试钉住，而不是靠人肉复检。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = ROOT / "main.py"
WEB_API = ROOT / "super_astrbot" / "web" / "api.py"
APP_PY = ROOT / "super_astrbot" / "app.py"
PAGES = ROOT / "pages" / "dashboard"

_DOCUMENTED_FILTERS = {
    "command",
    "command_group",
    # 分组内的子命令注册
    "group",
    "event_message_type",
    "platform_adapter_type",
    "permission_type",
    "on_astrbot_loaded",
    "on_llm_request",
    "on_llm_response",
    "after_message_sent",
    "on_agent_begin",
    "on_agent_done",
    "on_decorating_result",
    "on_using_llm_tool",
    "on_llm_tool_respond",
    "llm_tool",
    # 自定义门控（插件在注册前用 hasattr 探测，框架缺失时整体降级）
    "custom_filter",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_metadata_declares_marketplace_fields() -> None:
    meta = _read(ROOT / "metadata.yaml")
    for key in (
        "name:",
        "display_name:",
        "desc:",
        "short_desc:",
        "version:",
        "author:",
        "repo:",
        "astrbot_version:",
        "support_platforms:",
    ):
        assert key in meta, f"metadata.yaml 缺少市场展示字段 {key}"
    assert "name: astrbot_plugin_Super_AstrBot" in meta, "元数据插件名必须与目录名一致"


def test_conf_schema_is_at_plugin_root_and_parseable() -> None:
    schema = json.loads(_read(ROOT / "_conf_schema.json"))
    assert isinstance(schema, dict) and schema, "配置 Schema 不能为空"
    for group in ("basic", "memory", "journal"):
        assert group in schema, f"配置分组 {group} 缺失"


def test_star_lifecycle_and_command_registration() -> None:
    main = _read(MAIN_PY)
    assert "class SuperAstrBot(Star)" in main
    assert "async def initialize" in main and "async def terminate" in main
    assert "def __init__(self, context: Context, config" in main, "构造签名需接收 (context, config)"
    assert "@filter.command(" in main, "指令应通过 @filter.command 注册"


def test_only_documented_filters_are_used() -> None:
    used = set(re.findall(r"@filter\.([a-z_]+)", _read(MAIN_PY)))
    unknown = used - _DOCUMENTED_FILTERS
    assert not unknown, f"使用了文档之外的 filter：{sorted(unknown)}"


def test_web_routes_are_prefixed_and_endpoints_are_relative() -> None:
    api = _read(WEB_API)
    # route 必须带插件名前缀（文档：注册时带名字，Page 端调用时不带）
    assert "for prefix in (f\"/{PLUGIN_NAME}\", f\"/{PLUGIN_NAME_LOWER}\"):" in api
    assert 'context.register_web_api(f"{prefix}/{endpoint}", handler, methods, desc)' in api

    endpoints = re.findall(r'\("([^"]+)",\s*_[a-z_]+\(app\),\s*\[', api)
    assert endpoints, "未解析到任何端点"
    for endpoint in endpoints:
        assert endpoint, "端点不能为空"
        assert not endpoint.startswith("/"), f"端点不应以 / 开头：{endpoint}"
        assert "\\" not in endpoint and ".." not in endpoint, f"端点含非法路径：{endpoint}"
        assert "://" not in endpoint and "?" not in endpoint and "#" not in endpoint, (
            f"端点不得含 scheme/query/fragment：{endpoint}"
        )
    assert len(endpoints) == len(set(endpoints)), "端点重复注册会互相覆盖"


def test_response_contract_and_upload_field() -> None:
    api = _read(WEB_API)
    assert 'return json_response({"status": "ok", "data": data})' in api, "统一 ok 信封缺失"
    assert "error_response(" in api, "统一错误信封缺失"
    assert 'files.get("file")' in api, "上传字段名必须固定为 file"


def test_file_download_uses_documented_channel_with_fallback() -> None:
    api = _read(WEB_API)
    assert "from astrbot.api.web import file_response" in api, "应使用文档的 file_response"
    assert "except ImportError:" in api, "老版本框架无 file_response 时需降级"
    front = _read(PAGES / "app.js")
    assert 'typeof bridge.download === "function"' in front, "前端应优先使用 bridge.download"


def test_page_and_i18n_declaration() -> None:
    assert (PAGES / "index.html").is_file()
    for locale in ("zh-CN", "en-US"):
        data = json.loads(_read(ROOT / ".astrbot-plugin" / "i18n" / f"{locale}.json"))
        title = data.get("pages", {}).get("dashboard", {}).get("title")
        assert title, f"{locale} 缺少 pages.dashboard.title（页面标题会显示为 dashboard）"


def test_persistent_data_stays_under_data_dir() -> None:
    """文档明确「持久化数据请存储于 data 目录下，而非插件自身目录」。"""
    app = _read(APP_PY)
    assert "self._data_dir" in app, "数据目录应由宿主解析后统一持有"
    # 备份包、迁移前快照、提示词覆盖文件都必须基于 data_dir 拼接
    assert "self._data_dir / BACKUP_DIR_NAME" in _read(ROOT / "super_astrbot/backup/service.py")
    assert "_DB_FILENAME" in app and "self._data_dir" in app
