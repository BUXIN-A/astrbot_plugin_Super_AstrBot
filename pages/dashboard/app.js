/* Super_AstrBot 控制台前端（ES Module）
 *
 * 为什么必须是 module 且只走 bridge：
 * 1. AstrBot 把 bridge SDK 注入到 </body> 之前；classic 内联脚本会在注入前执行，
 *    此时 window.AstrBotPluginPage 为 null。module 脚本天然 defer，能保证顺序。
 * 2. 插件页运行在 sandbox iframe（无 allow-same-origin），origin 为 opaque，
 *    直接 fetch 会变成跨源请求且拿不到 Dashboard 的鉴权，浏览器抛 "Failed to fetch"。
 *    因此这里**完全不使用 fetch**，所有请求都通过 bridge 代理。
 */

const I18N_PREFIX = "pages.dashboard.";
const THEME_KEY = "super-astrbot-theme";

const LOCAL_I18N = {
  "zh-CN": {
    "shell.title": "Super_AstrBot 控制台",
    "shell.subtitle": "长期记忆 · 自我学习",
    "nav.overview": "总览",
    "nav.features": "功能",
    "nav.memories": "记忆",
    "nav.recall": "检索",
    "nav.journals": "周记",
    "nav.reviews": "待审",
    "nav.persona": "学习",
    "nav.system": "系统",
    "features.title": "功能开关",
    "features.hint": "开关会立即写入插件配置并热应用；标注「需重载」的项目将在重载插件后生效。",
    "features.empty": "没有可用的功能项。",
    "features.switchOn": "开",
    "features.switchOff": "关",
    "features.needsReload": "需重载",
    "features.runtimeUnsupported": "环境不支持",
    "features.blockedBy": "依赖未开启",
    "domain.basic": "基础",
    "domain.memory": "记忆",
    "domain.reflection": "自我学习",
    "domain.journal": "周记",
    "domain.agent": "Agent 工具",
    "domain.context": "上下文治理",
    "domain.group": "群聊语义",
    "domain.proactive": "主动交互",
    "domain.persona": "拟人化学习",
    "overview.manageFeatures": "管理功能",
    "action.refresh": "刷新",
    "action.theme": "主题",
    "action.query": "查询",
    "action.search": "检索",
    "action.prev": "上一页",
    "action.next": "下一页",
    "action.close": "关闭",
    "action.reindex": "重建检索索引",
    "action.approve": "批准",
    "action.reject": "驳回",
    "overview.capabilities": "能力状态",
    "overview.recent": "最近写入的记忆",
    "stat.active": "正式记忆",
    "stat.buffered": "对话缓冲",
    "stat.pending": "待审",
    "stat.archived": "归档",
    "stat.journals": "周记",
    "stat.tasks": "后台任务",
    "filter.status": "状态",
    "filter.kind": "类型",
    "filter.keyword": "关键词",
    "filter.all": "全部",
    "status.active": "正式",
    "status.buffered": "缓冲",
    "status.pending": "待审",
    "status.archived": "归档",
    "status.forgotten": "已遗忘",
    "recall.query": "检索词",
    "recall.umo": "会话 UMO（可选）",
    "recall.limit": "条数",
    "recall.hint": "不填 UMO 时按关键词跨作用域匹配；填写后走混合检索并展示打分构成。",
    "journals.title": "周记（现实记忆）",
    "persona.title": "拟人化学习",
    "persona.hint": "风格样本、群内用语与好感度都在这里查看；未经批准的学习结果不会影响对话。",
    "persona.umo": "会话 UMO（可选）",
    "persona.styles": "表达样本",
    "persona.jargons": "群内用语",
    "persona.affinity": "好感度",
    "persona.approval": "需审批",
    "reviews.title": "待审记录",
    "reviews.disabled": "待审队列包含反思产出与拟人化学习结果，批准后才会生效。",
    "system.framework": "框架与环境",
    "system.jobs": "后台任务",
    "system.budget": "辅助调用预算",
    "system.embedding": "嵌入模型提供商",
    "system.maintenance": "维护",
    "system.reindexHint": "根据当前配置重建关键词索引与向量索引，不会删除记忆。",
    "modal.detail": "详情",
    "errors.bridgeMissing": "插件页桥接 SDK 未加载：请重载插件或刷新页面",
    "errors.requestFailed": "请求失败",
    "empty.none": "暂无数据",
  },
  "en-US": {
    "shell.title": "Super_AstrBot Console",
    "shell.subtitle": "Long-term memory · Self-learning",
    "nav.overview": "Overview",
    "nav.features": "Features",
    "nav.memories": "Memories",
    "nav.recall": "Recall",
    "nav.journals": "Journal",
    "nav.reviews": "Reviews",
    "nav.persona": "Learning",
    "nav.system": "System",
    "features.title": "Feature toggles",
    "features.hint": "Toggles are written to the plugin config and applied immediately; items marked \"reload\" take effect after reloading the plugin.",
    "features.empty": "No features available.",
    "features.switchOn": "On",
    "features.switchOff": "Off",
    "features.needsReload": "Reload",
    "features.runtimeUnsupported": "Unsupported",
    "features.blockedBy": "Dependencies off",
    "domain.basic": "Basics",
    "domain.memory": "Memory",
    "domain.reflection": "Self-learning",
    "domain.journal": "Journal",
    "domain.agent": "Agent tools",
    "domain.context": "Context control",
    "domain.group": "Group semantics",
    "domain.proactive": "Proactive chat",
    "domain.persona": "Persona learning",
    "overview.manageFeatures": "Manage features",
    "action.refresh": "Refresh",
    "action.theme": "Theme",
    "action.query": "Query",
    "action.search": "Search",
    "action.prev": "Prev",
    "action.next": "Next",
    "action.close": "Close",
    "action.reindex": "Rebuild index",
    "action.approve": "Approve",
    "action.reject": "Reject",
    "overview.capabilities": "Capabilities",
    "overview.recent": "Recently written memories",
    "stat.active": "Active",
    "stat.buffered": "Buffered",
    "stat.pending": "Pending",
    "stat.archived": "Archived",
    "stat.journals": "Journals",
    "stat.tasks": "Tasks",
    "filter.status": "Status",
    "filter.kind": "Kind",
    "filter.keyword": "Keyword",
    "filter.all": "All",
    "status.active": "Active",
    "status.buffered": "Buffered",
    "status.pending": "Pending",
    "status.archived": "Archived",
    "status.forgotten": "Forgotten",
    "recall.query": "Query",
    "recall.umo": "Session UMO (optional)",
    "recall.limit": "Limit",
    "recall.hint": "Without UMO it matches keywords across all scopes; with UMO it runs hybrid retrieval with score breakdown.",
    "journals.title": "Journal (real-life memory)",
    "persona.title": "Persona learning",
    "persona.hint": "Style samples, group jargon and affinity are listed here; unapproved learnings never affect replies.",
    "persona.umo": "Session UMO (optional)",
    "persona.styles": "Style samples",
    "persona.jargons": "Group jargon",
    "persona.affinity": "Affinity",
    "persona.approval": "Needs review",
    "reviews.title": "Pending reviews",
    "reviews.disabled": "The queue holds reflection results and persona learnings; they take effect only after approval.",
    "system.framework": "Framework",
    "system.jobs": "Scheduled jobs",
    "system.budget": "LLM budget",
    "system.embedding": "Embedding providers",
    "system.maintenance": "Maintenance",
    "system.reindexHint": "Rebuild keyword and vector indexes from current config. Memories are not deleted.",
    "modal.detail": "Detail",
    "errors.bridgeMissing": "Plugin page bridge SDK not loaded: reload the plugin or refresh the page",
    "errors.requestFailed": "Request failed",
    "empty.none": "No data",
  },
};

