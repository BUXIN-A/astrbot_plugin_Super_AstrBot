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
    "nav.graph": "图谱",
    "nav.monitor": "监控",
    "nav.prompts": "提示词",
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
    "domain.graph": "记忆图谱",
    "domain.review": "自动审核",
    "domain.maibot": "MaiBot 增强",
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
    "graph.title": "记忆图谱",
    "graph.hint": "实体与关系从长期记忆中抽取并按作用域隔离；填写 UMO 只查看对应会话的图谱。",
    "graph.umo": "会话 UMO（可选）",
    "graph.limit": "节点上限",
    "graph.query": "查询",
    "graph.layout": "环形布局 · 滚轮缩放 · 拖拽平移",
    "graph.enabled": "开关",
    "graph.nodes": "节点",
    "graph.edges": "关系",
    "graph.empty": "暂无图谱数据",
    "graph.truncated": "数据已截断（可提高节点上限）",
    "graph.detail": "节点详情",
    "monitor.title": "运行监控",
    "monitor.hint": "聚合最近的运行指标与自动审核情况，指标名形如 llm.calls、retrieval.calls。",
    "monitor.range": "时间范围",
    "monitor.bucket": "粒度",
    "monitor.metric": "指标",
    "monitor.trend": "趋势",
    "monitor.summary": "指标汇总",
    "monitor.review": "自动审核",
    "monitor.empty": "该区间暂无数据",
    "monitor.hour": "小时",
    "monitor.day": "天",
    "monitor.live": "实时指标",
    "monitor.pending": "待处理",
    "monitor.decidedAuto": "自动通过",
    "monitor.retention": "保留天数",
    "prompts.title": "提示词定制",
    "prompts.hint":
      "文本框已填好内置提示词，可直接修改；自定义模板必须保留标注的必填占位符，否则保存会被拒绝。「重置为默认」恢复内置内容。",
    "prompts.meta": "共 {total} 项，已自定义 {custom} 项；保存后立即生效。",
    "prompts.empty": "没有可定制的提示词。",
    "prompts.custom": "已自定义",
    "prompts.builtin": "内置默认",
    "prompts.required": "必填占位符",
    "action.save": "保存",
    "action.resetDefault": "重置为默认",
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
    "nav.graph": "Graph",
    "nav.monitor": "Monitor",
    "nav.prompts": "Prompts",
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
    "domain.graph": "Knowledge graph",
    "domain.review": "Auto review",
    "domain.maibot": "MaiBot boost",
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
    "graph.title": "Knowledge graph",
    "graph.hint": "Entities and relations are extracted from long-term memory and isolated by scope; set an UMO to view one session only.",
    "graph.umo": "Session UMO (optional)",
    "graph.limit": "Node limit",
    "graph.query": "Query",
    "graph.layout": "Ring layout · scroll to zoom · drag to pan",
    "graph.enabled": "Enabled",
    "graph.nodes": "Nodes",
    "graph.edges": "Relations",
    "graph.empty": "No graph data",
    "graph.truncated": "Truncated (raise the node limit)",
    "graph.detail": "Node detail",
    "monitor.title": "Monitor",
    "monitor.hint": "Aggregates recent runtime metrics and auto-review status; metric names look like llm.calls, retrieval.calls.",
    "monitor.range": "Range",
    "monitor.bucket": "Bucket",
    "monitor.metric": "Metric",
    "monitor.trend": "Trend",
    "monitor.summary": "Metric totals",
    "monitor.review": "Auto review",
    "monitor.empty": "No data in this window",
    "monitor.hour": "Hour",
    "monitor.day": "Day",
    "monitor.live": "Live metrics",
    "monitor.pending": "Pending",
    "monitor.decidedAuto": "Auto-approved",
    "monitor.retention": "Retention (days)",
    "prompts.title": "Prompt customization",
    "prompts.hint":
      "Each box is pre-filled with the built-in prompt. Custom templates must keep every required placeholder or the save is rejected. \"Reset to default\" restores the built-in text.",
    "prompts.meta": "{total} items, {custom} customized; changes take effect immediately.",
    "prompts.empty": "No customizable prompts.",
    "prompts.custom": "Customized",
    "prompts.builtin": "Built-in",
    "prompts.required": "Required placeholders",
    "action.save": "Save",
    "action.resetDefault": "Reset to default",
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

