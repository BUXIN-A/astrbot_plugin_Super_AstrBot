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
    "nav.journals": "现实桥",
    "nav.weeklies": "每周总结",
    "nav.reviews": "待审",
    "nav.persona": "学习",
    "nav.graph": "图谱",
    "nav.monitor": "监控",
    "nav.models": "模型",
    "nav.prompts": "提示词",
    "nav.system": "系统",
    "nav.identity": "身份",
    "features.title": "功能开关",
    "features.hint": "开关与「具体设置」都会立即写入插件配置并落盘，与插件配置页双向同步；标注「需重载」的项目将在重载插件后生效。",
    "features.empty": "没有可用的功能项。",
    "features.switchOn": "开",
    "features.switchOff": "关",
    "features.settings": "具体设置",
    "features.settingsOpen": "收起设置",
    "features.syncedAt": "与配置页双向同步 · %1",
    "features.syncNow": "立即同步",
    "features.syncFailed": "同步失败（插件可能在重载），保留当前显示",
    "features.externalSync": "已从插件配置页同步 %1 项改动",
    "features.listHint": "逗号分隔",
    "features.editHint": "失焦或回车后保存",
    "features.needsReload": "需重载",
    "features.runtimeUnsupported": "环境不支持",
    "features.blockedBy": "依赖未开启",
    "domain.basic": "基础",
    "domain.memory": "记忆",
    "domain.reflection": "自我学习",
    "domain.journal": "现实桥",
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
    "stat.journals": "现实桥",
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
    "journals.title": "现实桥（周记 / 日记 / 随笔）",
    "journals.add": "+ 新增记录",
    "journals.edit": "编辑",
    "journals.delete": "删除",
    "journals.scope": "作用域",
    "journals.scopeHint": "global:* / user:123 / group:456",
    "table.actions": "操作",
    "journals.emotion": "情绪 (1-5)",
    "journals.content": "内容",
    "action.confirm": "确认",
    "journals.tags": "标签（逗号分隔）",
    "journals.exported": "已导出",
    "journals.imported": "导入完成：新增 %1，跳过 %2",
    "journals.type": "类型",
    "journals.titleField": "标题",
    "journals.createdAt": "创建时间",
    "journals.titleHint": "留空自动使用当天日期时间（例 2015061517:00）",
    "journals.selectPage": "本页全选",
    "journals.selectFilter": "全选当前筛选（{count} 条）",
    "journals.selectNone": "清空选择",
    "journals.selectedCount": "已选 {count} 条",
    "journals.noneSelected": "尚未选择",
    "journals.exportSelected": "导出所选 JSON",
    "journals.exportSelectedDone": "已导出 {count} 条",
    "journals.empty": "还没有记录（可用 /sab 日记 内容 记录，或点「新增记录」）",
    "journals.newTitle": "+ 新增记录",
    "jtype.all": "全部",
    "jtype.weekly": "周记",
    "jtype.diary": "日记",
    "jtype.essay": "随笔",
    "weeklies.title": "每周总结",
    "weeklies.hint": "每周总结由「周度洞察」基于本周现实桥记录自动生成，独立于现实桥管理；可删除或导入导出。",
    "weeklies.empty": "还没有每周总结（开启周度洞察后自动生成，或导入历史 JSON）",
    "weeklies.imported": "导入完成：新增 %1，跳过 %2",
    "action.exportJournals": "导出全部 JSON",
    "action.importJournals": "导入 JSON",
    "action.exportWeeklies": "导出 JSON",
    "action.importWeeklies": "导入 JSON",
    "persona.title": "拟人化学习",
    "persona.hint": "风格样本、群内用语与好感度都在这里查看；未经批准的学习结果不会影响对话。",
    "persona.umo": "会话 UMO（可选）",
    "persona.styles": "表达样本",
    "persona.jargons": "群内用语",
    "persona.affinity": "好感度",
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
    "system.maintenance": "维护",
    "system.configMaintenance": "配置维护",
    "system.configHint": "导出当前插件配置为 JSON 备份；导入会按配置 Schema 校验并合并，导入后自动热应用（能力开关与各模块配置即时生效，无需重载）。",
    "system.configImported": "配置已导入并热应用",
    "action.importConfig": "导入配置 JSON",
    "action.exportConfig": "导出配置 JSON",
    "action.exportMemories": "导出 JSON",
    "action.importMemories": "导入 JSON",
    "memories.imported": "导入完成：新增 %1，跳过 %2",
    "system.reindexHint": "根据当前配置重建关键词索引与向量索引，不会删除记忆。",
    "backup.title": "备份与导出",
    "backup.hint": "全局备份：把插件全部业务表（记忆 / 现实桥 / 图谱 / 拟人化学习 / 待审 / 身份观测等）、配置与数据库快照打包成一个 zip。包内 manifest.json 记录逐表行数、每个文件的大小与 sha256，并写明未包含的表及原因；服务器 backups/ 目录保留最近若干份。",
    "backup.notes": "备注（可选）",
    "backup.export": "导出备份 ZIP",
    "backup.import": "导入备份 ZIP",
    "backup.imported": "导入完成",
    "backup.mode": "导入模式",
    "backup.modeMerge": "合并导入（推荐，保留备份之后的新数据）",
    "backup.modeReplace": "完全覆盖（恢复到备份时刻）",
    "backup.modeMergeShort": "合并导入",
    "backup.modeReplaceShort": "完全覆盖",
    "backup.resultMode": "本次模式",
    "backup.replaceConfirmTitle": "完全覆盖确认",
    "backup.replaceConfirmBody": "将用备份内容覆盖业务表：备份里没有的行会被删除，且不可撤销（恢复前会自动留一份数据库快照）。",
    "backup.replaceConfirmPlan": "将写入 {1} 行；预计删除 ≥{2} 行（估计值：当前行数 − 备份行数，主键有差异时实际更多）。",
    "backup.replaceConfirmTail": "确认后立即执行。",
    "backup.dbRestore": "整库恢复",
    "backup.dbRestoreHint": "用包内数据库快照替换整个库：这是唯一能连未导出的表（风格 / 图谱 / 待审）与运行态一起还原的方式。",
    "backup.dbRestoreConfirm": "将断开数据库连接 → 把当前库另存为 pre-restore 备份 → 用快照覆盖 → 重新连接并跑迁移。任一步失败会自动回滚原库。",
    "backup.dbRestored": "整库恢复完成",
    "backup.dbRestoreBackup": "原库备份",
    "backup.importHint": "恢复为逐表合并（备份优先）：备份里有的行一定恢复，备份之后新产生的行不受影响；写库前自动留一份数据库快照。数据库文件另存到 backups/，整库恢复需停用插件后手动替换。",
    "backup.downloading": "正在打包，包体较大时请稍候…",
    "backup.done": "备份包已生成：{1}",
    "backup.savedAt": "服务器副本：{1}",
    "backup.empty": "还没有备份包（点「导出备份 ZIP」生成第一份）",
    "backup.colFile": "文件",
    "backup.colSize": "大小",
    "backup.colTime": "生成时间",
    "backup.colPath": "服务器路径",
    "identity.title": "身份诊断",
    "identity.hint": "记忆按「谁说的」归属，而部分平台的用户标识会随会话变化。这里观测每条会话上出现过的发送者标识：若同一昵称对应多个 ID，说明平台 ID 不稳定，应改用昵称策略，否则记忆仍会被会话切碎。",
    "identity.clear": "清空观测",
    "identity.cleared": "已清空 {1} 条身份观测",
    "identity.strategy": "身份策略",
    "identity.scopeType": "作用域类型",
    "identity.tracking": "观测开关",
    "identity.umoCount": "已观测会话",
    "identity.verdict": "结论",
    "identity.colUmo": "会话 UMO",
    "identity.colPlatform": "平台",
    "identity.colSenderId": "发送者 ID",
    "identity.colSenderName": "昵称",
    "identity.colScope": "作用域",
    "identity.colEvents": "次数",
    "identity.colLast": "最近",
    "identity.empty": "还没有身份观测数据（用户说一句话后即可看到）",
    "identity.scopeTitle": "作用域分布与迁移",
    "identity.scopeHint": "只有带发送者标识的记忆才能按用户归属；迁移前请先预览，落库前插件会自动备份数据库。",
    "identity.fromScope": "来源作用域",
    "identity.toScope": "迁移目标",
    "identity.toUser": "按发送者归属到用户",
    "identity.toGlobal": "整体提升为全局",
    "identity.toArchive": "仅归档",
    "identity.toUserElseArchive": "能归属的归用户，其余归档",
    "identity.preview": "预览",
    "identity.apply": "执行迁移",
    "identity.applyConfirm": "即将写入数据库（会先自动备份）：",
    "identity.colTotal": "总数",
    "identity.colActive": "可召回",
    "identity.colAttributed": "带身份",
    "identity.distEmpty": "还没有记忆数据",
    "identity.on": "开启",
    "identity.off": "关闭",
    "verdict.empty": "无样本",
    "verdict.unstable_id": "ID 不稳定",
    "verdict.stable_id": "ID 稳定",
    "verdict.single": "样本不足",
    "reviews.approveAll": "批准当前筛选全部",
    "reviews.rejectAll": "驳回当前筛选全部",
    "reviews.batchConfirm": "将对当前筛选下的全部待审记录执行：",
    "reviews.batchDone": "已处理 {1} 条",
    "modal.detail": "详情",
    "peek.memoryTitle": "记忆 #{id}",
    "peek.journalTitle": "现实记录 #{id}",
    "peek.weeklyTitle": "每周总结 #{id}",
    "peek.edit": "编辑",
    "peek.delete": "删除",
    "peek.save": "保存",
    "peek.cancel": "取消",
    "peek.content": "内容",
    "peek.metadata": "元数据",
    "peek.editContent": "编辑内容",
    "peek.needContent": "内容不能为空",
    "peek.saved": "已保存",
    "peek.deleted": "已删除",
    "peek.gone": "条目不存在（可能已被删除，或不在当前页）",
    "peek.untitled": "（无标题）",
    "peek.empty": "（空内容）",
    "peek.importance": "重要度 {value}",
    "peek.emotion": "情绪 {value}",
    "peek.close": "关闭详情面板",
    "peek.field.status": "状态",
    "peek.field.kind": "类型",
    "peek.field.source": "来源",
    "peek.field.scope": "作用域",
    "peek.field.importance": "重要度",
    "peek.field.confidence": "置信度",
    "peek.field.accessCount": "访问次数",
    "peek.field.createdAt": "创建时间",
    "peek.field.updatedAt": "更新时间",
    "peek.field.lastAccessAt": "最近访问",
    "peek.field.tags": "标签",
    "peek.field.senderId": "发送者 ID",
    "peek.field.senderName": "发送者昵称",
    "peek.field.originUmo": "来源会话",
    "peek.field.title": "标题",
    "peek.field.emotion": "情绪",
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
    "nav.journals": "Reality Bridge",
    "nav.weeklies": "Weekly digest",
    "nav.reviews": "Reviews",
    "nav.persona": "Learning",
    "nav.identity": "Identity",
    "nav.graph": "Graph",
    "nav.monitor": "Monitor",
    "nav.models": "Models",
    "nav.prompts": "Prompts",
    "nav.system": "System",
    "features.title": "Feature toggles",
    "features.hint": "Toggles and per-feature settings are written to the plugin config immediately and stay two-way in sync with the config page; items marked \"reload\" take effect after reloading the plugin.",
    "features.empty": "No features available.",
    "features.switchOn": "On",
    "features.switchOff": "Off",
    "features.settings": "Settings",
    "features.settingsOpen": "Hide settings",
    "features.syncedAt": "Two-way synced with config page · %1",
    "features.syncNow": "Sync now",
    "features.syncFailed": "Sync failed (plugin may be reloading), keeping current view",
    "features.externalSync": "Synced %1 change(s) from the plugin config page",
    "features.listHint": "Comma separated",
    "features.editHint": "Saves on blur or Enter",
    "features.needsReload": "Reload",
    "features.runtimeUnsupported": "Unsupported",
    "features.blockedBy": "Dependencies off",
    "domain.basic": "Basics",
    "domain.memory": "Memory",
    "domain.reflection": "Self-learning",
    "domain.journal": "Reality Bridge",
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
    "stat.journals": "Bridge",
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
    "journals.title": "Reality Bridge (weekly / diary / essay)",
    "journals.add": "+ Add entry",
    "journals.edit": "Edit",
    "journals.delete": "Delete",
    "journals.scope": "Scope",
    "journals.scopeHint": "global:* / user:123 / group:456",
    "table.actions": "Actions",
    "journals.emotion": "Emotion (1-5)",
    "journals.content": "Content",
    "action.confirm": "Confirm",
    "journals.tags": "Tags (comma separated)",
    "journals.exported": "Exported",
    "journals.imported": "Imported: %1 added, %2 skipped",
    "journals.type": "Type",
    "journals.titleField": "Title",
    "journals.createdAt": "Created",
    "journals.titleHint": "Leave empty to use today's date-time (e.g. 2015061517:00)",
    "journals.selectPage": "Select page",
    "journals.selectFilter": "Select all filtered ({count})",
    "journals.selectNone": "Clear selection",
    "journals.selectedCount": "{count} selected",
    "journals.noneSelected": "Nothing selected",
    "journals.exportSelected": "Export selected JSON",
    "journals.exportSelectedDone": "Exported {count} entries",
    "journals.empty": "No entries yet (use /sab diary <text>, or click \"Add entry\")",
    "journals.newTitle": "+ Add entry",
    "jtype.all": "All",
    "jtype.weekly": "Weekly",
    "jtype.diary": "Diary",
    "jtype.essay": "Essay",
    "weeklies.title": "Weekly digest",
    "weeklies.hint": "Weekly digests are generated by the weekly insight from this week's reality-bridge entries; manage, export or import them here.",
    "weeklies.empty": "No weekly digests yet (enable weekly insight, or import a JSON backup)",
    "weeklies.imported": "Imported: %1 added, %2 skipped",
    "action.exportJournals": "Export all JSON",
    "action.importJournals": "Import JSON",
    "action.exportWeeklies": "Export JSON",
    "action.importWeeklies": "Import JSON",
    "persona.title": "Persona learning",
    "persona.hint": "Style samples, group jargon and affinity are listed here; unapproved learnings never affect replies.",
    "persona.umo": "Session UMO (optional)",
    "persona.styles": "Style samples",
    "persona.jargons": "Group jargon",
    "persona.affinity": "Affinity",
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
    "system.maintenance": "Maintenance",
    "system.configMaintenance": "Config maintenance",
    "system.configHint": "Export the plugin config as a JSON backup; import validates against the schema, merges and hot-applies (capability switches and module configs take effect immediately, no reload needed).",
    "system.configImported": "Config imported and hot-applied",
    "action.exportConfig": "Export config JSON",
    "action.importConfig": "Import config JSON",
    "action.exportMemories": "Export JSON",
    "action.importMemories": "Import JSON",
    "memories.imported": "Imported: %1 added, %2 skipped",
    "system.reindexHint": "Rebuild keyword and vector indexes from current config. Memories are not deleted.",
    "backup.title": "Backup & export",
    "backup.hint": "Global backup: packs every business table (memories, reality bridge, graph, persona learning, reviews, identity observations ...), the config and a database snapshot into one zip. manifest.json records per-table row counts, each file's size and sha256, and why some tables are excluded; recent archives are kept in the server's backups/ directory.",
    "backup.notes": "Note (optional)",
    "backup.export": "Export backup ZIP",
    "backup.import": "Import backup ZIP",
    "backup.imported": "Import finished",
    "backup.mode": "Import mode",
    "backup.modeMerge": "Merge (recommended; keeps data created after the backup)",
    "backup.modeReplace": "Full replace (restore to the backup moment)",
    "backup.modeMergeShort": "Merge",
    "backup.modeReplaceShort": "Full replace",
    "backup.resultMode": "Mode used",
    "backup.replaceConfirmTitle": "Confirm full replace",
    "backup.replaceConfirmBody": "Business tables will be overwritten by the backup: rows missing from the backup are deleted and this cannot be undone (a database snapshot is taken first).",
    "backup.replaceConfirmPlan": "Will write {1} rows; at least {2} rows are expected to be deleted (estimate: current rows minus backup rows; more when primary keys differ).",
    "backup.replaceConfirmTail": "Runs immediately after confirmation.",
    "backup.dbRestore": "Restore whole database",
    "backup.dbRestoreHint": "Replaces the entire database with the snapshot inside the package: the only way to restore tables that were never exported (persona / graph / reviews) plus runtime state.",
    "backup.dbRestoreConfirm": "The plugin will disconnect, save the current database as a pre-restore backup, overwrite it with the snapshot, then reconnect and run migrations. Any failure rolls the original database back automatically.",
    "backup.dbRestored": "Whole-database restore finished",
    "backup.dbRestoreBackup": "Previous database backup",
    "backup.importHint": "Restores table by table with backup-wins merging: rows present in the backup are restored, rows created after the backup are untouched; a database snapshot is taken before writing. The database file is stashed under backups/ and must be swapped in while the plugin is disabled.",
    "backup.downloading": "Packing, this can take a moment for large data...",
    "backup.done": "Backup archive created: {1}",
    "backup.savedAt": "Server copy: {1}",
    "backup.empty": "No backup archives yet (click \"Export backup ZIP\")",
    "backup.colFile": "File",
    "backup.colSize": "Size",
    "backup.colTime": "Created",
    "backup.colPath": "Server path",
    "identity.title": "Identity diagnostics",
    "identity.hint": "Memories are attributed to whoever said them, but some platforms change the user identifier per session. These observations show which sender identifiers appear on which session: if one nickname maps to several IDs, the platform ID is unstable and the nickname strategy should be used instead.",
    "identity.clear": "Clear observations",
    "identity.cleared": "Cleared {1} identity observations",
    "identity.strategy": "Identity strategy",
    "identity.scopeType": "Scope type",
    "identity.tracking": "Observation",
    "identity.umoCount": "Sessions seen",
    "identity.verdict": "Verdict",
    "identity.colUmo": "Session UMO",
    "identity.colPlatform": "Platform",
    "identity.colSenderId": "Sender ID",
    "identity.colSenderName": "Nickname",
    "identity.colScope": "Scope",
    "identity.colEvents": "Events",
    "identity.colLast": "Last seen",
    "identity.empty": "No identity observations yet (they appear after a user speaks)",
    "identity.scopeTitle": "Scope distribution & migration",
    "identity.scopeHint": "Only memories carrying a sender identifier can be attributed to a user. Preview first; the plugin backs up the database before writing.",
    "identity.fromScope": "From scope",
    "identity.toScope": "Target",
    "identity.toUser": "Attribute to user by sender",
    "identity.toGlobal": "Promote everything to global",
    "identity.toArchive": "Archive only",
    "identity.toUserElseArchive": "Attributed to user, rest archived",
    "identity.preview": "Preview",
    "identity.apply": "Migrate now",
    "identity.applyConfirm": "This writes to the database (an automatic backup runs first):",
    "identity.colTotal": "Total",
    "identity.colActive": "Recallable",
    "identity.colAttributed": "Attributed",
    "identity.distEmpty": "No memories yet",
    "identity.on": "on",
    "identity.off": "off",
    "verdict.empty": "No samples",
    "verdict.unstable_id": "ID unstable",
    "verdict.stable_id": "ID stable",
    "verdict.single": "Not enough samples",
    "reviews.approveAll": "Approve all filtered",
    "reviews.rejectAll": "Reject all filtered",
    "reviews.batchConfirm": "Applies to every pending record under the current filter:",
    "reviews.batchDone": "Handled {1} records",
    "modal.detail": "Detail",
    "peek.memoryTitle": "Memory #{id}",
    "peek.journalTitle": "Journal #{id}",
    "peek.weeklyTitle": "Weekly #{id}",
    "peek.edit": "Edit",
    "peek.delete": "Delete",
    "peek.save": "Save",
    "peek.cancel": "Cancel",
    "peek.content": "Content",
    "peek.metadata": "Metadata",
    "peek.editContent": "Edit content",
    "peek.needContent": "Content cannot be empty",
    "peek.saved": "Saved",
    "peek.deleted": "Deleted",
    "peek.gone": "Item not found (it may have been deleted or is off the current page)",
    "peek.untitled": "(untitled)",
    "peek.empty": "(empty)",
    "peek.importance": "Importance {value}",
    "peek.emotion": "Mood {value}",
    "peek.close": "Close detail panel",
    "peek.field.status": "Status",
    "peek.field.kind": "Kind",
    "peek.field.source": "Source",
    "peek.field.scope": "Scope",
    "peek.field.importance": "Importance",
    "peek.field.confidence": "Confidence",
    "peek.field.accessCount": "Access count",
    "peek.field.createdAt": "Created",
    "peek.field.updatedAt": "Updated",
    "peek.field.lastAccessAt": "Last access",
    "peek.field.tags": "Tags",
    "peek.field.senderId": "Sender ID",
    "peek.field.senderName": "Sender name",
    "peek.field.originUmo": "Origin session",
    "peek.field.title": "Title",
    "peek.field.emotion": "Mood",
    "errors.bridgeMissing": "Plugin page bridge SDK not loaded: reload the plugin or refresh the page",
    "errors.requestFailed": "Request failed",
    "empty.none": "No data",
  },
};