const state = {
  page: "overview",
  locale: "zh-CN",
  context: null,
  overview: null,
  memories: { offset: 0, limit: 20, status: "active", kind: "", keyword: "", total: 0 },
  journals: { offset: 0, limit: 20, total: 0 },
};

/* ---------------------------------------------------------------------- */
/* 基础工具                                                                */
/* ---------------------------------------------------------------------- */

function getBridge() {
  return window.AstrBotPluginPage || null;
}

function t(key, fallback) {
  const dict = LOCAL_I18N[state.locale] || LOCAL_I18N["zh-CN"];
  // 先算本地兜底：桥接的 fallback 必须收到「真正的兜底文案」，
  // 否则传 key 本身会让桥接把 key 当成有效翻译返回，本地字典永远用不上。
  const local = fallback !== undefined ? fallback : dict[key] !== undefined ? dict[key] : key;
  const bridge = getBridge();
  if (bridge && typeof bridge.t === "function") {
    try {
      const value = bridge.t(I18N_PREFIX + key, local);
      if (value) return value;
    } catch (error) {
      /* 桥接 i18n 不可用时退回本地字典 */
    }
  }
  return local;
}

/** 完整转义五个字符：只转义部分会留下属性注入（存储型 XSS）风险。 */
function esc(value) {
  return String(value === null || value === undefined ? "" : value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function fmtTime(ts) {
  const value = Number(ts || 0);
  if (!value) return "—";
  const date = new Date(value * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function fmtDuration(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value)) return "—";
  if (value < 60) return `${Math.round(value)}s`;
  if (value < 3600) return `${Math.round(value / 60)}m`;
  return `${(value / 3600).toFixed(1)}h`;
}

function num(value, digits = 2) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : "—";
}

function $(id) {
  return document.getElementById(id);
}

function toast(message, kind = "info") {
  const region = $("toasts");
  if (!region) return;
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.textContent = String(message);
  region.appendChild(node);
  window.setTimeout(() => node.remove(), 4200);
}

function openModal(title, html) {
  $("modal-title").textContent = title;
  $("modal-body").innerHTML = html;
  $("modal").hidden = false;
}

function closeModal() {
  $("modal").hidden = true;
  $("modal-body").innerHTML = "";
}

/* ---------------------------------------------------------------------- */
/* API（仅经 bridge）                                                       */
/* ---------------------------------------------------------------------- */

async function ensureBridge() {
  const bridge = getBridge();
  if (!bridge || typeof bridge.ready !== "function") {
    throw new Error(t("errors.bridgeMissing"));
  }
  if (!state.context) {
    state.context = await bridge.ready();
  }
  return bridge;
}

/** 兼容两种响应包裹：bridge 可能已解一层，也可能原样返回后端信封。 */
function unwrap(response) {
  const body =
    response && response.data && response.data.status ? response.data : response;
  if (body && body.status === "ok") return body.data === undefined ? {} : body.data;
  if (body && body.status === "error") {
    throw new Error(body.message || t("errors.requestFailed"));
  }
  if (body && body.success === false) {
    throw new Error(body.message || body.error || t("errors.requestFailed"));
  }
  return body === undefined || body === null ? {} : body;
}

function cleanParams(params) {
  const result = {};
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    result[key] = value;
  });
  return result;
}