/** 简单插值：把 {name} 替换为对应值，缺值原样保留。 */
function tpl(text, values) {
  return String(text).replace(/\{(\w+)\}/g, (match, name) =>
    values[name] === undefined ? match : String(values[name])
  );
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
  graph: "domain.graph",
  review: "domain.review",
  maibot: "domain.maibot",
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
    if (result.rerank) parts.push(result.rerank);
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
            // 只渲染数值项；rerank_source 是来源标签，单独翻译成中文可读说明。
            const chips = Object.entries(breakdown)
              .filter(([, value]) => typeof value === "number")
              .map(([key, value]) => `${key}=${num(value, 3)}`);
            const source = breakdown.rerank_source;
            if (source === "provider") chips.push("重排序=模型");
            else if (source === "lexical") chips.push("重排序=词法兜底");
            if (chips.length === 0) return `<span class="muted">—</span>`;
            return `<span class="muted">${esc(chips.join("  "))}</span>`;
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
    const rerank = data.rerank || {};

    const kv = (label, value) =>
      `<div class="kv"><div class="k">${esc(label)}</div><div class="v">${esc(value)}</div></div>`;

    const rerankText = !rerank.enabled
      ? "关闭"
      : `${rerank.available ? "可用" : "模型不可用（回退 " + (rerank.fallback || "none") + "）"} · ${rerank.state || ""}`;

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
      kv("重排序能力", rerankText),
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
/* 章节：图谱                                                              */
/* ---------------------------------------------------------------------- */

const GRAPH_COLORS = {
  person: "#4c8dff",
  place: "#3fb950",
  org: "#d29922",
  event: "#f85149",
  concept: "#a371f7",
  thing: "#39c5cf",
};

const graphState = {
  data: null,
  layout: new Map(), // 归一化坐向 + 外圈标记，按 id 排序保证刷新不抖动
  view: { scale: 1, offsetX: 0, offsetY: 0 },
  size: { width: 0, height: 0 },
  activeId: null,
  hoverId: null,
  neighbors: new Set(),
  drag: { active: false, x: 0, y: 0, moved: false },
};

function graphFont() {
  return 'system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif';
}

function graphColor(type) {
  return GRAPH_COLORS[String(type || "").toLowerCase()] || "#8b949e";
}

function graphThemeColor(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

/** 稳定环形布局：按 id 排序后均匀铺在圆周上，孤立节点放到外圈。 */
function buildGraphLayout(nodes) {
  const sorted = [...(nodes || [])].sort((a, b) => Number(a.id) - Number(b.id));
  const layout = new Map();
  const place = (list, outer) => {
    const total = list.length || 1;
    list.forEach((node, index) => {
      const angle = (Math.PI * 2 * index) / total - Math.PI / 2;
      layout.set(String(node.id), { ux: Math.cos(angle), uy: Math.sin(angle), outer });
    });
  };
  place(sorted.filter((node) => Number(node.degree || 0) > 0), false);
  place(sorted.filter((node) => Number(node.degree || 0) <= 0), true);
  graphState.layout = layout;
}

function graphRings() {
  const half = Math.min(graphState.size.width, graphState.size.height) / 2;
  const main = Math.max(24, half - 40);
  return { main, outer: Math.max(main + 12, half - 12) };
}

function graphNodeScreen(node) {
  const { width, height } = graphState.size;
  const rings = graphRings();
  const layout = graphState.layout.get(String(node.id));
  const cx = width / 2;
  const cy = height / 2;
  let wx = cx;
  let wy = cy;
  if (layout) {
    const radius = layout.outer ? rings.outer : rings.main;
    wx = cx + layout.ux * radius;
    wy = cy + layout.uy * radius;
  }
  const { scale, offsetX, offsetY } = graphState.view;
  return {
    x: wx * scale + offsetX,
    y: wy * scale + offsetY,
    radius: 5 + Math.min(8, Number(node.degree || 0)),
  };
}

function graphNodeLabel(node) {
  return String(node.label || node.name || node.canonical_name || `#${node.id}`);
}

function truncateLabel(text, limit = 8) {
  const value = String(text || "");
  return value.length > limit ? `${value.slice(0, limit)}…` : value;
}

/** 原生 Canvas 2D 绘图：无任何外部图表库。 */
function drawGraph(data) {
  const canvas = $("gp-canvas");
  const stage = canvas ? canvas.parentElement : null;
  if (!canvas || !stage) return;
  const rect = stage.getBoundingClientRect();
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  graphState.size = { width, height };

  const textColor = graphThemeColor("--text-dim", "#98a1b0");
  const accent = graphThemeColor("--accent", "#4c8dff");
  const nodes = (data && data.nodes) || [];
  const edges = (data && data.edges) || [];

  if (nodes.length === 0) {
    ctx.fillStyle = textColor;
    ctx.font = `13px ${graphFont()}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(t("graph.empty"), width / 2, height / 2);
    return;
  }

  const nodeMap = new Map(nodes.map((node) => [String(node.id), node]));
  const focus = graphState.activeId;
  const scale = graphState.view.scale;

  // 边：半透明灰线，缩放 > 1 时才画关系标签
  edges.forEach((edge) => {
    const src = nodeMap.get(String(edge.src_entity_id));
    const dst = nodeMap.get(String(edge.dst_entity_id));
    if (!src || !dst) return;
    const a = graphNodeScreen(src);
    const b = graphNodeScreen(dst);
    const related =
      focus && graphState.neighbors.has(String(src.id)) && graphState.neighbors.has(String(dst.id));
    ctx.globalAlpha = focus ? (related ? 0.9 : 0.12) : 0.55;
    ctx.strokeStyle = focus && related ? accent : "#8b949e";
    ctx.lineWidth = 0.6 + Math.min(2, Number(edge.weight || 0));
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
    if (scale > 1 && edge.relation) {
      ctx.globalAlpha = focus ? (related ? 0.8 : 0.1) : 0.6;
      ctx.fillStyle = textColor;
      ctx.font = `10px ${graphFont()}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(truncateLabel(edge.relation, 8), (a.x + b.x) / 2, (a.y + b.y) / 2 - 4);
    }
  });
  ctx.globalAlpha = 1;

  // 节点：半径随度数增长，颜色按实体类型映射
  nodes.forEach((node) => {
    const pos = graphNodeScreen(node);
    const key = String(node.id);
    const faded = focus && !graphState.neighbors.has(key);
    ctx.globalAlpha = faded ? 0.2 : 1;
    ctx.beginPath();
    ctx.arc(pos.x, pos.y, pos.radius, 0, Math.PI * 2);
    ctx.fillStyle = graphColor(node.entity_type);
    ctx.fill();
    if (key === focus) {
      ctx.lineWidth = 2;
      ctx.strokeStyle = accent;
      ctx.stroke();
    }
    ctx.fillStyle = textColor;
    ctx.font = `${Math.max(9, Math.min(13, 11 * scale))}px ${graphFont()}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText(truncateLabel(graphNodeLabel(node)), pos.x, pos.y + pos.radius + 4);
  });
  ctx.globalAlpha = 1;
}

function graphHitTest(mx, my) {
  if (!graphState.data) return null;
  let best = null;
  let bestDistance = Infinity;
  (graphState.data.nodes || []).forEach((node) => {
    const pos = graphNodeScreen(node);
    const distance = Math.hypot(pos.x - mx, pos.y - my);
    if (distance <= pos.radius + 8 && distance < bestDistance) {
      best = node;
      bestDistance = distance;
    }
  });
  return best;
}

function showGraphTip(node, clientX, clientY) {
  const tip = $("gp-tip");
  const stage = document.querySelector(".graph-stage");
  if (!tip || !stage) return;
  tip.innerHTML =
    `<div class="tip-name">${esc(graphNodeLabel(node))}</div>` +
    `<div class="tip-row"><span>类型</span><span>${esc(node.entity_type || "—")}</span></div>` +
    `<div class="tip-row"><span>权重</span><span>${esc(num(node.weight))}</span></div>` +
    `<div class="tip-row"><span>证据</span><span>${esc(node.evidence ?? "—")}</span></div>`;
  tip.hidden = false;
  const rect = stage.getBoundingClientRect();
  let left;
  let top;
  if (typeof clientX === "number" && typeof clientY === "number") {
    left = clientX - rect.left + 12;
    top = clientY - rect.top + 12;
  } else {
    const pos = graphNodeScreen(node);
    left = pos.x + 14;
    top = pos.y + 14;
  }
  left = Math.max(4, Math.min(left, rect.width - tip.offsetWidth - 4));
  top = Math.max(4, Math.min(top, rect.height - tip.offsetHeight - 4));
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

function hideGraphTip() {
  const tip = $("gp-tip");
  if (tip) tip.hidden = true;
}

/** 高亮某节点及其一跳邻居，并在提示卡里展示详情。 */
function focusGraphNode(id) {
  const data = graphState.data;
  if (!data) return;
  const key = String(id);
  const node = (data.nodes || []).find((item) => String(item.id) === key);
  if (!node) return;
  const neighbors = new Set([key]);
  (data.edges || []).forEach((edge) => {
    const src = String(edge.src_entity_id);
    const dst = String(edge.dst_entity_id);
    if (src === key) neighbors.add(dst);
    if (dst === key) neighbors.add(src);
  });
  graphState.activeId = key;
  graphState.neighbors = neighbors;
  showGraphTip(node);
  drawGraph(data);
}

async function loadGraph() {
  const meta = $("gp-meta");
  const nodesEl = $("gp-nodes");
  const edgesEl = $("gp-edges");
  try {
    const umo = $("gp-umo").value.trim();
    const limitNodes = Number($("gp-limit").value) || 120;
    const data = await apiGet("graph", { umo, limit_nodes: limitNodes });
    graphState.data = data;
    graphState.activeId = null;
    graphState.hoverId = null;
    graphState.neighbors = new Set();
    graphState.view = { scale: 1, offsetX: 0, offsetY: 0 };
    hideGraphTip();

    const stats = data.stats || {};
    const parts = [
      `${t("graph.enabled")}：${data.enabled ? t("features.switchOn") : t("features.switchOff")}`,
      `${t("graph.nodes")}：${stats.entities ?? 0}`,
      `${t("graph.edges")}：${stats.relations ?? 0}`,
      `作用域：${stats.scopes ?? 0}`,
    ];
    if (data.truncated) parts.push(t("graph.truncated"));
    meta.textContent = parts.join("　|　");

    const nodes = data.nodes || [];
    const edges = data.edges || [];
    const labelOf = (id) => {
      const found = nodes.find((node) => String(node.id) === String(id));
      return found ? graphNodeLabel(found) : `#${id}`;
    };

    renderTable(
      nodesEl,
      [
        {
          title: t("graph.nodes"),
          render: (row) =>
            `<span class="clickable" data-node="${esc(row.id)}">${esc(graphNodeLabel(row))}</span>`,
        },
        { title: "类型", render: (row) => esc(row.entity_type || "—") },
        { title: "权重", className: "num", render: (row) => esc(num(row.weight)) },
        { title: "证据", className: "num", render: (row) => esc(row.evidence ?? "—") },
        { title: "度数", className: "num", render: (row) => esc(row.degree ?? 0) },
        { title: "作用域", render: (row) => `<span class="muted">${esc(row.scope || "")}</span>` },
      ],
      nodes,
      { emptyText: t("graph.empty") }
    );

    renderTable(
      edgesEl,
      [
        { title: "起点", render: (row) => esc(labelOf(row.src_entity_id)) },
        { title: "关系", render: (row) => esc(row.relation || "—") },
        { title: "终点", render: (row) => esc(labelOf(row.dst_entity_id)) },
        { title: "权重", className: "num", render: (row) => esc(num(row.weight)) },
        { title: "置信度", className: "num", render: (row) => esc(num(row.confidence)) },
      ],
      edges,
      { emptyText: t("graph.empty") }
    );

    buildGraphLayout(nodes);
    drawGraph(data);
  } catch (error) {
    meta.textContent = "";
    renderError(nodesEl, error);
    renderError(edgesEl, error);
  }
}

