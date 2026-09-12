# Changelog

本文件记录 Super_AstrBot 的版本变更。版本号以 `metadata.yaml` 为唯一来源。

## [0.5.0] - 未发布

新增「上下文治理」（路线 P2）：请求级限制送入模型的内容规模。**默认关闭**。

### 新增

- **请求级上下文治理**（`context.governance`，默认关闭，可热切换）
  - 三段式水位线：估算 ≤ 上限 → 不干预；超限 → 先做零成本占位压缩（早期工具结果、
    历史图片替换为占位文本，复估达标即结束、不调用模型）；仍超 → 把更早历史压成一条摘要消息。
  - **不改写持久化对话历史**：只替换本次请求的 `contexts`（整体赋新 list，绝不原地修改消息字典），
    关闭能力或任意一步失败时行为与未安装插件一致。
  - 保护与约束：最近 `max(2, keep_recent)` 条永不压缩且逐字不变；切分点不得落在 `tool` 消息上
    （`_align_head()` 前移，避免切断 `tool_calls` 配对）；`contexts` 开头连续的 `system`
    消息（系统提示词）原样保留、摘要插在其后；待压缩区间含框架 `_checkpoint`
    消息时放弃摘要；摘要提示词只允许压缩、不得新增，产出带「不是用户指令」的免责声明。
  - 摘要缓存：KV 键 `ctx-summary:<session_key>`，记录 `covered` / `anchor` / `summary`；
    前缀未被裁剪时做**增量续写**，命中缓存则本轮不调用模型。
  - 降级：摘要调用失败 / 预算耗尽 / 产出为空 → 保留占位压缩成果，退化为「仅占位压缩」；
    请求对象不可写回时如实报告「已放弃治理」，不做原地改写。
- **token 估算**（`support/tokens.py`，零第三方依赖、不引入 tiktoken）：CJK 按 1 token/字，
  其余非空白字符按 4 字符 1 token；每条消息计固定开销，另计 `tool_calls` 体积；
  图片 / 音频部件按固定值计。仅用于「是否越过水位线」的判定，宁可略高估。
- **配置与面板**：新增 `context` 配置分组（开关、token 上限、保留条数、最少消息数、摘要模型）；
  控制台「功能」页新增 `context` 域分组；`/sab status` 与面板 `status()` 的 `context` 字段
  暴露开关状态、上限与最近一次治理结果。

### 测试

通过用例 159 项（v0.4.0 为 142）。新增 `tests/test_context.py` 16 项：token 启发式估算、
配置钳制、未启用 / 未超阈值跳过、仅占位压缩不调模型、摘要覆盖与缓存写入、增量续写、
保留区逐字不变、tool 配对对齐、前导系统提示词原样保留、checkpoint 跳过摘要、
摘要失败 / 预算耗尽降级、占位压缩在摘要失败时仍生效、请求不可写时放弃治理、
以及「传入消息字典未被原地修改」的不变式。

## [0.4.0] - 未发布

新增「Agent 函数工具」（路线 P1.5）：让 Bot 主动读写记忆。**默认关闭**。

### 新增

- **Agent 记忆函数工具**（默认关闭，需重载插件生效）
  - `sab_memory_search`：检索长期记忆，命中结果按既有格式化规则回给模型；无命中给出明确提示。
  - `sab_memory_write`：写入一条长期记忆，来源标记为 `agent`（面板显示「Agent 写入」），
    与自动采集、反思产出可区分。
  - 分层：`harness/tools.py` 只做接线（构造 `FunctionTool`、注册/注销），业务规则在
    `memory/agent_tools.py: AgentMemoryBackend`，两者以 `MemoryToolBackend` 协议解耦。
  - 硬性约束：写入门槛与 `/sab remember` 一致（`basic.admin_only_commands` 开启时仅管理员）；
    内容上限 400 字；`kind` 白名单；`importance` 钳制到 0.1~1.0；
    工具内异常一律吞掉并返回可读文本；检索命中累计 `access_count`（与注入路径一致）。
  - 降级：框架未提供 `FunctionTool` 或注册接口不可用时跳过注册并告警一次，其它能力不受影响。
  - 控制台「功能」页新增 `agent` 分组，该开关标注「需重载」。