async function apiGet(endpoint, params) {
  const bridge = await ensureBridge();
  return unwrap(await bridge.apiGet(endpoint, cleanParams(params)));
}

async function apiPost(endpoint, body) {
  const bridge = await ensureBridge();
  return unwrap(await bridge.apiPost(endpoint, body || {}));
}

/* ---------------------------------------------------------------------- */
/* 渲染辅助                                                                */
/* ---------------------------------------------------------------------- */

function renderStats(target, pairs) {
  target.innerHTML = pairs
    .map(
      ([label, value]) =>
        `<div class="stat"><div class="label">${esc(label)}</div><div class="value">${esc(
          value === null || value === undefined ? "—" : value
        )}</div></div>`
    )
    .join("");
}

function renderEmpty(target, message) {
  target.innerHTML = `<div class="empty">${esc(message || t("empty.none"))}</div>`;
}

function renderError(target, error) {
  target.innerHTML = `<div class="err-text">${esc(error && error.message ? error.message : error)}</div>`;
}

function renderTable(target, columns, rows, options = {}) {
  if (!rows || rows.length === 0) {
    renderEmpty(target, options.emptyText);
    return;
  }
  const head = columns.map((column) => `<th>${esc(column.title)}</th>`).join("");
  const body = rows
    .map((row, index) => {
      const cells = columns
        .map((column) => {
          const cls = column.className ? ` class="${column.className}"` : "";
          const content = column.render
            ? column.render(row, index)
            : esc(row[column.key]);
          return `<td${cls}>${content}</td>`;
        })
        .join("");
      const rowAttrs = options.rowAttrs ? options.rowAttrs(row) : "";
      return `<tr${rowAttrs}>${cells}</tr>`;
    })
    .join("");
  target.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function truncate(text, limit = 120) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value;
}

function kindPill(kind) {
  return `<span class="pill info">${esc(kind)}</span>`;
}

function statusPill(status) {
  const map = {
    active: "on",
    buffered: "info",
    pending: "warn",
    archived: "off",
    forgotten: "err",
  };
  return `<span class="pill ${map[status] || "off"}">${esc(status)}</span>`;
}

/* ---------------------------------------------------------------------- */
/* 章节：总览                                                              */
/* ---------------------------------------------------------------------- */

async function loadOverview(force = false) {
  const statsEl = $("ov-stats");
  try {
    if (!state.overview || force) {
      state.overview = await apiGet("overview");
    }
    const data = state.overview;
    const memory = data.memory || {};
    const framework = data.framework || {};

    renderStats(statsEl, [
      [t("stat.active"), memory.active],
      [t("stat.buffered"), memory.buffered],
      [t("stat.pending"), memory.pending],
      [t("stat.archived"), memory.archived],
      [t("stat.journals"), memory.journals],
      [t("stat.tasks"), data.pending_tasks],
    ]);

    $("footer-version").textContent = `v${data.plugin_version || "?"} · AstrBot ${framework.version || "?"}`;
    $("footer-ready").textContent = data.ready ? `就绪 · ${data.database || ""}` : "未就绪";

    // 能力开关（点击任意一个跳到「功能」页）
    const caps = data.capabilities || {};
    $("ov-caps").innerHTML =
      Object.entries(caps)
        .map(
          ([key, value]) =>
            `<span class="pill ${value ? "on" : "off"} clickable" data-goto="features" title="${esc(key)}">${esc(
              key.replace(/^(basic|memory|reflection|journal)\./, "")
            )}</span>`
        )
        .join("") +
      `<span class="muted clickable" data-goto="features">→ ${esc(t("overview.manageFeatures"))}</span>`;

    // 降级原因
    const degraded = data.degraded || [];
    const missing = framework.missing || [];
    const notes = [
      ...degraded.map((item) => `${item.capability}：${item.reason}`),
      ...(missing.length ? [`框架符号缺失：${missing.join("、")}`] : []),
      ...(data.fts === false ? ["SQLite 无 FTS5，关键词检索已降级为 LIKE"] : []),
    ];
    $("ov-degraded").innerHTML = notes.length
      ? notes.map((text) => `<div class="item">${esc(text)}</div>`).join("")
      : "";
  } catch (error) {
    renderError(statsEl, error);
  }

  // 最近记忆
  try {
    const result = await apiGet("memories", { limit: 5, status: "active" });
    renderTable(
      $("ov-recent"),
      [
        { title: "#", key: "id", className: "num" },
        {
          title: "内容",
          className: "content",
          render: (row) =>
            `<span class="clickable" data-memory="${esc(row.id)}">${esc(truncate(row.content, 110))}</span>`,
        },
        { title: "类型", render: (row) => kindPill(row.kind) },
        { title: "来源", key: "source" },
        { title: "重要度", className: "num", render: (row) => esc(num(row.importance)) },
        { title: "时间", className: "num", render: (row) => esc(fmtTime(row.created_at)) },
      ],
      result.items || [],
      { emptyText: "还没有正式记忆（对话累积后会被反思提炼为记忆）" }
    );
  } catch (error) {
    renderError($("ov-recent"), error);
  }
}