function bindGraphCanvas() {
  const canvas = $("gp-canvas");
  if (!canvas) return;

  canvas.addEventListener(
    "wheel",
    (event) => {
      if (!graphState.data) return;
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const mx = event.clientX - rect.left;
      const my = event.clientY - rect.top;
      const view = graphState.view;
      const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
      const next = Math.min(3, Math.max(0.3, view.scale * factor));
      const ratio = next / view.scale;
      view.offsetX = mx - (mx - view.offsetX) * ratio;
      view.offsetY = my - (my - view.offsetY) * ratio;
      view.scale = next;
      drawGraph(graphState.data);
    },
    { passive: false }
  );

  canvas.addEventListener("mousedown", (event) => {
    if (event.button !== 0 || !graphState.data) return;
    event.preventDefault();
    graphState.drag = { active: true, x: event.clientX, y: event.clientY, moved: false };
    canvas.style.cursor = "grabbing";
  });

  canvas.addEventListener("mousemove", (event) => {
    if (!graphState.data) return;
    const rect = canvas.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    const drag = graphState.drag;
    if (drag.active) {
      const dx = event.clientX - drag.x;
      const dy = event.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      graphState.view.offsetX += dx;
      graphState.view.offsetY += dy;
      drag.x = event.clientX;
      drag.y = event.clientY;
      hideGraphTip();
      drawGraph(graphState.data);
      return;
    }
    const hit = graphHitTest(mx, my);
    if (hit) {
      graphState.hoverId = String(hit.id);
      showGraphTip(hit, event.clientX, event.clientY);
      canvas.style.cursor = "pointer";
    } else {
      graphState.hoverId = null;
      hideGraphTip();
      canvas.style.cursor = "grab";
    }
  });

  canvas.addEventListener("mouseleave", () => {
    graphState.hoverId = null;
    hideGraphTip();
    if (!graphState.drag.active) canvas.style.cursor = "grab";
  });

  window.addEventListener("mouseup", (event) => {
    const drag = graphState.drag;
    if (!drag.active) return;
    const wasClick = !drag.moved;
    drag.active = false;
    canvas.style.cursor = "grab";
    if (wasClick) {
      const rect = canvas.getBoundingClientRect();
      const hit = graphHitTest(event.clientX - rect.left, event.clientY - rect.top);
      if (hit) focusGraphNode(hit.id);
    }
  });
}