const FEATURES_POLL_MS = 5000;

const state = {
  page: "overview",
  locale: "zh-CN",
  context: null,
  overview: null,
  featuresItems: [],
  featuresClosedGroups: new Set(),
  featuresOpenSettings: new Set(),
  memories: {
    offset: 0,
    limit: 20,
    status: "active",
    kind: "",
    keyword: "",
    sort: "created_desc",
    total: 0,
  },
  journals: {
    offset: 0,
    limit: 20,
    keyword: "",
    type: "",
    sort: "event_desc",
    total: 0,
    /** 后端配置的默认类型（journal.default_entry_type），新增记录时预选。 */
    defaultType: "weekly",
  },
  journalsItems: [],
  /** 已勾选的记录 id（跨页保留，直到筛选或翻页动作重置）。 */
  journalsSelected: new Set(),
  /** 勾选状态是否为「全选当前筛选」：此时导出交给后端按筛选条件取全量。 */
  journalsSelectAll: false,
  weeklies: { offset: 0, limit: 20, keyword: "", total: 0 },
  weekliesItems: [],
  reviews: { offset: 0, limit: 20, origin: "", umo: "", total: 0 },
};

/* ---------------------------------------------------------------------- */
/* 基础工具                                                                */
/* ---------------------------------------------------------------------- */

function getBridge() {
  return window.AstrBotPluginPage || null;
}

function t(key, fallback) {
  // 本地字典优先（随插件打包、切语言即时生效），bridge 里注册的插件 i18n 可覆盖，
  // fallback（通常是节点现有文案）只作为 key 完全未知的最后兜底。
  // 否则传节点文案当 fallback 会让本地字典永远用不上，静态节点切不了语言。
  const dict = LOCAL_I18N[state.locale] || LOCAL_I18N["zh-CN"];
  const base = LOCAL_I18N["zh-CN"];
  const local =
    dict[key] !== undefined
      ? dict[key]
      : base[key] !== undefined
        ? base[key]
        : fallback !== undefined
          ? fallback
          : key;
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
  window.setTimeout(() => {
    if (!node.isConnected) return;
    // 出场比入场快（150ms vs 200ms），transitionend 后移除；reduced-motion 下可能不触发，兜底定时
    node.classList.add("leaving");
    const done = () => node.remove();
    node.addEventListener("transitionend", done, { once: true });
    window.setTimeout(done, 400);
  }, 4200);
}

let modalCloseTimer = 0;

function openModal(title, html) {
  // 快速「关闭→再打开」时，取消还没播完的退场回调，别把新弹窗藏掉
  window.clearTimeout(modalCloseTimer);
  $("modal").classList.remove("closing");
  $("modal-title").textContent = title;
  $("modal-body").innerHTML = html;
  $("modal").hidden = false;
}