/* ---------------------------------------------------------------------- */
/* 章节：功能开关                                                          */
/* ---------------------------------------------------------------------- */

const DOMAIN_TITLES = {
  basic: "domain.basic",
  memory: "domain.memory",
  reflection: "domain.reflection",
  journal: "domain.journal",
  agent: "domain.agent",
  context: "domain.context",
  group: "domain.group",
  proactive: "domain.proactive",
  persona: "domain.persona",
};

function featureStatusPill(item) {
  if (item.status === "needs_reload") {
    return `<span class="pill warn">${esc(t("features.needsReload"))}</span>`;
  }
  if (item.status === "runtime_unsupported") {
    return `<span class="pill warn">${esc(t("features.runtimeUnsupported"))}</span>`;
  }
  const label = item.enabled ? t("features.switchOn") : t("features.switchOff");
  return `<span class="pill ${item.enabled ? "on" : "off"}">${esc(label)}</span>`;
}

function renderFeatureItem(item) {
  // 「需重载」与「环境不支持」两类不允许在页面上直接切换：
  // 前者切了也要重载才生效，后者切了会立刻被能力解析回退，徒增困惑。
  const locked = item.status === "needs_reload" || item.status === "runtime_unsupported";
  const blocked = (item.blocked_by || []).length
    ? `<div class="blocked">${esc(`${t("features.blockedBy")}：${item.blocked_by.join("、")}`)}</div>`
    : "";
  return `
    <div class="feature-item">
      <div class="info">
        <div class="name">${esc(item.title)} ${featureStatusPill(item)}
          <span class="key">${esc(item.key)}</span></div>
        <div class="desc">${esc(item.description || "")}</div>
        ${blocked}
      </div>
      <label class="switch">
        <input type="checkbox" data-feature="${esc(item.key)}"
          ${item.enabled ? "checked" : ""} ${locked ? "disabled" : ""} />
        <span class="track"></span><span class="thumb"></span>
      </label>
    </div>`;
}

async function loadFeatures() {
  const target = $("feat-list");
  try {
    const result = await apiGet("features");
    const items = result.items || [];
    if (items.length === 0) {
      renderEmpty(target, t("features.empty"));
      return;
    }

    const groups = new Map();
    items.forEach((item) => {
      const domain = item.domain || "other";
      if (!groups.has(domain)) groups.set(domain, []);
      groups.get(domain).push(item);
    });

    const blocks = [];
    groups.forEach((groupItems, domain) => {
      const title = DOMAIN_TITLES[domain] ? t(DOMAIN_TITLES[domain]) : domain;
      blocks.push(`<div class="muted">${esc(title)}</div>`);
      groupItems.forEach((item) => blocks.push(renderFeatureItem(item)));
    });
    target.innerHTML = blocks.join("");
  } catch (error) {
    renderError(target, error);
  }
}

async function handleFeatureToggle(key, enabled, input) {
  input.disabled = true;
  try {
    const result = await apiPost("feature-toggle", { key, enabled });
    toast(result.message || "已更新", "ok");
    // 概览页的能力指示与系统页都依赖 overview，清缓存后按需重取
    state.overview = null;
    await loadFeatures();
  } catch (error) {
    toast(error.message || String(error), "err");
    input.checked = !enabled;
    input.disabled = false;
  }
}

/* ---------------------------------------------------------------------- */
/* 章节：记忆                                                              */
/* ---------------------------------------------------------------------- */

function memoryColumns() {
  return [
    { title: "#", key: "id", className: "num" },
    {
      title: "内容",
      className: "content",
      render: (row) =>
        `<span class="clickable" data-memory="${esc(row.id)}">${esc(truncate(row.content, 150))}</span>`,
    },
    { title: "类型", render: (row) => kindPill(row.kind) },
    { title: "来源", key: "source" },
    { title: "作用域", render: (row) => `<span class="muted">${esc(row.scope)}</span>` },
    { title: "重要度", className: "num", render: (row) => esc(num(row.importance)) },
    { title: "访问", className: "num", key: "access_count" },
    { title: "创建", className: "num", render: (row) => esc(fmtTime(row.created_at)) },
  ];
}