function observeGraphResize() {
  const stage = document.querySelector(".graph-stage");
  if (!stage) return;
  if (typeof ResizeObserver === "function") {
    const observer = new ResizeObserver(() => {
      if (graphState.data) drawGraph(graphState.data);
    });
    observer.observe(stage);
  } else {
    window.addEventListener("resize", () => {
      if (graphState.data) drawGraph(graphState.data);
    });
  }
}

/* ---------------------------------------------------------------------- */
/* 章节：监控                                                              */
/* ---------------------------------------------------------------------- */

const MONITOR_LIVE_METRICS = [
  ["LLM 调用", "llm.calls"],
  ["LLM 失败", "llm.errors"],
  ["检索次数", "retrieval.calls"],
  ["注入字符", "inject.chars"],
  ["任务运行", "scheduler.runs"],
  ["任务失败", "scheduler.failures"],
];

function metricBucketValue(bucket) {
  if (!bucket) return null;
  const total = Number(bucket.total || 0);
  if (total > 0) return total;
  return Number(bucket.count || 0);
}

function metricValue(source, name) {
  return source ? metricBucketValue(source[name]) : null;
}

function fillMetricOptions(names) {
  const select = $("mt-metric");
  if (!select) return;
  const signature = names.join(",");
  if (select.dataset.signature === signature) return;
  const current = select.value;
  select.dataset.signature = signature;
  select.innerHTML = names
    .map((name) => `<option value="${esc(name)}">${esc(name)}</option>`)
    .join("");
  if (names.includes(current)) select.value = current;
}