function closeModal() {
  const modal = $("modal");
  if (modal.hidden || modal.classList.contains("closing")) return;
  // 先播 150ms 退场动画再隐藏，避免瞬间消失的跳变；
  // transitionend 会冒泡，只认 modal 自身的 opacity 过渡，另有定时器兜底
  modal.classList.add("closing");
  const done = () => {
    window.clearTimeout(modalCloseTimer);
    modal.classList.remove("closing");
    modal.hidden = true;
    $("modal-body").innerHTML = "";
  };
  modal.addEventListener("transitionend", function handler(event) {
    if (event.target !== modal) return;
    modal.removeEventListener("transitionend", handler);
    done();
  });
  modalCloseTimer = window.setTimeout(done, 400);
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

/* 表格排序登记表：``options.sortable`` 的表格留下最近一次数据与排序状态，
   点击表头时按同一份数据重排，无需重新请求后端。 */
const TABLE_STATE = new Map();

function columnSortable(column) {
  return Boolean(column.key) || typeof column.sortValue === "function";
}

function tableSortValue(column, row, index) {
  if (typeof column.sortValue === "function") return column.sortValue(row, index);
  return column.key ? row[column.key] : "";
}

function sortTableRows(columns, rows, sortIndex, direction) {
  const column = columns[sortIndex];
  if (!column) return rows;
  const factor = direction === "desc" ? -1 : 1;
  return rows.slice().sort((left, right) => {
    const a = tableSortValue(column, left);
    const b = tableSortValue(column, right);
    if (typeof a === "number" && typeof b === "number") return (a - b) * factor;
    return String(a ?? "").localeCompare(String(b ?? ""), "zh-CN", { numeric: true }) * factor;
  });
}

/** 键值对单元格：面板多个分区共用。 */
function kv(label, value) {
  return `<div class="kv"><div class="k">${esc(label)}</div><div class="v">${esc(value)}</div></div>`;
}

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
    TABLE_STATE.delete(target.id);
    renderEmpty(target, options.emptyText);
    return;
  }
  const previous = TABLE_STATE.get(target.id);
  const entry = {
    columns,
    rows,
    options,
    sortable: Boolean(options.sortable),
    sortIndex: previous && previous.sortIndex >= 0 ? previous.sortIndex : -1,
    direction: previous ? previous.direction : "desc",
  };
  if (entry.sortable && target.id) TABLE_STATE.set(target.id, entry);
  else TABLE_STATE.delete(target.id);
  paintTable(target, entry);
}

function paintTable(target, entry) {
  const { columns, rows, sortable, sortIndex, direction } = entry;
  const data = sortIndex >= 0 ? sortTableRows(columns, rows, sortIndex, direction) : rows;
  const head = columns
    .map((column, index) => {
      const clickable = sortable && columnSortable(column);
      const cls = clickable ? ' class="sortable"' : "";
      const attr = clickable ? ` data-sort-col="${index}"` : "";
      const mark =
        clickable && index === sortIndex
          ? `<span class="sort-mark">${direction === "desc" ? "▼" : "▲"}</span>`
          : "";
      return `<th${cls}${attr}>${esc(column.title)}${mark}</th>`;
    })
    .join("");
  const body = data
    .map((row, index) => {
      const cells = columns
        .map((column) => {
          const cls = column.className ? ` class="${column.className}"` : "";
          const content = column.render ? column.render(row, index) : esc(row[column.key]);
          return `<td${cls}>${content}</td>`;
        })
        .join("");
      const rowAttrs = entry.options.rowAttrs ? entry.options.rowAttrs(row) : "";
      return `<tr${rowAttrs}>${cells}</tr>`;
    })
    .join("");
  target.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

/** 点击可排序表头：在已加载的数据上切换升/降序，不重新请求后端。 */
function handleSortClick(header) {
  const wrap = header.closest(".table-wrap");
  const entry = wrap ? TABLE_STATE.get(wrap.id) : null;
  if (!entry) return;
  const index = Number(header.dataset.sortCol);
  entry.direction = entry.sortIndex === index && entry.direction === "desc" ? "asc" : "desc";
  entry.sortIndex = index;
  paintTable(wrap, entry);
}

function truncate(text, limit = 120) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value;
}

function kindPill(kind) {
  return `<span class="pill info">${esc(kind)}</span>`;
}

/** 顶部细进度条：切换分区与手动刷新时给出统一的加载反馈。 */
function showLoading(active) {
  const bar = $("loading-bar");
  if (bar) bar.classList.toggle("active", Boolean(active));
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
  const settings = item.settings || [];
  const settingsBtn = settings.length
    ? `<button type="button" class="btn ghost small${
        state.featuresOpenSettings.has(item.key) ? " active" : ""
      }" data-settings-toggle="${esc(item.key)}">${esc(
        state.featuresOpenSettings.has(item.key) ? t("features.settingsOpen") : t("features.settings")
      )}</button>`
    : "";
  return `
    <div class="feature-item">
      <div class="info">
        <div class="name">${esc(item.title)} ${featureStatusPill(item)}
          <span class="key">${esc(item.key)}</span></div>
        <div class="desc">${esc(item.description || "")}</div>
        ${blocked}
      </div>
      <div class="ops">
        ${settingsBtn}
        <label class="switch">
          <input type="checkbox" data-feature="${esc(item.key)}"
            ${item.enabled ? "checked" : ""} ${locked ? "disabled" : ""} />
          <span class="track"></span><span class="thumb"></span>
        </label>
      </div>
    </div>`;
}

function renderSettingRow(spec) {
  const type = spec.type || "string";
  // prev 用于保存失败时回滚；JSON 字符串里的引号由 esc 转义
  const prevAttr = `data-prev='${esc(JSON.stringify(spec.value === undefined ? null : spec.value))}'`;
  const common = `data-setting="${esc(spec.key)}" data-type="${esc(type)}" ${prevAttr}`;
  let control;
  if (type === "bool") {
    control = `<label class="switch"><input type="checkbox" ${common} ${
      spec.value ? "checked" : ""
    } /><span class="track"></span><span class="thumb"></span></label>`;
  } else if (type === "int" || type === "float") {
    control = `<input type="number" ${type === "int" ? 'step="1"' : 'step="any"'} ${common} value="${esc(
      spec.value === null || spec.value === undefined ? "" : spec.value
    )}" />`;
  } else if (type === "string" && Array.isArray(spec.options) && spec.options.length) {
    const opts = spec.options
      .map((opt, index) => {
        const label = Array.isArray(spec.labels) ? spec.labels[index] : opt;
        const selected = String(spec.value ?? "") === String(opt) ? "selected" : "";
        return `<option value="${esc(opt)}" ${selected}>${esc(label ?? opt)}</option>`;
      })
      .join("");
    control = `<select ${common}>${opts}</select>`;
  } else if (type === "text") {
    control = `<textarea rows="3" ${common} placeholder="${esc(t("features.editHint"))}">${esc(
      spec.value ?? ""
    )}</textarea>`;
  } else if (type === "list") {
    const text = Array.isArray(spec.value) ? spec.value.join(", ") : String(spec.value ?? "");
    control = `<input type="text" ${common} value="${esc(text)}" placeholder="${esc(
      t("features.listHint")
    )}" />`;
  } else {
    control = `<input type="text" ${common} value="${esc(spec.value ?? "")}" />`;
  }
  const hint = [spec.key, type === "list" ? t("features.listHint") : "", spec.hint]
    .filter(Boolean)
    .map((part) => esc(part))
    .join(" · ");
  return `
    <div class="setting-row">
      <div class="setting-info">
        <div class="setting-name">${esc(spec.description || spec.key)}</div>
        <div class="setting-key">${hint}</div>
      </div>
      <div class="setting-ctl">${control}</div>
    </div>`;
}

function renderFeatureBlock(item) {
  const settings = item.settings || [];
  const open = state.featuresOpenSettings.has(item.key);
  const panel = settings.length
    ? `<div class="feature-settings${open ? " open" : ""}" data-settings-panel="${esc(item.key)}">
        <div class="feature-settings-inner">${settings.map(renderSettingRow).join("")}</div>
      </div>`
    : "";
  return `<div class="feature-block">${renderFeatureItem(item)}${panel}</div>`;
}

function renderFeatureGroups(items) {
  const groups = new Map();
  items.forEach((item) => {
    const domain = item.domain || "other";
    if (!groups.has(domain)) groups.set(domain, []);
    groups.get(domain).push(item);
  });

  const blocks = [];
  groups.forEach((groupItems, domain) => {
    const title = DOMAIN_TITLES[domain] ? t(DOMAIN_TITLES[domain]) : domain;
    const open = !state.featuresClosedGroups.has(domain);
    blocks.push(`
      <section class="fgroup${open ? " open" : ""}" data-group="${esc(domain)}">
        <button type="button" class="fgroup-head" data-group-toggle="${esc(domain)}">
          <span class="chev" aria-hidden="true">▸</span>
          <span class="fgroup-title">${esc(title)}</span>
          <span class="fgroup-count mono">${groupItems.length}</span>
        </button>
        <div class="fgroup-body"><div class="fgroup-body-inner">
          ${groupItems.map(renderFeatureBlock).join("")}
        </div></div>
      </section>`);
  });
  $("feat-list").innerHTML = blocks.join("");
}

function updateFeatureSyncTime() {
  const node = $("feat-sync");
  if (!node) return;
  node.textContent = t("features.syncedAt").replaceAll("%1", new Date().toLocaleTimeString());
  const line = node.closest("#feat-sync-line");
  if (line) line.classList.remove("sync-error");
}

/** 对比拉取到的数据与内存快照，统计外部改动（自己的保存会同步快照，不会误报）。 */
function diffFeatureChanges(items) {
  const previous = state.featuresItems || [];
  if (!previous.length) return [];
  const prevEnabled = new Map(previous.map((item) => [item.key, !!item.enabled]));
  const prevValues = new Map();
  previous.forEach((item) =>
    (item.settings || []).forEach((spec) => prevValues.set(spec.key, JSON.stringify(spec.value ?? null)))
  );

  const changed = [];
  items.forEach((item) => {
    if (prevEnabled.has(item.key) && prevEnabled.get(item.key) !== !!item.enabled) {
      changed.push(item.title || item.key);
    }
    (item.settings || []).forEach((spec) => {
      if (prevValues.has(spec.key) && prevValues.get(spec.key) !== JSON.stringify(spec.value ?? null)) {
        changed.push(spec.description || spec.key);
      }
    });
  });
  return changed;
}

async function loadFeatures(options = {}) {
  const silent = !!options.silent;
  const target = $("feat-list");
  try {
    const result = await apiGet("features");
    const items = result.items || [];
    if (items.length === 0) {
      renderEmpty(target, t("features.empty"));
      return;
    }
    // 轮询路径：先对比再更新快照，外部改动给出可见提示
    if (silent) {
      const changed = diffFeatureChanges(items);
      if (changed.length) {
        toast(t("features.externalSync").replaceAll("%1", String(changed.length)), "ok");
      }
    }
    state.featuresItems = items;
    renderFeatureGroups(items);
    updateFeatureSyncTime();
  } catch (error) {
    if (silent) {
      // 官方配置页保存会触发插件热重载，重载窗口内请求可能失败：
      // 保留当前界面不砸掉，只标记同步状态，下一轮轮询自动恢复
      const node = $("feat-sync");
      if (node) {
        node.textContent = t("features.syncFailed");
        const line = node.closest("#feat-sync-line");
        if (line) line.classList.add("sync-error");
      }
    } else {
      renderError(target, error);
    }
  }
}

function syncFeaturesNow() {
  if (state.page !== "features") return;
  const list = $("feat-list");
  if (list && list.contains(document.activeElement)) return;
  loadFeatures({ silent: true });
}

/** 双向同步（读取向）：轮询 + 聚焦/可见即拉取，官方配置页的改动最迟数秒内到达。 */
function startFeatureSyncPolling() {
  window.setInterval(() => {
    if (state.page !== "features" || document.hidden) return;
    syncFeaturesNow();
  }, FEATURES_POLL_MS);
  // 切回面板页 / 窗口聚焦时立即拉一次，不等下一个周期
  const pullOnWake = () => {
    if (state.page === "features" && !document.hidden) syncFeaturesNow();
  };
  document.addEventListener("visibilitychange", pullOnWake);
  window.addEventListener("focus", pullOnWake);
}



async function saveFeatureSetting(key, control) {
  const type = control.dataset.type;
  let value;
  if (type === "bool") value = control.checked;
  else if (type === "int" || type === "float") {
    value = control.value === "" ? null : Number(control.value);
  } else {
    value = control.value;
  }
  control.disabled = true;
  try {
    const result = await apiPost("feature-setting", { key, value });
    toast(result.message || t("features.saved") || "已保存", "ok");
    // 同步内存快照，重载/轮询前界面与后端一致
    for (const item of state.featuresItems || []) {
      const spec = (item.settings || []).find((entry) => entry.key === key);
      if (spec) {
        spec.value = result.value;
        break;
      }
    }
    state.overview = null;
  } catch (error) {
    toast(error.message || String(error), "err");
    // 回滚到保存前的值
    let prev = null;
    try {
      prev = JSON.parse(control.dataset.prev || "null");
    } catch (parseError) {
      prev = null;
    }
    if (type === "bool") control.checked = !!prev;
    else if (type === "list") control.value = Array.isArray(prev) ? prev.join(", ") : "";
    else control.value = prev === null || prev === undefined ? "" : prev;
  } finally {
    control.disabled = false;
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
    {
      title: "重要度",
      className: "num",
      render: (row) => esc(num(row.importance)),
      sortValue: (row) => Number(row.importance || 0),
    },
    { title: "访问", className: "num", key: "access_count" },
    {
      title: "创建",
      className: "num",
      render: (row) => esc(fmtTime(row.created_at)),
      sortValue: (row) => Number(row.created_at || 0),
    },
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
      sort: cursor.sort,
    });
    cursor.total = Number(result.total || 0);
    // 服务端已按 cursor.sort 排序；表格不再提供列排序，避免两种排序口径打架。
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
/* 章节：现实桥（周记 / 日记 / 随笔）                                        */
/* ---------------------------------------------------------------------- */