async function loadMemories() {
  const target = $("mem-table");
  const cursor = state.memories;
  try {
    const result = await apiGet("memories", {
      offset: cursor.offset,
      limit: cursor.limit,
      status: cursor.status,
      kind: cursor.kind,
      keyword: cursor.keyword,
    });
    cursor.total = Number(result.total || 0);
    renderTable(target, memoryColumns(), result.items || [], {
      emptyText: "该筛选条件下没有记忆。",
    });
  } catch (error) {
    renderError(target, error);
  }
  const from = cursor.total === 0 ? 0 : cursor.offset + 1;
  const to = Math.min(cursor.offset + cursor.limit, cursor.total);
  $("mem-page-info").textContent = `${from}-${to} / ${cursor.total}`;
  $("mem-prev").disabled = cursor.offset <= 0;
  $("mem-next").disabled = cursor.offset + cursor.limit >= cursor.total;
}

/* ---------------------------------------------------------------------- */
/* 章节：检索                                                              */
/* ---------------------------------------------------------------------- */

async function runRecall() {
  const query = $("recall-query").value.trim();
  const umo = $("recall-umo").value.trim();
  const limit = Math.max(1, Math.min(20, Number($("recall-limit").value) || 5));
  const target = $("recall-table");
  const meta = $("recall-meta");

  if (!query) {
    meta.textContent = "请输入检索词。";
    renderEmpty(target, "请输入检索词。");
    return;
  }

  meta.textContent = "检索中…";
  renderEmpty(target, "检索中…");
  try {
    const result = await apiPost("search", { query, umo, limit });
    const parts = [
      `命中 ${result.total || 0} 条`,
      `检索路：${result.routes || "—"}`,
      `耗时：${result.elapsed_ms !== undefined ? `${result.elapsed_ms}ms` : "—"}`,
    ];
    if (result.degraded) parts.push(`提示：${result.degraded}`);
    meta.textContent = parts.join("　|　");

    renderTable(
      target,
      [
        { title: "#", key: "id", className: "num" },
        {
          title: "内容",
          className: "content",
          render: (row) =>
            `<span class="clickable" data-memory="${esc(row.id)}">${esc(truncate(row.content, 160))}</span>`,
        },
        {
          title: "最终分",
          className: "num",
          render: (row) => esc(row.score === null || row.score === undefined ? "—" : num(row.score, 3)),
        },
        {
          title: "打分构成",
          render: (row) => {
            const breakdown = row.breakdown || {};
            const keys = Object.keys(breakdown);
            if (keys.length === 0) return `<span class="muted">—</span>`;
            return `<span class="muted">${esc(
              keys.map((key) => `${key}=${num(breakdown[key], 3)}`).join("  ")
            )}</span>`;
          },
        },
      ],
      result.items || []
    );
  } catch (error) {
    meta.textContent = "";
    renderError(target, error);
  }
}

/* ---------------------------------------------------------------------- */
/* 章节：周记                                                              */
/* ---------------------------------------------------------------------- */

async function loadJournals() {
  const target = $("jr-table");
  const cursor = state.journals;
  try {
    const result = await apiGet("journals", { offset: cursor.offset, limit: cursor.limit });
    cursor.total = Number(result.total || 0);
    renderTable(
      target,
      [
        { title: "#", key: "id", className: "num" },
        { title: "内容", className: "content", render: (row) => esc(row.content) },
        {
          title: "标签",
          render: (row) => {
            const tags = Array.isArray(row.tags) ? row.tags : [];
            return tags.length ? tags.map((tag) => `<span class="tag">${esc(tag)}</span>`).join("") : "—";
          },
        },
        { title: "情绪", className: "num", render: (row) => esc(row.emotion || "—") },
        { title: "时间", className: "num", render: (row) => esc(fmtTime(row.event_time)) },
      ],
      result.items || [],
      { emptyText: "还没有周记（可用 /sab journal 内容 #标签 记录）" }
    );
  } catch (error) {
    renderError(target, error);
  }
  const from = cursor.total === 0 ? 0 : cursor.offset + 1;
  const to = Math.min(cursor.offset + cursor.limit, cursor.total);
  $("jr-page-info").textContent = `${from}-${to} / ${cursor.total}`;
  $("jr-prev").disabled = cursor.offset <= 0;
  $("jr-next").disabled = cursor.offset + cursor.limit >= cursor.total;
}

/* ---------------------------------------------------------------------- */
/* 章节：待审                                                              */
/* ---------------------------------------------------------------------- */

async function loadReviews() {
  const target = $("rv-table");
  const hint = $("rv-hint");
  try {
    const result = await apiGet("reviews", { limit: 50 });
    const origins = (result.origins || []).join("、") || "—";
    hint.textContent = `${t("reviews.disabled")}（来源：${origins}）`;

    const items = result.items || [];
    if (items.length === 0) {
      renderEmpty(target, "待审队列为空。");
      return;
    }
    renderTable(
      target,
      [
        { title: "#", key: "id", className: "num" },
        { title: "内容", className: "content", render: (row) => esc(row.summary || "") },
        { title: "来源", key: "origin" },
        { title: "作用域", render: (row) => `<span class="muted">${esc(row.scope || "")}</span>` },
        { title: "时间", className: "num", render: (row) => esc(fmtTime(row.created_at)) },
        {
          title: "操作",
          className: "actions",
          render: (row) =>
            `<button class="btn" data-review="${esc(row.id)}" data-action="approve">${esc(
              t("action.approve")
            )}</button><button class="btn ghost" data-review="${esc(row.id)}" data-action="reject">${esc(
              t("action.reject")
            )}</button>`,
        },
      ],
      items
    );
  } catch (error) {
    hint.textContent = "";
    renderError(target, error);
  }
}

