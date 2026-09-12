# Super_AstrBot

> 面向 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的**长期记忆 + 自我学习**增强插件。
> 分层自适应检索、记忆生命周期、周记现实记忆、反思式自我学习；内置 Harness 适配层与循环控制基础设施。

- 作者：**BUXIN-A**　仓库：<https://github.com/BUXIN-A/astrbot_plugin_Super_AstrBot>
- 版本：见 `metadata.yaml`（版本号只信任该文件，代码不硬编码）
- 要求：AstrBot `>= 4.24.2`；主要面向 **QQ（aiocqhttp / qq_official）**
- 依赖：**零第三方运行时依赖**（持久化用标准库 `sqlite3`；`jieba` 为可选增强）

---

## 功能

| 能力 | 说明 |
|---|---|
| 长期记忆 | 对话自动沉淀、检索时召回并注入；注入内容**不写入对话历史**、不破坏模型前缀缓存 |
| 分层自适应检索 | 关键词路（SQLite FTS5，缺失时 LIKE 降级）+ 向量语义路（检测到 Embedding 提供商才启用）+ RRF 融合 + 多因子加权（相关性/重要性/新近度）+ 词袋去重 |
| 自我学习（反思） | 累积到阈值或到达时间间隔后，让模型回顾对话缓冲，提炼洞察写入长期记忆；可开启「人工审批」模式 |
| 周记（现实记忆） | `/sab journal` 记录现实生活；作为高权重记忆参与召回；每周固定时间产出周度洞察 |
| 记忆生命周期 | 重要度指数衰减（访问越多越抗衰）、长期低价值记忆自动归档、对话缓冲自动清理 |
| 可观测 | `/sab status` 环境与运行诊断、`/sab why` 打分明细、官方插件面板（`pages/dashboard`） |

### 为什么这样设计

- **不修改 AstrBot Core**：只使用公开 API，不 monkey-patch；框架符号访问集中在 `super_astrbot/harness/`，版本升级只需改一处。
- **失败只降级、不打断对话**：无 Embedding 提供商 → 自动退化为纯关键词检索；无 FTS5 → 自动 LIKE；LLM 调用失败 → 跳过本次反思，缓冲保留待重试。
- **对话缓冲与正式记忆分离**：自动采集的原始对话以 `status='buffered'` 落库，**不参与检索**，只作为反思原料；反思产出才是可检索的长期记忆。避免「每句话都变记忆」把检索淹没。
- **后台任务可收敛**：所有后台工作经 `TaskScope` / `Scheduler`，插件卸载时全部取消；每日任务当日幂等、跨重载不重复。

---

## 安装

### 方式一：插件市场（推荐）

在 AstrBot WebUI 的插件市场搜索 `Super_AstrBot` 安装，或在「安装插件」中填入仓库地址：

```
https://github.com/BUXIN-A/astrbot_plugin_Super_AstrBot
```

### 方式二：手动放入

```bash
cd <AstrBot 根目录>/data/plugins
git clone https://github.com/BUXIN-A/astrbot_plugin_Super_AstrBot.git
```

然后在 WebUI 插件管理页对 `astrbot_plugin_Super_AstrBot` 点击 **重载插件**。

安装后插件会自动创建 `data/plugin_data/astrbot_plugin_Super_AstrBot/` 与数据库，并在日志中输出一行：
`Super_AstrBot 已就绪；生效能力：…`

> 数据一律写入 AstrBot 的 `data/` 目录，升级或重装插件不会丢失。

---

## 指令

| 指令 | 说明 |
|---|---|
| `/sab help` | 显示帮助 |
| `/sab status` | 环境与运行诊断：版本、框架符号、生效能力、降级原因、任务、预算 |
| `/sab search <关键词>` | 检索记忆（命中数、检索路、耗时、打分明细） |
| `/sab why <关键词>` | 同上，调参时查看打分构成 |
| `/sab remember <内容>` | 手动写入一条长期记忆 |
| `/sab journal <内容> [#标签]` | 写一条周记 |
| `/sab journals` | 查看最近周记 |
| `/sab review` | 查看待审记忆（需开启审批模式） |
| `/sab approve <编号>` / `/sab reject <编号>` | 批准 / 驳回待审记忆 |
| `/sab reset confirm` | 清空当前作用域的记忆与缓冲（不可逆） |
| `/sab reindex` | 重建 FTS / 向量索引 |