/** 累计量型指标（字符数/耗时/token/命中）用 total 更合适。 */
function metricUsesTotal(name) {
  return /\.(chars|ms|tokens|hits|duration_ms)$/.test(String(name || ""));
}

function renderMonitorLive(data) {
  const live = data.live || {};
  const totals = data.totals || {};
  const pairs = MONITOR_LIVE_METRICS.map(([label, name]) => {
    let value = metricValue(live, name);
    if (value === null) value = metricValue(totals, name);
    return [label, value === null ? "—" : value];
  });
  renderStats($("mt-live"), pairs);
}

function renderMonitorChart(data, chosen) {
  const svg = $("mt-chart");
  const legend = $("mt-legend");
  if (!svg) return;
  const series = ((data.series || {})[chosen] || []).slice();
  if (!chosen || series.length === 0) {
    svg.innerHTML = `<text x="400" y="120" text-anchor="middle">${esc(t("monitor.empty"))}</text>`;
    if (legend) legend.textContent = "";
    return;
  }

  const W = 800;
  const H = 240;
  const padX = 12;
  const padTop = 14;
  const plotBottom = H - 24;
  const count = series.length;
  const useTotal = metricUsesTotal(chosen);
  const values = series.map((point) => Number(useTotal ? point.total : point.count) || 0);
  const top = Math.max(...values) > 0 ? Math.max(...values) * 1.1 : 1; // 全 0 时留基线，避免除零
  const xFor = (index) => (count === 1 ? W / 2 : padX + (index / (count - 1)) * (W - padX * 2));
  const yFor = (value) => plotBottom - (value / top) * (plotBottom - padTop);
  const points = values.map((value, index) => `${xFor(index).toFixed(1)},${yFor(value).toFixed(1)}`);

  const grid = [padTop, (padTop + plotBottom) / 2, plotBottom]
    .map(
      (y) =>
        `<line class="grid" x1="${padX}" y1="${y.toFixed(1)}" x2="${W - padX}" y2="${y.toFixed(1)}" />`
    )
    .join("");

  const labelIndexes = [...new Set(count === 1 ? [0] : [0, Math.floor((count - 1) / 2), count - 1])];
  const labels = labelIndexes
    .map((index) => {
      const ts = series[index] && series[index].bucket_ts;
      const anchor = index === 0 ? "start" : index === count - 1 ? "end" : "middle";
      const x = index === 0 ? padX : index === count - 1 ? W - padX : xFor(index);
      return `<text x="${x.toFixed(1)}" y="${H - 8}" text-anchor="${anchor}">${esc(fmtTime(ts))}</text>`;
    })
    .join("");

  const area = `${padX},${plotBottom} ${points.join(" ")} ${W - padX},${plotBottom}`;

  svg.innerHTML =
    `<defs><linearGradient id="mt-grad" x1="0" y1="0" x2="0" y2="1">` +
    `<stop class="stop-a" offset="0%" /><stop class="stop-b" offset="100%" /></linearGradient></defs>` +
    `${grid}` +
    `<polygon class="area" points="${area}" />` +
    `<polyline class="line" points="${points.join(" ")}" />` +
    `${labels}`;

  const sum = values.reduce((acc, value) => acc + value, 0);
  if (legend) {
    legend.innerHTML =
      `<span class="pill info">${esc(chosen)}</span>` +
      `<span class="muted"> ${esc(useTotal ? "Σ total" : "Σ count")}：${esc(sum)}</span>`;
  }
}

