"""面板 Web API 契约测试。

``web/api.py`` 是唯一接触 AstrBot Web 框架的适配层，本地开发环境通常没有安装
``astrbot``，因此这里注入一个最小 ``astrbot.api.web`` 替身再来驱动真实处理器：

- 路由注册表里的每个端点都必须对应一个真实存在的处理器，且端点不重复
  （重复注册会让后注册的覆盖前一个，面板表现为「接口莫名返回旧数据」）；
- API 里用到的 ``app.xxx`` 都必须在 ``SuperAstrBotApp`` 上真实存在
  （否则只会在运行时抛 AttributeError，面板表现为整页报错）；
- 现实桥列表与勾选导出的返回结构要与前端约定一致（标题、类型、数组化标签、类型计数）。
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from super_astrbot.app import SuperAstrBotApp
from super_astrbot.spec.scopes import MemoryScope

from .test_app_integration import FakeContext, FakeStar


# --------------------------------------------------------------------------- #
# 框架替身：只在真实 astrbot 缺失时注入，避免遮蔽线上依赖
# --------------------------------------------------------------------------- #


class FakeQuery:
    def __init__(self, params: dict[str, Any]) -> None:
        self._params = params

    def get(self, name: str, default: Any = None, type: Any = None) -> Any:  # noqa: A002
        value = self._params.get(name, default)
        if value is None or type is None or isinstance(value, type):
            return value
        try:
            return type(value)
        except (TypeError, ValueError):
            return default


class FakeRequest:
    """``request`` 的替身：query / json / files 三个面。"""

    def __init__(
        self,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> None:
        self.query = FakeQuery(query or {})
        self._body = body
        self._files = files or {}

    async def json(self, default: Any = None) -> Any:
        return self._body if self._body is not None else default

    async def files(self) -> dict[str, Any]:
        return self._files


def _install_web_stub() -> Any:
    """注入最小 web 模块；返回持有 ``request`` 的模块对象。

    注意：``web/api.py`` 在导入时就绑定了 ``request`` 这个名字，因此测试只能
    **原地改这个对象的属性**，不能把模块属性换成另一个对象（换了也读不到）。
    """
    if "astrbot.api.web" in sys.modules:
        return sys.modules["astrbot.api.web"]

    web = types.ModuleType("astrbot.api.web")
    web.request = FakeRequest()
    # 真实框架的 json_response 直接返回响应体（信封由处理器的 _ok() 自己拼），
    # 这里必须同样处理，否则会重复包一层导致断言看到的是嵌套结构。
    web.json_response = lambda payload: payload
    web.error_response = lambda message, status_code=400: {
        "status": "error",
        "message": message,
        "status_code": status_code,
    }
    astrbot = sys.modules.setdefault("astrbot", types.ModuleType("astrbot"))
    api = sys.modules.setdefault("astrbot.api", types.ModuleType("astrbot.api"))
    setattr(astrbot, "api", api)
    setattr(api, "web", web)
    sys.modules["astrbot.api.web"] = web
    return web


_WEB = _install_web_stub()
_REQUEST: FakeRequest = _WEB.request

from super_astrbot.web import api as web_api  # noqa: E402  必须在注入替身之后导入


def _use_request(
    *,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
) -> None:
    _REQUEST.query = FakeQuery(query or {})
    _REQUEST._body = body
    _REQUEST._files = files or {}


def _data(response: dict[str, Any]) -> dict[str, Any]:
    assert response.get("status") == "ok", response
    return response["data"]


@pytest.fixture()
def app(tmp_path: Path) -> Any:
    return SuperAstrBotApp(star=FakeStar(), context=FakeContext(), config={}, data_dir=tmp_path)


# --------------------------------------------------------------------------- #
# 注册表与依赖面
# --------------------------------------------------------------------------- #


def test_routes_point_to_existing_handlers() -> None:
    import ast

    source = (Path(web_api.__file__)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    defined = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    endpoints: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Tuple)
            and len(node.elts) == 4
            and isinstance(node.elts[0], ast.Constant)
            and isinstance(node.elts[0].value, str)
            and isinstance(node.elts[1], ast.Call)
        ):
            endpoints.append(node.elts[0].value)
            func = node.elts[1].func
            if isinstance(func, ast.Name):
                assert func.id in defined, f"端点 {node.elts[0].value} 指向未定义的 {func.id}"

    assert len(endpoints) == len(set(endpoints)), "端点重复注册会互相覆盖"
    assert "journals/export-selected" in endpoints
    assert "journals" in endpoints


def test_api_only_calls_existing_app_members() -> None:
    import re

    source = Path(web_api.__file__).read_text(encoding="utf-8")
    members = set(dir(SuperAstrBotApp))
    referenced = set(re.findall(r"\bapp\.([a-z_][a-z0-9_]*)", source))
    missing = sorted(name for name in referenced if name not in members)
    assert missing == [], f"API 层引用了 App 上不存在的成员：{missing}"


# --------------------------------------------------------------------------- #
# 现实桥：列表与导出
# --------------------------------------------------------------------------- #


def test_journals_endpoint_filters_and_payload(app: Any) -> None:
    async def _run() -> dict[str, Any]:
        await app.start()
        try:
            await app.panel_journal_add(
                {
                    "content": "面试前夜的碎念",
                    "title": "面试前夜",
                    "type": "diary",
                    "tags": ["工作"],
                    "emotion": 4,
                    "event_time": 1757500000.0,
                }
            )
            await app.panel_journal_add({"content": "本周跑步三次", "event_time": 1757500000.0})

            _use_request(query={"entry_type": "diary", "limit": 20})
            filtered = _data(await web_api._journals(app)())
            _use_request(query={})
            everything = _data(await web_api._journals(app)())
            return {"filtered": filtered, "everything": everything}
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    filtered = data["filtered"]
    assert filtered["total"] == 1 and filtered["entry_type"] == "diary"
    assert filtered["types"] == ["weekly", "diary", "essay"]
    assert filtered["default_type"] == "weekly"
    assert filtered["type_counts"] == {"weekly": 1, "diary": 1}

    item = filtered["items"][0]
    assert item["title"] == "面试前夜" and item["type"] == "diary"
    assert item["tags"] == ["工作"], "标签必须是数组，字符串会让前端标签列渲染成空"
    assert set(item) >= {"id", "title", "content", "type", "tags", "emotion", "created_at"}

    # 未填标题的记录：读取侧补「当天日期时间」
    plain = next(row for row in data["everything"]["items"] if row["content"] == "本周跑步三次")
    assert plain["type"] == "weekly"
    assert plain["title"].startswith("2025") and ":" in plain["title"]


def test_export_selected_endpoint_validates_and_expands(app: Any) -> None:
    async def _run() -> dict[str, Any]:
        await app.start()
        try:
            first = await app.panel_journal_add({"content": "记录一", "type": "diary"})
            await app.panel_journal_add({"content": "记录二", "type": "essay"})

            _use_request(body={"ids": [first["journal_id"]], "all": False})
            picked = await web_api._journal_export_selected(app)()
            _use_request(body={"ids": [], "all": False})
            empty = await web_api._journal_export_selected(app)()
            _use_request(body={"all": True, "type": "essay", "keyword": ""})
            expanded = _data(await web_api._journal_export_selected(app)())
            return {"picked": _data(picked), "empty": empty, "expanded": expanded}
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    assert data["picked"]["mode"] == "ids" and data["picked"]["count"] == 1
    assert data["picked"]["items"][0]["content"] == "记录一"
    assert data["picked"]["kind"] == "super_astrbot.journals"
    assert data["empty"]["status"] == "error", "没有勾选任何条目应返回错误而不是空文件"
    assert data["expanded"]["mode"] == "filter" and data["expanded"]["count"] == 1
    assert data["expanded"]["items"][0]["type"] == "essay"


def test_journal_write_endpoints_reject_bad_input(app: Any) -> None:
    async def _run() -> dict[str, Any]:
        await app.start()
        try:
            _use_request(body={"content": "   "})
            empty_add = await web_api._journal_add(app)()
            _use_request(body={"id": 0, "content": "正文"})
            bad_id = await web_api._journal_update(app)()
            _use_request(body={"id": 1, "content": " "})
            empty_content = await web_api._journal_update(app)()
            return {
                "empty_add": empty_add,
                "bad_id": bad_id,
                "empty_content": empty_content,
            }
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    for response in data.values():
        assert response["status"] == "error", response
    assert "id" in data["bad_id"]["message"]


def test_journal_import_endpoint_keeps_type_and_title(app: Any) -> None:
    async def _run() -> dict[str, Any]:
        await app.start()
        try:
            payload = json_dumps(
                [
                    {
                        "title": "来自旧备份",
                        "content": "旧文件里的记录",
                        "entry_type": "essay",
                        "tags": ["迁移"],
                        "event_time": 1757500000.0,
                        "scope": "user:10086",
                    },
                    {"content": ""},
                ]
            )

            class _Upload:
                async def read(self) -> bytes:
                    return payload.encode("utf-8")

            _use_request(files={"file": _Upload()})
            response = _data(await web_api._journal_import(app)())
            rows = await app.memory.list_all_journals(offset=0, limit=10)
            return {"response": response, "row": rows[0]}
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    assert data["response"]["imported"] == 1 and data["response"]["skipped"] == 1
    assert data["row"]["entry_type"] == "essay"
    assert data["row"]["title"] == "来自旧备份"


def json_dumps(payload: Any) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


UMO = "aiocqhttp:FriendMessage:10086"


def test_backup_import_endpoint_roundtrip(app: Any) -> None:
    """HTTP 层：一次导出 → 上传回灌，创建时间等元数据保持不变。"""

    class _Upload:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        async def read(self) -> bytes:
            return self._payload

    async def _run() -> dict[str, Any]:
        await app.start()
        try:
            await app._memory_service.remember_text(
                MemoryScope.for_session(UMO), "带原始时间的记忆", created_at=1_700_000_000.0
            )
            built = await app.panel_backup_build({})
            raw = Path(built["path"]).read_bytes()

            _use_request(files={"file": _Upload(raw)})
            imported = _data(await web_api._backup_import(app)())
            _use_request(files={})
            missing = await web_api._backup_import(app)()
            rows = await app.memory.list_all(offset=0, limit=5)
            return {"imported": imported, "missing": missing, "rows": rows}
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    imported = data["imported"]
    assert imported["manifest"]["kind"] == "super_astrbot.backup"
    # 逐表 INSERT OR REPLACE：同一份备份灌回同一个库不会产生重复行
    assert len(data["rows"]) == 1, f"恢复不应制造重复：{[r.content for r in data['rows']]}"
    assert data["rows"][0].created_at == 1_700_000_000.0, "创建时间按原值回填"
    assert imported["planned_rows"] > 0 and imported["rows_written"] > 0
    assert "关键词索引重建" in imported["reindex"]
    assert imported["tables"]["memories"] == 1
    assert data["missing"]["status"] == "error", "缺文件字段应报错"


# --------------------------------------------------------------------------- #
# 记忆 / 每周总结：侧滑详情面板的编辑与删除
# --------------------------------------------------------------------------- #


def test_memory_write_endpoints_roundtrip(app: Any) -> None:
    """记忆编辑/删除接口：改完能按新正文检索到，删完不再可召回。"""

    async def _run() -> dict[str, Any]:
        await app.start()
        try:
            scope = MemoryScope.for_session("aiocqhttp:FriendMessage:1")
            memory_id = await app.memory.remember_text(scope, "用户喜欢在周末爬山放松")

            _use_request(body={"id": memory_id, "content": "用户改成傍晚沿着江边跑步"})
            updated = await web_api._memory_update(app)()
            recalled = await app.memory.recall(scope, "江边 跑步")
            stale = await app.memory.recall(scope, "爬山")

            _use_request(body={"id": memory_id, "content": "   "})
            blank = await web_api._memory_update(app)()
            _use_request(body={"id": 0, "content": "内容够长"})
            bad_id = await web_api._memory_update(app)()

            _use_request(body={"id": memory_id})
            deleted = await web_api._memory_delete(app)()
            _use_request(body={"id": memory_id})
            deleted_again = await web_api._memory_delete(app)()
            after = await app.memory.recall(scope, "江边 跑步")
            return {
                "updated": updated,
                "content": " ".join(item.content for item in recalled.items),
                "stale_hits": len(stale.items),
                "blank": blank,
                "bad_id": bad_id,
                "deleted": deleted,
                "deleted_again": deleted_again,
                "after_delete": len(after.items),
            }
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    assert _data(data["updated"])["ok"] is True
    assert "江边" in data["content"], "编辑后应能按新正文召回"
    assert data["stale_hits"] == 0, "旧正文不该再被召回"
    assert data["blank"]["status"] == "error"
    assert "内容" in data["blank"]["message"]
    assert data["bad_id"]["status"] == "error"
    assert _data(data["deleted"])["ok"] is True
    # 删除是幂等的（状态置为已遗忘）：重复点删除仍报「已删除」，不会因为已经不在了而报错
    assert _data(data["deleted_again"])["ok"] is True
    assert data["after_delete"] == 0


def test_weekly_update_endpoint_edits_memory_row(app: Any) -> None:
    """每周总结就是 memories 里 source=weekly_reflection 的行：编辑走同一条写入路径。"""

    async def _run() -> dict[str, Any]:
        await app.start()
        try:
            scope = MemoryScope.for_session("aiocqhttp:FriendMessage:1")
            weekly_id = await app.memory.remember_text(
                scope,
                "本周用户完成了插件修复",
                kind="insight",
                source="weekly_reflection",
                importance=0.75,
            )
            _use_request(body={"id": weekly_id, "content": "本周用户完成了两轮修复与回归测试"})
            updated = await web_api._weekly_update(app)()
            page = await app.weeklies_page(offset=0, limit=10)
            listed = [item for item in page["items"] if item["id"] == weekly_id]
            _use_request(body={"id": weekly_id, "content": ""})
            blank = await web_api._weekly_update(app)()
            return {
                "updated": updated,
                "content": listed[0]["content"] if listed else "",
                "count": page["total"],
                "blank": blank,
            }
        finally:
            await app.shutdown()

    data = asyncio.run(_run())
    assert _data(data["updated"])["ok"] is True
    assert data["content"] == "本周用户完成了两轮修复与回归测试"
    assert data["count"] == 1
    assert data["blank"]["status"] == "error"