### 兼容性说明

- 工具以 `FunctionTool(handler=...)` 构造：AstrBot 执行器优先调用 `handler`，其次子类
  `call()`，最后回退旧版 `run()`，因此同一份实现覆盖新旧几代执行路径。
- 注册优先走官方 `context.add_llm_tools(*tools)`，不可用时退化为写入
  `FunctionToolManager.func_list`；注册前按工具名去重，重载不会堆积同名工具。
- 刻意不做热切换（不调用 `deactivate_llm_tool`）：运行期停用在框架侧语义不稳定，
  一旦失效会出现「界面显示已关闭、模型仍能看到工具」的静默不一致。

### 测试

通过用例 142 项（v0.3.1 为 127）。新增 `tests/test_agent_tools.py`：工具 schema 构造、
参数规整（字符串数字与非法值）、工具内异常兜底、注册/去重/注销与降级、写入的权限门槛、
内容与类型校验、检索命中累计访问次数。

## [0.3.1] - 未发布

全量代码审查与加固：修复一个会造成数据丢失的作用域缺陷，并系统性消除存储层并发、
检索正确性、写放大与死代码问题。

### 修复

- **重置会话会连带删除全局记忆**（严重）
  `/sab reset confirm` 经 `MemoryService.reset_scope`，其中误用 `retrieval_scopes()` 取作用域集合；
  该函数的语义是「检索并集」，总会附加全局作用域，导致一次会话级重置会不可逆地清空全局共享记忆。
  现改为只作用于传入的单一作用域，并补回归测试锁定。

- **数据库读操作未纳入串行化锁**
  `Database.query/query_one/scalar` 绕过 `asyncio.Lock`，与 `transaction()`（持锁 + `BEGIN IMMEDIATE`）
  并发时可能读到未提交的中间态，也违背模块自述的不变量。现读路径同样持锁。

- **LIKE 查询未转义通配符**
  面板关键词与 FTS 降级路径直接把 `%{term}%` 拼进 `LIKE`，用户输入中的 `%` / `_`
  会被当作通配符放大匹配范围。现统一转义并声明 `ESCAPE`。

- **向量检索的作用域过滤时机错误**
  原先先从全库按 `updated_at` 取 `vector_max_scan` 条向量、再在应用层按作用域过滤；
  其它作用域的新向量会挤占扫描额度，导致当前作用域的向量可能一条都取不到（向量路静默失效）。
  现改为在 SQL 层与 `memories` 联结后按作用域过滤。

- **聊天记录类型判定在 API 缺失时可能误判**
  `is_group` 原先依赖 `(not is_private) and umo or group_id` 的运算优先级；当 `is_private_chat`
  不可用时会把私聊判成群聊。现以该 API 为唯一依据，缺失时才退化为按 `group_id` 判断。

- 面板「已配置」状态改用与能力解析一致的布尔转换，配置写成字符串 `"false"` 时不再误显示为已开启。
- `AstrBotInjector` 协议签名补齐 `prefer` 参数，与实现对齐。
- 框架符号解析失败时保留已读到的版本号，避免诊断信息退化为 `unknown`。

### 优化

- **消除写放大**：对话缓冲的「裁剪 + 过期清理」由每条消息两次 DELETE 改为按写入条数节流；
  批量归档/遗忘的索引与向量删除由 `2N` 次单条 SQL 改为 `IN` 批量删除。
- **配置读取去重**：`as_bool` / `as_int` / `as_float` / `as_str` 收敛到 `spec/capabilities.py`，
  删除 memory / learning / journal 三处重复实现，保证同一份 schema 在各域的边界语义一致。