const JOURNAL_TYPES = ["weekly", "diary", "essay"];

/** 类型中文名；未知类型原样回退，避免渲染出 "jtype.xxx" 这类半成品文案。 */
function journalTypeLabel(type) {
  const key = `jtype.${type}`;
  const text = t(key);
  return text === key ? String(type || "") : text;
}

/** 默认标题：与后端 spec.entry_types.TITLE_TIME_FORMAT 一致（YYYYMMDDHH:MM）。 */
function defaultJournalTitle(date) {
  const moment = date instanceof Date ? date : new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${moment.getFullYear()}${pad(moment.getMonth() + 1)}${pad(moment.getDate())}` +
    `${pad(moment.getHours())}:${pad(moment.getMinutes())}`
  );
}

function journalRowIds() {
  return (state.journalsItems || [])
    .map((item) => Number(item.id))
    .filter((id) => Number.isFinite(id));
}

function isJournalSelected(id) {
  return state.journalsSelectAll || state.journalsSelected.has(Number(id));
}

/** 筛选条件变化时丢弃勾选：否则「已选」会跨条件悄悄累积。 */
function resetJournalSelection() {
  state.journalsSelected = new Set();
  state.journalsSelectAll = false;
}

function syncJournalSelectionUi() {
  const ids = journalRowIds();
  const selected = state.journalsSelectAll
    ? Number(state.journals.total || 0)
    : state.journalsSelected.size;

  const info = $("jr-select-info");
  if (info) {
    info.textContent = selected
      ? tpl(t("journals.selectedCount"), { count: selected })
      : t("journals.noneSelected");
  }
  const exportButton = $("jr-export-selected");
  if (exportButton) exportButton.disabled = selected === 0;
  const matchButton = $("jr-select-match");
  if (matchButton) {
    matchButton.textContent = tpl(t("journals.selectFilter"), {
      count: Number(state.journals.total || 0),
    });
  }
  const checkAll = $("jr-check-all");
  if (checkAll) {
    checkAll.checked = ids.length > 0 && ids.every((id) => isJournalSelected(id));
    checkAll.disabled = ids.length === 0;
  }
}

/** 只改行选中样式，不重绘表格（重绘会打断勾选连击）。 */
function syncJournalRowSelection() {
  document.querySelectorAll("#jr-table tbody tr").forEach((row) => {
    const box = row.querySelector("[data-journal-check]");
    if (box) row.classList.toggle("selected", box.checked);
  });
}

function journalColumns() {
  return [
    {
      title: "",
      className: "check",
      render: (row) =>
        `<input type="checkbox" data-journal-check="${esc(row.id)}"${
          isJournalSelected(row.id) ? " checked" : ""
        } aria-label="${esc(t("journals.selectPage"))}" />`,
    },
    { title: "#", key: "id", className: "num" },
    {
      title: t("journals.titleField"),
      className: "title-cell",
      // 标题与内容都可点开侧滑详情（编辑/删除在面板头部，行内按钮保持原样）
      render: (row) =>
        `<span class="clickable" data-journal="${esc(row.id)}">${esc(row.title || "—")}</span>`,
    },
    {
      title: t("journals.content"),
      className: "content",
      render: (row) =>
        `<span class="clickable" data-journal="${esc(row.id)}">${esc(row.content)}</span>`,
    },
    {
      title: t("journals.type"),
      className: "num",
      render: (row) =>
        `<span class="pill jt-${esc(row.type || "")}">${esc(journalTypeLabel(row.type))}</span>`,
    },
    {
      title: t("journals.tags"),
      render: (row) => {
        const tags = Array.isArray(row.tags) ? row.tags : [];
        return tags.length
          ? tags.map((tag) => `<span class="tag">${esc(tag)}</span>`).join("")
          : "—";
      },
    },
    { title: t("journals.emotion"), className: "num", render: (row) => esc(row.emotion || "—") },
    {
      title: t("journals.createdAt"),
      className: "num",
      render: (row) => esc(fmtTime(row.created_at)),
    },
    {
      title: t("table.actions"),
      className: "actions",
      render: (row) =>
        `<button class="link-btn" data-journal-edit="${esc(row.id)}">${esc(
          t("journals.edit")
        )}</button><button class="link-btn danger-soft" data-journal-del="${esc(
          row.id
        )}">${esc(t("journals.delete"))}</button>`,
    },
  ];
}

function paintJournalsTable() {
  renderTable($("jr-table"), journalColumns(), state.journalsItems || [], {
    emptyText: t("journals.empty"),
    rowAttrs: (row) => (isJournalSelected(row.id) ? ' class="selected"' : ""),
  });
}

async function loadJournals() {
  const target = $("jr-table");
  const cursor = state.journals;
  try {
    const result = await apiGet("journals", {
      offset: cursor.offset,
      limit: cursor.limit,
      keyword: cursor.keyword,
      entry_type: cursor.type,
      sort: cursor.sort,
    });
    cursor.total = Number(result.total || 0);
    if (JOURNAL_TYPES.includes(result.default_type)) {
      cursor.defaultType = result.default_type;
    }
    state.journalsItems = result.items || [];
    paintJournalsTable();
    syncPeekAfterReload("journal", state.journalsItems);
  } catch (error) {
    renderError(target, error);
  }
  const from = cursor.total === 0 ? 0 : cursor.offset + 1;
  const to = Math.min(cursor.offset + cursor.limit, cursor.total);
  $("jr-page-info").textContent = `${from}-${to} / ${cursor.total}`;
  $("jr-prev").disabled = cursor.offset <= 0;
  $("jr-next").disabled = cursor.offset + cursor.limit >= cursor.total;
  syncJournalSelectionUi();
}

/* ---------------------------------------------------------------------- */
/* 章节：现实桥管理（admin-diary-proxy 模式：面板增/编/删/导入导出）          */
/* ---------------------------------------------------------------------- */

function openJournalEditor(entry) {
  entry = entry || {};
  const isEdit = !!entry.id;
  const tagsText = Array.isArray(entry.tags) ? entry.tags.join(", ") : String(entry.tags ?? "");
  // 新增时预填「当天日期时间」的默认标题：与后端补的默认值一致，用户可随手改掉。
  const type = JOURNAL_TYPES.includes(entry.type)
    ? entry.type
    : state.journals.type || state.journals.defaultType || JOURNAL_TYPES[0];
  const title = isEdit ? String(entry.title || "") : defaultJournalTitle();
  openModal(
    isEdit ? `${t("journals.edit")} #${entry.id}` : t("journals.newTitle"),
    `
      <div class="form-grid">
        <label class="field">
          <span>${esc(t("journals.type"))}</span>
          <select id="jrf-type">
            ${JOURNAL_TYPES.map(
              (value) =>
                `<option value="${value}"${value === type ? " selected" : ""}>${esc(
                  journalTypeLabel(value)
                )}</option>`
            ).join("")}
          </select>
        </label>
        <label class="field grow">
          <span>${esc(t("journals.titleField"))}</span>
          <input type="text" id="jrf-title" value="${esc(title)}"
            placeholder="${esc(t("journals.titleHint"))}" />
        </label>
        <label class="field">
          <span>${esc(t("journals.emotion"))}</span>
          <input type="number" id="jrf-emotion" min="1" max="5"
            value="${esc(entry.emotion ?? "")}" />
        </label>
      </div>
      <div class="form-grid" style="margin-top:8px">
        <label class="field grow">
          <span>${esc(t("journals.scope"))}</span>
          <input type="text" id="jrf-scope" value="${esc(entry.scope || "global:*")}"
            placeholder="${esc(t("journals.scopeHint"))}" ${isEdit ? "disabled" : ""} />
        </label>
        <label class="field grow">
          <span>${esc(t("journals.tags"))}</span>
          <input type="text" id="jrf-tags" value="${esc(tagsText)}" />
        </label>
      </div>
      <label class="field grow" style="margin-top:8px">
        <span>${esc(t("journals.content"))}</span>
        <textarea id="jrf-content" rows="5">${esc(entry.content || "")}</textarea>
      </label>
      <div class="row" style="margin-top:12px;justify-content:flex-end">
        <button class="btn ghost" id="journal-cancel">${esc(t("action.close"))}</button>
        <button class="btn" id="journal-save" data-id="${esc(entry.id ?? "")}">${esc(
          t("action.save")
        )}</button>
      </div>`
  );
}

async function saveJournal() {
  const id = Number($("journal-save").dataset.id || 0);
  const payload = {
    content: $("jrf-content").value,
    title: $("jrf-title").value,
    entry_type: $("jrf-type").value,
    tags: $("jrf-tags").value,
    emotion: $("jrf-emotion").value === "" ? null : Number($("jrf-emotion").value),
  };
  try {
    let result;
    if (id) {
      result = await apiPost("journals/update", { id, ...payload });
    } else {
      payload.scope = $("jrf-scope").value.trim() || "global:*";
      result = await apiPost("journals/add", payload);
    }
    toast(result.message || "已保存", "ok");
    closeModal();
    resetJournalSelection();
    // 抽屉里点「编辑」→ 保存后要把面板内容一起刷新，否则停在旧正文上
    await loadJournals();
  } catch (error) {
    toast(error.message || String(error), "err");
  }
}

function downloadJsonExport(payload, filename) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function importJsonFile(file, endpoint, successText) {
  try {
    const result = await uploadFile(endpoint, file);
    toast(
      (successText || "导入完成").replace("%1", String(result.imported ?? 0)).replace("%2", String(result.skipped ?? 0)),
      "ok"
    );
    return true;
  } catch (error) {
    toast(error.message || String(error), "err");
    return false;
  }
}

/** 文件上传导入：必须走 bridge.upload（multipart，字段名固定 file），
 * 不能用 apiPost——JSON body 无法承载文件，后端 request.files() 会拿不到。 */
async function uploadFile(endpoint, file) {
  const bridge = await ensureBridge();
  return unwrap(await bridge.upload(endpoint, file));
}

/** 轻量确认弹窗：复用详情 modal，返回 Promise<boolean>。 */
/**
 * 轻量确认弹窗。
 *
 * ``text`` 默认按纯文本转义；需要多行排版时传 ``options.html = true``（调用方自行保证内容安全）。
 * 返回的 Promise 在用户点「确认」时 resolve ``true``、点「关闭」时 resolve ``false``——
 * 覆盖恢复这类不可逆操作必须能区分「没确认」和「确认了」，不能一厢情愿地当成同意。
 */
function confirmRequest(title, text, onConfirm, options = {}) {
  const body = options.html ? String(text) : esc(text);
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(value);
    };
    const cleanup = () => {
      const ok = $("confirm-ok");
      const cancel = $("confirm-cancel");
      if (ok) ok.removeEventListener("click", onOk);
      if (cancel) cancel.removeEventListener("click", onCancel);
      closeModal();
    };
    const onOk = async () => {
      finish(true);
      try {
        await onConfirm();
      } catch (error) {
        toast(error.message || String(error), "err");
      }
    };
    const onCancel = () => finish(false);

    openModal(
      title,
      `<p style="margin:0 0 12px">${body}</p>
       <div class="row" style="justify-content:flex-end">
         <button class="btn ghost" id="confirm-cancel">${esc(t("action.close"))}</button>
         <button class="btn danger" id="confirm-ok">${esc(t("action.confirm"))}</button>
       </div>`
    );
    $("confirm-ok").addEventListener("click", onOk);
    $("confirm-cancel").addEventListener("click", onCancel);
  });
}

/* ---------------------------------------------------------------------- */
/* 章节：每周总结（周度洞察产出，独立管理 + 导入导出）                        */
/* ---------------------------------------------------------------------- */