function renderMonitorTotals(data) {
  const rows = Object.entries(data.totals || {}).map(([name, bucket]) => {
    const count = Number((bucket || {}).count || 0);
    const total = Number((bucket || {}).total || 0);
    return { name, count, total, avg: count > 0 ? total / count : null };
  });
  renderTable(
    $("mt-totals"),
    [
      { title: t("monitor.metric"), render: (row) => esc(row.name) },
      { title: "次数", className: "num", render: (row) => esc(row.count) },
      { title: "合计", className: "num", render: (row) => esc(row.total) },
      {
        title: "均值",
        className: "num",
        render: (row) => esc(row.avg === null ? "—" : num(row.avg)),
      },
    ],
    rows,
    { emptyText: t("monitor.empty") }
  );
}

function renderMonitorReview(data) {
  const target = $("mt-review");
  if (!target) return;
  const review = data.review || {};
  const flag = (value) => (value ? t("features.switchOn") : t("features.switchOff"));
  const kv = (label, value) =>
    `<div class="kv"><div class="k">${esc(label)}</div><div class="v">${esc(value)}</div></div>`;
  target.innerHTML = [
    kv(t("monitor.review"), flag(review.enabled)),
    kv("LLM", flag(review.use_llm)),
    kv("审核待处理", review.pending ?? 0),
    kv(t("monitor.decidedAuto"), review.decided_by_auto ?? 0),
    kv(t("monitor.pending"), data.pending ?? 0),
    kv(t("monitor.retention"), data.retention_days ?? "—"),
  ].join("");
}