- **死代码清理**：移除未使用的 `MutableFlag`、`unwrap`、`VectorRetriever._log_debug`、
  `MemoryRepository.iter_active`、`decode_tags`/`coerce_tags`，以及 `update_fields` 中不可达的 `tags` 分支。
- 嵌入能力探测复用已装配的 harness 网关，避免探测实例与实际检索实例结论分叉。
- `MemoryItem.from_row` 只取一次 `sqlite3.Row.keys()`；前端 `navigate()` 缩进统一；
  面板读取私有点改为走 `MemoryService` 的公开方法。

### 注释

- 清理与实现矛盾或叙述开发史的注释：`TaskScope` 用法示例改为 `is not ABANDONED`、
  jieba 分词模式说明改为「精确模式」、移除对已不存在模块的引用。

### 测试

通过用例 127 项（v0.3.0 为 119）。新增覆盖：重置不越界删除全局记忆、LIKE 通配符转义、
向量按作用域扫描、遗忘同时清理索引与向量、配置值转换语义、字符串布尔的能力状态、
`is_group` 判定优先级与降级。

## [0.3.0] - 未发布

新增「功能管理界面」；并修复服务器日志与面板暴露的两个真实缺陷。

### 新增

- **控制台「功能」分区**：图形化开关，覆盖全部能力（总开关、调试日志、长期记忆、
  对话采集、群聊采集、私聊采集、关键词检索、向量检索、反思自学习、周记、周度洞察）。
  - 开关即时写入插件配置并**热应用**（检索路增删、调度任务启停、领域配置同步）；
  - 总开关标注「需重载」并禁用开关（涉及整条运行时链路，热切换不安全）；
  - 运行环境不支持的能力（如未配置嵌入模型提供商时的向量检索）禁用并说明原因；
  - 依赖未开启的项给出提示，避免开启后被能力解析静默回退；
  - 概览页的能力指示可直接点击跳转到本分区。
- **插件图标**：新增 `logo.png`（WebUI 插件列表使用）与面板品牌图标 `pages/dashboard/logo.svg`。
- 后端新增接口 `features`（功能清单）与 `feature-toggle`（切换开关）。

### 修复

