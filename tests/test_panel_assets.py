"""控制台静态契约测试（导航重构版 · Notion 风格）。

这些断言直接对应真实服务器上踩过的坑，用来防止回归：

1. 脚本必须是 ``type="module"``：AstrBot 把 bridge SDK 注入到 ``</body>`` 之前，
   classic 内联脚本会先执行，此时 ``window.AstrBotPluginPage`` 为 null。
2. **不得出现 ``fetch(``**：插件页运行在无 ``allow-same-origin`` 的 sandbox iframe 中，
   直连请求会抛 ``Failed to fetch``；所有请求必须经 bridge。
3. 不得引用外部 CDN（离线环境与 CSP 都会出问题）。
4. 前后端 endpoint 必须一一对应，避免接口漂移导致面板空白。
5. **导航重构版的结构**：五组 19 页（总览 / 功能 / 人格×4 / 记忆×5 / 共情与主动×2 /
   记录×2 / 系统×4）；身份诊断并入「风格样本」、待审并入同页标签页。
6. **Notion 风格硬约束**：无渐变、无大圆角、无重阴影、悬停只变背景色（150ms）、
   米色底 #f7f6f3、⋮⋮ 拖拽手柄、浅深主题一一镜像、prefers-reduced-motion 有兜底。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "pages" / "dashboard"
WEB_API = ROOT / "super_astrbot" / "web" / "api.py"
I18N_DIR = ROOT / ".astrbot-plugin" / "i18n"

FRONTEND_ENDPOINTS = (
    # 原有接口（旧入口零丢失）
    "overview",
    "models",
    "features",
    "feature-toggle",
    "memories",
    "memory",
    "memories/update",
    "memories/delete",
    "weeklies/update",
    "journals",
    "journals/export-selected",
    "reviews",
    "reviews/batch",
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
    # 融合域接口（导航重构版新增）
    "persona/forge",
    "persona/forge-update",
    "persona/evolution",
    "persona/evolution-reset",
    "persona/affinity",
    "members/list",
    "members/strategy",
    "identity/observe",
    "identity/migrate",
    "memory/list",
    "memory/recall",
    "memory/facets",
    "memory/backends",
    "memory/tiers",
    "memory/decay",
    "memory/worldbook",
    "memory/worldbook-add",
    "memory/worldbook-update",
    "memory/worldbook-del",
    "empathy/config",
    "empathy/log",
    "proactive/schedule",
    "proactive/queue",
    "proactive/log",
    "group/context",
    "fusion/pipeline",
    "monitor/fusion",
    "graph/rebuild",
    "graph/timeline",
)

COMPAT_ENDPOINTS = (
    # 兼容别名：仅后端注册（老面板/脚本可能仍在调用），前端已迁到 /*/ 新路由
    "search",
    "memory/list",
    "identities",
    "scopes",
    "scopes/migrate",
)

NAV_PAGES = (
    "overview",
    "features",
    "persona-forge",
    "persona-legacy",
    "persona-evolution",
    "style-samples",
    "memories",
    "recall",
    "graph",
    "memory-backend",
    "worldbook",
    "empathy",
    "proactive",
    "journals",
    "weeklies",
    "monitor",
    "models",
    "prompts",
    "system",
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
    for endpoint in FRONTEND_ENDPOINTS:
        assert f'"{endpoint}"' in js, f"前端未使用接口 {endpoint}"
        assert f'("{endpoint}"' in backend, f"后端未注册接口 {endpoint}"
    # 兼容别名只要求后端仍注册（旧入口零丢失），前端可不再引用
    for endpoint in COMPAT_ENDPOINTS:
        assert f'("{endpoint}"' in backend, f"后端缺少兼容路由 {endpoint}"


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


# --------------------------------------------------------------------------- #
# 导航重构版：五组 19 页                                                   #
# --------------------------------------------------------------------------- #


def test_nav_pages_and_groups() -> None:
    html = _read(PAGES / "index.html")
    js = _read(PAGES / "app.js")

    nav_pages = re.findall(r'class="nav-item" data-page="([a-z-]+)"', html)
    assert tuple(nav_pages) == NAV_PAGES, f"导航顺序不符：{nav_pages}"

    # 分组容器：人格 / 记忆 / 共情与主动 / 记录 可折叠，系统平铺
    for group in ("persona", "memory", "empathy", "record", "system"):
        assert f'data-group="{group}"' in html, f"缺少导航分组 {group}"
    assert html.count('class="nav-group-head"') == 4, "四个分组应为可折叠（系统组平铺）"
    assert 'class="nav-group flat"' in html and 'class="nav-group-label"' in html

    # 页码顺序由 PAGE_TITLES 决定，必须与导航顺序一致
    titles = re.findall(r'^  "?([a-z-]+)"?: "nav\.', js, flags=re.M)
    assert titles == list(NAV_PAGES), f"PAGE_TITLES 顺序与导航不一致：{titles}"


def test_style_samples_tabs_merge_reviews_and_identity() -> None:
    """风格样本页内：同页切换「风格样本 / 待审」；身份诊断并入本页。"""
    html = _read(PAGES / "index.html")
    js = _read(PAGES / "app.js")

    assert 'id="ss-tabs"' in html and 'data-tab="ss-samples"' in html and 'data-tab="ss-review"' in html
    assert 'id="ss-samples"' in html and 'id="ss-review"' in html
    assert 'id="ss-member-table"' in html, "缺少群友风格档案表"
    assert 'id="ss-strategy-member"' in html and 'id="ss-strategy-save"' in html, "缺少差异化策略编辑器"

    # 待审控件（原待审页）必须仍在本页标签页里
    for element in ("rv-origin", "rv-umo", "rv-search", "rv-approve-all", "rv-reject-all", "rv-table"):
        assert f'id="{element}"' in html, f"待审标签页缺少控件 {element}"
    assert "reviews/batch" in js

    # 身份诊断控件（原身份页）并入本页
    for element in (
        "id-table",
        "id-summary",
        "id-hint",
        "id-refresh",
        "id-clear",
        "sc-from",
        "sc-to",
        "sc-preview",
        "sc-apply",
    ):
        assert f'id="{element}"' in html, f"身份诊断区缺少控件 {element}"
    assert "migrateScope(true)" in js and "migrateScope(false)" in js, "迁移必须「先预览、再执行」"


def test_new_fusion_pages_present() -> None:
    """融合新增页面：人格内核 / 演化轨迹 / 记忆后端 / 世界书 / 共情管线 / 主动关怀。"""
    html = _read(PAGES / "index.html")
    widgets = {
        "persona-forge": ("pf-meta", "pf-core", "pf-save", "pf-text", "pf-relations"),
        "persona-evolution": ("pe-meta", "pe-radar", "pe-drift", "pe-timeline"),
        "memory-backend": ("mb-backends", "mb-tiers", "mb-decay", "mb-rebuild"),
        "worldbook": ("wb-table", "wb-save", "wb-triggers", "wb-content"),
        "empathy": ("ep-meta", "ep-stage-identify", "ep-temperature", "ep-log", "ep-save"),
        "proactive": ("pq-table", "pq-save", "pq-schedule", "pq-log", "pq-status"),
    }
    for page, ids in widgets.items():
        assert f'id="page-{page}"' in html, f"缺少页面 {page}"
        for element in ids:
            assert f'id="{element}"' in html, f"{page} 页缺少控件 {element}"

    # 总览新增：编排流水线与融合健康；图谱新增：图层与时间线；监控新增：融合健康
    for element in ("ov-flow", "ov-fusion", "gp-layer", "gp-timeline", "gp-rebuild", "mt-fusion", "feat-group"):
        assert f'id="{element}"' in html, f"缺少控件 {element}"


def test_every_page_has_loader_and_title() -> None:
    js = _read(PAGES / "app.js")
    for page in NAV_PAGES:
        key = f'"{page}"' if "-" in page else page
        assert f'{key}: "nav.' in js, f"PAGE_TITLES 缺少 {page}"
    # 未知页面必须回退到存在的默认页
    assert 'PAGE_TITLES[page] ? page : "overview"' in js
    assert "page-overview" in _read(PAGES / "index.html")


# --------------------------------------------------------------------------- #
# Notion 风格硬约束                                                        #
# --------------------------------------------------------------------------- #


def test_notion_style_tokens_light_and_dark() -> None:
    css = _read(PAGES / "styles.css")
    for token in ("#f7f6f3", "#efedea", "#e3e1db"):
        assert token in css, f"缺少 Notion 标志色 {token}"
    light, dark = css.split('html[data-theme="dark"]', 1)
    for variable in (
        "--bg:",
        "--bg-soft:",
        "--panel:",
        "--panel-2:",
        "--border:",
        "--text:",
        "--text-dim:",
        "--accent:",
    ):
        assert variable in light, f"浅色主题缺少 {variable}"
        assert variable in dark, f"深色主题缺少 {variable}"
    assert 'data-theme="light"' in _read(PAGES / "index.html"), "默认站浅色（Notion 文档观感）"


def test_notion_style_forbidden_patterns() -> None:
    css = _read(PAGES / "styles.css")
    assert "gradient" not in css, "Notion 风格禁止渐变"
    assert "rounded-2xl" not in css and "border-radius: 999px" not in css, "禁止大圆角 / 胶囊形"
    assert "translateY" not in css, "禁止位移类动效（仅面板进出场允许 translateX）"
    assert "265, 0.04" in css or "15, 15, 15, 0.04" in css, "阴影必须轻量"


def test_notion_interaction_feedback_is_color_only() -> None:
    css = _read(PAGES / "styles.css")
    assert re.search(r"\.btn:hover:not\(:disabled\)\s*\{\s*background: var\(--panel-2\)", css)
    assert re.search(r"\.btn:active:not\(:disabled\)\s*\{\s*background: var\(--panel-3\)", css)
    assert "150ms" in css, "交互反馈统一 150ms"
    # 拖拽手柄（Drag Handle Illusion）：默认透明，卡片悬停时浮现
    assert ".drag-handle" in css and "opacity: 0" in css
    assert re.search(r"\.card:hover > \.drag-handle\s*\{\s*opacity", css)
    assert "injectDragHandles" in _read(PAGES / "app.js"), "拖拽手柄应由前端注入"


def test_notion_typography_rules() -> None:
    css = _read(PAGES / "styles.css")
    assert "-apple-system" in css and "PingFang SC" in css and "Microsoft YaHei" in css
    for banned in ("Inter", "Roboto", "Geist"):
        assert banned not in css, f"不应使用 {banned} 字体"
    assert "text-transform: uppercase" not in css, "母项不做大写字母 + 字距的「眉标」"
    assert re.search(r"\.nav-group:not\(\.flat\) \.nav-items \.nav-item\s*\{\s*font-size: 13px", css)
    assert re.search(r"\.nav-group\.flat \.nav-items \.nav-item\s*\{\s*font-size: 14px", css)


def test_reduced_motion_and_responsive_cover_new_widgets() -> None:
    css = _read(PAGES / "styles.css")
    media = css.split("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert ".peek-panel" in media, "减少动态模式必须覆盖侧滑面板"
    assert "@media (max-width: 980px)" in css, "缺少窄屏适配"


# --------------------------------------------------------------------------- #
# 现实桥 / 备份 / 侧滑面板（原有硬约束，保持不回归）                        #
# --------------------------------------------------------------------------- #


def test_bridge_page_columns_and_type_options() -> None:
    """现实桥页的列顺序与三种文本类型选项是需求硬约束，防止后续改版时漂移。

    列顺序：``# / 标题 / 内容 / 类型 / 标签 / 情绪 / 创建时间 / 操作``（外加最左侧勾选列）。
    """
    html = _read(PAGES / "index.html")
    js = _read(PAGES / "app.js")

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
        # 融合域文案
        "nav.personaForge",
        "nav.styleSamples",
        "nav.worldbook",
        "nav.empathy",
        "nav.proactive",
        "forge.title",
        "evolution.title",
        "styleSamples.tabReview",
        "backend.decay",
        "worldbook.title",
        "empathy.temperature",
        "proactive.newItem",
        "overview.pipeline",
        "monitor.fusion",
    ):
        assert f'"{key}"' in zh, f"中文文案缺少 {key}"


def test_backup_section_present_in_system_page() -> None:
    """系统页备份栏：导出 / 导入 / 历史表都在，且是系统页最后一张卡片。"""
    html = _read(PAGES / "index.html")
    js = _read(PAGES / "app.js")
    for element in ("bk-notes", "bk-export", "bk-import", "bk-import-label", "bk-table"):
        assert f'id="{element}"' in html, f"备份栏缺少控件 {element}"
    for removed in ("bk-config", "bk-database", "bk-data"):
        assert f'id="{removed}"' not in html, f"备份类型勾选项应已移除：{removed}"

    assert "bridge.download(" in js, "备份下载应优先使用 bridge.download"
    for endpoint in ("backup/export", "backup/import", "backup/list"):
        assert endpoint in js, f"前端未使用 {endpoint}"
    assert 'data-i18n="backup.title"' in html
    # 备份卡片位于系统页最下方（配置维护 / 维护之后）
    system_page = html[html.index('id="page-system"') :]
    headings = re.findall(r'<h2 data-i18n="([^"]+)"', system_page)
    assert headings[-1] == "backup.title", f"备份栏应排在系统页最后：{headings}"


def test_backup_import_mode_ui_present() -> None:
    """导入模式选择、覆盖二次确认（含删除估算）、整库恢复入口都要在页面上。"""
    html = _read(PAGES / "index.html")
    js = _read(PAGES / "app.js")

    assert 'id="bk-mode"' in html
    assert '<option value="merge"' in html and '<option value="replace"' in html
    assert 'id="bk-db-actions"' in html, "缺少整库恢复入口容器"

    assert "dry_run: true" in js and "dry_run: false" in js
    assert "renderDatabaseRestoreEntry" in js
    assert "backup/replace-database" in js
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
    """中英文字典的键集合必须一致（重复键按「后者胜出」会静默改语言）。"""
    js = _read(PAGES / "app.js")
    zh_start = js.index('"zh-CN": {')
    en_start = js.index('"en-US": {')
    end = js.index("const FEATURES_POLL_MS")
    zh = set(re.findall(r'^\s*"([^"]+)":', js[zh_start:en_start], flags=re.M)) - {"zh-CN"}
    en = set(re.findall(r'^\s*"([^"]+)":', js[en_start:end], flags=re.M)) - {"en-US"}
    assert not (zh - en), f"英文字典缺少：{sorted(zh - en)}"
    assert not (en - zh), f"中文字典缺少：{sorted(en - zh)}"
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
    assert "openJournalEditor(entry)" in js
    assert "function openModal(" in js and "function closeModal(" in js


def test_peek_panel_motion_is_coordinated_with_modal() -> None:
    """动效必须复用现有令牌与同一套进出场节奏，否则抽屉会比弹窗「快一拍/慢一拍」。"""
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

    assert _z(".peek-overlay") < _z(".modal")
    assert _z(".peek-panel") < _z(".modal")
    assert _z(".toast-region") > _z(".modal")
