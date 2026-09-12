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
    "features",
    "feature-toggle",
    "memories",
    "memory",
    "search",
    "journals",
    "reviews",
    "review-action",
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
    html = _read(PAGES / "index.html")
    assert "./logo.svg" in html, "面板品牌区应使用插件图标"


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
