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
    "shell.subtitle": "单人格 · 群聊拟人化",
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
    "nav.groupPersona": "人格",
    "nav.groupMemory": "记忆",
    "nav.groupEmpathy": "共情与主动",
    "nav.groupRecord": "记录",
    "nav.groupSystem": "系统",
    "nav.personaForge": "人格内核",
    "nav.personaLegacy": "表达·黑话·好感度",
    "nav.personaEvolution": "演化轨迹",
    "nav.styleSamples": "风格样本",
    "nav.memoryBackend": "记忆后端",
    "nav.worldbook": "世界书",
    "nav.empathy": "共情管线",
    "nav.proactive": "主动关怀",
    "domain.members": "群友识别",
    "domain.forge": "三层人格",
    "domain.evolution": "人格演化",
    "domain.empathy": "共情管线",
    "domain.latrace": "时序图谱",
    "domain.tiers": "三级记忆",
    "domain.worldbook": "世界书",
    "domain.fusion": "融合与衰减",
    "action.delete": "删除",
    "action.edit": "编辑",
    "action.cancel": "取消",
    "action.confirm": "确认",
    "overview.manageFeatures": "管理功能",
    "overview.pipeline": "群聊消息编排流水线",
    "overview.pipelineHint": "拟人化成败落在①身份识别 → ②作用域归属 → ⑤回复生成：识别不清=认错人，作用域错=记忆串台，无策略=对谁都一个调。",
    "overview.fusion": "融合模块健康",
    "features.groupContext": "群上下文与接管原则",
    "forge.title": "三层人格（PersonaForge · 单人格）",
    "forge.hint": "bot 只有一个稳定人格：注入与演化都作用在这一份画像上；群友差异由「群友策略」在表层微调。",
    "forge.reset": "重置为出厂",
    "forge.previewNow": "生成预览",
    "forge.layerCore": "① 核心特质（认知与特质）",
    "forge.layerStyle": "② 表层风格（语言与行为模式）",
    "forge.layerState": "③ 动态状态（心情 · 能量 · 关系）",
    "forge.mbti": "MBTI",
    "forge.defense": "防御机制",
    "forge.values": "价值观（逗号分隔）",
    "forge.interests": "兴趣标签（逗号分隔）",
    "forge.sentence": "句长偏好",
    "forge.vocabulary": "词汇等级",
    "forge.punctuation": "标点习惯",
    "forge.emoji": "表情使用",
    "forge.catchphrases": "口头禅（逗号分隔）",
    "forge.toneMarkers": "语气词（逗号分隔）",
    "forge.mood": "当前心情",
    "forge.energy": "能量值（0-100）",
    "forge.text": "注入预览（人格摘要）",
    "forge.saved": "三层人格已保存",
    "forge.resetDone": "已重置为出厂人格",
    "evolution.title": "人格演化轨迹（character-sim）",
    "evolution.hint": "每轮互动按经验类型施加微量特质漂移（单轮上限 0.03），长期累积成人物弧光。",
    "evolution.resetEvents": "清空轨迹",
    "evolution.radar": "9 轴人格雷达",
    "evolution.radarHint": "5 个大五轴 + 3 个派生轴 + 兴趣广度；派生轴为线性折算，不额外引入自由度。",
    "evolution.drift": "漂移曲线（按日）",
    "evolution.timeline": "里程碑与事件时间线",
    "styleSamples.tabSamples": "风格样本",
    "styleSamples.tabReview": "待审",
    "styleSamples.members": "群友风格档案（distilly 离线蒸馏 + 在线自写）",
    "styleSamples.membersHint": "有离线蒸馏产物时优先接入，否则用该群友的高权重记忆在线自写；档案只存表层策略。",
    "styleSamples.distill": "刷新档案",
    "styleSamples.strategy": "差异化对话策略",
    "styleSamples.member": "群友",
    "styleSamples.loadStrategy": "载入策略",
    "styleSamples.relation": "关系类型",
    "styleSamples.tone": "语气",
    "styleSamples.address": "称呼",
    "styleSamples.topics": "话题（逗号分隔）",
    "styleSamples.taboo": "禁忌（逗号分隔）",
    "styleSamples.patterns": "表达样本（persona · StyleService）",
    "styleSamples.strategySaved": "群友策略已保存",
    "styleSamples.strategyDeleted": "群友策略已删除",
    "styleSamples.noMember": "先在下方群友档案里选一位群友",
    "styleSamples.senderIdHint": "发送者标识：",
    "filter.tier": "层级（letta）",
    "filter.sender": "发送者",
    "filter.allSenders": "全部群友",
    "filter.pageOnlyHint": "发送者 / 层级过滤作用于当前拉取的整批结果（最多 100 条）。",
    "tier.core": "核心",
    "tier.recall": "召回",
    "tier.archive": "归档",
    "recall.sender": "发送者过滤",
    "recall.provenance": "证据链",
    "recall.expanded": "图扩展召回",
    "backend.title": "记忆后端状态（进程内）",
    "backend.hint": "全部后端都在插件进程内运行：SQLite + LATRACE 时序图谱 + letta 三级分级；不启动任何外部服务。",
    "backend.rebuild": "重建时序图谱",
    "backend.tiers": "三级记忆占比（核心 / 召回 / 归档）",
    "backend.decay": "艾宾浩斯衰减曲线（AMBRACE）",
    "backend.curveNatural": "自然遗忘",
    "backend.curveReviewed": "主动复习后",
    "backend.curveThreshold": "接近遗忘阈值",
    "worldbook.title": "世界书 / Lorebook（AMBRACE）",
    "worldbook.hint": "触发词在当前消息里出现时注入；触发词留空表示手动条目（只存档不注入）。",
    "worldbook.newEntry": "新条目",
    "worldbook.triggers": "触发词（逗号分隔）",
    "worldbook.priority": "优先级",
    "worldbook.scope": "作用域",
    "worldbook.scopeGlobal": "全局",
    "worldbook.scopeUser": "指定群友",
    "worldbook.scopeSession": "指定会话",
    "worldbook.scopeId": "作用域标识（群友 ID / UMO）",
    "worldbook.enabled": "启用",
    "worldbook.on": "启用",
    "worldbook.off": "停用",
    "worldbook.content": "注入内容",
    "worldbook.saved": "世界书条目已保存",
    "worldbook.deleted": "世界书条目已删除",
    "empathy.title": "三阶段共情（CogEmp）",
    "empathy.hint": "识别 → 理解 → 共情：只在识别到情绪信号时注入「怎么回」的语气指引，不代写回复，也不改变人格。",
    "empathy.stageIdentify": "① 情绪识别",
    "empathy.stageUnderstand": "② 原因理解",
    "empathy.stageEmpathize": "③ 认知共情润色",
    "empathy.temperature": "共情温度",
    "empathy.log": "最近共情事件",
    "empathy.saved": "共情设置已保存",
    "proactive.title": "主动关怀（约定回访 + 前瞻）",
    "proactive.hint": "队列项到期后由调度器投递；发送失败自动重试，连续 3 次失败转为「已跳过」。",
    "proactive.newItem": "+ 登记回访",
    "proactive.pending": "待发",
    "proactive.sent": "已发送",
    "proactive.skipped": "已跳过",
    "proactive.canceled": "已取消",
    "proactive.kind": "类型",
    "proactive.kindCallback": "约定回访",
    "proactive.kindForecast": "前瞻关怀",
    "proactive.umo": "目标会话（UMO）",
    "proactive.dueIn": "延迟（小时）",
    "proactive.content": "内容",
    "proactive.schedule": "计划轨 / 空闲轨（原主动消息）",
    "proactive.saved": "回访已登记",
    "proactive.deleted": "队列项已删除",
    "proactive.needUmo": "请填写目标会话 UMO",
    "graph.layer": "图层",
    "graph.layerTkg": "时序图谱（TKG）",
    "graph.layerCooccur": "共现图谱",
    "graph.rebuild": "重建时序图谱",
    "graph.timeline": "关系时间线（证据链）",
    "graph.edgeRecent": "近 7 天",
    "graph.edgeNormal": "较早",
    "graph.edgeStale": "超过 30 天",
    "monitor.fusion": "融合服务健康",
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
    "domain.consolidation": "记忆整合",
    "domain.journal": "现实桥",
    "domain.agent": "Agent 工具",
    "domain.context": "上下文治理",
    "domain.group": "群聊语义",
    "domain.proactive": "主动交互",
    "domain.persona": "拟人化学习",
    "domain.graph": "记忆图谱",
    "domain.review": "自动审核",
    "domain.maibot": "MaiBot 增强",
    "action.refresh": "刷新",
    "action.theme": "主题",
    "action.nav": "导航",
    "badge.pending": "待审",
    "desc.overview": "一屏看清运行状态、能力开关与最近写入",
    "desc.features": "逐项开启 / 关闭能力，即改即生效",
    "desc.personaForge": "单人格的三层内核与双过程内省",
    "desc.personaLegacy": "表达样本、群内用语与好感度明细",
    "desc.personaEvolution": "人格漂移曲线、里程碑与失谐点",
    "desc.styleSamples": "群友风格档案、差异化策略与待审队列",
    "desc.memories": "浏览、筛选与检索长期记忆",
    "desc.recall": "输入一句话，看召回结果与打分明细",
    "desc.graph": "记忆知识图谱的实体与关系",
    "desc.memoryBackend": "三级记忆分层、时序图谱与艾宾浩斯衰减",
    "desc.worldbook": "触发词命中即注入的事实卡",
    "desc.empathy": "情绪识别 → 原因理解 → 语气指引",
    "desc.proactive": "约定回访与前瞻关怀的到期队列",
    "desc.journals": "周记 / 日记 / 随笔等现实记录",
    "desc.weeklies": "每周总结的浏览与导入导出",
    "desc.monitor": "运行指标、趋势图与自动审核状态",
    "desc.models": "三类模型的分工与当前提供商",
    "desc.prompts": "逐项覆盖各功能的系统提示词与模板",
    "desc.system": "框架诊断、后台任务与维护操作",
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
    "shell.subtitle": "Single persona · Group chat anthropomorphic",
    "nav.overview": "Overview",
    "nav.features": "Features",
    "nav.memories": "Memories",
    "nav.recall": "Recall",
    "nav.journals": "Reality Bridge",
    "nav.weeklies": "Weekly digest",
    "nav.reviews": "Reviews",
    "nav.persona": "Learning",
    "nav.identity": "Identity",
    "nav.groupPersona": "Persona",
    "nav.groupMemory": "Memory",
    "nav.groupEmpathy": "Empathy & proactive",
    "nav.groupRecord": "Records",
    "nav.groupSystem": "System",
    "nav.personaForge": "Persona core",
    "nav.personaLegacy": "Style · jargon · affinity",
    "nav.personaEvolution": "Evolution",
    "nav.styleSamples": "Style samples",
    "nav.memoryBackend": "Memory backends",
    "nav.worldbook": "Worldbook",
    "nav.empathy": "Empathy pipeline",
    "nav.proactive": "Proactive care",
    "domain.members": "Member recognition",
    "domain.forge": "PersonaForge",
    "domain.evolution": "Persona evolution",
    "domain.empathy": "Empathy pipeline",
    "domain.latrace": "Temporal graph",
    "domain.tiers": "Three-tier memory",
    "domain.worldbook": "Worldbook",
    "domain.fusion": "Fusion & decay",
    "action.delete": "Delete",
    "action.edit": "Edit",
    "action.cancel": "Cancel",
    "action.confirm": "Confirm",
    "overview.manageFeatures": "Manage features",
    "overview.pipeline": "Group chat orchestration pipeline",
    "overview.pipelineHint": "Anthropomorphic quality hinges on ① identity → ② scope → ⑤ reply: mis-identification, cross-talk and one-size-fits-all tone are the failure modes.",
    "overview.fusion": "Fusion module health",
    "features.groupContext": "Group context & takeover rules",
    "forge.title": "Three-layer persona (PersonaForge)",
    "forge.hint": "One stable persona: injection and evolution both target this single profile; member-level differences are surface tweaks.",
    "forge.reset": "Reset to default",
    "forge.previewNow": "Preview",
    "forge.layerCore": "① Core traits",
    "forge.layerStyle": "② Speaking style",
    "forge.layerState": "③ Dynamic state",
    "forge.mbti": "MBTI",
    "forge.defense": "Defense mechanism",
    "forge.values": "Values (comma separated)",
    "forge.interests": "Interests (comma separated)",
    "forge.sentence": "Sentence length",
    "forge.vocabulary": "Vocabulary level",
    "forge.punctuation": "Punctuation habit",
    "forge.emoji": "Emoji usage",
    "forge.catchphrases": "Catchphrases (comma separated)",
    "forge.toneMarkers": "Tone markers (comma separated)",
    "forge.mood": "Current mood",
    "forge.energy": "Energy (0-100)",
    "forge.text": "Injection preview",
    "forge.saved": "Persona saved",
    "forge.resetDone": "Reset to default persona",
    "evolution.title": "Persona evolution (character-sim)",
    "evolution.hint": "Each exchange applies a micro trait shift (capped at 0.03 per turn), accumulating into a character arc.",
    "evolution.resetEvents": "Clear events",
    "evolution.radar": "9-axis persona radar",
    "evolution.radarHint": "Five Big Five axes plus three derived axes and interest breadth; derived axes are linear projections.",
    "evolution.drift": "Daily drift",
    "evolution.timeline": "Milestones & events",
    "styleSamples.tabSamples": "Style samples",
    "styleSamples.tabReview": "Pending review",
    "styleSamples.members": "Member style profiles",
    "styleSamples.membersHint": "Prefers offline distilly artifacts, otherwise builds from top-weight memories; profiles store surface strategy only.",
    "styleSamples.distill": "Refresh profiles",
    "styleSamples.strategy": "Differentiated dialogue strategy",
    "styleSamples.member": "Member",
    "styleSamples.loadStrategy": "Load strategy",
    "styleSamples.relation": "Relationship",
    "styleSamples.tone": "Tone",
    "styleSamples.address": "Address as",
    "styleSamples.topics": "Topics (comma separated)",
    "styleSamples.taboo": "Taboos (comma separated)",
    "styleSamples.patterns": "Style samples",
    "styleSamples.strategySaved": "Strategy saved",
    "styleSamples.strategyDeleted": "Strategy deleted",
    "styleSamples.noMember": "Pick a member from the roster first",
    "styleSamples.senderIdHint": "Sender ID: ",
    "filter.tier": "Tier (letta)",
    "filter.sender": "Sender",
    "filter.allSenders": "All members",
    "filter.pageOnlyHint": "Sender / tier filters apply to the fetched batch (up to 100 rows).",
    "tier.core": "Core",
    "tier.recall": "Recall",
    "tier.archive": "Archive",
    "recall.sender": "Sender filter",
    "recall.provenance": "Evidence chain",
    "recall.expanded": "Graph-expanded",
    "backend.title": "Memory backend status (in-process)",
    "backend.hint": "All backends run inside the plugin process: SQLite, LATRACE temporal graph, letta tiering. No external services.",
    "backend.rebuild": "Rebuild temporal graph",
    "backend.tiers": "Three-tier distribution",
    "backend.decay": "Ebbinghaus forgetting curve",
    "backend.curveNatural": "Natural forgetting",
    "backend.curveReviewed": "After active review",
    "backend.curveThreshold": "Recall threshold",
    "worldbook.title": "Worldbook / Lorebook",
    "worldbook.hint": "Entries inject when a trigger appears in the current message; empty triggers mean manual-only.",
    "worldbook.newEntry": "New entry",
    "worldbook.triggers": "Triggers (comma separated)",
    "worldbook.priority": "Priority",
    "worldbook.scope": "Scope",
    "worldbook.scopeGlobal": "Global",
    "worldbook.scopeUser": "Specific member",
    "worldbook.scopeSession": "Specific session",
    "worldbook.scopeId": "Scope id (member id / UMO)",
    "worldbook.enabled": "Enabled",
    "worldbook.on": "On",
    "worldbook.off": "Off",
    "worldbook.content": "Injected content",
    "worldbook.saved": "Entry saved",
    "worldbook.deleted": "Entry deleted",
    "empathy.title": "Three-stage empathy (CogEmp)",
    "empathy.hint": "Identify → understand → empathize: injects tone guidance only when emotion is detected; never rewrites the reply or the persona.",
    "empathy.stageIdentify": "① Emotion identification",
    "empathy.stageUnderstand": "② Cause understanding",
    "empathy.stageEmpathize": "③ Cognitive empathy",
    "empathy.temperature": "Empathy temperature",
    "empathy.log": "Recent empathy events",
    "empathy.saved": "Empathy settings saved",
    "proactive.title": "Proactive care (callbacks & forecast)",
    "proactive.hint": "Due items are delivered by the scheduler; failures retry and then fall back to skipped.",
    "proactive.newItem": "+ New callback",
    "proactive.pending": "Pending",
    "proactive.sent": "Sent",
    "proactive.skipped": "Skipped",
    "proactive.canceled": "Canceled",
    "proactive.kind": "Kind",
    "proactive.kindCallback": "Callback",
    "proactive.kindForecast": "Forecast",
    "proactive.umo": "Target session (UMO)",
    "proactive.dueIn": "Delay (hours)",
    "proactive.content": "Content",
    "proactive.schedule": "Scheduled / idle tracks",
    "proactive.saved": "Callback queued",
    "proactive.deleted": "Queue item deleted",
    "proactive.needUmo": "Please fill in the target session UMO",
    "graph.layer": "Layer",
    "graph.layerTkg": "Temporal graph (TKG)",
    "graph.layerCooccur": "Co-occurrence graph",
    "graph.rebuild": "Rebuild temporal graph",
    "graph.timeline": "Relation timeline (evidence)",
    "graph.edgeRecent": "Last 7 days",
    "graph.edgeNormal": "Older",
    "graph.edgeStale": "Older than 30 days",
    "monitor.fusion": "Fusion service health",
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
    "domain.consolidation": "Consolidation",
    "domain.journal": "Reality Bridge",
    "domain.agent": "Agent tools",
    "domain.context": "Context control",
    "domain.group": "Group semantics",
    "domain.proactive": "Proactive chat",
    "domain.persona": "Persona learning",
    "domain.graph": "Knowledge graph",
    "domain.review": "Auto review",
    "domain.maibot": "MaiBot boost",
    "action.refresh": "Refresh",
    "action.theme": "Theme",
    "action.nav": "Navigation",
    "badge.pending": "Pending",
    "desc.overview": "Runtime status, capability toggles and recent writes at a glance",
    "desc.features": "Toggle capabilities item by item; applies immediately",
    "desc.personaForge": "The single persona's three-layer core and dual-process introspection",
    "desc.personaLegacy": "Style samples, group jargon and affinity details",
    "desc.personaEvolution": "Persona drift curve, milestones and dissonance points",
    "desc.styleSamples": "Member style profiles, per-member strategy and the review queue",
    "desc.memories": "Browse, filter and search long-term memories",
    "desc.recall": "Try a query and inspect recall results plus score breakdown",
    "desc.graph": "Entities and relations of the memory knowledge graph",
    "desc.memoryBackend": "Three memory tiers, temporal graph and Ebbinghaus decay",
    "desc.worldbook": "Fact cards injected when a trigger word matches",
    "desc.empathy": "Emotion detection -> cause understanding -> tone guidance",
    "desc.proactive": "Due queue of scheduled callbacks and forecasts",
    "desc.journals": "Journal / diary / notes stored as real-life records",
    "desc.weeklies": "Browse and import / export weekly summaries",
    "desc.monitor": "Runtime metrics, trend chart and auto-review status",
    "desc.models": "The three model roles and the active providers",
    "desc.prompts": "Override each feature's system prompts and templates",
    "desc.system": "Framework diagnostics, scheduled jobs and maintenance",
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
    status: "all",
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
      // 让表头继承列类名（如 num），使数值列表头与单元格右对齐
      const classes = [clickable ? "sortable" : "", column.className || ""].filter(Boolean).join(" ");
      const cls = classes ? ` class="${classes}"` : "";
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

    // 顶栏徽标：待审条数（点击直达待审队列所在页）
    try {
      const pending = await apiGet("reviews", { limit: 1 });
      const badge = $("tb-pending");
      if (badge) {
        const count = Number(pending.total || 0);
        badge.hidden = count <= 0;
        badge.textContent = `${t("badge.pending")} ${count}`;
        badge.title = `${t("badge.pending")} ${count}`;
      }
    } catch (error) {
      /* 徽标失败不影响总览 */
    }

    // 能力开关（点击任意一个跳到「功能」页）
    const caps = data.capabilities || {};
    $("ov-caps").innerHTML =
      Object.entries(caps)
        .map(
          ([key, value]) =>
            `<span class="pill ${value ? "on" : "off"} clickable" data-goto="features" title="${esc(key)}">${esc(
              capabilityLabel(key)
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

    // 顶栏状态线：就绪 / 降级一目了然；存在降级或未就绪时转红（危急值观感）
    const line = $("app-status-line");
    if (line) {
      const critical = !data.ready || notes.length > 0;
      line.classList.toggle("critical", critical);
      line.textContent = critical
        ? `${data.ready ? "运行中" : "未就绪"} · 降级 ${notes.length} 项`
        : "运行中 · 无降级";
    }
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

  // 融合域：编排流水线（①→⑧）与融合模块健康；两块各自失败不影响总览其余部分
  await loadPipeline();
  await loadFusionHealth("ov-fusion");
}

/* ---------------------------------------------------------------------- */
/* 章节：功能开关                                                          */
/* ---------------------------------------------------------------------- */

const DOMAIN_TITLES = {
  basic: "domain.basic",
  memory: "domain.memory",
  reflection: "domain.reflection",
  consolidation: "domain.consolidation",
  journal: "domain.journal",
  agent: "domain.agent",
  context: "domain.context",
  group: "domain.group",
  proactive: "domain.proactive",
  persona: "domain.persona",
  graph: "domain.graph",
  review: "domain.review",
  maibot: "domain.maibot",
  // 融合域（群聊拟人化）：与 spec/capabilities.py 的 domain 一一对应
  members: "domain.members",
  forge: "domain.forge",
  evolution: "domain.evolution",
  empathy: "domain.empathy",
  latrace: "domain.latrace",
  tiers: "domain.tiers",
  worldbook: "domain.worldbook",
  fusion: "domain.fusion",
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
    // 群上下文与接管原则：与开关同页展示，避免「开了群聊语义却不知道阈值」的猜测
    await loadGroupContext();
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

function valClass(n, kind = "score") {
  const v = Number(n);
  if (!Number.isFinite(v)) return "v";
  if (kind === "score") {
    if (v >= 8) return "v v-critical";   // 高重要度 → 危急值观感
    if (v >= 6) return "v v-warn";
    return "v";
  }
  return "v";
}

/** 能力键 → 面板上的短标签：去掉通用后缀，保留域前缀（避免一屏「enabled」）。 */
function capabilityLabel(key) {
  const text = String(key || "");
  return (
    text
      .replace(/\.(enabled|auto|decay|apply)$/, "")
      .replace(/^basic$/, t("domain.basic")) || text
  );
}

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
    {
      title: "状态",
      render: (row) =>
        `<span class="pill ${memoryStatusPill(row.status)}">${esc(
          t(`status.${row.status}`, row.status)
        )}</span>`,
    },
    { title: "发送者", render: (row) => esc(row.sender_name || row.sender_id || "—") },
    { title: "层级", render: (row) => tierPill(tierOf(row)) },
    { title: "来源", key: "source" },
    { title: "作用域", render: (row) => `<span class="muted">${esc(row.scope)}</span>` },
    {
      title: "重要度",
      className: "num",
      render: (row) => `<span class="${valClass(row.importance)}">${esc(num(row.importance))}</span>`,
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

    // 好感度：改用 persona/affinity 面板（按群友 + 人工校准滑块），
    // 只读表由 loadAffinityPanel 内部渲染，避免两处口径漂移。
    await loadAffinityPanel();
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
    const result = await apiGet("identity/observe");
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
    const result = await apiPost("identity/migrate", payload);
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
  person: "#2f9fb0",
  place: "#3aa86b",
  org: "#d99a3c",
  event: "#e05a5a",
  concept: "#8a7fd4",
  thing: "#4fb3c9",
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
    // 时序图谱附加信息：关系时间线（证据链），失败不影响共现图谱渲染
    await loadGraphTimeline();
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
    await loadFusionHealth("mt-fusion");
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

/* 导航顺序 = 页码顺序（导航重构版）：总览 / 功能 / 人格×4 / 记忆×5 / 共情与主动×2 / 记录×2 / 系统×4 */
const PAGE_TITLES = {
  overview: "nav.overview",
  features: "nav.features",
  "persona-forge": "nav.personaForge",
  "persona-legacy": "nav.personaLegacy",
  "persona-evolution": "nav.personaEvolution",
  "style-samples": "nav.styleSamples",
  memories: "nav.memories",
  recall: "nav.recall",
  graph: "nav.graph",
  "memory-backend": "nav.memoryBackend",
  worldbook: "nav.worldbook",
  empathy: "nav.empathy",
  proactive: "nav.proactive",
  journals: "nav.journals",
  weeklies: "nav.weeklies",
  monitor: "nav.monitor",
  models: "nav.models",
  prompts: "nav.prompts",
  system: "nav.system",
};

/** 每个页面的「一句话用途」：显示在顶栏标题下方，降低新用户的摸索成本。 */
const PAGE_DESCS = {
  overview: "desc.overview",
  features: "desc.features",
  "persona-forge": "desc.personaForge",
  "persona-legacy": "desc.personaLegacy",
  "persona-evolution": "desc.personaEvolution",
  "style-samples": "desc.styleSamples",
  memories: "desc.memories",
  recall: "desc.recall",
  graph: "desc.graph",
  "memory-backend": "desc.memoryBackend",
  worldbook: "desc.worldbook",
  empathy: "desc.empathy",
  proactive: "desc.proactive",
  journals: "desc.journals",
  weeklies: "desc.weeklies",
  monitor: "desc.monitor",
  models: "desc.models",
  prompts: "desc.prompts",
  system: "desc.system",
};

const LOADERS = {
  overview: () => loadOverview(true),
  features: () => loadFeatures(),
  "persona-forge": () => loadForge(),
  "persona-legacy": () => loadPersona(),
  "persona-evolution": () => loadEvolution(),
  "style-samples": () => loadStyleSamples(),
  memories: () => loadMemories(),
  recall: () => runRecall(),
  graph: () => loadGraph(),
  "memory-backend": () => loadMemoryBackend(),
  worldbook: () => loadWorldbook(),
  empathy: () => loadEmpathy(),
  proactive: () => loadProactive(),
  journals: () => loadJournals(),
  weeklies: () => loadWeeklies(),
  monitor: () => loadMonitor(),
  models: () => loadModels(),
  prompts: () => loadPrompts(),
  system: () => loadSystem(true),
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
    const active = node.dataset.page === target;
    node.classList.toggle("active", active);
    if (active) node.setAttribute("aria-current", "page");
    else node.removeAttribute("aria-current");
  });
  document.querySelectorAll(".page").forEach((node) => {
    node.classList.toggle("active", node.id === `page-${target}`);
  });
  // 编辑风页题：编号前缀（01/02…）随导航顺序生成
  const order = Object.keys(PAGE_TITLES);
  const index = String(order.indexOf(target) + 1).padStart(2, "0");
  revealNavGroup(target);
  const indexNode = $("page-index");
  if (indexNode) indexNode.textContent = index;
  $("page-title").innerHTML = `<span class="title-index">${index}</span>${esc(t(PAGE_TITLES[target]))}`;
  const descNode = $("page-desc");
  if (descNode) descNode.textContent = t(PAGE_DESCS[target] || "", "");
  setNavOpen(false);
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
    const text = t(key, node.getAttribute("aria-label") || "");
    node.setAttribute("aria-label", text);
    node.setAttribute("title", text);
  });
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme === "light" ? "light" : "dark";
  // 画布颜色取自 CSS 变量，主题切换后必须重绘，否则图谱仍是旧主题色
  if (graphState && graphState.data) {
    try { drawGraph(graphState.data); } catch (error) { /* 画布未挂载时忽略 */ }
  }
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
    // 过滤模式下分页在「本批结果」内进行（见 loadMemoriesFiltered）
    if (memFilterState.active) {
      memFilterState.offset = Math.max(0, memFilterState.offset - memFilterState.limit);
      paintMemoryBatch($("mem-table"));
      return;
    }
    state.memories.offset = Math.max(0, state.memories.offset - state.memories.limit);
    loadMemories();
  });
  $("mem-next").addEventListener("click", () => {
    if (memFilterState.active) {
      memFilterState.offset += memFilterState.limit;
      paintMemoryBatch($("mem-table"));
      return;
    }
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
    setNavOpen(false);
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
  bindFusionControls();
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

  applyTheme(theme || "light");
  applyStaticI18n();
  navigate(currentPageFromHash(), { skipLoad: true, skipHash: true });
  await loadOverview(true);
  if (state.page !== "overview") {
    await runLoader(state.page);
  }
  // 功能页与插件配置页的双向同步：轮询拉取外部改动（写方向即时落盘）
  startFeatureSyncPolling();
  // 融合域首屏：群友清单（记忆页筛选项依赖它）与导航角标
  loadMembers().catch(() => {});
  loadFusionHealth("ov-fusion").catch(() => {});
}

/* ======================================================================
 * 融合域页面逻辑（群聊拟人化 · 与实施总纲第 3 节逐页对应）
 * ----------------------------------------------------------------------
 * 本段追加在既有逻辑之后，只新增函数与两个绑定入口（bindNavGroups /
 * bindFusionControls），不改写既有页面行为；所有请求仍只走 bridge。
 * ====================================================================== */

/* ---------------------------------------------------------------------- */
/* 通用小工具                                                              */
/* ---------------------------------------------------------------------- */

/** 把「逗号分隔文本」拆成数组（中英文逗号与换行都接受）。 */
function splitList(value) {
  return String(value || "")
    .replace(/，/g, ",")
    .replace(/\n/g, ",")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

/** 0~1 数值 → 百分比文本。 */
function asPct(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${Math.round(number * 100)}%`;
}