async function loadMonitor(isRetry = false) {
  const meta = $("mt-meta");
  const rangeSelect = $("mt-range");
  try {
    const rangeHours = Number(rangeSelect.value) || 24;
    const bucketSeconds = Number($("mt-bucket").value) || 3600;
    const requested = $("mt-metric").value || "";
    const data = await apiGet("monitor", {
      range_hours: rangeHours,
      bucket_seconds: bucketSeconds,
      metrics: requested,
    });

    const names = Array.isArray(data.metrics) ? data.metrics : [];
    fillMetricOptions(names);
    const select = $("mt-metric");
    const chosen = names.includes(requested) ? requested : names[0] || "";
    select.value = chosen;

    const series = (data.series || {})[chosen] || [];
    // 首次未指定指标时，若后端只回传所选指标的曲线，补一次请求
    if (!isRetry && chosen && series.length === 0) {
      await loadMonitor(true);
      return;
    }

    const option = rangeSelect.selectedOptions[0];
    const rangeLabel = option ? option.textContent : `${rangeHours}h`;
    meta.textContent =
      `${t("monitor.range")}：${rangeLabel}　|　` +
      `${t("monitor.bucket")}：${bucketSeconds >= 86400 ? t("monitor.day") : t("monitor.hour")}　|　` +
      `${t("monitor.metric")}：${chosen || "—"}`;

    renderMonitorLive(data);
    renderMonitorChart(data, chosen);
    renderMonitorTotals(data);
    renderMonitorReview(data);
  } catch (error) {
    meta.textContent = "";
    renderError($("mt-live"), error);
    renderError($("mt-totals"), error);
    const svg = $("mt-chart");
    if (svg) svg.innerHTML = "";
    const legend = $("mt-legend");
    if (legend) legend.textContent = "";
  }
}

/* ---------------------------------------------------------------------- */
/* 章节：提示词定制                                                        */
/* ---------------------------------------------------------------------- */