所有子命令都挂在**同一个顶层指令** `sab` 下（别名 `superastrbot`，等价）。
之所以只注册一个顶层指令：AstrBot 的指令冲突检测以指令「完整名」为键，注册项越少，
与其它插件撞名的概率越低，也完全不受框架参数推导行为变化的影响。

默认「管理指令仅管理员可用」，可在配置中关闭。

---

## 控制台（插件页面）

在 AstrBot 插件详情页 → **Pages** → `dashboard` 打开，共六个分区：

| 分区 | 内容 |
|---|---|
| 总览 | 记忆/缓冲/待审/归档/周记/任务统计，能力开关，降级原因，最近写入的记忆 |
| 记忆 | 按状态、类型、关键词筛选 + 分页；点击任意一行查看完整内容与元数据 |
| 检索 | 可选填写会话 UMO；展示命中数、检索路、耗时与**打分明细**（调参用） |
| 周记 | 浏览周记（标签、情绪、时间） |
| 待审 | 审批反思产出：批准写入长期记忆，驳回则丢弃 |
| 系统 | 框架版本与符号诊断、调度任务、调用预算、嵌入模型提供商、重建检索索引 |

实现要点（也是踩过坑后的结论）：

- 页面脚本是 `type="module"`：AstrBot 把 bridge SDK 注入到 `</body>` 之前，
  classic 内联脚本会先执行导致 bridge 为空；
- **完全不走 `fetch`**：插件页运行在无 `allow-same-origin` 的 sandbox iframe 中，
  直连请求必然抛 `Failed to fetch`，所有请求都经 `window.AstrBotPluginPage` 代理；
- 样式与脚本自包含，不引用任何 CDN；HTML 转义覆盖五个字符以防存储型 XSS。

---

## 配置

配置项见 `_conf_schema.json`，分组如下：

- **基础设置**：总开关、调试日志、命令权限、默认记忆作用域（会话/用户/全局）
- **记忆与检索**：采集开关、召回条数、注入预算与方式、FTS/向量开关、RRF 常数、半衰期、多因子权重、最低分、去重阈值
- **自我学习（反思）**：触发模式（轮数/间隔/任一）、阈值、冷却、反思模型、最多产出条数、是否需审批
- **周记**：开关、写入权限、召回加权、周度洞察时间
- **运行与性能**：LLM 超时、辅助调用并发与每日上限、停止感知轮询间隔、向量扫描上限

> 排障时先开启「调试日志」，会用 `DEBUG` 级别输出检索路命中、注入字符数、注入方式等细节。

**关于「嵌入（Embedding）模型提供商」下拉**：AstrBot 的 `_special: "select_provider"`
在框架内被**硬编码为对话模型**，也不存在嵌入模型专用的选择器，因此本项目改为
**运行时把真实的嵌入模型列表注入配置 schema**（每 10 分钟重新注入一次，因为保存配置会
触发插件重载并重建配置对象）。两种降级情形：

- 尚未配置任何嵌入提供商 → 该字段保持**文本框**，可直接手填提供商 ID；
- 注入尚未完成（插件刚启动的几秒内）→ 同样是文本框，稍后自动变为下拉。

> 若你希望下拉立即出现，保存配置后等几秒再打开即可。

---

## 排障（面向服务器部署）

插件设计为「**任何异常都不打断正常对话**」，因此问题通常表现为**能力静默降级**而不是报错。排查顺序：

1. 执行 `/sab status`，逐行对照：

