"""控制台静态契约测试。

这些断言直接对应 v0.1.0 在真实服务器上踩到的坑，用来防止回归：

1. 脚本必须是 ``type="module"``：AstrBot 把 bridge SDK 注入到 ``</body>`` 之前，
   classic 内联脚本会先执行，此时 ``window.AstrBotPluginPage`` 为 null。
2. **不得出现 ``fetch(``**：插件页运行在无 ``allow-same-origin`` 的 sandbox iframe 中，
   直连请求会抛 ``Failed to fetch``；所有请求必须经 bridge。
3. 不得引用外部 CDN（离线环境与 CSP 都会出问题）。
4. 前后端 endpoint 必须一一对应，避免接口漂移导致面板空白。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "pages" / "dashboard"
WEB_API = ROOT / "super_astrbot" / "web" / "api.py"
I18N_DIR = ROOT / ".astrbot-plugin" / "i18n"

ENDPOINTS = (
    "overview",
    "models",
    "features",
    "feature-toggle",
    "memories",
    "memory",
    "memories/update",
    "memories/delete",
    "weeklies/update",
    "search",
    "journals",
    "journals/export-selected",
    "reviews",
    "reviews/batch",
    "identities",
    "scopes",
    "scopes/migrate",
    "backup/export",
    "backup/import",
    "backup/list",
    "backup/replace-database",
    "review-action",
    "persona",
    "graph",
    "monitor",
    "prompts",
    "prompt-save",
    "prompt-reset",
    "maintenance",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_page_files_exist() -> None:
    assert (PAGES / "index.html").is_file()
    assert (PAGES / "app.js").is_file()
    assert (PAGES / "styles.css").is_file()


def test_entry_script_is_module_and_relative() -> None:
    html = _read(PAGES / "index.html")
    assert re.search(r'<script\s+type="module"\s+src="\./app\.js"\s*>\s*</script>', html), (
        "入口脚本必须是 type=module 的相对路径引用，否则会在 bridge 注入前执行"
    )
    assert 'href="./styles.css"' in html


def test_no_direct_fetch_anywhere() -> None:
    """直连 fetch 在 sandbox iframe 中必然失败，必须全部走 bridge。"""
    for name in ("index.html", "app.js"):
        content = _read(PAGES / name)
        assert "fetch(" not in content, f"{name} 中出现了 fetch()，请改为 bridge 调用"
    assert "new EventSource" not in _read(PAGES / "app.js"), "SSE 必须使用 bridge.subscribeSSE"


def test_bridge_is_called_lazily_and_readied() -> None:
    js = _read(PAGES / "app.js")
    assert "window.AstrBotPluginPage" in js
    assert "bridge.ready()" in js, "首次请求前必须 await bridge.ready()"
    assert ".apiGet(" in js and ".apiPost(" in js


def test_no_external_cdn_references() -> None:
    html = _read(PAGES / "index.html")
    external = re.findall(r'(?:src|href)\s*=\s*["\']https?://[^"\']+', html)
    assert external == [], f"页面引用了外部资源：{external}"


def test_frontend_endpoints_match_backend_registration() -> None:
    js = _read(PAGES / "app.js")
    backend = _read(WEB_API)
    for endpoint in ENDPOINTS:
        assert f'"{endpoint}"' in js, f"前端未使用接口 {endpoint}"
        assert f'("{endpoint}"' in backend, f"后端未注册接口 {endpoint}"


def test_html_escape_covers_five_characters() -> None:
    """只转义部分字符会留下属性注入（存储型 XSS）风险。"""
    js = _read(PAGES / "app.js")
    for token in ("&amp;", "&lt;", "&gt;", "&quot;", "&#39;"):
        assert token in js, f"转义函数缺少 {token}"


def test_plugin_i18n_declares_page_title() -> None:
    for locale in ("zh-CN", "en-US"):
        path = I18N_DIR / f"{locale}.json"
        assert path.is_file(), f"缺少插件 i18n：{path.name}"
        data = json.loads(_read(path))
        title = data.get("pages", {}).get("dashboard", {}).get("title")
        assert title, f"{locale} 未声明 pages.dashboard.title（WebUI 标签页会显示为 dashboard）"


def test_plugin_logo_present() -> None:
    """AstrBot 要求插件目录下有 logo.png（1:1，推荐 256x256）。"""
    logo = ROOT / "logo.png"
    assert logo.is_file(), "缺少 logo.png"
    data = logo.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "logo.png 不是合法 PNG"
    assert len(data) > 0


def test_panel_uses_plugin_logo() -> None:
    """品牌区允许两种形态：主题切换的矢量图标，或自定义文字品牌（本地定制）。"""
    html = _read(PAGES / "index.html")
    uses_svg_logos = "./logo-light.svg" in html and "./logo-dark.svg" in html
    has_text_brand = 'class="brand-name"' in html
    assert uses_svg_logos or has_text_brand, "品牌区应使用主题图标或文字品牌"
    if uses_svg_logos:
        assert (PAGES / "logo.svg").exists(), "同名兜底图标应保留"


def test_feature_toggle_ui_present() -> None:
    """功能开关界面必须存在且走 bridge。"""
    html = _read(PAGES / "index.html")
    js = _read(PAGES / "app.js")
    assert 'id="feat-list"' in html, "缺少功能开关容器"
    assert 'data-page="features"' in html, "缺少「功能」导航项"
    assert "loadFeatures" in js
    assert "handleFeatureToggle" in js
    assert "feature-toggle" in js


def test_every_capability_domain_has_frontend_title() -> None:
    """功能面板按 domain 分组渲染；注册表新增域时前端必须同步标题映射。"""
    from super_astrbot.spec.capabilities import CAPABILITIES

    js = _read(PAGES / "app.js")
    for domain in {item.domain for item in CAPABILITIES}:
        assert f'{domain}: "domain.' in js, f"前端缺少功能域 {domain} 的标题映射"
        assert f'"domain.{domain}"' in js, f"前端缺少功能域 {domain} 的中文文案"


def test_bridge_page_columns_and_type_options() -> None:
    """现实桥页的列顺序与三种文本类型选项是需求硬约束，防止后续改版时漂移。

    列顺序：``# / 标题 / 内容 / 类型 / 标签 / 情绪 / 创建时间 / 操作``（外加最左侧勾选列）。
    """
    html = _read(PAGES / "index.html")
    js = _read(PAGES / "app.js")

    # 1) 表头顺序：直接读 journalColumns() 里的 title 字段顺序
    block = js.split("function journalColumns()", 1)[1].split("function paintJournalsTable()", 1)[0]
    order = re.findall(r'title: t\("([^"]+)"\)', block)
    assert order == [
        "journals.titleField",
        "journals.content",
        "journals.type",
        "journals.tags",
        "journals.emotion",
        "journals.createdAt",
        "table.actions",
    ], f"现实桥列表列顺序不符：{order}"
    assert '"#"' in block, "缺少序号列"
    assert 'className: "check"' in block, "缺少勾选列（多选导出依赖它）"

    # 2) 类型下拉与编辑器三选一：取值与后端词表一一对应
    from super_astrbot.spec.entry_types import ENTRY_TYPES

    assert 'option value="" data-i18n="jtype.all"' in html
    for entry_type in ENTRY_TYPES:
        assert f'<option value="{entry_type}"' in html, f"类型筛选缺少 {entry_type}"
    assert "JOURNAL_TYPES = [" in js, "前端缺少类型白名单"
    assert 'id="jrf-type"' in js, "编辑器缺少类型选择器"


def test_bridge_page_bulk_actions_present() -> None:
    """多选 / 单选 / 全选 / 导出所选都必须挂在页面上。"""
    html = _read(PAGES / "index.html")
    js = _read(PAGES / "app.js")
    for element in ("jr-check-all", "jr-select-match", "jr-select-none", "jr-export-selected"):
        assert f'id="{element}"' in html, f"缺少批量操作控件 {element}"
    for handler in ("resetJournalSelection", "syncJournalSelectionUi", "journals/export-selected"):
        assert handler in js, f"缺少批量操作逻辑 {handler}"


def test_bridge_i18n_keys_complete() -> None:
    """中文文案是面板的兜底字典：缺键会直接显示成 key，等于界面坏掉。"""
    js = _read(PAGES / "app.js")
    zh = js.split('"zh-CN": {', 1)[1].split('"en-US": {', 1)[0]
    for key in (
        "nav.journals",
        "journals.title",
        "journals.titleField",
        "journals.createdAt",
        "journals.type",
        "journals.selectFilter",
        "journals.exportSelected",
        "jtype.weekly",
        "jtype.diary",
        "jtype.essay",
    ):
        assert f'"{key}"' in zh, f"中文文案缺少 {key}"


def test_backup_section_present_in_system_page() -> None:
    """系统页备份栏：导出 / 导入 / 历史表都在，且是系统页最后一张卡片。

    刻意不再提供「导出类型」勾选项：备份包固定包含配置 + 数据库快照 + 全部数据，
    少一处勾选就少一种「以为备份全了」的误判。
    """
    html = _read(PAGES / "index.html")
    js = _read(PAGES / "app.js")
    for element in ("bk-notes", "bk-export", "bk-import", "bk-import-label", "bk-table"):
        assert f'id="{element}"' in html, f"备份栏缺少控件 {element}"
    for removed in ("bk-config", "bk-database", "bk-data"):
        assert f'id="{removed}"' not in html, f"备份类型勾选项应已移除：{removed}"

    # 下载必须走官方 bridge.download，不可用时才退回内联 base64
    assert "bridge.download(" in js, "备份下载应优先使用 bridge.download"
    for endpoint in ("backup/export", "backup/import", "backup/list"):
        assert endpoint in js, f"前端未使用 {endpoint}"
    assert 'data-i18n="backup.title"' in html
    # 备份卡片位于系统页最下方（配置维护 / 维护之后）
    system_page = html[html.index('id="page-system"') : html.index('id="page-identity"')]
    headings = re.findall(r'<h2 data-i18n="([^"]+)"', system_page)
    assert headings[-1] == "backup.title", f"备份栏应排在系统页最后：{headings}"


def test_identity_page_present() -> None:
    """身份诊断页：观测表 + 作用域迁移控件。"""
    html = _read(PAGES / "index.html")
    js = _read(PAGES / "app.js")
    assert 'data-page="identity"' in html, "缺少「身份」导航项"
    assert "identity: () => loadIdentity()" in js
    for element in ("id-table", "id-summary", "sc-from", "sc-to", "sc-preview", "sc-apply"):
        assert f'id="{element}"' in html, f"身份页缺少控件 {element}"
    # 迁移是「先预览、再执行」，两者不能合并成一个按钮
    assert "migrateScope(true)" in js and "migrateScope(false)" in js


def test_review_batch_controls_present() -> None:
    html = _read(PAGES / "index.html")
    js = _read(PAGES / "app.js")
    assert 'id="rv-approve-all"' in html and 'id="rv-reject-all"' in html
    assert "reviews/batch" in js


def test_identity_nav_sits_right_after_learning() -> None:
    """身份页签排在「学习」下方，且页码顺序与导航一致（页码由 PAGE_TITLES 顺序生成）。"""
    html = _read(PAGES / "index.html")
    js = _read(PAGES / "app.js")

    nav_pages = re.findall(r'class="nav-item" data-page="([a-z]+)"', html)
    assert nav_pages.index("identity") == nav_pages.index("persona") + 1, nav_pages
    assert nav_pages[-1] == "system", "系统应留在最后一位"

    title_keys = re.findall(r'^  ([a-z]+): "nav\.', js, flags=re.M)
    assert title_keys.index("identity") == title_keys.index("persona") + 1, title_keys


def test_referenced_assets_exist() -> None:
    """HTML 里引用的本地静态资源必须真的在包里。

    线上表现是「页面静默少图 / 404」，而且打包分发时最容易漏——一次实际事故就是
    欢迎页的 girl.jpg 丢了却没人发现。
    """
    html = _read(PAGES / "index.html")
    js = _read(PAGES / "app.js")
    referenced = set(re.findall(r'(?:src|href)="\./([^"?#]+)"', html))
    referenced |= set(re.findall(r'src\.\s*=\s*"\./([^"?#]+)"', html))
    assert referenced, "未解析到任何本地资源引用"
    missing = sorted(name for name in referenced if not (PAGES / name).is_file())
    assert not missing, f"index.html 引用了不存在的资源：{missing}"

    # 插件 i18n 与 logo 是市场/页面壳的硬依赖
    assert (ROOT / "metadata.yaml").is_file()
    assert (PAGES / "logo.svg").is_file(), "缺少品牌兜底图标 logo.svg"

    # 默认落地页必须真实存在（导航被裁剪过一轮后曾出现「打开就是空白」）
    assert 'PAGE_TITLES[page] ? page : "overview"' in js, "未知页面应回退到存在的默认页"
    assert 'page-overview' in html, "默认落地页 overview 必须存在"


def test_backup_import_mode_ui_present() -> None:
    """导入模式选择、覆盖二次确认（含删除估算）、整库恢复入口都要在页面上。"""
    html = _read(PAGES / "index.html")
    js = _read(PAGES / "app.js")

    # 模式下拉：两个选项，取值与后端约定一致
    assert 'id="bk-mode"' in html
    assert '<option value="merge"' in html and '<option value="replace"' in html
    assert 'id="bk-db-actions"' in html, "缺少整库恢复入口容器"

    # 覆盖模式必须走「先预览（dry_run）→ 二次确认 → 真正导入」
    assert "dry_run: true" in js and "dry_run: false" in js
    assert "renderDatabaseRestoreEntry" in js
    assert "backup/replace-database" in js
    # 取消不能当成同意：确认弹窗必须能解析出 false
    assert "finish(false)" in js, "取消路径必须给出明确的否定结果"


def test_backup_mode_i18n_complete() -> None:
    js = _read(PAGES / "app.js")
    zh = js.split('"zh-CN": {', 1)[1].split('"en-US": {', 1)[0]
    for key in (
        "backup.mode",
        "backup.modeMerge",
        "backup.modeReplace",
        "backup.modeMergeShort",
        "backup.modeReplaceShort",
        "backup.resultMode",
        "backup.replaceConfirmTitle",
        "backup.replaceConfirmBody",
        "backup.replaceConfirmPlan",
        "backup.replaceConfirmTail",
        "backup.dbRestore",
        "backup.dbRestoreHint",
        "backup.dbRestoreConfirm",
        "backup.dbRestored",
        "backup.dbRestoreBackup",
    ):
        assert f'"{key}"' in zh, f"中文文案缺少 {key}"


def test_i18n_dictionaries_have_same_keys() -> None:
    """中英文字典的键集合必须一致。

    踩过的坑：新增文案只往一处插（甚至插错段落），JS 对象字面量里重复键按「后者胜出」
    处理，界面会静默变成另一种语言——测试盯住键集合最省事。
    """
    js = _read(PAGES / "app.js")
    zh_start = js.index('"zh-CN": {')
    en_start = js.index('"en-US": {')
    end = js.index("const FEATURES_POLL_MS")
    zh = set(re.findall(r'^\s*"([^"]+)":', js[zh_start:en_start], flags=re.M)) - {"zh-CN"}
    en = set(re.findall(r'^\s*"([^"]+)":', js[en_start:end], flags=re.M)) - {"en-US"}
    assert not (zh - en), f"英文字典缺少：{sorted(zh - en)}"
    assert not (en - zh), f"中文字典缺少：{sorted(en - zh)}"
    # 同一段落里不允许出现重复键（后者会覆盖前者）
    for name, block in (("zh-CN", js[zh_start:en_start]), ("en-US", js[en_start:end])):
        keys = re.findall(r'^\s*"([^"]+)":', block, flags=re.M)
        duplicates = {key for key in keys if keys.count(key) > 1}
        assert not duplicates, f"{name} 字典存在重复键：{sorted(duplicates)}"


def test_peek_panel_structure_present() -> None:
    """侧滑详情面板的骨架必须在页面上（记忆 / 现实桥 / 每周总结共用）。"""
    html = _read(PAGES / "index.html")
    for element in (
        "peek-overlay",
        "peek-panel",
        "peek-title",
        "peek-badges",
        "peek-actions",
        "peek-body",
        "peek-close",
    ):
        assert f'id="{element}"' in html, f"侧滑面板缺少 {element}"
    # 初始状态必须隐藏且不可聚焦（inert），否则起始就露出一条空白抽屉
    panel = html.split('id="peek-panel"', 1)[1].split(">", 1)[0]
    assert "hidden" in panel, "面板初始应 hidden"
    assert 'aria-hidden="true"' in panel, "面板初始应 aria-hidden"


def test_peek_panel_is_wired_to_three_pages() -> None:
    """记忆 / 现实桥 / 每周总结都走同一只抽屉，且都有编辑与删除入口。"""
    js = _read(PAGES / "app.js")
    for fn in ("openMemoryDetail", "openJournalDetail", "openWeeklyDetail"):
        assert f"function {fn}(" in js or f"async function {fn}(" in js, f"缺少 {fn}"
    for hook in ('data-journal=', "data-weekly=", "data-memory="):
        assert hook in js, f"列表行缺少可点击钩子 {hook}"
    for attr in ("data-peek-edit", "data-peek-delete", "data-peek-save", "data-peek-cancel"):
        assert attr in js, f"面板操作缺少 {attr}"
    # 现实桥的编辑沿用既有表单（modal 盖在抽屉之上），不能被面板取代掉
    assert "openJournalEditor(entry)" in js
    # 旧居中弹窗保留给表单/确认框使用
    assert "function openModal(" in js and "function closeModal(" in js


def test_peek_panel_motion_is_coordinated_with_modal() -> None:
    """动效必须复用现有令牌与同一套进出场节奏，否则抽屉会比弹窗「快一拍/慢一拍」。

    约定（见 styles.css 顶部注释）：入场 animation 用 --dur-enter + --ease-out，
    出场 .closing 用 --dur-exit（出场比入场快），只动 transform/opacity。
    """
    css = _read(PAGES / "styles.css")
    panel = css.split(".peek-panel {", 1)[1]
    assert "var(--dur-enter)" in panel and "var(--ease-out)" in panel, "入场应复用动效令牌"
    assert "var(--dur-exit)" in panel, "出场应复用动效令牌（比入场快）"
    assert "translateX(100%)" in css, "抽屉应从右侧滑入"
    media = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".peek-panel" in media, "减少动态模式必须覆盖侧滑面板"


def test_peek_panel_stacks_below_modal_above_nothing() -> None:
    """层级：抽屉 < 居中弹窗（编辑表单/删除确认要盖住抽屉）；提示条在两者之上。"""
    css = _read(PAGES / "styles.css")

    def _z(selector: str) -> int:
        block = css.split(f"{selector} {{", 1)[1].split("}", 1)[0]
        match = re.search(r"z-index:\s*(\d+)", block)
        assert match, f"{selector} 未声明 z-index"
        return int(match.group(1))

    modal = _z(".modal")
    panel = _z(".peek-panel")
    overlay = _z(".peek-overlay")
    toast = _z(".toast-region")
    assert overlay < panel, "遮罩应在抽屉之下"
    assert panel < modal, "抽屉必须低于居中弹窗，否则弹窗会被抽屉盖住"
    assert toast > modal and toast > panel, "提示条要让抽屉打开时仍可见"


def test_peek_i18n_keys_complete() -> None:
    js = _read(PAGES / "app.js")
    zh = js.split('"zh-CN": {', 1)[1].split('"en-US": {', 1)[0]
    for key in (
        "peek.memoryTitle",
        "peek.journalTitle",
        "peek.weeklyTitle",
        "peek.edit",
        "peek.delete",
        "peek.save",
        "peek.cancel",
        "peek.content",
        "peek.metadata",
        "peek.editContent",
        "peek.needContent",
        "peek.gone",
    ):
        assert f'"{key}"' in zh, f"中文文案缺少 {key}"
    assert "data-i18n-aria" in js and "data-i18n-aria" in _read(PAGES / "index.html"), (
        "纯图标按钮的无障碍文案应走 data-i18n-aria"
    )