async function handleReviewAction(id, action, button) {
  button.disabled = true;
  try {
    await apiPost("review-action", { id: Number(id), action });
    toast(action === "approve" ? "已批准" : "已驳回", "ok");
    await loadReviews();
    state.overview = null;
  } catch (error) {
    toast(error.message || String(error), "err");
    button.disabled = false;
  }
}

/* ---------------------------------------------------------------------- */
/* 章节：拟人化学习                                                        */
/* ---------------------------------------------------------------------- */

async function loadPersona() {
  const target = $("pn-style");
  const meta = $("pn-meta");
  const umo = $("pn-umo").value.trim();
  try {
    const result = await apiGet("persona", { umo, limit: 30 });
    const counts = result.counts || {};
    const enabled = result.enabled || {};
    const flag = (value) => (value ? "开" : "关");
    meta.textContent =
      `表达样本 ${counts.style ?? 0} 条（${flag(enabled.style)}）｜` +
      `群内用语 ${counts.jargon ?? 0} 条（${flag(enabled.jargon)}）｜` +
      `好感度 ${counts.affinity ?? 0} 条（${flag(enabled.affinity)}）｜` +
      `待审 ${result.pending ?? 0} 条`;

    renderTable(
      $("pn-style"),
      [
        { title: "#", key: "id", className: "num" },
        { title: "场景", className: "content", render: (row) => esc(row.situation || "") },
        { title: "表达", className: "content", render: (row) => esc(row.expression || "") },
        { title: "权重", className: "num", render: (row) => esc(num(row.weight)) },
        { title: "命中", className: "num", key: "hits" },
        { title: "作用域", render: (row) => `<span class="muted">${esc(row.scope || "")}</span>` },
      ],
      result.style || [],
      { emptyText: "还没有学到表达样本" }
    );

    renderTable(
      $("pn-jargon"),
      [
        { title: "#", key: "id", className: "num" },
        { title: "词语", render: (row) => esc(row.term || "") },
        { title: "含义", className: "content", render: (row) => esc(row.meaning || "") },
        { title: "置信度", className: "num", render: (row) => esc(num(row.confidence)) },
        { title: "证据", className: "num", key: "evidence" },
        { title: "作用域", render: (row) => `<span class="muted">${esc(row.scope || "")}</span>` },
      ],
      result.jargon || [],
      { emptyText: "还没有收录群内用语" }
    );

    renderTable(
      $("pn-affinity"),
      [
        { title: "对象", render: (row) => esc(row.target_id || "") },
        { title: "好感度", className: "num", render: (row) => esc(num(row.score)) },
        { title: "情绪", render: (row) => esc(row.mood || "—") },
        { title: "交互", className: "num", key: "interactions" },
        { title: "作用域", render: (row) => `<span class="muted">${esc(row.scope || "")}</span>` },
        {
          title: "最近交互",
          className: "num",
          render: (row) => esc(row.last_interaction ? fmtTime(row.last_interaction) : "—"),
        },
      ],
      result.affinity || [],
      { emptyText: "还没有好感度记录" }
    );
  } catch (error) {
    meta.textContent = "";
    renderError(target, error);
  }
}

/* ---------------------------------------------------------------------- */
/* 章节：系统                                                              */
/* ---------------------------------------------------------------------- */