| 输出 | 含义 | 处理 |
|---|---|---|
| `就绪：否` | 初始化未完成或持久层不可用 | 查看日志中 `Super_AstrBot 初始化失败` / `数据库初始化失败` |
| `框架符号缺失：…` | AstrBot 版本不匹配，相关能力已降级 | 升级/降级 AstrBot，或查看下方「符号缺失」表 |
| `FTS=降级` | 当前 SQLite 构建无 FTS5 | 功能可用（走 LIKE 检索），检索精度略降 |
| `向量：降级` | 未检测到 Embedding 提供商 | 在 AstrBot 配置 Embedding 提供商后重载插件 |
| `- 未生效：memory.xxx（…）` | 该能力被配置或前置条件关闭 | 按括号内原因处理 |

2. **注入没生效**：确认 `/sab search <关键词>` 能检索到记忆；能检索但没注入，通常是 AstrBot 版本过老（缺 `TextPart` → 会回退到系统提示词注入，仍应生效）。`/sab status` 的「符号缺失」行可直接判定。

3. **不写记忆**：确认「启用长期记忆」与「自动采集对话写入记忆」为开启；命令类消息（以 `/`、`!`、`#`、`.` 开头）与过短消息**不会**进入缓冲，这是有意设计。

4. **反思不触发**：默认「按对话轮数」需缓冲累计到 `trigger_rounds`（默认 30）且距上次反思超过冷却时间（默认 60 分钟）。想快速验证可临时把「触发方式」改为「按时间间隔」、把阈值调到 2~3。

5. **面板打不开或提示「加载失败」**：面板为可选能力，注册失败**不影响**核心功能（日志会有 `注册 Web API 失败` 警告）。确认 AstrBot `>= 4.24.2`（Pages 需要该版本）。若页面能打开但显示「桥接 SDK 未加载」，说明页面未在 bridge 注入后执行 —— 本项目的入口脚本已使用 `type="module"` 规避该问题，如仍出现请反馈 AstrBot 版本。

6. **日志**：AstrBot 日志中搜索 `Super_AstrBot` 即可过滤本插件的全部输出。

7. **备份/迁移**：直接复制 `data/plugin_data/astrbot_plugin_Super_AstrBot/super_astrbot.db` 即可；迁移到新机器时保留该文件。

8. **看到「指令冲突」告警时如何判断是否本插件引起**：AstrBot 的冲突列表会给出每条指令的「所属插件」，且同一对冲突的两行属于**同一个插件**才算该插件自身重复注册。
   Super_AstrBot 只注册**一个**顶层指令 `sab`（别名 `superastrbot`），因此：

   - 若冲突行的插件名是 `Super_AstrBot` / `astrbot_plugin_Super_AstrBot` 且指令名是 `sab` → 与另一个也用了 `sab` 的插件撞名，改任一方指令名即可；
   - 若冲突行的插件名是别的插件（例如某个成本统计插件把同一批指令既以模块路径、又以展示名各注册了一次）→ **与本插件无关**，请向该插件反馈；
   - 本插件不会出现 `help` / `reset` / `status` 之类的顶层指令，所以不会与 AstrBot 内置指令冲突。

### 框架符号缺失对照

| 缺失符号 | 影响 |
|---|---|
| `TextPart` | 无法使用「临时内容块」注入，自动回退到系统提示词注入 |
| `ProviderRequest` | `on_llm_request` 钩子签名不匹配，记忆注入失效（需 AstrBot ≥ 4.24） |
| `StarTools` | 数据目录改为按 `data/plugin_data/<插件名>` 兜底推断 |
| `web_request` / `json_response` | 面板后端不可用（核心功能不受影响） |

---

## 架构

```
main.py                 装配层：注册钩子 / 指令 / Web API（薄）
└── super_astrbot/
    ├── spec/           规格与契约（能力注册表、作用域、错误类型）
    ├── harness/        ★ 唯一接触 AstrBot 的层（协议 + 适配 + 兼容探测）
    ├── loop/           循环控制：TaskScope / Scheduler / ConcurrencyGate / LLMBudget
    ├── storage/        持久层：sqlite3 连接、迁移、仓储、可恢复写日志
    ├── support/        共享工具（分词、相似度）
    ├── memory/         记忆域：模型 / 配置 / 分层检索 / 生命周期 / 服务
    ├── journal/        周记域
    ├── learning/       自我学习域（反思 + 审批）
    ├── commands/       指令逻辑（不依赖 AstrBot）
    └── web/            面板后端适配
```