async function loadWeeklies() {
  const target = $("wk-table");
  const cursor = state.weeklies;
  try {
    const result = await apiGet("weeklies", {
      offset: cursor.offset,
      limit: cursor.limit,
      keyword: cursor.keyword,
    });
    cursor.total = Number(result.total || 0);
    state.weekliesItems = result.items || [];
    syncPeekAfterReload("weekly", state.weekliesItems);
    renderTable(
      target,
      [
        { title: "#", key: "id", className: "num" },
        {
          title: "内容",
          className: "content",
          render: (row) =>
            `<span class="clickable" data-weekly="${esc(row.id)}">${esc(row.content)}</span>`,
        },
        { title: "重要度", className: "num", render: (row) => esc(num(row.importance)) },
        { title: "作用域", render: (row) => `<span class="muted">${esc(row.scope)}</span>` },
        { title: "时间", className: "num", render: (row) => esc(fmtTime(row.created_at)) },
        {
          title: t("table.actions"),
          className: "actions",
          render: (row) =>
            `<button class="link-btn danger-soft" data-weekly-del="${row.id}">${esc(
              t("journals.delete")
            )}</button>`,
        },
      ],
      result.items || [],
      { emptyText: t("weeklies.empty") }
    );
  } catch (error) {
    renderError(target, error);
  }
  const from = cursor.total === 0 ? 0 : cursor.offset + 1;
  const to = Math.min(cursor.offset + cursor.limit, cursor.total);
  $("wk-page-info").textContent = `${from}-${to} / ${cursor.total}`;
  $("wk-prev").disabled = cursor.offset <= 0;
  $("wk-next").disabled = cursor.offset + cursor.limit >= cursor.total;
}

/* ---------------------------------------------------------------------- */
/* 章节：待审                                                              */
/* ---------------------------------------------------------------------- */

/** 刷新「来源」下拉：选项来自后端实际出现过的来源，保留用户当前选择。 */
function syncOriginOptions(origins, current) {
  const select = $("rv-origin");
  if (!select) return;
  const values = ["", ...origins.filter(Boolean)];
  if (current && !values.includes(current)) values.push(current);
  const signature = values.join("|");
  if (select.dataset.signature === signature) {
    select.value = current || "";
    return;
  }
  select.dataset.signature = signature;
  select.innerHTML = values
    .map((value) => `<option value="${esc(value)}">${esc(value || "全部")}</option>`)
    .join("");
  select.value = current || "";
}

async function loadReviews() {
  const target = $("rv-table");
  const hint = $("rv-hint");
  const cursor = state.reviews;
  try {
    const result = await apiGet("reviews", {
      limit: cursor.limit,
      offset: cursor.offset,
      origin: cursor.origin,
      umo: cursor.umo,
    });
    cursor.total = Number(result.total || 0);
    syncOriginOptions(result.origins || [], cursor.origin);
    hint.textContent = cursor.total
      ? `${t("reviews.disabled")}（共 ${cursor.total} 条待审）`
      : `${t("reviews.disabled")}（队列为空）`;

    renderTable(
      target,
      [
        { title: "#", key: "id", className: "num" },
        { title: "内容", className: "content", render: (row) => esc(row.summary || "") },
        { title: "来源", key: "origin" },
        { title: "作用域", render: (row) => `<span class="muted">${esc(row.scope || "")}</span>` },
        {
          title: "时间",
          className: "num",
          render: (row) => esc(fmtTime(row.created_at)),
          sortValue: (row) => Number(row.created_at || 0),
        },
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
      result.items || [],
      { emptyText: "当前筛选下没有待审记录。" }
    );
  } catch (error) {
    hint.textContent = "";
    renderError(target, error);
  }
  const from = cursor.total === 0 ? 0 : cursor.offset + 1;
  const to = Math.min(cursor.offset + cursor.limit, cursor.total);
  $("rv-page-info").textContent = `${from}-${to} / ${cursor.total}`;
  $("rv-prev").disabled = cursor.offset <= 0;
  $("rv-next").disabled = cursor.offset + cursor.limit >= cursor.total;
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
        { title: "场景", key: "situation", className: "content", render: (row) => esc(row.situation || "") },
        { title: "表达", key: "expression", className: "content", render: (row) => esc(row.expression || "") },
        { title: "权重", key: "weight", className: "num", render: (row) => esc(num(row.weight)) },
        { title: "命中", className: "num", key: "hits" },
        { title: "作用域", key: "scope", render: (row) => `<span class="muted">${esc(row.scope || "")}</span>` },
      ],
      result.style || [],
      { sortable: true, emptyText: "还没有学到表达样本" }
    );

    renderTable(
      $("pn-jargon"),
      [
        { title: "#", key: "id", className: "num" },
        { title: "词语", key: "term", render: (row) => esc(row.term || "") },
        { title: "含义", key: "meaning", className: "content", render: (row) => esc(row.meaning || "") },
        { title: "置信度", key: "confidence", className: "num", render: (row) => esc(num(row.confidence)) },
        { title: "证据", className: "num", key: "evidence" },
        { title: "作用域", key: "scope", render: (row) => `<span class="muted">${esc(row.scope || "")}</span>` },
      ],
      result.jargon || [],
      { sortable: true, emptyText: "还没有收录群内用语" }
    );

    renderTable(
      $("pn-affinity"),
      [
        { title: "对象", key: "target_id", render: (row) => esc(row.target_id || "") },
        { title: "好感度", key: "score", className: "num", render: (row) => esc(num(row.score)) },
        { title: "情绪", key: "mood", render: (row) => esc(row.mood || "—") },
        { title: "交互", className: "num", key: "interactions" },
        { title: "作用域", key: "scope", render: (row) => `<span class="muted">${esc(row.scope || "")}</span>` },
        {
          title: "最近交互",
          key: "last_interaction",
          className: "num",
          render: (row) => esc(row.last_interaction ? fmtTime(row.last_interaction) : "—"),
        },
      ],
      result.affinity || [],
      { sortable: true, emptyText: "还没有好感度记录" }
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

    $("sys-fw").innerHTML = [
      kv("插件版本", data.plugin_version || "—"),
      kv("AstrBot 版本", framework.version || "—"),
      kv("框架符号", framework.symbols_ok ? "完整" : "有缺失（已降级）"),
      kv("缺失符号", (framework.missing || []).join("、") || "无"),
      kv("数据库", data.database || "—"),
      kv("FTS 全文索引", data.fts ? "可用" : "降级为 LIKE"),
      kv("检索路", (memory.routes || []).join(" + ") || "—"),
      kv("注入方式", memory.injection_method || "—"),
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
    await loadBackups();
  } catch (error) {
    renderError($("sys-fw"), error);
  }
}

/* ---------------------------------------------------------------------- */
/* 章节：备份与导出（配置 + 数据库快照 + 各类数据 → zip）                     */
/* ---------------------------------------------------------------------- */