/** 带符号的微小数值（特质漂移用），保留 3 位小数。 */
function signed(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const text = Math.abs(number) < 0.0005 ? "0" : number.toFixed(3);
  return number > 0 ? `+${text}` : text;
}

/** 层级（letta）判定：与后端 memory/tiers.py 同一套只读规则。 */
function tierOf(row) {
  const status = String(row.status || "active");
  if (status === "archived" || status === "forgotten") return "archive";
  const importance = Number(row.importance || 0);
  const access = Number(row.access_count || 0);
  if (importance >= 0.75 || access >= 3) return "core";
  if (importance < 0.35) return "archive";
  return "recall";
}

const TIER_LABEL = { core: "核心", recall: "召回", archive: "归档" };
const TIER_PILL = { core: "on", recall: "info", archive: "off" };

function tierPill(tier) {
  return `<span class="pill ${TIER_PILL[tier] || "off"}">${esc(TIER_LABEL[tier] || tier)}</span>`;
}

/** 读取主题色（SVG 图形着色用；主题切换后重绘即可换色）。 */
function themeColor(name, fallback) {
  try {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  } catch (error) {
    return fallback;
  }
}

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

/** 空态占位（与既有 renderEmpty 同款观感）。 */
function renderSoftEmpty(target, text) {
  if (!target) return;
  target.innerHTML = `<div class="empty">${esc(text)}</div>`;
}