async function loadSystem(force = false) {
  try {
    if (!state.overview || force) {
      state.overview = await apiGet("overview");
    }
    const data = state.overview;
    const framework = data.framework || {};
    const memory = data.memory || {};

    const kv = (label, value) =>
      `<div class="kv"><div class="k">${esc(label)}</div><div class="v">${esc(value)}</div></div>`;

    $("sys-fw").innerHTML = [
      kv("插件版本", data.plugin_version || "—"),
      kv("AstrBot 版本", framework.version || "—"),
      kv("框架符号", framework.symbols_ok ? "完整" : "有缺失（已降级）"),
      kv("缺失符号", (framework.missing || []).join("、") || "无"),
      kv("数据库", data.database || "—"),
      kv("FTS 全文索引", data.fts ? "可用" : "降级为 LIKE"),
      kv("检索路", (memory.routes || []).join(" + ") || "—"),
      kv("注入方式", memory.injection_method || "—"),
      kv("向量能力", memory.vector_available ? "可用" : "不可用"),
      kv("后台任务", String(data.pending_tasks ?? "—")),
    ].join("");

    renderTable(
      $("sys-jobs"),
      [
        { title: "任务", key: "key" },
        { title: "类型", key: "kind" },
        { title: "运行", className: "num", key: "runs" },
        { title: "失败", className: "num", key: "failures" },
        { title: "跳过", className: "num", key: "skipped" },
        {
          title: "下次执行",
          className: "num",
          render: (row) => esc(row.seconds_to_next === null ? "—" : `约 ${fmtDuration(row.seconds_to_next)} 后`),
        },
        {
          title: "最近错误",
          render: (row) => `<span class="muted">${esc(truncate(row.last_error, 60) || "—")}</span>`,
        },
      ],
      data.scheduler || []
    );

    const budget = data.budget || {};
    renderTable(
      $("sys-budget"),
      [
        { title: "日期", key: "day" },
        { title: "已用", className: "num", key: "used" },
        {
          title: "每日上限",
          className: "num",
          render: (row) => esc(row.daily_limit ? row.daily_limit : "不限制"),
        },
        {
          title: "剩余",
          className: "num",
          render: (row) => esc(row.remaining === null || row.remaining === undefined ? "—" : row.remaining),
        },
        { title: "被拒次数", className: "num", key: "rejected" },
        {
          title: "按用途",
          render: (row) => {
            const byPurpose = row.by_purpose || {};
            const keys = Object.keys(byPurpose);
            return keys.length
              ? `<span class="muted">${esc(keys.map((key) => `${key}=${byPurpose[key]}`).join("  "))}</span>`
              : "—";
          },
        },
      ],
      Object.keys(budget).length ? [budget] : []
    );

    renderTable(
      $("sys-embed"),
      [
        { title: "提供商 ID", key: "id" },
        { title: "模型", render: (row) => esc(row.model || "—") },
      ],
      data.embedding_providers || [],
      { emptyText: "未检测到嵌入模型提供商：检索将只使用关键词路（可在 AstrBot 中配置嵌入提供商）" }
    );
  } catch (error) {
    renderError($("sys-fw"), error);
  }
}

/* ---------------------------------------------------------------------- */
/* 记忆详情                                                                */
/* ---------------------------------------------------------------------- */

async function openMemoryDetail(id) {
  try {
    const item = await apiGet("memory", { id });
    const rows = [
      ["#", item.id],
      ["状态", item.status],
      ["类型", item.kind],
      ["来源", item.source],
      ["作用域", item.scope],
      ["重要度", item.importance],
      ["置信度", item.confidence],
      ["访问次数", item.access_count],
      ["创建时间", fmtTime(item.created_at)],
      ["最近访问", item.last_access_at ? fmtTime(item.last_access_at) : "从未"],
      ["标签", Array.isArray(item.tags) && item.tags.length ? item.tags.join("、") : "无"],
    ];
    const table = rows
      .map(([key, value]) => `<div class="kv"><div class="k">${esc(key)}</div><div class="v">${esc(value)}</div></div>`)
      .join("");
    openModal(`记忆 #${esc(item.id)}`, `<div class="kv-grid">${table}</div><pre>${esc(item.content)}</pre>`);
  } catch (error) {
    toast(error.message || String(error), "err");
  }
}

/* ---------------------------------------------------------------------- */
/* 路由与事件绑定                                                          */
/* ---------------------------------------------------------------------- */

const PAGE_TITLES = {
  overview: "nav.overview",
  features: "nav.features",
  memories: "nav.memories",
  recall: "nav.recall",
  journals: "nav.journals",
  reviews: "nav.reviews",
  persona: "nav.persona",
  system: "nav.system",
};

const LOADERS = {
  overview: () => loadOverview(true),
  features: () => loadFeatures(),
  memories: () => loadMemories(),
  journals: () => loadJournals(),
  reviews: () => loadReviews(),
  persona: () => loadPersona(),
  system: () => loadSystem(true),
};

function navigate(page, options = {}) {
  const target = PAGE_TITLES[page] ? page : "overview";
  state.page = target;
  document.querySelectorAll(".nav-item").forEach((node) => {
    node.classList.toggle("active", node.dataset.page === target);
  });
  document.querySelectorAll(".page").forEach((node) => {
    node.classList.toggle("active", node.id === `page-${target}`);
  });
  $("page-title").textContent = t(PAGE_TITLES[target]);
  if (!options.skipLoad && target !== "recall") {
    const loader = LOADERS[target];
    if (loader) loader();
  }
  if (!options.skipHash) {
    window.location.hash = `#/${target}`;
  }
}

function currentPageFromHash() {
  const match = /^#\/([a-z-]+)/.exec(window.location.hash || "");
  return match && PAGE_TITLES[match[1]] ? match[1] : "overview";
}

function applyStaticI18n() {
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    const key = node.getAttribute("data-i18n");
    const text = t(key, node.textContent);
    if (node.tagName === "TITLE") {
      document.title = text;
    } else if (node.tagName === "OPTION" || node.children.length === 0) {
      node.textContent = text;
    }
  });
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme === "light" ? "light" : "dark";
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  applyTheme(next);
  try {
    window.localStorage.setItem(THEME_KEY, next);
  } catch (error) {
    /* 隐私模式下 localStorage 不可用，忽略 */
  }
}