function fmtBytes(size) {
  const value = Number(size);
  if (!Number.isFinite(value) || value <= 0) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1048576).toFixed(2)} MB`;
}

async function loadBackups() {
  const table = $("bk-table");
  const meta = $("bk-meta");
  try {
    const result = await apiGet("backup/list");
    const tables = result.tables || [];
    const excluded = Object.keys(result.excluded || {});
    meta.textContent = `${tpl(t("backup.savedAt"), { 1: result.dir || "—" })}（保留最近 ${
      result.keep ?? "—"
    } 份 · 全量 ${tables.length} 张表 · 不含 ${excluded.join(" / ") || "无"}）`;
    renderTable(
      table,
      [
        { title: t("backup.colFile"), key: "filename" },
        { title: t("backup.colSize"), className: "num", render: (row) => esc(fmtBytes(row.size)) },
        {
          title: t("backup.colTime"),
          className: "num",
          render: (row) => esc(fmtTime(row.created_at)),
        },
        {
          title: t("backup.colPath"),
          render: (row) => `<span class="muted">${esc(row.path)}</span>`,
        },
      ],
      result.items || [],
      { emptyText: t("backup.empty") }
    );
  } catch (error) {
    renderError(table, error);
  }
}

/** 备份下载：优先走 bridge.download（官方通道），不可用时退回内联 base64。 */
async function downloadBackup() {
  const button = $("bk-export");
  // 备份包固定包含配置 + 数据库快照 + 全部数据（不再让用户挑类型：
  // 少一份勾选就少一种「以为备份全了」的错误）
  const params = { notes: $("bk-notes").value.trim() };
  button.disabled = true;
  toast(t("backup.downloading"), "info");
  try {
    const bridge = await ensureBridge();
    let filename = "super_astrbot_backup.zip";
    if (typeof bridge.download === "function") {
      const result = await bridge.download("backup/export", params);
      filename = (result && result.filename) || filename;
    } else {
      const payload = await apiGet("backup/export", params);
      filename = (payload && payload.filename) || filename;
      if (payload && payload.content) {
        const bytes = Uint8Array.from(atob(payload.content), (char) => char.charCodeAt(0));
        const blob = new Blob([bytes], { type: "application/zip" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        link.click();
        URL.revokeObjectURL(url);
      }
    }
    toast(tpl(t("backup.done"), { 1: filename }), "ok");
    await loadBackups();
  } catch (error) {
    toast(error.message || String(error), "err");
  } finally {
    button.disabled = false;
  }
}

/** 文件 → base64（分块拼接，避免大文件触发参数长度上限）。 */
async function fileToBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunk = 0x8000;
  for (let index = 0; index < bytes.length; index += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(index, index + chunk));
  }
  return btoa(binary);
}

/** 备份包导入：merge（默认）或 replace（完全覆盖，需二次确认并展示删除估算）。 */
async function importBackup(file) {
  const button = $("bk-import-label");
  const mode = $("bk-mode").value === "replace" ? "replace" : "merge";
  button.disabled = true;
  try {
    const content = await fileToBase64(file);
    if (mode === "replace") {
      // 覆盖会删数据：先取一次预览（不写库）把「将写入 / 预计删除」摆给用户看
      const preview = await apiPost("backup/import", {
        mode,
        dry_run: true,
        content_b64: content,
      });
      const ok = await confirmRequest(
        t("backup.replaceConfirmTitle"),
        `${t("backup.replaceConfirmBody")}<br />${tpl(t("backup.replaceConfirmPlan"), {
          1: preview.rows_written ?? 0,
          2: preview.estimated_deleted_total ?? 0,
        })}<br />${t("backup.replaceConfirmTail")}`,
        async () => {},
        { html: true }
      );
      if (!ok) {
        button.disabled = false;
        return;
      }
    }
    const result = await apiPost("backup/import", { mode, dry_run: false, content_b64: content });
    toast(result.message || t("backup.imported"), "ok");
    // 明细写在卡片里（toast 只放摘要，避免一长串挤在一起看不清）
    const lines = [];
    lines.push(
      `${t("backup.resultMode")}：${
        result.mode === "replace" ? t("backup.modeReplaceShort") : t("backup.modeMergeShort")
      }`
    );
    if (result.config && result.config.message) lines.push(`配置：${result.config.message}`);
    const tables = Object.entries(result.tables || {}).filter(([, rows]) => rows > 0);
    if (tables.length) {
      lines.push(`逐表恢复：${tables.map(([name, rows]) => `${name} ${rows} 行`).join("、")}`);
    }
    if (result.reindex) lines.push(result.reindex);
    if (result.backup) lines.push(`恢复前快照：${result.backup}`);
    if (result.database && result.database.message) lines.push(result.database.message);
    if (Array.isArray(result.warnings) && result.warnings.length) {
      lines.push(`提示：${result.warnings.join("；")}`);
    }
    $("bk-result").textContent = lines.join("　·　");
    renderDatabaseRestoreEntry(result);
    await loadBackups();
  } catch (error) {
    $("bk-result").textContent = error.message || String(error);
    toast(error.message || String(error), "err");
  } finally {
    button.disabled = false;
  }
}

/** 包内有数据库快照时给出「整库恢复」入口（未导出的表与 kv_state 只能靠它还原）。 */
function renderDatabaseRestoreEntry(result) {
  const host = $("bk-db-actions");
  if (!host) return;
  const path = result && result.database ? result.database.saved_to : "";
  if (!path || !result.can_replace_database) {
    host.hidden = true;
    host.innerHTML = "";
    return;
  }
  host.hidden = false;
  host.innerHTML =
    `<button class="btn danger" id="bk-db-restore">${esc(t("backup.dbRestore"))}</button>` +
    `<span class="muted">${esc(t("backup.dbRestoreHint"))}</span>`;
  const button = $("bk-db-restore");
  if (!button) return;
  button.addEventListener("click", () =>
    confirmRequest(t("backup.dbRestore"), t("backup.dbRestoreConfirm"), async () => {
      const outcome = await apiPost("backup/replace-database", { path });
      toast(outcome.message || t("backup.dbRestored"), "ok");
      $("bk-result").textContent = `${outcome.message || ""}　·　${
        outcome.backup ? `${t("backup.dbRestoreBackup")}：${outcome.backup}` : ""
      }`;
      await loadBackups();
    })
  );
}

/* ---------------------------------------------------------------------- */
/* 章节：身份诊断与作用域迁移                                              */
/* ---------------------------------------------------------------------- */

const VERDICT_KIND = {
  empty: "off",
  single: "warn",
  unstable_id: "warn",
  stable_id: "on",
};

async function loadIdentity() {
  const summary = $("id-summary");
  const hint = $("id-hint");
  const table = $("id-table");
  try {
    const result = await apiGet("identities");
    const analysis = result.analysis || {};
    summary.innerHTML = [
      kv(t("identity.strategy"), result.strategy || "—"),
      kv(t("identity.scopeType"), result.scope_type || "—"),
      kv(t("identity.umoCount"), String(result.total ?? 0)),
      kv(t("identity.tracking"), result.tracking ? t("identity.on") : t("identity.off")),
      kv(t("identity.verdict"), t(`verdict.${analysis.verdict}`)),
    ].join("");
    hint.textContent = analysis.hint || "";
    renderTable(
      table,
      [
        { title: t("identity.colUmo"), render: (row) => esc(row.umo) },
        { title: t("identity.colPlatform"), render: (row) => esc(row.platform || "—") },
        { title: t("identity.colSenderId"), render: (row) => esc(row.sender_id || "—") },
        { title: t("identity.colSenderName"), render: (row) => esc(row.sender_name || "—") },
        { title: t("identity.colScope"), render: (row) => esc(row.user_key || "—") },
        { title: t("identity.colEvents"), className: "num", render: (row) => esc(row.events) },
        {
          title: t("identity.colLast"),
          className: "num",
          render: (row) => esc(fmtTime(row.last_seen)),
        },
      ],
      result.items || [],
      { emptyText: t("identity.empty") }
    );
    await loadScopes();
  } catch (error) {
    renderError(table, error);
  }
}

async function loadScopes() {
  const table = $("sc-table");
  try {
    const result = await apiGet("scopes");
    renderTable(
      table,
      [
        {
          title: t("filter.kind"),
          render: (row) => `<span class="mono">${esc(row.scope_type)}</span>`,
        },
        { title: t("identity.colScope"), render: (row) => esc(row.scope_id) },
        { title: t("identity.colTotal"), className: "num", render: (row) => esc(row.total) },
        { title: t("identity.colActive"), className: "num", render: (row) => esc(row.active) },
        {
          title: t("identity.colAttributed"),
          className: "num",
          render: (row) => esc(row.attributed),
        },
        {
          title: t("identity.colLast"),
          className: "num",
          render: (row) => esc(fmtTime(row.last_at)),
        },
      ],
      result.scopes || [],
      { emptyText: t("identity.distEmpty") }
    );
  } catch (error) {
    renderError(table, error);
  }
}

async function migrateScope(dryRun) {
  const payload = {
    to: $("sc-to").value,
    from_scope_type: $("sc-from").value,
    dry_run: dryRun,
  };
  const out = $("sc-result");
  try {
    const result = await apiPost("scopes/migrate", payload);
    out.textContent = result.message || "";
    if (result.dry_run) {
      toast(result.message || "", "info");
      return;
    }
    toast(result.message || "", "ok");
    await loadScopes();
  } catch (error) {
    out.textContent = error.message || String(error);
    toast(error.message || String(error), "err");
  }
}

/* ---------------------------------------------------------------------- */
/* 章节：模型                                                              */
/* ---------------------------------------------------------------------- */

function modelStatusText(kind, info) {
  if (kind === "embedding") {
    if (!info.available) return "不可用（检索降级为关键词路）";
    const dim = info.dimension ? `${info.dimension} 维` : "维度未知";
    return `可用 · ${dim}${info.selected ? "" : " · 自动选择"}`;
  }
  if (kind === "rerank") {
    if (!info.enabled) return "未启用";
    if (!info.available) return `模型不可用（回退 ${info.fallback || "none"}）`;
    return `可用 · 权重 ${num(info.weight, 2)} · 候选 ${info.candidates ?? "—"}`;
  }
  return info.model || "—";
}

async function loadModels() {
  const meta = $("md-meta");
  try {
    const result = await apiGet("models");
    const chat = result.chat || {};
    const embedding = result.embedding || {};
    const rerank = result.rerank || {};

    meta.innerHTML = [
      kv("对话模型", `${(chat.providers || []).length} 个可用`),
      kv("嵌入模型", modelStatusText("embedding", embedding)),
      kv("重排序模型", modelStatusText("rerank", rerank)),
      kv(
        "重排序状态",
        rerank.enabled ? rerank.state || "—" : "未启用（检索使用融合排名）"
      ),
    ].join("");

    renderTable(
      $("md-aux"),
      [
        { title: "功能", key: "title" },
        { title: "模型类型", key: "model_type" },
        {
          title: "配置的提供商",
          render: (row) =>
            row.provider_id
              ? esc(row.provider_id)
              : `<span class="muted">会话默认</span>`,
        },
        { title: "配置项", key: "config_key" },
      ],
      chat.auxiliary || [],
      { sortable: true, emptyText: "没有需要单独指定模型的辅助功能。" }
    );

    const rows = [];
    (chat.providers || []).forEach((item) =>
      rows.push({ kind: "对话", id: item.id, model: item.model || "—" })
    );
    (embedding.providers || []).forEach((item) =>
      rows.push({ kind: "嵌入", id: item.id, model: item.model || "—" })
    );
    (rerank.providers || []).forEach((item) =>
      rows.push({ kind: "重排序", id: item.id, model: item.model || "—" })
    );
    renderTable(
      $("md-providers"),
      [
        { title: "类型", key: "kind" },
        { title: "提供商 ID", key: "id" },
        { title: "模型", key: "model" },
      ],
      rows,
      {
        sortable: true,
        emptyText: "未检测到任何模型提供商：请在 AstrBot 中配置对话 / 嵌入 / 重排序提供商。",
      }
    );
  } catch (error) {
    renderError(meta, error);
  }
}

/* ---------------------------------------------------------------------- */
/* 侧滑详情面板（记忆 / 现实桥 / 每周总结共用一只抽屉）                       */
/* ---------------------------------------------------------------------- */

/* 节奏与居中弹窗同源：入场交给 CSS animation，出场先加 .closing 播完再隐藏；
   transitionend 只认面板自身，另有定时器兜底（reduced-motion 下不会触发过渡）。
   抽屉层级低于 modal，因此抽屉里点「编辑」弹出的表单、删除确认框都盖在抽屉之上。 */
let peekCloseTimer = 0;
const peekState = { type: "", item: null, isEditing: false };

function peekPanelFill(titleHtml, parts = {}) {
  $("peek-title").innerHTML = titleHtml;
  $("peek-badges").innerHTML = parts.badges || "";
  $("peek-actions").innerHTML = parts.actions || "";
  $("peek-body").innerHTML = parts.body || "";
}

function openPeekPanel() {
  // 快速「关闭 → 再打开」时取消未播完的退场回调，别把刚打开的面板藏掉
  window.clearTimeout(peekCloseTimer);
  const panel = $("peek-panel");
  const overlay = $("peek-overlay");
  panel.classList.remove("closing");
  overlay.classList.remove("closing");
  panel.hidden = false;
  overlay.hidden = false;
  panel.setAttribute("aria-hidden", "false");
  panel.removeAttribute("inert");
}

function closePeekPanel() {
  peekState.type = "";
  peekState.item = null;
  peekState.isEditing = false;
  const panel = $("peek-panel");
  const overlay = $("peek-overlay");
  if (panel.hidden) return;
  panel.classList.add("closing");
  overlay.classList.add("closing");
  const done = () => {
    window.clearTimeout(peekCloseTimer);
    panel.classList.remove("closing");
    overlay.classList.remove("closing");
    panel.hidden = true;
    overlay.hidden = true;
    panel.setAttribute("aria-hidden", "true");
    panel.setAttribute("inert", "");
    peekPanelFill("—");
  };
  const onEnd = (event) => {
    if (event.target !== panel) return;
    panel.removeEventListener("transitionend", onEnd);
    done();
  };
  panel.addEventListener("transitionend", onEnd);
  peekCloseTimer = window.setTimeout(done, 400);
}

function peekBadges(items) {
  return items
    .filter(Boolean)
    .map(([cls, text]) => `<span class="pill ${cls}">${esc(text)}</span>`)
    .join("");
}

function peekSection(title, inner) {
  return `<section class="peek-section"><h4 class="peek-section-title">${esc(
    title
  )}</h4>${inner}</section>`;
}

function peekMetaGrid(pairs) {
  const cells = pairs
    .map(
      ([label, value]) =>
        `<div class="peek-meta-item"><span class="peek-meta-label">${esc(
          label
        )}</span><span class="peek-meta-value">${esc(value)}</span></div>`
    )
    .join("");
  return `<div class="peek-meta-grid">${cells}</div>`;
}

function peekActionButtons(editing) {
  if (editing) {
    return (
      `<button class="btn" data-peek-save>${esc(t("peek.save"))}</button>` +
      `<button class="btn ghost" data-peek-cancel>${esc(t("peek.cancel"))}</button>`
    );
  }
  return (
    `<button class="btn" data-peek-edit>${esc(t("peek.edit"))}</button>` +
    `<button class="btn danger" data-peek-delete>${esc(t("peek.delete"))}</button>`
  );
}

function peekEditSection(value) {
  return peekSection(
    t("peek.editContent"),
    `<textarea class="peek-textarea" id="peek-edit-content">${esc(value)}</textarea>`
  );
}

function peekTagsText(tags) {
  return Array.isArray(tags) && tags.length ? tags.join("、") : "—";
}

function memoryStatusPill(status) {
  if (status === "active") return "on";
  if (status === "buffered" || status === "pending") return "warn";
  return "off";
}

function reRenderPeek() {
  const { type, item } = peekState;
  if (!item) return;
  if (type === "memory") renderMemoryPeek(item);
  else if (type === "journal") renderJournalPeek(item);
  else if (type === "weekly") renderWeeklyPeek(item);
}

function renderMemoryPeek(item) {
  const editing = peekState.isEditing;
  const meta = peekMetaGrid([
    [t("peek.field.status"), t(`status.${item.status}`, item.status)],
    [t("peek.field.kind"), item.kind],
    [t("peek.field.source"), item.source],
    [t("peek.field.scope"), item.scope],
    [t("peek.field.importance"), num(item.importance)],
    [t("peek.field.confidence"), num(item.confidence)],
    [t("peek.field.accessCount"), String(item.access_count ?? 0)],
    [t("peek.field.createdAt"), fmtTime(item.created_at)],
    [t("peek.field.updatedAt"), item.updated_at ? fmtTime(item.updated_at) : "—"],
    [t("peek.field.lastAccessAt"), item.last_access_at ? fmtTime(item.last_access_at) : "—"],
    [t("peek.field.tags"), peekTagsText(item.tags)],
    // 身份三列是「跨会话识别用户」的依据，放在详情里便于核对归属
    [t("peek.field.senderId"), item.sender_id || "—"],
    [t("peek.field.senderName"), item.sender_name || "—"],
    [t("peek.field.originUmo"), item.origin_umo || "—"],
  ]);
  peekPanelFill(tpl(t("peek.memoryTitle"), { id: item.id }), {
    badges: peekBadges([
      [memoryStatusPill(item.status), t(`status.${item.status}`, item.status)],
      ["info", item.kind],
      ["off", tpl(t("peek.importance"), { value: num(item.importance) })],
    ]),
    actions: peekActionButtons(editing),
    body: editing
      ? peekEditSection(item.content)
      : peekSection(t("peek.content"), `<pre class="peek-content">${esc(item.content)}</pre>`) +
        peekSection(t("peek.metadata"), meta),
  });
}

function renderJournalPeek(entry) {
  const meta = peekMetaGrid([
    [t("peek.field.title"), entry.title || t("peek.untitled")],
    [t("peek.field.kind"), journalTypeLabel(entry.type)],
    [t("peek.field.emotion"), entry.emotion ? String(entry.emotion) : "—"],
    [t("peek.field.scope"), entry.scope || "—"],
    [t("peek.field.tags"), peekTagsText(entry.tags)],
    [t("peek.field.createdAt"), fmtTime(entry.created_at)],
  ]);
  peekPanelFill(tpl(t("peek.journalTitle"), { id: entry.id }), {
    badges: peekBadges([
      [`jt-${entry.type || "weekly"}`, journalTypeLabel(entry.type)],
      entry.emotion ? ["info", tpl(t("peek.emotion"), { value: entry.emotion })] : null,
    ]),
    // 现实桥的编辑沿用既有表单（modal 在抽屉之上），面板这里只提供入口
    actions: peekActionButtons(false),
    body:
      peekSection(
        t("peek.field.title"),
        `<pre class="peek-content">${esc(entry.title || t("peek.untitled"))}</pre>`
      ) +
      peekSection(
        t("peek.content"),
        `<pre class="peek-content">${esc(entry.content || t("peek.empty"))}</pre>`
      ) +
      peekSection(t("peek.metadata"), meta),
  });
}

function renderWeeklyPeek(item) {
  const editing = peekState.isEditing;
  const meta = peekMetaGrid([
    [t("peek.field.importance"), num(item.importance)],
    [t("peek.field.kind"), item.kind || "insight"],
    [t("peek.field.scope"), item.scope],
    [t("peek.field.createdAt"), fmtTime(item.created_at)],
  ]);
  peekPanelFill(tpl(t("peek.weeklyTitle"), { id: item.id }), {
    badges: peekBadges([
      ["weekly-type", t("nav.weeklies")],
      ["off", tpl(t("peek.importance"), { value: num(item.importance) })],
    ]),
    actions: peekActionButtons(editing),
    body: editing
      ? peekEditSection(item.content)
      : peekSection(t("peek.content"), `<pre class="peek-content">${esc(item.content)}</pre>`) +
        peekSection(t("peek.metadata"), meta),
  });
}

/** 列表刷新后同步抽屉：同一条记录换成新数据，列表里已经没有了就直接关掉。 */
function syncPeekAfterReload(type, items) {
  if (peekState.type !== type || $("peek-panel").hidden || !peekState.item) return;
  const updated = (items || []).find((item) => String(item.id) === String(peekState.item.id));
  if (!updated) {
    closePeekPanel();
    return;
  }
  peekState.item = updated;
  reRenderPeek();
}

async function openMemoryDetail(id) {
  try {
    const item = await apiGet("memory", { id });
    peekState.type = "memory";
    peekState.item = item;
    peekState.isEditing = false;
    renderMemoryPeek(item);
    openPeekPanel();
  } catch (error) {
    toast(error.message || String(error), "err");
  }
}

function openJournalDetail(id) {
  const entry = (state.journalsItems || []).find((item) => String(item.id) === String(id));
  if (!entry) {
    toast(t("peek.gone"), "warn");
    return;
  }
  peekState.type = "journal";
  peekState.item = entry;
  peekState.isEditing = false;
  renderJournalPeek(entry);
  openPeekPanel();
}

function openWeeklyDetail(id) {
  const entry = (state.weekliesItems || []).find((item) => String(item.id) === String(id));
  if (!entry) {
    toast(t("peek.gone"), "warn");
    return;
  }
  peekState.type = "weekly";
  peekState.item = entry;
  peekState.isEditing = false;
  renderWeeklyPeek(entry);
  openPeekPanel();
}

/** 抽屉内的「编辑」：记忆/每周总结就地把正文换成文本框，现实桥交给既有表单。 */
function peekStartEdit() {
  if (peekState.type === "journal") {
    const entry = peekState.item;
    if (entry) openJournalEditor(entry);
    return;
  }
  peekState.isEditing = true;
  reRenderPeek();
  const box = $("peek-edit-content");
  if (box) box.focus();
}

function peekCancelEdit() {
  peekState.isEditing = false;
  reRenderPeek();
}

async function peekSaveContent(button) {
  const { type, item } = peekState;
  const box = $("peek-edit-content");
  if (!item || !box) return;
  const content = box.value.trim();
  if (!content) {
    toast(t("peek.needContent"), "warn");
    box.focus();
    return;
  }
  const endpoint = type === "weekly" ? "weeklies/update" : "memories/update";
  if (button) button.disabled = true;
  try {
    const result = await apiPost(endpoint, { id: Number(item.id), content });
    toast(result.message || t("peek.saved"), "ok");
    if (type === "weekly") {
      item.content = content;
      peekState.isEditing = false;
      renderWeeklyPeek(item);
      loadWeeklies();
    } else {
      await openMemoryDetail(item.id);
      loadMemories();
    }
  } catch (error) {
    toast(error.message || String(error), "err");
  } finally {
    if (button) button.disabled = false;
  }
}

function peekDelete() {
  const { type, item } = peekState;
  if (!item) return;
  const endpoints = { weekly: "weeklies/delete", journal: "journals/delete", memory: "memories/delete" };
  const labelKeys = {
    weekly: "peek.weeklyTitle",
    journal: "peek.journalTitle",
    memory: "peek.memoryTitle",
  };
  const endpoint = endpoints[type] || endpoints.memory;
  const labelKey = labelKeys[type] || labelKeys.memory;
  confirmRequest(t("peek.delete"), tpl(t(labelKey), { id: item.id }), async () => {
    const result = await apiPost(endpoint, { id: Number(item.id) });
    toast(result.message || t("peek.deleted"), "ok");
    closePeekPanel();
    if (type === "weekly") loadWeeklies();
    else if (type === "journal") {
      resetJournalSelection();
      loadJournals();
    } else loadMemories();
  });
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
          sortValue: (row) => graphNodeLabel(row),
          render: (row) =>
            `<span class="clickable" data-node="${esc(row.id)}">${esc(graphNodeLabel(row))}</span>`,
        },
        { title: "类型", key: "entity_type", render: (row) => esc(row.entity_type || "—") },
        { title: "权重", key: "weight", className: "num", render: (row) => esc(num(row.weight)) },
        { title: "证据", key: "evidence", className: "num", render: (row) => esc(row.evidence ?? "—") },
        { title: "度数", key: "degree", className: "num", render: (row) => esc(row.degree ?? 0) },
        { title: "作用域", key: "scope", render: (row) => `<span class="muted">${esc(row.scope || "")}</span>` },
      ],
      nodes,
      { sortable: true, emptyText: t("graph.empty") }
    );

    renderTable(
      edgesEl,
      [
        {
          title: "起点",
          sortValue: (row) => labelOf(row.src_entity_id),
          render: (row) => esc(labelOf(row.src_entity_id)),
        },
        { title: "关系", key: "relation", render: (row) => esc(row.relation || "—") },
        {
          title: "终点",
          sortValue: (row) => labelOf(row.dst_entity_id),
          render: (row) => esc(labelOf(row.dst_entity_id)),
        },
        { title: "权重", key: "weight", className: "num", render: (row) => esc(num(row.weight)) },
        {
          title: "置信度",
          key: "confidence",
          className: "num",
          render: (row) => esc(num(row.confidence)),
        },
      ],
      edges,
      { sortable: true, emptyText: t("graph.empty") }
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
  ["注入回退", "inject.fallbacks"],
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
  weeklies: "nav.weeklies",
  reviews: "nav.reviews",
  persona: "nav.persona",
  identity: "nav.identity",
  graph: "nav.graph",
  monitor: "nav.monitor",
  models: "nav.models",
  prompts: "nav.prompts",
  system: "nav.system",
};

const LOADERS = {
  overview: () => loadOverview(true),
  features: () => loadFeatures(),
  memories: () => loadMemories(),
  journals: () => loadJournals(),
  weeklies: () => loadWeeklies(),
  reviews: () => loadReviews(),
  persona: () => loadPersona(),
  graph: () => loadGraph(),
  monitor: () => loadMonitor(),
  models: () => loadModels(),
  prompts: () => loadPrompts(),
  system: () => loadSystem(true),
  identity: () => loadIdentity(),
};

/** 统一执行分区加载器，并驱动顶部加载条。 */
async function runLoader(page) {
  const loader = LOADERS[page];
  if (!loader) return;
  showLoading(true);
  try {
    await loader();
  } finally {
    showLoading(false);
  }
}

function navigate(page, options = {}) {
  const target = PAGE_TITLES[page] ? page : "overview";
  state.page = target;
  document.querySelectorAll(".nav-item").forEach((node) => {
    node.classList.toggle("active", node.dataset.page === target);
  });
  document.querySelectorAll(".page").forEach((node) => {
    node.classList.toggle("active", node.id === `page-${target}`);
  });
  // 编辑风页题：编号前缀（01/02…）随导航顺序生成
  const order = Object.keys(PAGE_TITLES);
  const index = String(order.indexOf(target) + 1).padStart(2, "0");
  const indexNode = $("page-index");
  if (indexNode) indexNode.textContent = index;
  $("page-title").innerHTML = `<span class="title-index">${index}</span>${esc(t(PAGE_TITLES[target]))}`;
  if (!options.skipLoad && target !== "recall") {
    runLoader(target);
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
  // 属性型文案（读屏与提示气泡）：纯图标按钮的可见文本是符号，兜底文案留在属性里
  document.querySelectorAll("[data-i18n-aria]").forEach((node) => {
    const key = node.getAttribute("data-i18n-aria");
    node.setAttribute("aria-label", t(key, node.getAttribute("aria-label") || ""));
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
      if (state.page === "recall") await runRecall();
      else await runLoader(state.page);
      toast("已刷新", "ok");
    } catch (error) {
      toast(error.message || String(error), "err");
    } finally {
      button.disabled = false;
    }
  });

  $("btn-theme").addEventListener("click", toggleTheme);

  const syncNowButton = $("feat-sync-now");
  if (syncNowButton) {
    syncNowButton.addEventListener("click", () => {
      syncNowButton.disabled = true;
      loadFeatures({ silent: true }).finally(() => {
        syncNowButton.disabled = false;
      });
    });
  }

  // 记忆列表筛选
  $("mem-search").addEventListener("click", () => {
    state.memories.status = $("mem-status").value;
    state.memories.kind = $("mem-kind").value;
    state.memories.keyword = $("mem-keyword").value.trim();
    state.memories.sort = $("mem-sort").value;
    state.memories.offset = 0;
    loadMemories();
  });
  $("mem-sort").addEventListener("change", () => {
    state.memories.sort = $("mem-sort").value;
    state.memories.offset = 0;
    loadMemories();
  });
  $("mem-keyword").addEventListener("keydown", (event) => {
    if (event.key === "Enter") $("mem-search").click();
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

  // 现实桥：类型/关键词筛选、排序与分页（筛选条件变化会清空勾选）
  const journalQuery = () => {
    state.journals.type = $("jr-type").value;
    state.journals.keyword = $("jr-keyword").value.trim();
    state.journals.sort = $("jr-sort").value;
    state.journals.offset = 0;
    resetJournalSelection();
    loadJournals();
  };
  $("jr-search").addEventListener("click", journalQuery);
  $("jr-sort").addEventListener("change", journalQuery);
  $("jr-type").addEventListener("change", journalQuery);
  $("jr-keyword").addEventListener("keydown", (event) => {
    if (event.key === "Enter") journalQuery();
  });
  $("jr-prev").addEventListener("click", () => {
    state.journals.offset = Math.max(0, state.journals.offset - state.journals.limit);
    resetJournalSelection();
    loadJournals();
  });
  $("jr-next").addEventListener("click", () => {
    state.journals.offset += state.journals.limit;
    resetJournalSelection();
    loadJournals();
  });

  // 现实桥：勾选（单选 / 本页全选 / 全选当前筛选 / 清空）
  $("jr-table").addEventListener("change", (event) => {
    const box = event.target.closest("[data-journal-check]");
    if (!box) return;
    const id = Number(box.dataset.journalCheck);
    if (state.journalsSelectAll) {
      // 从「全选当前筛选」降级为显式集合：先把本页其余条目落进集合再摘掉这一条
      state.journalsSelectAll = false;
      journalRowIds().forEach((item) => state.journalsSelected.add(item));
    }
    if (box.checked) state.journalsSelected.add(id);
    else state.journalsSelected.delete(id);
    syncJournalRowSelection();
    syncJournalSelectionUi();
  });
  $("jr-check-all").addEventListener("change", (event) => {
    const ids = journalRowIds();
    if (state.journalsSelectAll) {
      state.journalsSelectAll = false;
      state.journalsSelected = new Set(ids);
    }
    ids.forEach((id) => {
      if (event.target.checked) state.journalsSelected.add(id);
      else state.journalsSelected.delete(id);
    });
    paintJournalsTable();
    syncJournalSelectionUi();
  });
  $("jr-select-match").addEventListener("click", () => {
    state.journalsSelectAll = true;
    state.journalsSelected = new Set();
    paintJournalsTable();
    syncJournalSelectionUi();
    if (Number(state.journals.total || 0) === 0) {
      toast(t("journals.noneSelected"), "warn");
    }
  });
  $("jr-select-none").addEventListener("click", () => {
    resetJournalSelection();
    paintJournalsTable();
    syncJournalSelectionUi();
  });

  // 现实桥管理：新增 / 导出全部 / 导出所选 / 导入 / 行内编辑删除
  $("jr-add").addEventListener("click", () => openJournalEditor(null));
  $("jr-export").addEventListener("click", async () => {
    try {
      const payload = await apiGet("journals/export");
      downloadJsonExport(payload, "super_astrbot_journals.json");
      toast(t("journals.exported"), "ok");
    } catch (error) {
      toast(error.message || String(error), "err");
    }
  });
  $("jr-export-selected").addEventListener("click", async () => {
    const selectAll = state.journalsSelectAll;
    const ids = [...state.journalsSelected];
    if (!selectAll && ids.length === 0) {
      toast(t("journals.noneSelected"), "warn");
      return;
    }
    const button = $("jr-export-selected");
    button.disabled = true;
    try {
      const payload = await apiPost("journals/export-selected", {
        ids,
        all: selectAll,
        entry_type: state.journals.type || "",
        keyword: state.journals.keyword || "",
      });
      downloadJsonExport(payload, "super_astrbot_journals_selected.json");
      toast(
        tpl(t("journals.exportSelectedDone"), { count: Number(payload.count || ids.length) }),
        "ok"
      );
    } catch (error) {
      toast(error.message || String(error), "err");
    } finally {
      button.disabled = false;
      syncJournalSelectionUi();
    }
  });
  $("jr-import-label").addEventListener("click", () => $("jr-import").click());
  $("jr-import").addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const done = await importJsonFile(file, "journals/import", t("journals.imported"));
    if (done) {
      resetJournalSelection();
      loadJournals();
    }
    event.target.value = "";
  });
  $("jr-table").addEventListener("click", (event) => {
    const editNode = event.target.closest("[data-journal-edit]");
    const delNode = event.target.closest("[data-journal-del]");
    if (editNode) {
      const entry = (state.journalsItems || []).find(
        (item) => String(item.id) === String(editNode.dataset.journalEdit)
      );
      if (entry) openJournalEditor(entry);
    } else if (delNode) {
      const id = delNode.dataset.journalDel;
      confirmRequest(t("journals.delete"), `#${id}`, async () => {
        const result = await apiPost("journals/delete", { id: Number(id) });
        toast(result.message || "已删除", "ok");
        resetJournalSelection();
        loadJournals();
      });
    } else {
      return; // 不是行内按钮 → 交给文档级委托（点开侧滑详情）
    }
    // 行内按钮优先于整行点击：别再让文档级委托把面板也打开
    event.stopPropagation();
  });
  $("modal-body").addEventListener("click", (event) => {
    if (event.target.closest("#journal-save")) saveJournal();
    else if (event.target.closest("#journal-cancel")) closeModal();
  });

  // 备份与导出
  $("bk-export").addEventListener("click", downloadBackup);
  $("bk-import-label").addEventListener("click", () => $("bk-import").click());
  $("bk-import").addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    await importBackup(file);
    event.target.value = "";
  });

  // 身份诊断与作用域迁移
  $("id-refresh").addEventListener("click", () => loadIdentity());
  $("id-clear").addEventListener("click", async () => {
    try {
      const result = await apiPost("identities/clear", {});
      toast(tpl(t("identity.cleared"), { 1: result.removed ?? 0 }), "ok");
      await loadIdentity();
    } catch (error) {
      toast(error.message || String(error), "err");
    }
  });
  $("sc-preview").addEventListener("click", () => migrateScope(true));
  $("sc-apply").addEventListener("click", () => {
    const to = $("sc-to").value;
    confirmRequest(
      t("identity.apply"),
      `${t("identity.applyConfirm")}${$("sc-from").value} → ${to}`,
      () => migrateScope(false)
    );
  });

  // 待审：按当前筛选批量批准 / 驳回
  const batchReview = (action) => {
    confirmRequest(
      action === "approve" ? t("reviews.approveAll") : t("reviews.rejectAll"),
      `${t("reviews.batchConfirm")} origin=${$("rv-origin").value || "全部"} umo=${
        $("rv-umo").value.trim() || "全部"
      }`,
      async () => {
        const result = await apiPost("reviews/batch", {
          action,
          origin: $("rv-origin").value,
          umo: $("rv-umo").value.trim(),
        });
        $("rv-batch-result").textContent = result.message || "";
        toast(tpl(t("reviews.batchDone"), { 1: result.handled ?? 0 }), "ok");
        await loadReviews();
      }
    );
  };
  $("rv-approve-all").addEventListener("click", () => batchReview("approve"));
  $("rv-reject-all").addEventListener("click", () => batchReview("reject"));

  // 每周总结：查询 / 翻页 / 导出 / 导入 / 行内删除
  const weekliesQuery = () => {
    state.weeklies.keyword = $("wk-keyword").value.trim();
    state.weeklies.offset = 0;
    loadWeeklies();
  };
  $("wk-search").addEventListener("click", weekliesQuery);
  $("wk-keyword").addEventListener("keydown", (event) => {
    if (event.key === "Enter") weekliesQuery();
  });
  $("wk-prev").addEventListener("click", () => {
    if (state.weeklies.offset > 0) {
      state.weeklies.offset = Math.max(0, state.weeklies.offset - state.weeklies.limit);
      loadWeeklies();
    }
  });
  $("wk-next").addEventListener("click", () => {
    state.weeklies.offset += state.weeklies.limit;
    loadWeeklies();
  });
  $("wk-export").addEventListener("click", async () => {
    try {
      const payload = await apiGet("weeklies/export");
      downloadJsonExport(payload, "super_astrbot_weeklies.json");
      toast(t("journals.exported"), "ok");
    } catch (error) {
      toast(error.message || String(error), "err");
    }
  });
  $("wk-import-label").addEventListener("click", () => $("wk-import").click());
  $("wk-import").addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const done = await importJsonFile(file, "weeklies/import", t("weeklies.imported"));
    if (done) loadWeeklies();
    event.target.value = "";
  });
  $("wk-table").addEventListener("click", (event) => {
    const delNode = event.target.closest("[data-weekly-del]");
    if (!delNode) return; // 不是行内删除 → 交给文档级委托（点开侧滑详情）
    const id = delNode.dataset.weeklyDel;
    confirmRequest(t("journals.delete"), `#${id}`, async () => {
      const result = await apiPost("weeklies/delete", { id: Number(id) });
      toast(result.message || "已删除", "ok");
      loadWeeklies();
    });
    // 行内按钮优先于整行点击
    event.stopPropagation();
  });


  // 待审：来源筛选与分页
  const reviewQuery = () => {
    state.reviews.origin = $("rv-origin").value;
    state.reviews.umo = $("rv-umo").value.trim();
    state.reviews.offset = 0;
    loadReviews();
  };
  $("rv-search").addEventListener("click", reviewQuery);
  $("rv-origin").addEventListener("change", reviewQuery);
  $("rv-umo").addEventListener("keydown", (event) => {
    if (event.key === "Enter") reviewQuery();
  });
  $("rv-prev").addEventListener("click", () => {
    state.reviews.offset = Math.max(0, state.reviews.offset - state.reviews.limit);
    loadReviews();
  });
  $("rv-next").addEventListener("click", () => {
    state.reviews.offset += state.reviews.limit;
    loadReviews();
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
      const note = result.note || stats.note || "";
      // 向量没建起来的原因必须写在界面上：否则「向量 0 条」看起来像坏掉了。
      $("sys-reindex-note").textContent = note;
      toast(
        `索引重建完成：关键词 ${stats.indexed ?? 0} 条，向量 ${stats.vectorized ?? 0} 条`,
        note ? "warn" : "ok"
      );
    } catch (error) {
      toast(error.message || String(error), "err");
    } finally {
      button.disabled = false;
    }
  });

  // 事件委托：跳转、详情、审批、表头排序
  document.addEventListener("click", (event) => {
    const sortHeader = event.target.closest("th[data-sort-col]");
    if (sortHeader) {
      handleSortClick(sortHeader);
      return;
    }
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
    const journalNode = event.target.closest("[data-journal]");
    if (journalNode) {
      openJournalDetail(journalNode.dataset.journal);
      return;
    }
    const weeklyNode = event.target.closest("[data-weekly]");
    if (weeklyNode) {
      openWeeklyDetail(weeklyNode.dataset.weekly);
      return;
    }
    // 侧滑面板头部的「编辑 / 保存 / 取消 / 删除」
    if (event.target.closest("[data-peek-edit]")) {
      peekStartEdit();
      return;
    }
    if (event.target.closest("[data-peek-cancel]")) {
      peekCancelEdit();
      return;
    }
    const peekSave = event.target.closest("[data-peek-save]");
    if (peekSave) {
      peekSaveContent(peekSave);
      return;
    }
    if (event.target.closest("[data-peek-delete]")) {
      peekDelete();
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

  // 配置维护：导出 / 导入（导入后热应用，能力开关与各模块配置即时生效）
  $("cfg-export").addEventListener("click", async () => {
    try {
      const payload = await apiGet("config/export");
      downloadJsonExport(payload, "super_astrbot_config.json");
      toast(t("journals.exported"), "ok");
    } catch (error) {
      toast(error.message || String(error), "err");
    }
  });
  $("cfg-import-label").addEventListener("click", () => $("cfg-import").click());
  $("cfg-import").addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    try {
      const result = await uploadFile("config/import", file);
      toast(result.message || t("system.configImported"), "ok");
      // 配置可能影响能力开关与总览统计：清缓存刷新
      state.overview = null;
      if (state.page === "system") {
        /* 系统页无独立 loader，能力状态在总览/功能页体现 */
      }
    } catch (error) {
      toast(error.message || String(error), "err");
    }
    event.target.value = "";
  });

  // 记忆导入导出
  $("mem-export").addEventListener("click", async () => {
    try {
      const payload = await apiGet("memories/export");
      downloadJsonExport(payload, "super_astrbot_memories.json");
      toast(t("journals.exported"), "ok");
    } catch (error) {
      toast(error.message || String(error), "err");
    }
  });
  $("mem-import-label").addEventListener("click", () => $("mem-import").click());
  $("mem-import").addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    try {
      const result = await uploadFile("memories/import", file);
      toast(
        t("memories.imported")
          .replaceAll("%1", String(result.imported ?? 0))
          .replaceAll("%2", String(result.skipped ?? 0)),
        "ok"
      );
      loadMemories();
    } catch (error) {
      toast(error.message || String(error), "err");
    }
    event.target.value = "";
  });

  // 功能开关（复选框只有 click 无法覆盖键盘操作，用 change 更稳妥）
  document.addEventListener("change", (event) => {
    const input = event.target.closest("[data-feature]");
    if (input) {
      handleFeatureToggle(input.dataset.feature, input.checked, input);
      return;
    }
    const setting = event.target.closest("[data-setting]");
    if (setting) saveFeatureSetting(setting.dataset.setting, setting);
  });

  // 功能页：域折叠 + 单个功能的具体设置折叠
  $("feat-list").addEventListener("click", (event) => {
    const groupButton = event.target.closest("[data-group-toggle]");
    if (groupButton) {
      const domain = groupButton.dataset.groupToggle;
      const group = groupButton.closest(".fgroup");
      const willOpen = !group.classList.contains("open");
      group.classList.toggle("open", willOpen);
      if (willOpen) state.featuresClosedGroups.delete(domain);
      else state.featuresClosedGroups.add(domain);
      return;
    }
    const settingsButton = event.target.closest("[data-settings-toggle]");
    if (settingsButton) {
      const key = settingsButton.dataset.settingsToggle;
      const panel = document.querySelector(
        `[data-settings-panel="${CSS.escape(key)}"]`
      );
      if (!panel) return;
      const willOpen = !panel.classList.contains("open");
      panel.classList.toggle("open", willOpen);
      if (willOpen) state.featuresOpenSettings.add(key);
      else state.featuresOpenSettings.delete(key);
      settingsButton.classList.toggle("active", willOpen);
      settingsButton.textContent = willOpen
        ? t("features.settingsOpen")
        : t("features.settings");
    }
  });

  $("modal-close").addEventListener("click", closeModal);
  $("modal").addEventListener("click", (event) => {
    if (event.target === $("modal")) closeModal();
  });

  // 侧滑详情面板：关闭按钮、遮罩点击、ESC
  $("peek-close").addEventListener("click", closePeekPanel);
  $("peek-overlay").addEventListener("click", closePeekPanel);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    // 面板里点「编辑」/「删除」会弹出居中弹窗：ESC 先关弹窗，再关抽屉
    const modal = $("modal");
    if (modal && !modal.hidden) {
      closeModal();
      return;
    }
    closePeekPanel();
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
      await ensureBridge();
      // ensureBridge 返回的是 bridge 本身；ready() 的结果存在 state.context
      const context = state.context;
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
            const idxNode = $("page-index");
            const idxText = idxNode ? idxNode.textContent : "";
            $("page-title").innerHTML = `<span class="title-index">${idxText}</span>${esc(t(PAGE_TITLES[state.page]))}`;
            runLoader(state.page);
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
    await runLoader(state.page);
  }
  // 功能页与插件配置页的双向同步：轮询拉取外部改动（写方向即时落盘）
  startFeatureSyncPolling();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