- **调度器误报「未完成执行」**（由服务器日志暴露）
  日志出现 `任务 reflection-scan 未完成执行（超时或令牌失效），将按失败处理`。
  根因：调度器用「返回值为 `None`」判断任务被放弃，而**所有任务正常结束时也返回 `None``，
  于是每次运行都被误判。现引入显式哨兵 `ABANDONED`：
  `TaskScope.run` 仅在停止/超时/令牌失效时返回该哨兵，调度器据此判定跳过，
  正常结束不再误报（并新增回归测试锁定该语义）。

- **向量能力永久不可用**（由控制台诊断暴露）
  服务器已配置 `ollama_embedding`，但状态始终显示「向量能力不可用（运行环境不支持）」。
  两个叠加原因：
  1. AstrBot 的生命周期是「先加载插件、后初始化 ProviderManager」，插件启动阶段的探测
     必然是空的；
  2. 探测失败结果被永久缓存（`_resolved = True`），后续不再重试。
  现改为：探测失败**不缓存**、允许重试；并新增 `@filter.on_astrbot_loaded()` 钩子在框架
  完全就绪后复检能力，自动挂上向量检索路（日志会打印「能力状态已更新」与当前检索路）。

- `provider_manager` 若为会抛异常的属性/代理对象，`getattr` 不会吞异常导致枚举外泄；
  现已显式包裹。

### 测试

通过用例 119 项（v0.2.0 为 104）。新增覆盖：哨兵与 `None` 的语义区分、调度器跳过判定、
`set_path` 写路径、能力元数据完整性、功能开关热切换与依赖级联、向量能力恢复、
面板功能开关 UI 与图标存在性。

## [0.2.0] - 未发布

修复 v0.1.0 在真实服务器上暴露的三个问题，并重建控制台界面。

### 修复

- **指令面收敛，消除冲突可能**（问题 1）
  `main.py` 不再用 `@filter.command_group("sab")` 注册十余条子指令，改为**单一顶层指令 `sab`**（别名 `superastrbot`），子命令由 `commands/parser.py` 自行解析。
  原因：AstrBot 的指令冲突检测以指令「完整名」为键，注册项越多冲突面越大；收敛后只剩一个名字，且不再依赖框架的 `GreedyStr`/参数推导行为。
  另注：用户截图中 10 对冲突全部来自 `astrbot_plugin_cost_control` 自身重复注册（同一指令分别以模块路径与展示名 `Token成本控制` 各出现一次），与 Super_AstrBot 无关 —— 我们的子指令完整名是 `"sab xxx"`，不会与顶层 `help`/`reset` 撞名。

- **嵌入模型下拉列表改为真实数据**（问题 2）
  `_special: "select_provider"` 在 AstrBot 中被硬编码为 `chat_completion`，且框架不存在嵌入模型专用的 special 值，因此配置页列出的必然是对话模型。
  现改为**运行时把真实的嵌入模型列表注入 schema**（`harness/schema_options.py` + `AstrBotEmbeddingGateway.list_providers()`），并每 10 分钟重新注入一次（保存插件配置会触发重载并重建 `AstrBotConfig`，注入会丢失）。
  安全降级：**没有嵌入提供商时不注入 options**，字段保持文本框，可手动填写 ID —— 避免变成「只有自动选择一项」的不可输入下拉框。

- **控制台「加载失败：Failed to fetch」**（问题 3）
  两个叠加原因：① 原实现是 classic 内联脚本，在 AstrBot 注入 bridge SDK **之前**执行，`window.AstrBotPluginPage` 为 null；② 兜底逻辑直接 `fetch`，而插件页运行在 sandbox iframe（无 `allow-same-origin`），跨源请求必然抛 `Failed to fetch`。
  现改为：脚本使用 `<script type="module">`（天然 defer，保证在 bridge 之后执行）+ **完全移除 fetch、只经 bridge 调用**。

### 变更

- **控制台界面重建**（融合 livingmemory 与 self_learning 的做法）
  - 侧边栏 hash 路由 + 六个分区：总览 / 记忆 / 检索 / 周记 / 待审 / 系统；
  - 总览：统计卡 + 能力开关 + 降级原因 + 最近记忆；
  - 记忆：状态/类型/关键词筛选 + 分页 + 点击查看详情（弹窗）；
  - 检索：可选会话 UMO，展示最终分与打分构成；
  - 待审：批准 / 驳回；
  - 系统：框架版本与符号诊断、任务调度、调用预算、嵌入提供商列表、重建索引；
  - 深/浅色主题（跟随 Dashboard 并可本地记忆）、Toast 反馈、桥接 i18n；
  - 样式与脚本完全自包含，无 CDN / 无外部字体图标；HTML 转义覆盖五个字符以防存储型 XSS。
- 新增 `.astrbot-plugin/i18n/{zh-CN,en-US}.json`，使页面在 WebUI 中的标题与描述正确显示。
- 后端新增/增强接口：`memories`（状态/类型/关键词筛选 + 总数）、`memory`（单条详情）、`maintenance`（重建索引），`overview` 增加框架诊断与嵌入提供商列表。
- 移除 `metadata.yaml` 的 `pages` 声明：AstrBot 通过扫描 `pages/<name>/index.html` 自动发现，无需声明。

### 测试

新增指令解析、schema 注入、嵌入模型枚举、配置页注入链路等测试；总计通过用例增至 100+。

## [0.1.0] - 未发布

首个可用版本：完成「规格 → Harness → 循环控制 → 持久层 → 记忆闭环 → 周记 → 命令 → 面板」全链路。

### 新增

- **规格层**：`SPEC.md` 作为唯一权威规格；`super_astrbot/spec/` 提供能力注册表（含依赖解析与环境降级覆盖）、作用域契约、统一错误与结构化降级结果。
- **Harness 层**：全项目唯一接触 AstrBot 的位置。包含协议定义、框架符号宽容探测（缺符号只降级不崩溃）、事件纯数据视图、宿主适配（数据目录/配置/日志/KV/主动发送）、LLM 网关（预算+超时+错误归一化）、Embedding 网关（不可用即让位）、注入器（`extra_user_content_parts` + `mark_as_temp`，宿主不支持时回退系统提示词）。
- **循环控制层**：`TaskScope`（停止感知、代次令牌防迟到结果、任务收敛）、`Scheduler`（日/周期任务，当日幂等、跨重载不重复、启动补偿）、`ConcurrencyGate`（按 key 读共享/写独占）、`LLMBudget`（每日上限 + 并发上限）。
- **持久层**：基于标准库 `sqlite3`（零第三方依赖）的异步封装；版本化增量迁移、迁移前自动备份、WAL、原子事务、可恢复写日志（启动重放）。
- **记忆域**：记忆模型与状态机、分层自适应检索（FTS5 关键词路 + 可选向量语义路 + RRF 融合 + 相关性/重要性/新近度多因子加权 + 词袋去重）、记忆生命周期（写入带写日志、索引维护、重要度指数衰减、归档、缓冲清理、索引重建）。
- **周记域**：周记写入（同时落 `journals` 与 `memories`）、查询、周度反思原料输出。
- **自我学习域**：反思触发判定（缓冲量 / 时间间隔 / 冷却三重约束）、结构化产出解析与强校验、审批制写入、待审队列、反思运行留痕。
- **指令**：`/sab help|status|search|why|remember|journal|journals|review|approve|reject|reset|reindex`。
- **面板**：`pages/dashboard/index.html` + 6 个后端接口（总览/记忆/检索/周记/待审/审批），同时注册原始大小写与全小写两套路由前缀以规避大小写匹配问题。
- **测试**：68 项，覆盖能力解析、迁移幂等、FTS 与降级、事务回滚、写日志、融合与加权、去重、端到端召回、缓冲隔离、注入与清理、任务作用域/令牌/超时、调度幂等与跨重载、预算、并发门闸、反思解析与闭环、审批、指令权限，以及**应用容器整体集成**（启动→注入→采集→状态→卸载）。

### 修复（开发过程中发现并修正的缺陷）

- **对话缓冲未生效**：`MemoryRepository.insert` 硬编码 `status='active'`，导致自动采集的对话片段被当作正式记忆参与检索，破坏「缓冲只作反思原料」的核心设计。现已支持显式传入状态。
- **待审队列顺序不确定**：`list_pending` 仅按 `created_at` 排序，同一时间戳的多条记录顺序随机，会导致审批错对象。现增加 `id` 作为次级排序键。
- **全局统计运行时报错**：`MemoryService.stats_all` 使用了未导入的 `STATUS_PENDING` / `STATUS_ARCHIVED` 常量。已补齐导入。
- **维护任务缺导入**：`MemoryLifecycle.maintain` 使用了未导入的 `ScopeType`。已补齐导入。
- **残留无效代码**：`app.py` 保留了引用已移除 `asyncio` 导入的空函数。已删除。
- **应用容器无法注入数据目录**：导致整条链路无法在无 AstrBot 环境下测试（也即长期未被验证）。现 `SuperAstrBotApp` 与 `AstrBotHost` 支持显式 `data_dir`。

### 已知限制

- 面板与向量检索路径尚未在真实 AstrBot 实例上实测（本地无运行环境）。
- 尚未向 Agent 暴露函数工具（`memory_search` / `memory_write`），列为下一项工作。

### 其它

- 采用 AGPL-3.0 许可证（`LICENSE`）。
- 仓库：<https://github.com/BUXIN-A/astrbot_plugin_Super_AstrBot>；平台面向 QQ（aiocqhttp / qq_official）。