function renderPromptItem(item) {
  const badge = item.custom
    ? `<span class="pill on">${esc(t("prompts.custom"))}</span>`
    : `<span class="pill off">${esc(t("prompts.builtin"))}</span>`;
  const required = (item.required || []).length
    ? `<div class="muted">${esc(t("prompts.required"))}：${esc(
        item.required.map((name) => `{${name}}`).join("、")
      )}</div>`
    : "";
  return `
    <div class="prompt-item">
      <div class="name">${esc(item.title)} ${badge}
        <span class="key">${esc(item.key)}</span></div>
      ${item.hint ? `<div class="desc">${esc(item.hint)}</div>` : ""}
      ${required}
      <textarea class="prompt-text" data-prompt-text="${esc(item.key)}"
        rows="10" spellcheck="false"></textarea>
      <div class="row">
        <button class="btn" data-prompt-save="${esc(item.key)}">${esc(t("action.save"))}</button>
        <button class="btn ghost" data-prompt-reset="${esc(item.key)}">${esc(
          t("action.resetDefault")
        )}</button>
      </div>
    </div>`;
}

async function loadPrompts() {
  const target = $("pm-list");
  const meta = $("pm-meta");
  try {
    const result = await apiGet("prompts");
    const items = result.items || [];
    if (items.length === 0) {
      target.innerHTML = "";
      meta.textContent = t("prompts.empty");
      return;
    }

    const blocks = [];
    let group = "";
    items.forEach((item) => {
      if (item.group && item.group !== group) {
        group = item.group;
        blocks.push(`<div class="muted prompt-group">${esc(group)}</div>`);
      }
      blocks.push(renderPromptItem(item));
    });
    target.innerHTML = blocks.join("");

    // 默认值可能含 < & 等字符，用 value 赋值而非拼进 HTML 属性，避免转义歧义。
    items.forEach((item) => {
      const node = document.querySelector(`[data-prompt-text="${item.key}"]`);
      if (node) node.value = item.value || "";
    });

    const custom = items.filter((item) => item.custom).length;
    meta.textContent = tpl(t("prompts.meta"), { total: items.length, custom });
  } catch (error) {
    renderError(target, error);
    meta.textContent = "";
  }
}

async function handlePromptSave(key, button) {
  const textarea = document.querySelector(`[data-prompt-text="${key}"]`);
  if (!textarea) return;
  button.disabled = true;
  try {
    const result = await apiPost("prompt-save", { key, value: textarea.value });
    toast(result.message || t("action.save"), "ok");
    await loadPrompts();
  } catch (error) {
    toast(error.message || String(error), "err");
    button.disabled = false;
  }
}

async function handlePromptReset(key, button) {
  button.disabled = true;
  try {
    const result = await apiPost("prompt-reset", { key });
    toast(result.message || t("action.resetDefault"), "ok");
    await loadPrompts();
  } catch (error) {
    toast(error.message || String(error), "err");
    button.disabled = false;
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
  graph: "nav.graph",
  monitor: "nav.monitor",
  prompts: "nav.prompts",
  system: "nav.system",
};

const LOADERS = {
  overview: () => loadOverview(true),
  features: () => loadFeatures(),
  memories: () => loadMemories(),
  journals: () => loadJournals(),
  reviews: () => loadReviews(),
  persona: () => loadPersona(),
  graph: () => loadGraph(),
  monitor: () => loadMonitor(),
  prompts: () => loadPrompts(),
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

  // 图谱
  $("gp-query").addEventListener("click", loadGraph);
  $("gp-umo").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadGraph();
  });
  bindGraphCanvas();

  // 监控
  $("mt-query").addEventListener("click", () => loadMonitor());
  ["mt-range", "mt-bucket", "mt-metric"].forEach((id) => {
    $(id).addEventListener("change", () => loadMonitor());
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
    const nodeItem = event.target.closest("[data-node]");
    if (nodeItem) {
      focusGraphNode(nodeItem.dataset.node);
      return;
    }
    const reviewNode = event.target.closest("[data-review]");
    if (reviewNode) {
      handleReviewAction(reviewNode.dataset.review, reviewNode.dataset.action, reviewNode);
      return;
    }
    const promptSave = event.target.closest("[data-prompt-save]");
    if (promptSave) {
      handlePromptSave(promptSave.dataset.promptSave, promptSave);
      return;
    }
    const promptReset = event.target.closest("[data-prompt-reset]");
    if (promptReset) {
      handlePromptReset(promptReset.dataset.promptReset, promptReset);
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
  observeGraphResize();

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