/* ---------------------------------------------------------------------- */
/* 导航分组折叠 + 标签页                                                   */
/* ---------------------------------------------------------------------- */

const NAV_GROUPS_KEY = "super-astrbot-nav-groups";

function bindNavGroups() {
  let collapsed = [];
  try {
    collapsed = JSON.parse(window.localStorage.getItem(NAV_GROUPS_KEY) || "[]");
  } catch (error) {
    collapsed = [];
  }
  document.querySelectorAll(".nav-group").forEach((group) => {
    const key = group.dataset.group;
    if (!key) return;
    if (collapsed.includes(key)) group.classList.add("collapsed");
    const head = group.querySelector(".nav-group-head");
    if (!head) return;
    head.addEventListener("click", () => {
      const next = !group.classList.contains("collapsed");
      group.classList.toggle("collapsed", next);
      head.setAttribute("aria-expanded", next ? "false" : "true");
      const current = new Set(
        Array.from(document.querySelectorAll(".nav-group.collapsed")).map((node) => node.dataset.group)
      );
      try {
        window.localStorage.setItem(NAV_GROUPS_KEY, JSON.stringify(Array.from(current)));
      } catch (error) {
        /* 隐私模式忽略 */
      }
    });
  });
}

/** 切页时自动展开目标页所在分组：折叠状态不该把用户挡在门外。 */
function revealNavGroup(page) {
  const item = document.querySelector(`.nav-item[data-page="${CSS.escape(page)}"]`);
  if (!item) return;
  const group = item.closest(".nav-group");
  if (!group || !group.classList.contains("collapsed")) return;
  group.classList.remove("collapsed");
  const head = group.querySelector(".nav-group-head");
  if (head) head.setAttribute("aria-expanded", "true");
  const current = new Set(
    Array.from(document.querySelectorAll(".nav-group.collapsed")).map((node) => node.dataset.group)
  );
  try {
    window.localStorage.setItem(NAV_GROUPS_KEY, JSON.stringify(Array.from(current)));
  } catch (error) {
    /* 忽略 */
  }
}

/* ---------------------------------------------------------------------- */
/* 窄屏侧栏抽屉                                                            */
/* ---------------------------------------------------------------------- */

const NAV_MEDIA = "(max-width: 980px)";

/** 打开 / 收起窄屏侧栏抽屉；桌面端调用是安全的空操作。 */
function setNavOpen(open) {
  const app = document.querySelector(".app");
  const toggle = $("nav-toggle");
  const backdrop = $("sidebar-backdrop");
  const shouldOpen = Boolean(open) && window.matchMedia(NAV_MEDIA).matches;
  if (app) app.classList.toggle("nav-open", shouldOpen);
  if (toggle) toggle.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
  if (backdrop) backdrop.hidden = !shouldOpen;
  document.body.classList.toggle("nav-locked", shouldOpen);
}

function bindSidebar() {
  const toggle = $("nav-toggle");
  const backdrop = $("sidebar-backdrop");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const app = document.querySelector(".app");
      setNavOpen(!(app && app.classList.contains("nav-open")));
    });
  }
  if (backdrop) backdrop.addEventListener("click", () => setNavOpen(false));
  // 视口变宽后立即收起，避免遗留抽屉状态
  window.addEventListener("resize", () => {
    if (!window.matchMedia(NAV_MEDIA).matches) setNavOpen(false);
  });
}

function bindTabs() {
  const tabs = document.querySelectorAll(".tabs .tab");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const container = tab.closest(".page") || document;
      const name = tab.dataset.tab;
      container.querySelectorAll(".tabs .tab").forEach((node) => {
        node.classList.toggle("active", node === tab);
      });
      container.querySelectorAll(".tab-pane").forEach((pane) => {
        pane.classList.toggle("active", pane.id === name);
      });
    });
  });
}

/** Notion 招牌「悬停出现拖拽手柄」：给每张卡片注入 ⋮⋮ 手柄。 */
function injectDragHandles() {
  document.querySelectorAll(".content .card").forEach((card) => {
    if (card.querySelector(":scope > .drag-handle")) return;
    const handle = document.createElement("span");
    handle.className = "drag-handle";
    handle.setAttribute("aria-hidden", "true");
    handle.textContent = "⋮⋮";
    card.insertBefore(handle, card.firstChild);
  });
}