依赖方向自上而下单向：`memory/journal/learning` → `loop` → `storage` → `harness`。
框架版本变动只需改 `harness/` 与 `main.py`。完整规格见 [SPEC.md](SPEC.md)。

---

## 开发与测试

```bash
# 运行测试（无需 AstrBot 环境，使用最小替身）
python -m pytest tests -q

# 代码规范（提交前必做）
python -m ruff check .
python -m ruff format .
```

测试覆盖：能力依赖解析、迁移幂等、FTS 与降级、事务回滚、可恢复写日志、RRF 融合与加权、
去重、端到端召回、缓冲隔离、全局作用域可见性、注入与清理、任务作用域/代次令牌/超时、
调度幂等与跨重载、预算限流、并发门闸、反思解析与闭环、审批流程、指令权限与流程，
以及**应用容器整体集成**（启动 → 注入 → 采集 → 状态 → 卸载）。

> 本地 AstrBot 源码仅用于阅读框架 API（`astrbot/core/**`），实际运行验证在服务器上进行。

---

## 隐私与数据

- 所有数据保存在本地 SQLite（`data/plugin_data/astrbot_plugin_Super_AstrBot/super_astrbot.db`）。
- 插件不上报任何遥测，不访问外部网络（除调用你在 AstrBot 中配置的模型提供商）。
- 记忆内容会随检索注入到模型请求中（这是功能本身）；若不需要，可将「记忆注入方式」设为「仅检索不注入」。
- 可随时用 `/sab reset confirm` 清空当前作用域数据。

---

## 致谢与灵感来源

本插件的设计思路来自对以下开源项目的学习（**仅借鉴设计思路与公开 API 用法，未复制其代码**）：

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) — 插件规范与公开 API
- [astrbot_plugin_livingmemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory) — 混合检索、RRF 融合、记忆生命周期、可恢复写日志
- [astrbot_plugin_memory_beyond](https://github.com/AlanBacker/astrbot_plugin_memory_beyond) — 注入不污染历史、摘要思路、零依赖取向
- [astrbot_plugin_group_chat_plus](https://github.com/Him666233/astrbot_plugin_group_chat_plus) — 判断型 AI 的推理协议与结果归一化解析
- [astrbot_plugin_self_learning](https://github.com/NickCharlie/astrbot_plugin_self_learning) — 审批制写入、成本控制、工程韧性
- [astrbot_plugin_proactive_chat](https://github.com/Pancakes-Labs/astrbot_plugin_proactive_chat) — 双轨调度与幂等
- [astrbot_plugin_livingmemory_ext](https://github.com/yulimfish/astrbot_plugin_livingmemory_ext) — 幂等定时任务、跨插件只读协作

逐项借鉴映射见项目开发笔记《Super_AstrBot 项目学习分析文档》（未随仓库分发）。

---

## 许可证

本项目采用 **GNU Affero General Public License v3.0（AGPL-3.0）**，全文见 [LICENSE](LICENSE)。
Copyright (C) 2026 BUXIN-A。

> 若你需要以不同许可证分发，或希望移植本项目代码，请先阅读 AGPL-3.0 的条款。

---

## 待办

- [ ] 在服务器上验证：控制台六个分区的数据加载、向量路启用后的检索效果、指令冲突列表中不再出现本插件

## 后续规划

- P1.5 Agent 函数工具：`memory_search` / `memory_write`，让 Bot 主动读写记忆
- P2 上下文治理：token 估算、工具/图片历史占位、摘要水位线
- P3 群聊语义：读空气决策、注意力、冷却、并发消息合并
- P4 主动交互：双轨调度、竞态保护、免打扰
- P5 拟人化学习：风格 few-shot、群组黑话、好感度（审查制）