function bindEvents() {
  $("nav").addEventListener("click", (event) => {
    const button = event.target.closest(".nav-item");
    if (button) navigate(button.dataset.page);
  });

  $("btn-refresh").addEventListener("click", async () => {
    const button = $("btn-refresh");
    button.disabled = true;
    state.overview = null;
    try {
      const loader = LOADERS[state.page];
      if (loader) await loader();
      else if (state.page === "recall") await runRecall();
      toast("已刷新", "ok");
    } catch (error) {
      toast(error.message || String(error), "err");
    } finally {
      button.disabled = false;
    }
  });

  $("btn-theme").addEventListener("click", toggleTheme);

  // 记忆列表筛选
  $("mem-search").addEventListener("click", () => {
    state.memories.status = $("mem-status").value;
    state.memories.kind = $("mem-kind").value;
    state.memories.keyword = $("mem-keyword").value.trim();
    state.memories.offset = 0;
    loadMemories();
  });
  $("mem-prev").addEventListener("click", () => {
    state.memories.offset = Math.max(0, state.memories.offset - state.memories.limit);
    loadMemories();
  });
  $("mem-next").addEventListener("click", () => {
    state.memories.offset += state.memories.limit;
    loadMemories();
  });

  // 检索
  $("recall-run").addEventListener("click", runRecall);
  $("recall-query").addEventListener("keydown", (event) => {
    if (event.key === "Enter") runRecall();
  });

  // 周记分页
  $("jr-prev").addEventListener("click", () => {
    state.journals.offset = Math.max(0, state.journals.offset - state.journals.limit);
    loadJournals();
  });
  $("jr-next").addEventListener("click", () => {
    state.journals.offset += state.journals.limit;
    loadJournals();
  });

  // 拟人化学习
  $("pn-query").addEventListener("click", loadPersona);
  $("pn-umo").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadPersona();
  });

  // 维护
  $("btn-reindex").addEventListener("click", async () => {
    const button = $("btn-reindex");
    button.disabled = true;
    try {
      const result = await apiPost("maintenance", { action: "reindex" });
      const stats = result.stats || {};
      toast(
        `索引重建完成：关键词 ${stats.indexed ?? 0} 条，向量 ${stats.vectorized ?? 0} 条`,
        "ok"
      );
    } catch (error) {
      toast(error.message || String(error), "err");
    } finally {
      button.disabled = false;
    }
  });

  // 事件委托：跳转、详情、审批
  document.addEventListener("click", (event) => {
    const gotoNode = event.target.closest("[data-goto]");
    if (gotoNode) {
      navigate(gotoNode.dataset.goto);
      return;
    }
    const memoryNode = event.target.closest("[data-memory]");
    if (memoryNode) {
      openMemoryDetail(memoryNode.dataset.memory);
      return;
    }
    const reviewNode = event.target.closest("[data-review]");
    if (reviewNode) {
      handleReviewAction(reviewNode.dataset.review, reviewNode.dataset.action, reviewNode);
    }
  });

  // 功能开关（复选框只有 click 无法覆盖键盘操作，用 change 更稳妥）
  document.addEventListener("change", (event) => {
    const input = event.target.closest("[data-feature]");
    if (input) handleFeatureToggle(input.dataset.feature, input.checked, input);
  });

  $("modal-close").addEventListener("click", closeModal);
  $("modal").addEventListener("click", (event) => {
    if (event.target === $("modal")) closeModal();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal();
  });

  window.addEventListener("hashchange", () => {
    const next = currentPageFromHash();
    // 自己调用 navigate 时也会触发 hashchange，避免重复加载
    if (next === state.page) return;
    navigate(next);
  });
}

/* ---------------------------------------------------------------------- */
/* 启动                                                                    */
/* ---------------------------------------------------------------------- */

async function init() {
  bindEvents();

  // 主题：本地记忆优先，其次跟随 Dashboard
  let theme = null;
  try {
    theme = window.localStorage.getItem(THEME_KEY);
  } catch (error) {
    theme = null;
  }

  if (getBridge()) {
    try {
      const context = await ensureBridge();
      if (context) {
        if (context.locale) state.locale = context.locale;
        if (!theme && typeof context.isDark === "boolean") {
          theme = context.isDark ? "dark" : "light";
        }
      }
      const bridge = getBridge();
      if (bridge && typeof bridge.onContext === "function") {
        bridge.onContext((next) => {
          if (next && next.locale && next.locale !== state.locale) {
            state.locale = next.locale;
            applyStaticI18n();
            $("page-title").textContent = t(PAGE_TITLES[state.page]);
            const loader = LOADERS[state.page];
            if (loader) loader();
          }
          if (next && typeof next.isDark === "boolean") {
            applyTheme(next.isDark ? "dark" : "light");
          }
        });
      }
    } catch (error) {
      toast(error.message || String(error), "err");
    }
  } else {
    toast(t("errors.bridgeMissing"), "err");
  }

  applyTheme(theme || "dark");
  applyStaticI18n();
  navigate(currentPageFromHash(), { skipLoad: true, skipHash: true });
  await loadOverview(true);
  if (state.page !== "overview") {
    const loader = LOADERS[state.page];
    if (loader) await loader();
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