/* ---------------------------------------------------------------------- */
/* 总览：编排流水线 + 融合健康                                             */
/* ---------------------------------------------------------------------- */

async function loadPipeline() {
  const flow = $("ov-flow");
  const summary = $("ov-pipeline-summary");
  if (!flow) return;
  try {
    const data = await apiGet("fusion/pipeline");
    const stages = data.stages || [];
    if (!stages.length) {
      renderSoftEmpty(flow, data.degraded || "编排流水线未装配");
      if (summary) summary.textContent = "";
      return;
    }
    flow.innerHTML = stages
      .map((stage) => {
        const detail = stage.detail || {};
        const hint = detail.hint || detail.error || "";
        return `<div class="step ${esc(stage.status || "on")}">
          <div class="n">${esc(stage.index)} ${esc(stage.status === "off" ? "未启用" : stage.status === "degraded" ? "降级" : "在线")}</div>
          <div class="t">${esc(stage.title)}</div>
          <div class="d">${esc(hint)}</div>
          <div class="pg clickable" data-goto="${esc(stage.target)}">前往 →</div>
        </div>`;
      })
      .join("");
    const info = data.summary || {};
    if (summary) {
      summary.textContent = `①→⑧：在线 ${info.on || 0} · 降级 ${info.degraded || 0} · 未启用 ${info.off || 0}`;
    }
  } catch (error) {
    renderError(flow, error);
  }
}

async function loadFusionHealth(targetId) {
  const target = $(targetId);
  if (!target) return;
  try {
    const data = await apiGet("monitor/fusion");
    const items = data.items || [];
    if (!items.length) {
      renderSoftEmpty(target, data.degraded || "融合模块未装配");
      return;
    }
    renderTable(
      target,
      [
        { title: "模块", render: (row) => `<span class="title">${esc(row.title)}</span>` },
        {
          title: "状态",
          render: (row) =>
            `<span class="pill ${row.status === "online" ? "on" : row.status === "degraded" ? "warn" : "off"}">${
              row.status === "online" ? "在线" : row.status === "degraded" ? "降级" : "未启用"
            }</span>`,
        },
        { title: "能力键", render: (row) => `<span class="mono muted">${esc(row.capability)}</span>` },
        { title: "详情", className: "content", render: (row) => esc(row.detail || "—") },
      ],
      items,
      { emptyText: "没有融合模块。" }
    );
    const summary = data.summary || {};
    const nav = $("nav-latrace");
    if (nav) {
      const latrace = items.find((item) => item.key === "latrace");
      nav.textContent = latrace
        ? latrace.status === "online"
          ? "就绪"
          : latrace.status === "degraded"
            ? "待回填"
            : "未启用"
        : "—";
    }
    if (targetId === "mt-fusion" && summary.degraded) {
      /* 监控页把降级数体现在提示里，避免只看表格漏掉 */
      $("mt-meta").textContent = `${$("mt-meta").textContent || ""}`.trim();
    }
  } catch (error) {
    renderError(target, error);
  }
}

/* ---------------------------------------------------------------------- */
/* 人格内核（PersonaForge 三层）                                           */
/* ---------------------------------------------------------------------- */

const forgeState = { snapshot: null };

async function loadForge() {
  try {
    const snapshot = await apiGet("persona/forge");
    forgeState.snapshot = snapshot;
    renderForge(snapshot);
  } catch (error) {
    renderError($("pf-meta"), error);
  }
}

function forgeDial(axis, label, value) {
  const percent = Math.round(Number(value || 0) * 100);
  return `<div class="dial">
    <span class="name">${esc(label)}</span>
    <input type="range" data-forge-axis="${esc(axis)}" min="0" max="100" step="1" value="${percent}" />
    <span class="val mono" data-forge-axis-value="${esc(axis)}">${(percent / 100).toFixed(2)}</span>
  </div>`;
}

function fillSelect(id, options, labels, current) {
  const node = $(id);
  if (!node) return;
  node.innerHTML = options
    .map(
      (option) =>
        `<option value="${esc(option)}"${option === current ? " selected" : ""}>${esc(
          labels[option] || option
        )}</option>`
    )
    .join("");
}

function renderForge(snapshot) {
  const profile = snapshot.profile;
  if (!profile) {
    renderSoftEmpty($("pf-meta"), snapshot.degraded || "三层人格未装配");
    return;
  }
  const meta = snapshot.meta || {};
  const labels = snapshot.labels || {};
  renderStats($("pf-meta"), [
    ["双过程内省", meta.introspection_enabled ? "开启" : "关闭"],
    ["当前心情", profile.dynamic_state.current_mood || "—"],
    ["能量值", `${profile.dynamic_state.energy_level}/100`],
    ["已建关系", `${meta.relationships || 0} 位群友`],
    ["内省次数", meta.introspection_count || 0],
    ["最近保存", meta.updated_at ? fmtTime(meta.updated_at) : "—"],
  ]);

  // ① 核心特质
  const bigFive = profile.core_traits.big_five || {};
  $("pf-mbti").value = profile.core_traits.mbti || "";
  fillSelect("pf-defense", Object.keys(labels.defense || {}), labels.defense || {}, profile.core_traits.defense_mechanism);
  $("pf-core").innerHTML = (snapshot.axes || [])
    .map((axis) => forgeDial(axis, (labels.big_five || {})[axis] || axis, bigFive[axis]))
    .join("");
  $("pf-values").value = (profile.core_traits.values || []).join(", ");
  $("pf-interests").value = (profile.interests || []).join(", ");

  // ② 表层风格
  const style = profile.speaking_style || {};
  fillSelect("pf-sentence", Object.keys(labels.sentence || {}), labels.sentence || {}, style.sentence_length);
  fillSelect("pf-vocabulary", Object.keys(labels.vocabulary || {}), labels.vocabulary || {}, style.vocabulary_level);
  fillSelect("pf-punctuation", Object.keys(labels.punctuation || {}), labels.punctuation || {}, style.punctuation_habit);
  fillSelect("pf-emoji", Object.keys(labels.emoji || {}), labels.emoji || {}, style.emoji_frequency);
  $("pf-catchphrases").value = (style.catchphrases || []).join(", ");
  $("pf-tone-markers").value = (style.tone_markers || []).join(", ");

  // ③ 动态状态
  const state = profile.dynamic_state || {};
  $("pf-mood").value = state.current_mood || "";
  const energy = Number(state.energy_level || 0);
  $("pf-energy").value = String(energy);
  $("pf-energy-value").textContent = String(energy);

  const relations = Object.entries(state.relationship_map || {});
  renderTable(
    $("pf-relations"),
    [
      { title: "对象", render: (row) => `<span class="mono">${esc(row.target)}</span>` },
      { title: "亲密度", className: "num", render: (row) => esc(num(row.intimacy, 1)) },
      { title: "历史摘要", className: "content", render: (row) => esc(row.history_summary || "—") },
    ],
    relations.map(([target, info]) => ({ target, ...info })),
    { emptyText: "还没有互动记录（聊过天之后关系会在这里累积）。" }
  );

  $("pf-monologue-state").textContent = meta.introspection_enabled
    ? `双过程内省已开启${meta.last_introspection_at ? ` · 上次 ${fmtTime(meta.last_introspection_at)}` : ""}`
    : "双过程内省关闭（功能页可开）";
  $("pf-text").textContent = snapshot.profile_text || "—";
}

function forgePatchFromForm() {
  const bigFive = {};
  document.querySelectorAll("[data-forge-axis]").forEach((input) => {
    bigFive[input.dataset.forgeAxis] = Number(input.value) / 100;
  });
  return {
    core_traits: {
      mbti: $("pf-mbti").value.trim(),
      big_five: bigFive,
      values: splitList($("pf-values").value),
      defense_mechanism: $("pf-defense").value,
    },
    speaking_style: {
      sentence_length: $("pf-sentence").value,
      vocabulary_level: $("pf-vocabulary").value,
      punctuation_habit: $("pf-punctuation").value,
      emoji_frequency: $("pf-emoji").value,
      catchphrases: splitList($("pf-catchphrases").value),
      tone_markers: splitList($("pf-tone-markers").value),
    },
    dynamic_state: {
      current_mood: $("pf-mood").value.trim() || "平静",
      energy_level: Number($("pf-energy").value),
    },
    interests: splitList($("pf-interests").value),
  };
}

async function saveForge(button) {
  if (button) button.disabled = true;
  try {
    const result = await apiPost("persona/forge-update", {
      action: "update",
      patch: forgePatchFromForm(),
    });
    toast(result.message || t("forge.saved"), "ok");
    if (result.snapshot) {
      forgeState.snapshot = result.snapshot;
      renderForge(result.snapshot);
    }
  } catch (error) {
    toast(error.message || String(error), "err");
  } finally {
    if (button) button.disabled = false;
  }
}

async function resetForge() {
  confirmRequest(
    t("forge.reset"),
    "会把三层人格恢复为出厂画像（当前数值不再保留），确定继续吗？",
    async () => {
      try {
        const result = await apiPost("persona/forge-update", { action: "reset" });
        toast(result.message || t("forge.resetDone"), "ok");
        if (result.snapshot) renderForge(result.snapshot);
      } catch (error) {
        toast(error.message || String(error), "err");
      }
    }
  );
}

/* ---------------------------------------------------------------------- */
/* 演化轨迹（character-sim）                                               */
/* ---------------------------------------------------------------------- */

const RADAR_AXES = [
  "openness",
  "conscientiousness",
  "extraversion",
  "agreeableness",
  "emotional_stability",
  "warmth",
  "assertiveness",
  "humor_inclination",
  "interest_breadth",
];

const DRIFT_COLORS = ["#2383e2", "#d44c47", "#0f7b6c", "#a67c00", "#8250df", "#337ea9", "#eb5757", "#4dab9a"];

async function loadEvolution() {
  try {
    const data = await apiGet("persona/evolution");
    if (data.degraded) {
      renderSoftEmpty($("pe-meta"), data.degraded);
      return;
    }
    const stats = data.stats || {};
    renderStats($("pe-meta"), [
      ["事件总数", stats.total],
      ["近 7 天", stats.recent],
      ["里程碑", stats.milestones],
      ["主要经验", (stats.by_experience || [])[0] ? `${(stats.by_experience || [])[0].label}×${(stats.by_experience || [])[0].count}` : "—"],
      ["雷达轴数", (data.radar || {}).axes ? (data.radar || {}).axes.length : 0],
      ["统计窗口", `${stats.window_days || 7} 天`],
    ]);
    drawRadar($("pe-radar"), data.radar || {});
    drawDrift($("pe-drift"), data.drift || {}, $("pe-drift-legend"));
    const timeline = data.timeline || [];
    const target = $("pe-timeline");
    if (!timeline.length) {
      renderSoftEmpty(target, "还没有演化事件（聊天里出现情绪 / 冲突 / 高光等信号后会自动记录）。");
      return;
    }
    target.innerHTML = timeline
      .map((item) => {
        const deltas = Object.entries(item.delta_labels || {})
          .map(([axis, value]) => `${esc(axis)} ${esc(signed(value))}`)
          .join("、");
        return `<div class="ev ${esc(item.kind)}">
          <div class="when">${esc(fmtTime(item.created_at))}</div>
          <div class="dot-mark"></div>
          <div class="what">
            <span class="pill ${item.kind === "milestone" ? "warn" : "info"}">${esc(
              item.kind === "milestone" ? "里程碑" : item.label || item.kind
            )}</span>
            ${esc(item.summary || "")}
            ${deltas ? `<div class="muted">特质漂移：${deltas}</div>` : ""}
          </div>
        </div>`;
      })
      .join("");
  } catch (error) {
    renderError($("pe-meta"), error);
  }
}

function drawRadar(svg, radar) {
  if (!svg) return;
  svg.innerHTML = "";
  const values = radar.values || {};
  const labels = radar.labels || {};
  const centerX = 180;
  const centerY = 150;
  const radius = 100;
  const axes = RADAR_AXES.filter((axis) => axis in labels || axis in values);
  if (!axes.length) {
    svg.appendChild(svgEl("text", { x: centerX, y: centerY, class: "axis-label", "text-anchor": "middle" }))
      .textContent = "无数据";
    return;
  }
  const gridColor = themeColor("--chart-grid", "rgba(55,53,47,0.12)");
  const textColor = themeColor("--text-dim", "#787774");
  const accent = themeColor("--accent", "#2383e2");

  // 4 圈网格 + 轴线
  [0.25, 0.5, 0.75, 1].forEach((ratio) => {
    const points = axes
      .map((axis, index) => {
        const angle = (Math.PI * 2 * index) / axes.length - Math.PI / 2;
        const r = radius * ratio;
        return `${centerX + r * Math.cos(angle)},${centerY + r * Math.sin(angle)}`;
      })
      .join(" ");
    svg.appendChild(svgEl("polygon", { points, fill: "none", stroke: gridColor, "stroke-width": 1 }));
  });
  axes.forEach((axis, index) => {
    const angle = (Math.PI * 2 * index) / axes.length - Math.PI / 2;
    svg.appendChild(
      svgEl("line", {
        x1: centerX,
        y1: centerY,
        x2: centerX + radius * Math.cos(angle),
        y2: centerY + radius * Math.sin(angle),
        stroke: gridColor,
        "stroke-width": 1,
      })
    );
    const label = svgEl("text", {
      x: centerX + (radius + 18) * Math.cos(angle),
      y: centerY + (radius + 18) * Math.sin(angle) + 3,
      class: "axis-label",
      fill: textColor,
      "text-anchor": "middle",
    });
    label.textContent = labels[axis] || axis;
    svg.appendChild(label);
  });

  // 数值多边形
  const points = axes
    .map((axis, index) => {
      const angle = (Math.PI * 2 * index) / axes.length - Math.PI / 2;
      const value = Math.max(0, Math.min(1, Number(values[axis] || 0)));
      const r = radius * value;
      return `${centerX + r * Math.cos(angle)},${centerY + r * Math.sin(angle)}`;
    })
    .join(" ");
  svg.appendChild(
    svgEl("polygon", { points, fill: accent, "fill-opacity": 0.16, stroke: accent, "stroke-width": 2 })
  );
}

function drawDrift(svg, drift, legendEl) {
  if (!svg) return;
  svg.innerHTML = "";
  const days = drift.days || [];
  const axesMap = drift.axes || {};
  const labels = drift.labels || {};
  if (!days.length) {
    const note = svgEl("text", { x: 400, y: 120, class: "axis-label", "text-anchor": "middle" });
    note.textContent = "暂无漂移记录";
    svg.appendChild(note);
    if (legendEl) legendEl.innerHTML = "";
    return;
  }
  const width = 800;
  const height = 240;
  const padLeft = 46;
  const padBottom = 26;
  const padTop = 22;
  // 后端返回的形状是 {days: [...], axes: {day: {axis: delta}}}；
  // 这里按「轴」重组成累计曲线（轴名取各日增量的键并集，缺日按 0 计）。
  const axisNames = [];
  days.forEach((day) => {
    Object.keys(axesMap[day] || {}).forEach((axis) => {
      if (!axisNames.includes(axis)) axisNames.push(axis);
    });
  });
  const series = {};
  axisNames.forEach((axis) => {
    let cumulative = 0;
    series[axis] = days.map((day) => {
      cumulative += Number((axesMap[day] || {})[axis] || 0);
      return cumulative;
    });
  });
  const all = Object.values(series).flat();
  const maxAbs = Math.max(0.02, ...all.map((value) => Math.abs(value)));
  const zeroY = padTop + (height - padTop - padBottom) / 2;
  const scaleY = (value) => zeroY - (value / maxAbs) * ((height - padTop - padBottom) / 2);
  const scaleX = (index) =>
    padLeft + (days.length === 1 ? 0 : (index * (width - padLeft - 20)) / (days.length - 1));

  const gridColor = themeColor("--chart-grid", "rgba(55,53,47,0.12)");
  const textColor = themeColor("--text-dim", "#787774");
  [0.25, 0.5, 0.75, 1].forEach((ratio) => {
    [1, -1].forEach((sign) => {
      const y = zeroY - sign * ratio * ((height - padTop - padBottom) / 2);
      svg.appendChild(svgEl("line", { x1: padLeft, y1: y, x2: width - 12, y2: y, class: "grid" }));
    });
  });
  svg.appendChild(
    svgEl("line", { x1: padLeft, y1: zeroY, x2: width - 12, y2: zeroY, stroke: gridColor, "stroke-width": 1 })
  );
  const zeroLabel = svgEl("text", { x: 8, y: zeroY + 3, class: "axis-label", fill: textColor });
  zeroLabel.textContent = "0";
  svg.appendChild(zeroLabel);
  const maxLabel = svgEl("text", { x: 6, y: scaleY(maxAbs) + 3, class: "axis-label", fill: textColor });
  maxLabel.textContent = `+${maxAbs.toFixed(2)}`;
  svg.appendChild(maxLabel);

  Object.entries(series).forEach(([axis, values], index) => {
    const color = DRIFT_COLORS[index % DRIFT_COLORS.length];
    const points = values.map((value, i) => `${scaleX(i)},${scaleY(value)}`).join(" ");
    svg.appendChild(svgEl("polyline", { points, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round" }));
  });
  // x 轴首尾日期
  const first = svgEl("text", { x: padLeft, y: height - 8, class: "axis-label", fill: textColor });
  first.textContent = days[0];
  svg.appendChild(first);
  if (days.length > 1) {
    const last = svgEl("text", { x: width - 12, y: height - 8, class: "axis-label", fill: textColor, "text-anchor": "end" });
    last.textContent = days[days.length - 1];
    svg.appendChild(last);
  }
  if (legendEl) {
    legendEl.innerHTML = Object.keys(series)
      .map(
        (axis, index) =>
          `<span><i style="background:${DRIFT_COLORS[index % DRIFT_COLORS.length]}"></i>${esc(labels[axis] || axis)}</span>`
      )
      .join("");
  }
}

/* ---------------------------------------------------------------------- */
/* 风格样本（群友档案 + 策略 + 表达样本）                                   */
/* ---------------------------------------------------------------------- */

const memberState = { members: [] };

async function loadStyleSamples() {
  await Promise.all([loadMembers(), loadStylePatterns()]);
}

async function loadMembers() {
  try {
    const data = await apiGet("members/list");
    const members = data.members || [];
    memberState.members = members;
    const stability = data.stability || {};
    const meta = $("ss-member-meta");
    if (meta) {
      meta.textContent = `群友 ${members.length} 位 · 标识稳定 ${stability.stable || 0} / 不稳定 ${stability.unstable || 0}`;
    }
    renderTable(
      $("ss-member-table"),
      [
        { title: "群友", render: (row) => `<span class="title">${esc(row.stable_name || row.sender_id)}</span>` },
        { title: "标识", render: (row) => `<span class="mono muted">${esc(row.sender_id)}</span>` },
        {
          title: "稳定性",
          render: (row) =>
            `<span class="pill ${row.stability === "stable" ? "on" : row.stability === "unstable" ? "err" : "off"}">${
              row.stability === "stable" ? "稳定" : row.stability === "unstable" ? "不稳定" : "未知"
            }</span>`,
        },
        { title: "好感度", className: "num", render: (row) => esc(row.affinity === null || row.affinity === undefined ? "—" : num(row.affinity, 2)) },
        { title: "记忆", className: "num", key: "memory_count" },
        { title: "关系", render: (row) => esc(row.relation || "—") },
        { title: "语气策略", render: (row) => esc(row.tone || "—") },
        { title: "来源", render: (row) => `<span class="muted">${esc(row.source || "未建档")}</span>` },
        { title: "最近互动", className: "num", render: (row) => esc(row.last_interact ? fmtTime(row.last_interact) : "—") },
        {
          title: "操作",
          className: "ops",
          render: (row) =>
            `<button class="link-btn" data-member-distill="${esc(row.sender_id)}">蒸馏档案</button>` +
            `<button class="link-btn" data-member-strategy="${esc(row.sender_id)}">编辑策略</button>`,
        },
      ],
      members,
      { emptyText: "还没有群友观测：让群友在群里说句话后，身份观测会自动建档。" }
    );

    const select = $("ss-strategy-member");
    if (select) {
      const current = select.value;
      select.innerHTML = members
        .map(
          (row) =>
            `<option value="${esc(row.sender_id)}">${esc(row.stable_name || row.sender_id)}（${esc(
              row.sender_id
            )}）</option>`
        )
        .join("");
      if (current && members.some((row) => row.sender_id === current)) select.value = current;
    }
    // 发送者筛选项来自 memory/facets（带记忆条数，口径与记忆表一致）
    let senderOptions = "";
    try {
      const facets = await apiGet("memory/facets");
      senderOptions = (facets.senders || [])
        .map(
          (row) =>
            `<option value="${esc(row.sender_id)}">${esc(row.sender_name || row.sender_id)}（${esc(
              row.count
            )}）</option>`
        )
        .join("");
    } catch (error) {
      senderOptions = members
        .map((row) => `<option value="${esc(row.sender_id)}">${esc(row.stable_name || row.sender_id)}</option>`)
        .join("");
    }
    ["mem-sender", "recall-sender"].forEach((id) => {
      const node = $(id);
      if (!node) return;
      const current = node.value;
      node.innerHTML = `<option value="">${esc(t("filter.allSenders"))}</option>${senderOptions}`;
      node.value = current;
    });
  } catch (error) {
    renderError($("ss-member-table"), error);
  }
}

async function loadStrategy(senderId) {
  const id = senderId || ($("ss-strategy-member") ? $("ss-strategy-member").value : "");
  if (!id) {
    toast(t("styleSamples.noMember"), "warn");
    return;
  }
  try {
    const data = await apiPost("members/strategy", { action: "get", sender_id: id });
    const row = data.strategy || {};
    $("ss-relation").value = row.relation || "";
    $("ss-tone").value = row.tone || "";
    $("ss-address").value = row.address_as || "";
    $("ss-topics").value = (row.topics || []).join(", ");
    $("ss-taboo").value = (row.taboo || []).join(", ");
    $("ss-strategy-meta").textContent = row.source
      ? `来源：${row.source}${row.updated_at ? ` · 更新于 ${fmtTime(row.updated_at)}` : ""}`
      : "该群友还没有策略（保存后即刻生效：只微调语气 / 称呼 / 话题 / 禁忌）";
  } catch (error) {
    toast(error.message || String(error), "err");
  }
}

async function saveStrategy() {
  const id = $("ss-strategy-member") ? $("ss-strategy-member").value : "";
  if (!id) {
    toast(t("styleSamples.noMember"), "warn");
    return;
  }
  try {
    const result = await apiPost("members/strategy", {
      action: "save",
      sender_id: id,
      relation: $("ss-relation").value.trim(),
      tone: $("ss-tone").value.trim(),
      address_as: $("ss-address").value.trim(),
      topics: splitList($("ss-topics").value),
      taboo: splitList($("ss-taboo").value),
      source: "manual",
    });
    toast(result.message || t("styleSamples.strategySaved"), "ok");
    await loadMembers();
  } catch (error) {
    toast(error.message || String(error), "err");
  }
}

async function deleteStrategy() {
  const id = $("ss-strategy-member") ? $("ss-strategy-member").value : "";
  if (!id) {
    toast(t("styleSamples.noMember"), "warn");
    return;
  }
  confirmRequest(t("action.delete"), `删除该群友的对话策略？（画像与记忆不受影响）`, async () => {
    try {
      const result = await apiPost("members/strategy", { action: "delete", sender_id: id });
      toast(result.message || t("styleSamples.strategyDeleted"), "ok");
      await loadMembers();
      await loadStrategy(id);
    } catch (error) {
      toast(error.message || String(error), "err");
    }
  });
}

async function distillMember(senderId) {
  try {
    const result = await apiPost("members/strategy", { action: "distill", sender_id: senderId });
    toast(`${result.origin === "distilly:offline" ? "已接入离线蒸馏档案" : "已用在线样本自写档案"}`, "ok");
    await loadMembers();
    await loadStrategy(senderId);
  } catch (error) {
    toast(error.message || String(error), "err");
  }
}

async function distillAll() {
  try {
    const result = await apiPost("members/strategy", { action: "distill-all", sender_id: "-" });
    toast(`已为 ${result.distilled || 0} 位未建档群友生成档案`, "ok");
    await loadMembers();
  } catch (error) {
    toast(error.message || String(error), "err");
  }
}

async function loadStylePatterns() {
  try {
    const data = await apiGet("persona", { limit: 50 });
    renderTable(
      $("ss-pattern-table"),
      [
        { title: "场景", render: (row) => esc(truncate(row.situation || "", 40)) },
        { title: "表达", className: "content", render: (row) => esc(truncate(row.expression || "", 60)) },
        { title: "权重", className: "num", render: (row) => esc(num(row.weight, 2)) },
        { title: "命中", className: "num", key: "hits" },
        { title: "作用域", render: (row) => `<span class="muted mono">${esc(row.scope || "")}</span>` },
      ],
      data.style || [],
      { emptyText: "还没有学习到表达样本（可在功能页开启「风格模仿」并批准待审记录）。" }
    );
  } catch (error) {
    renderError($("ss-pattern-table"), error);
  }
}

/* ---------------------------------------------------------------------- */
/* 记忆：发送者 / 层级过滤（作用于本批最多 100 条）                         */
/* ---------------------------------------------------------------------- */

const memFilterState = { active: false, rows: [], offset: 0, limit: 20 };

async function fetchMemoryBatch(cursor) {
  // 导航重构版以 memory/list 为规范路由（后端与旧 memories 同源，兼容别名继续可用）
  return apiGet("memory/list", {
    offset: 0,
    limit: 100,
    status: cursor.status,
    kind: cursor.kind,
    keyword: cursor.keyword,
    sort: cursor.sort,
  });
}

function paintMemoryBatch(target) {
  const rows = memFilterState.rows;
  const page = rows.slice(memFilterState.offset, memFilterState.offset + memFilterState.limit);
  renderTable(target, memoryColumns(), page, { emptyText: "该筛选条件下没有记忆。" });
  const from = rows.length === 0 ? 0 : memFilterState.offset + 1;
  const to = Math.min(memFilterState.offset + memFilterState.limit, rows.length);
  $("mem-page-info").textContent = `${from}-${to} / ${rows.length}（本批）`;
  $("mem-prev").disabled = memFilterState.offset <= 0;
  $("mem-next").disabled = memFilterState.offset + memFilterState.limit >= rows.length;
}

async function loadMemoriesFiltered() {
  const target = $("mem-table");
  const cursor = state.memories;
  try {
    const result = await fetchMemoryBatch(cursor);
    const sender = $("mem-sender") ? $("mem-sender").value : "";
    const tier = $("mem-tier") ? $("mem-tier").value : "";
    let rows = result.items || [];
    if (sender) rows = rows.filter((row) => String(row.sender_id || "") === sender);
    if (tier) rows = rows.filter((row) => tierOf(row) === tier);
    memFilterState.rows = rows;
    memFilterState.limit = cursor.limit;
    paintMemoryBatch(target);
  } catch (error) {
    renderError(target, error);
  }
}

async function loadMemories() {
  const sender = $("mem-sender") ? $("mem-sender").value : "";
  const tier = $("mem-tier") ? $("mem-tier").value : "";
  if (sender || tier) {
    memFilterState.active = true;
    memFilterState.offset = 0;
    return loadMemoriesFiltered();
  }
  memFilterState.active = false;
  const target = $("mem-table");
  const cursor = state.memories;
  try {
    const result = await apiGet("memory/list", {
      offset: cursor.offset,
      limit: cursor.limit,
      status: cursor.status,
      kind: cursor.kind,
      keyword: cursor.keyword,
      sort: cursor.sort,
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
/* 检索：混合召回 + 证据链 + 图扩展                                        */
/* ---------------------------------------------------------------------- */

async function runRecall() {
  const query = $("recall-query").value.trim();
  const umo = $("recall-umo").value.trim();
  const senderId = $("recall-sender") ? $("recall-sender").value : "";
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
    const result = await apiPost("memory/recall", { query, umo, sender_id: senderId, limit });
    const parts = [
      `命中 ${result.total || 0} 条`,
      `检索路：${result.routes || "—"}`,
      `耗时：${result.elapsed_ms !== undefined ? `${result.elapsed_ms}ms` : "—"}`,
    ];
    if (result.sender_id) parts.push(`发送者过滤：${result.sender_id}（本页过滤掉 ${result.filtered_out || 0} 条）`);
    if (result.tkg) parts.push("已附时序图谱证据链");
    if ((result.expanded || []).length) parts.push(`图扩展补充 ${result.expanded.length} 条`);
    if (result.rerank) parts.push(result.rerank);
    if (result.degraded) parts.push(`提示：${result.degraded}`);
    meta.textContent = parts.join("　|　");

    const rows = [...(result.items || []), ...(result.expanded || [])];
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
        { title: "发送者", render: (row) => esc(row.sender_name || row.sender_id || "—") },
        {
          title: "最终分",
          className: "num",
          render: (row) => esc(row.score === null || row.score === undefined ? "—" : num(row.score, 3)),
        },
        {
          title: "Provenance",
          className: "content",
          render: (row) => {
            if (row.source === "tkg-expand") {
              return `<span class="muted">图扩展：${esc(row.via || "")}</span>`;
            }
            const entities = ((row.provenance || {}).entities || []).join("、");
            const chips = [row.match ? esc(row.match) : "", entities ? `实体：${esc(entities)}` : ""].filter(Boolean);
            return chips.length ? `<span class="muted">${chips.join("　")}</span>` : `<span class="muted">—</span>`;
          },
        },
      ],
      rows,
      { emptyText: "没有命中任何记忆。" }
    );
  } catch (error) {
    meta.textContent = "";
    renderError(target, error);
  }
}

/* ---------------------------------------------------------------------- */
/* 记忆后端：后端状态 + 三级占比 + 衰减曲线                                */
/* ---------------------------------------------------------------------- */

const BACKEND_PILL = { online: "on", degraded: "warn", off: "off", "monitor-only": "info" };

async function loadMemoryBackend() {
  try {
    const data = await apiGet("memory/backends");
    const backends = data.backends || [];
    renderTable(
      $("mb-backends"),
      [
        { title: "后端", render: (row) => `<span class="title">${esc(row.title)}</span>` },
        {
          title: "状态",
          render: (row) =>
            `<span class="pill ${BACKEND_PILL[row.status] || "off"}">${
              row.status === "online"
                ? "就绪"
                : row.status === "degraded"
                  ? "降级"
                  : row.status === "monitor-only"
                    ? "仅展示"
                    : "未启用"
            }</span>`,
        },
        { title: "详情", className: "content", render: (row) => esc(row.detail || "—") },
      ],
      backends,
      { emptyText: data.degraded || "没有可用的记忆后端。" }
    );
    $("mb-meta").textContent = `当前生效后端：${data.active_backend === "latrace" ? "LATRACE 时序图谱（进程内）" : "本地 SQLite"}`;

    // 三级占比：走专用路由（含每层的样本与门槛说明）
    let tiers = [];
    try {
      const tierData = await apiGet("memory/tiers", { samples: 5 });
      tiers = tierData.tiers || [];
    } catch (error) {
      tiers = ((data.tiers_detail || {}).tiers) || [];
    }
    renderTable(
      $("mb-tiers"),
      [
        { title: "层级", render: (row) => `${tierPill(row.tier)} ${esc(row.label)}` },
        { title: "条数", className: "num", key: "count" },
        { title: "占比", className: "num", render: (row) => esc(asPct(row.share)) },
        { title: "说明", className: "content", render: (row) => esc(row.hint || "") },
      ],
      tiers,
      { emptyText: "还没有记忆，三级占比为空。" }
    );
    const decoded = await apiGet("memory/decay", { limit: 8 });
    drawDecayCurve($("mb-decay"), decoded.curves || {});
    $("mb-decay-meta").textContent = decoded.degraded
      ? decoded.degraded
      : `默认强度 ${decoded.strength_days} 天 · 写回重要度：${decoded.write_back ? "开启" : "关闭"} · 接近遗忘阈值 ${decoded.threshold_pct}%`;
    const samples = decoded.samples || [];
    renderTable(
      $("mb-decay-table"),
      [
        { title: "#", key: "id", className: "num" },
        { title: "内容", className: "content", render: (row) => esc(truncate(row.content, 80)) },
        { title: "发送者", render: (row) => esc(row.sender_name || row.sender_id || "—") },
        { title: "保留率", className: "num", render: (row) => `<span class="${valClass(Number(row.retention || 0) / 100)}">${esc(num(row.retention, 1))}%</span>` },
        { title: "强度(天)", className: "num", render: (row) => esc(num(row.strength_days, 1)) },
        { title: "距上次活跃(天)", className: "num", render: (row) => esc(num(row.elapsed_days, 1)) },
      ],
      samples,
      { emptyText: decoded.degraded || "暂无临近遗忘的记忆。" }
    );
  } catch (error) {
    renderError($("mb-backends"), error);
  }
}

function drawDecayCurve(svg, curves) {
  if (!svg) return;
  svg.innerHTML = "";
  const points = curves.points || [];
  const natural = curves.natural || [];
  const reviewed = curves.reviewed || [];
  if (!points.length) {
    const note = svgEl("text", { x: 400, y: 130, class: "axis-label", "text-anchor": "middle" });
    note.textContent = "暂无衰减数据";
    svg.appendChild(note);
    return;
  }
  const width = 800;
  const height = 260;
  const padLeft = 44;
  const padRight = 16;
  const padTop = 18;
  const padBottom = 30;
  const maxPct = Number(curves.max_pct || 120);
  const maxDay = Math.max(...points);
  const scaleX = (day) => padLeft + (day / maxDay) * (width - padLeft - padRight);
  const scaleY = (pct) => padTop + (1 - pct / maxPct) * (height - padTop - padBottom);
  const gridColor = themeColor("--chart-grid", "rgba(55,53,47,0.12)");
  const textColor = themeColor("--text-dim", "#787774");

  [0, 0.25, 0.5, 0.75, 1].forEach((ratio) => {
    const y = padTop + ratio * (height - padTop - padBottom);
    svg.appendChild(svgEl("line", { x1: padLeft, y1: y, x2: width - padRight, y2: y, class: "grid" }));
    const label = svgEl("text", { x: 8, y: y + 3, class: "axis-label", fill: textColor });
    label.textContent = `${Math.round(maxPct * (1 - ratio))}%`;
    svg.appendChild(label);
  });
  const threshold = svgEl("line", {
    x1: padLeft,
    y1: scaleY(Number(curves.threshold_pct || 20)),
    x2: width - padRight,
    y2: scaleY(Number(curves.threshold_pct || 20)),
    class: "line threshold",
  });
  svg.appendChild(threshold);

  const line = (series, className) => {
    const polyline = svgEl("polyline", {
      points: series.map((item) => `${scaleX(item.day)},${scaleY(item.retention)}`).join(" "),
      class: `line ${className}`,
    });
    svg.appendChild(polyline);
  };
  line(natural, "natural");
  line(reviewed, "reviewed");

  points.forEach((day) => {
    const label = svgEl("text", {
      x: scaleX(day),
      y: height - 8,
      class: "axis-label",
      fill: textColor,
      "text-anchor": "middle",
    });
    label.textContent = `${day}d`;
    svg.appendChild(label);
  });
}

/* ---------------------------------------------------------------------- */
/* 世界书（Lorebook）                                                      */
/* ---------------------------------------------------------------------- */

const worldbookState = { editingId: 0 };

function resetWorldbookEditor() {
  worldbookState.editingId = 0;
  $("wb-triggers").value = "";
  $("wb-priority").value = "5";
  $("wb-scope-type").value = "global";
  $("wb-scope-id").value = "";
  $("wb-enabled").value = "1";
  $("wb-content").value = "";
  $("wb-editor-meta").textContent = "新条目（保存后生效）";
}

async function loadWorldbook() {
  try {
    const data = await apiGet("memory/worldbook");
    const items = data.items || [];
    const stats = data.stats || {};
    renderTable(
      $("wb-table"),
      [
        { title: "#", key: "id", className: "num" },
        {
          title: "触发词",
          render: (row) =>
            (row.triggers || []).length
              ? (row.triggers || []).map((trigger) => `<span class="tag">${esc(trigger)}</span>`).join("")
              : `<span class="muted">手动条目</span>`,
        },
        { title: "注入内容", className: "content", render: (row) => esc(truncate(row.content, 120)) },
        { title: "作用域", render: (row) => `<span class="muted mono">${esc(row.scope)}</span>` },
        { title: "优先级", className: "num", key: "priority" },
        {
          title: "状态",
          render: (row) => `<span class="pill ${row.enabled ? "on" : "off"}">${row.enabled ? "启用" : "停用"}</span>`,
        },
        { title: "命中", className: "num", key: "hits" },
        {
          title: "操作",
          className: "ops",
          render: (row) =>
            `<button class="link-btn" data-worldbook-edit="${esc(row.id)}">编辑</button>` +
            `<button class="link-btn danger" data-worldbook-del="${esc(row.id)}">删除</button>`,
        },
      ],
      items,
      { emptyText: data.degraded || "还没有世界书条目（新增后按触发词自动注入）。" }
    );
    $("wb-editor-meta").textContent = `共 ${stats.entries || 0} 条 · 启用 ${stats.enabled || 0} 条 · 累计命中 ${stats.hits || 0} 次`;
    worldbookState.items = items;
  } catch (error) {
    renderError($("wb-table"), error);
  }
}

function editWorldbook(id) {
  const row = (worldbookState.items || []).find((item) => String(item.id) === String(id));
  if (!row) return;
  worldbookState.editingId = row.id;
  $("wb-triggers").value = (row.triggers || []).join(", ");
  $("wb-priority").value = String(row.priority);
  $("wb-scope-type").value = row.scope_type || "global";
  $("wb-scope-id").value = row.scope_id || "";
  $("wb-enabled").value = row.enabled ? "1" : "0";
  $("wb-content").value = row.content || "";
  $("wb-editor-meta").textContent = `编辑条目 #${row.id}`;
}

async function saveWorldbook() {
  const content = $("wb-content").value.trim();
  if (!content) {
    toast("注入内容不能为空", "warn");
    return;
  }
  const payload = {
    triggers: splitList($("wb-triggers").value),
    content,
    priority: Number($("wb-priority").value) || 5,
    scope_type: $("wb-scope-type").value,
    scope_id: $("wb-scope-id").value.trim(),
    enabled: $("wb-enabled").value === "1",
  };
  const endpoint = worldbookState.editingId ? "memory/worldbook-update" : "memory/worldbook-add";
  if (worldbookState.editingId) payload.id = worldbookState.editingId;
  try {
    const result = await apiPost(endpoint, payload);
    toast(result.message || t("worldbook.saved"), "ok");
    resetWorldbookEditor();
    await loadWorldbook();
  } catch (error) {
    toast(error.message || String(error), "err");
  }
}

async function deleteWorldbook(id) {
  confirmRequest(t("action.delete"), `删除世界书条目 #${id}？`, async () => {
    try {
      const result = await apiPost("memory/worldbook-del", { id: Number(id) });
      toast(result.message || t("worldbook.deleted"), "ok");
      await loadWorldbook();
    } catch (error) {
      toast(error.message || String(error), "err");
    }
  });
}

/* ---------------------------------------------------------------------- */
/* 共情管线（CogEmp）                                                      */
/* ---------------------------------------------------------------------- */

async function loadEmpathy() {
  try {
    const data = await apiGet("empathy/config");
    // 事件流单独走 empathy/log：与配置解耦，可按会话过滤
    const logData = await apiGet("empathy/log", { limit: 50 }).catch(() => ({ items: [] }));
    const config = data.config || {};
    const stats = data.stats || {};
    renderStats($("ep-meta"), [
      ["开关", config.enabled ? "开启" : "关闭"],
      ["共情温度", config.temperature !== undefined ? num(config.temperature, 2) : "—"],
      ["强度门槛", config.min_intensity !== undefined ? num(config.min_intensity, 2) : "—"],
      ["近 24h 事件", stats.events || 0],
      ["阶段数", (config.stage_identify ? 1 : 0) + (config.stage_understand ? 1 : 0) + (config.stage_empathize ? 1 : 0)],
      ["注入上限", `${config.max_injected_chars || 0} 字`],
    ]);
    if ($("ep-stage-identify")) $("ep-stage-identify").checked = Boolean(config.stage_identify);
    if ($("ep-stage-understand")) $("ep-stage-understand").checked = Boolean(config.stage_understand);
    if ($("ep-stage-empathize")) $("ep-stage-empathize").checked = Boolean(config.stage_empathize);
    if ($("ep-temperature")) {
      const value = Math.round(Number(config.temperature || 0.55) * 100);
      $("ep-temperature").value = String(value);
      $("ep-temperature-value").textContent = (value / 100).toFixed(2);
    }
    const items = (logData.items && logData.items.length ? logData.items : data.items) || [];
    renderTable(
      $("ep-log"),
      [
        { title: "时间", className: "num", render: (row) => esc(fmtTime(row.created_at)) },
        { title: "群友", render: (row) => esc(row.sender_name || row.sender_id || "—") },
        {
          title: "情绪",
          render: (row) => `<span class="pill ${row.emotion === "开心" || row.emotion === "感激" ? "on" : "warn"}">${esc(row.emotion || "—")}</span>`,
        },
        { title: "强度", className: "num", render: (row) => esc(num(row.intensity, 2)) },
        { title: "原因线索", render: (row) => esc((row.causes || []).join("、") || "—") },
        { title: "共情润色", className: "content", render: (row) => esc(truncate(row.guidance || "", 90)) },
      ],
      items,
      { emptyText: data.degraded || "还没有共情事件（识别到情绪后会自动留痕）。" }
    );
  } catch (error) {
    renderError($("ep-meta"), error);
  }
}

async function saveEmpathy() {
  try {
    const payload = {
      stage_identify: $("ep-stage-identify").checked,
      stage_understand: $("ep-stage-understand").checked,
      stage_empathize: $("ep-stage-empathize").checked,
      temperature: Number($("ep-temperature").value) / 100,
    };
    const result = await apiPost("empathy/config", payload);
    toast(result.message || t("empathy.saved"), "ok");
    await loadEmpathy();
  } catch (error) {
    toast(error.message || String(error), "err");
  }
}

/* ---------------------------------------------------------------------- */
/* 主动关怀（回访队列 + 计划轨）                                            */
/* ---------------------------------------------------------------------- */

const proactiveState = { editingId: 0 };

async function loadProactive() {
  try {
    const status = $("pq-status") ? $("pq-status").value : "";
    const data = await apiGet("proactive/queue", { status, limit: 50 });
    const stats = data.stats || {};
    if ($("pq-meta")) {
      $("pq-meta").textContent = `待发 ${stats.pending || 0} · 到期 ${stats.due || 0} · 已发送 ${stats.sent || 0} · 总数 ${stats.total || 0}`;
    }
    const nav = $("nav-queue");
    if (nav) nav.textContent = stats.pending ? String(stats.pending) : "—";
    renderTable(
      $("pq-table"),
      [
        { title: "#", key: "id", className: "num" },
        { title: "类型", render: (row) => `<span class="pill info">${esc(row.kind_label)}</span>` },
        { title: "对象", render: (row) => esc(row.target || row.umo || "—") },
        { title: "到期时间", className: "num", render: (row) => esc(row.due_at ? fmtTime(row.due_at) : "—") },
        { title: "内容", className: "content", render: (row) => esc(truncate(row.content, 90)) },
        {
          title: "状态",
          render: (row) =>
            `<span class="pill ${row.status === "pending" ? "warn" : row.status === "sent" ? "on" : "off"}">${esc(row.status_label)}</span>`,
        },
        { title: "尝试", className: "num", key: "attempts" },
        {
          title: "操作",
          className: "ops",
          render: (row) =>
            (row.status === "pending"
              ? `<button class="link-btn" data-queue-sent="${esc(row.id)}">标记已发</button>` +
                `<button class="link-btn" data-queue-cancel="${esc(row.id)}">取消</button>`
              : "") +
            `<button class="link-btn danger" data-queue-del="${esc(row.id)}">删除</button>`,
        },
      ],
      data.items || [],
      { emptyText: data.degraded || "回访队列为空（登记后到期自动投递）。" }
    );

    // 计划轨 / 空闲轨
    try {
      const schedule = await apiGet("proactive/schedule");
      const config = schedule.config || {};
      const snapshot = schedule.snapshot || {};
      renderStats($("pq-schedule"), [
        ["主动消息开关", config.enabled ? "开启" : "关闭"],
        ["计划轨", config.daily_enabled ? `每天 ${config.daily_time || "—"}` : "关闭"],
        ["空闲轨", config.idle_enabled ? `静默 ${config.idle_minutes || 0} 分钟` : "关闭"],
        ["目标会话", (config.targets || []).length],
        ["每日上限", config.daily_max || "—"],
        ["已发送", snapshot.sent !== undefined ? snapshot.sent : "—"],
      ]);
      const log = await apiGet("proactive/log", { limit: 20 });
      renderTable(
        $("pq-log"),
        [
          { title: "#", key: "id", className: "num" },
          { title: "类型", render: (row) => esc(row.kind_label) },
          { title: "对象", render: (row) => esc(row.target || row.umo || "—") },
          { title: "内容", className: "content", render: (row) => esc(truncate(row.content, 80)) },
          { title: "状态", render: (row) => esc(row.status_label) },
          { title: "更新时间", className: "num", render: (row) => esc(row.updated_at ? fmtTime(row.updated_at) : "—") },
        ],
        log.items || [],
        { emptyText: "还没有投递记录。" }
      );
    } catch (error) {
      renderSoftEmpty($("pq-log"), error.message || String(error));
    }
  } catch (error) {
    renderError($("pq-table"), error);
  }
}

function newProactiveItem() {
  proactiveState.editingId = 0;
  $("pq-content").value = "";
  $("pq-umo").value = "";
  $("pq-due-hours").value = "24";
  $("pq-editor-meta").textContent = "新登记（到期后自动投递）";
}

async function saveProactive() {
  const content = $("pq-content").value.trim();
  const umo = $("pq-umo").value.trim();
  if (!content) {
    toast("内容不能为空", "warn");
    return;
  }
  if (!umo) {
    toast(t("proactive.needUmo"), "warn");
    return;
  }
  try {
    const result = await apiPost("proactive/queue", {
      action: "add",
      kind: $("pq-kind").value,
      umo,
      target: umo,
      content,
      due_in_hours: Number($("pq-due-hours").value) || 24,
    });
    toast(result.message || t("proactive.saved"), "ok");
    newProactiveItem();
    await loadProactive();
  } catch (error) {
    toast(error.message || String(error), "err");
  }
}

async function updateQueueStatus(id, action) {
  try {
    await apiPost("proactive/queue", { action, id: Number(id) });
    toast("队列状态已更新", "ok");
    await loadProactive();
  } catch (error) {
    toast(error.message || String(error), "err");
  }
}

async function deleteQueueItem(id) {
  confirmRequest(t("action.delete"), `删除队列项 #${id}？`, async () => {
    try {
      const result = await apiPost("proactive/queue", { action: "delete", id: Number(id) });
      toast(result.message || t("proactive.deleted"), "ok");
      await loadProactive();
    } catch (error) {
      toast(error.message || String(error), "err");
    }
  });
}

/* ---------------------------------------------------------------------- */
/* 功能页：群上下文卡片                                                    */
/* ---------------------------------------------------------------------- */

async function loadGroupContext() {
  const target = $("feat-group");
  if (!target) return;
  try {
    const data = await apiGet("group/context");
    if (data.degraded) {
      renderSoftEmpty(target, data.degraded);
      return;
    }
    const pairs = [
      ["群聊语义", data.enabled ? "开启" : "关闭"],
      ["插话阈值", num(data.attention_threshold, 2)],
      ["冷却（秒）", data.cooldown_seconds],
      ["每小时上限", data.max_per_hour],
      ["合并窗口（秒）", num(data.merge_window_seconds, 1)],
      ["称呼词", (data.bot_aliases || []).join("、") || "—"],
      ["白名单", (data.whitelist || []).join("、") || "不限"],
      ["黑名单", (data.blacklist || []).join("、") || "无"],
    ];
    target.innerHTML = pairs.map(([label, value]) => kv(label, String(value))).join("");
  } catch (error) {
    renderSoftEmpty(target, error.message || String(error));
  }
}

/* ---------------------------------------------------------------------- */
/* 图谱页：图层切换 / 时间线 / 重建                                        */
/* ---------------------------------------------------------------------- */

async function rebuildTkg(button) {
  if (button) button.disabled = true;
  try {
    const result = await apiPost("graph/rebuild", { limit: 300 });
    toast(`时序图谱已回填：扫描 ${result.memories || 0} 条记忆，实体 ${result.entities || 0} 个`, "ok");
    await loadGraph();
  } catch (error) {
    toast(error.message || String(error), "err");
  } finally {
    if (button) button.disabled = false;
  }
}

async function loadGraphTimeline() {
  const target = $("gp-timeline");
  if (!target) return;
  try {
    const data = await apiGet("graph/timeline", { limit: 20 });
    const items = data.items || [];
    renderTable(
      target,
      [
        { title: "时间", className: "num", render: (row) => esc(row.valid_from ? fmtTime(row.valid_from) : "—") },
        { title: "主体", render: (row) => esc(row.src || "—") },
        { title: "关系", render: (row) => `<span class="pill info">${esc(row.relation)}</span>` },
        { title: "客体", render: (row) => esc(row.dst || "—") },
        { title: "权重", className: "num", render: (row) => esc(num(row.weight, 2)) },
        { title: "证据", className: "content", render: (row) => `<span class="muted mono">${esc(row.evidence || "—")}</span>` },
      ],
      items,
      { emptyText: data.degraded || "时序图谱为空：点「重建时序图谱」从既有记忆回填。" }
    );
  } catch (error) {
    renderError(target, error);
  }
}

/* ---------------------------------------------------------------------- */
/* 新页面控件绑定（在 init 中调用一次）                                     */
/* ---------------------------------------------------------------------- */

function bindFusionControls() {
  bindNavGroups();
  bindSidebar();
  bindTabs();
  injectDragHandles();

  // 人格内核
  if ($("pf-save")) $("pf-save").addEventListener("click", (event) => saveForge(event.currentTarget));
  if ($("pf-reload")) $("pf-reload").addEventListener("click", loadForge);
  if ($("pf-reset")) $("pf-reset").addEventListener("click", resetForge);
  if ($("pf-preview")) {
    $("pf-preview").addEventListener("click", () => {
      const snapshot = forgeState.snapshot || {};
      $("pf-text").textContent = snapshot.profile_text || "—";
      toast("已刷新注入预览（保存后生效的是表单内容）", "info");
    });
  }
  if ($("pf-core")) {
    $("pf-core").addEventListener("input", (event) => {
      const input = event.target.closest("[data-forge-axis]");
      if (!input) return;
      const chip = document.querySelector(`[data-forge-axis-value="${CSS.escape(input.dataset.forgeAxis)}"]`);
      if (chip) chip.textContent = (Number(input.value) / 100).toFixed(2);
    });
  }
  if ($("pf-energy")) {
    $("pf-energy").addEventListener("input", () => {
      $("pf-energy-value").textContent = $("pf-energy").value;
    });
  }

  // 演化轨迹
  if ($("pe-reload")) $("pe-reload").addEventListener("click", loadEvolution);
  if ($("pe-reset")) {
    $("pe-reset").addEventListener("click", () => {
      confirmRequest(t("evolution.resetEvents"), "清空全部演化事件？当前人格画像不会被重置。", async () => {
        try {
          const result = await apiPost("persona/evolution-reset", {});
          toast(`已清空 ${result.deleted || 0} 条演化事件`, "ok");
          await loadEvolution();
        } catch (error) {
          toast(error.message || String(error), "err");
        }
      });
    });
  }

  // 风格样本 / 群友档案
  if ($("ss-distill")) $("ss-distill").addEventListener("click", distillAll);
  if ($("ss-strategy-load")) $("ss-strategy-load").addEventListener("click", () => loadStrategy());
  if ($("ss-strategy-save")) $("ss-strategy-save").addEventListener("click", saveStrategy);
  if ($("ss-strategy-del")) $("ss-strategy-del").addEventListener("click", deleteStrategy);
  if ($("ss-strategy-member")) {
    $("ss-strategy-member").addEventListener("change", () => loadStrategy());
  }
  if ($("ss-member-table")) {
    $("ss-member-table").addEventListener("click", (event) => {
      const distill = event.target.closest("[data-member-distill]");
      if (distill) return distillMember(distill.dataset.memberDistill);
      const strategy = event.target.closest("[data-member-strategy]");
      if (strategy) {
        if ($("ss-strategy-member")) $("ss-strategy-member").value = strategy.dataset.memberStrategy;
        return loadStrategy(strategy.dataset.memberStrategy);
      }
    });
  }

  // 记忆页：过滤与分页
  if ($("mem-sender")) $("mem-sender").addEventListener("change", () => { memFilterState.offset = 0; loadMemories(); });
  if ($("mem-tier")) $("mem-tier").addEventListener("change", () => { memFilterState.offset = 0; loadMemories(); });
  if ($("mem-prev")) {
    $("mem-prev").addEventListener("click", () => {
      if (!memFilterState.active) return;
      memFilterState.offset = Math.max(0, memFilterState.offset - memFilterState.limit);
      paintMemoryBatch($("mem-table"));
    });
  }
  if ($("mem-next")) {
    $("mem-next").addEventListener("click", () => {
      if (!memFilterState.active) return;
      memFilterState.offset += memFilterState.limit;
      paintMemoryBatch($("mem-table"));
    });
  }

  // 记忆后端
  if ($("mb-reload")) $("mb-reload").addEventListener("click", loadMemoryBackend);
  if ($("mb-rebuild")) $("mb-rebuild").addEventListener("click", (event) => rebuildTkg(event.currentTarget));

  // 世界书
  if ($("wb-reload")) $("wb-reload").addEventListener("click", loadWorldbook);
  if ($("wb-clear-editor")) $("wb-clear-editor").addEventListener("click", resetWorldbookEditor);
  if ($("wb-save")) $("wb-save").addEventListener("click", saveWorldbook);
  if ($("wb-table")) {
    $("wb-table").addEventListener("click", (event) => {
      const edit = event.target.closest("[data-worldbook-edit]");
      if (edit) return editWorldbook(edit.dataset.worldbookEdit);
      const del = event.target.closest("[data-worldbook-del]");
      if (del) return deleteWorldbook(del.dataset.worldbookDel);
    });
  }

  // 共情
  if ($("ep-reload")) $("ep-reload").addEventListener("click", loadEmpathy);
  if ($("ep-save")) $("ep-save").addEventListener("click", saveEmpathy);
  if ($("ep-temperature")) {
    $("ep-temperature").addEventListener("input", () => {
      $("ep-temperature-value").textContent = (Number($("ep-temperature").value) / 100).toFixed(2);
    });
  }

  // 主动关怀
  if ($("pq-reload")) $("pq-reload").addEventListener("click", loadProactive);
  if ($("pq-status")) $("pq-status").addEventListener("change", loadProactive);
  if ($("pq-new")) $("pq-new").addEventListener("click", newProactiveItem);
  if ($("pq-save")) $("pq-save").addEventListener("click", saveProactive);
  if ($("pq-table")) {
    $("pq-table").addEventListener("click", (event) => {
      const sent = event.target.closest("[data-queue-sent]");
      if (sent) return updateQueueStatus(sent.dataset.queueSent, "sent");
      const cancel = event.target.closest("[data-queue-cancel]");
      if (cancel) return updateQueueStatus(cancel.dataset.queueCancel, "cancel");
      const del = event.target.closest("[data-queue-del]");
      if (del) return deleteQueueItem(del.dataset.queueDel);
    });
  }

  // 好感度校准（滑块改动即写入）
  bindAffinityPanel();

  // 图谱
  if ($("gp-rebuild")) $("gp-rebuild").addEventListener("click", (event) => rebuildTkg(event.currentTarget));
  if ($("gp-timeline-refresh")) $("gp-timeline-refresh").addEventListener("click", loadGraphTimeline);
  if ($("gp-layer")) $("gp-layer").addEventListener("change", loadGraph);
}


/* ---------------------------------------------------------------------- */
/* 好感度（按群友 · 人工校准滑块）                                          */
/* ---------------------------------------------------------------------- */

async function loadAffinityPanel() {
  const target = $("pn-affinity");
  if (!target) return;
  const umo = $("pn-umo") ? $("pn-umo").value.trim() : "";
  try {
    const data = await apiGet("persona/affinity", { umo, limit: 100 });
    const items = data.items || [];
    if (!items.length) {
      renderSoftEmpty(target, data.degraded || "还没有好感度记录（开启「社交好感度」后随互动累积）。");
      return;
    }
    target.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>群友</th><th>作用域</th><th>好感度</th><th>人工校准</th>
            <th>心情</th><th>互动次数</th><th>最近互动</th>
          </tr>
        </thead>
        <tbody>
          ${items
            .map((row) => {
              const score = Number(row.score || 0);
              return `<tr>
                <td class="mono">${esc(row.target_id || "—")}</td>
                <td class="muted mono">${esc(row.scope || "")}</td>
                <td class="num"><span class="${valClass(score)}">${esc(num(score, 2))}</span></td>
                <td>
                  <input type="range" min="0" max="100" step="1" value="${Math.round(score * 100)}"
                    data-affinity-target="${esc(row.target_id || "")}" />
                </td>
                <td>${esc(row.mood || "—")}</td>
                <td class="num">${esc(row.interactions ?? 0)}</td>
                <td class="num">${esc(row.last_interaction ? fmtTime(row.last_interaction) : "—")}</td>
              </tr>`;
            })
            .join("")}
        </tbody>
      </table>
      <p class="muted">拖动滑块即可人工校准：带 UMO 时写入会话作用域，不带时写入按用户归属的记录。</p>`;
  } catch (error) {
    renderError(target, error);
  }
}

async function saveAffinity(input) {
  const targetId = input.dataset.affinityTarget;
  if (!targetId) return;
  const umo = $("pn-umo") ? $("pn-umo").value.trim() : "";
  try {
    const result = await apiPost("persona/affinity", {
      target_id: targetId,
      umo,
      score: Number(input.value) / 100,
    });
    toast(`好感度已更新为 ${num(result.score, 2)}`, "ok");
    await loadAffinityPanel();
  } catch (error) {
    toast(error.message || String(error), "err");
  }
}

function bindAffinityPanel() {
  const target = $("pn-affinity");
  if (!target) return;
  target.addEventListener("change", (event) => {
    const input = event.target.closest("[data-affinity-target]");
    if (input) saveAffinity(input);
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
