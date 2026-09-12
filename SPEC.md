# Super_AstrBot 规格定义（SPEC）

> 本文件是本插件**唯一权威规格**。任何功能实现、配置新增、接口变更都必须先在此登记，再落代码。
> 配套文档：`docs/Super_AstrBot_项目学习分析文档.md`（外部，位于仓库上一级）为借鉴来源与依据。

---

## 1. 目标与非目标

### 1.1 目标

为 AstrBot 原生 AI 能力提供**可独立开关、可降级、可观测**的增强，聚焦三条主线：

| 编号 | 主线 | 说明 |
|---|---|---|
| G1 | 持久化数据 | 一套自愈、幂等、跨重启一致的 SQLite 持久层，承载记忆、周记、画像与运行状态 |
| G2 | AI 代理机器人 | 对话前召回相关长期记忆并注入（不落史、不破坏前缀缓存），对话后按需沉淀 |
| G3 | 自我学习 | 反思机制：定期回顾对话与周记，产出洞察并写回长期记忆（可选人工审批） |

### 1.2 非目标（明确不做，防止范围蔓延）

- ❌ 不修改、不 fork AstrBot Core；不接管 AstrBot 原生回复链路（只做增强与补充）。
- ❌ 不重复实现其他插件的完整能力（如群聊读空气决策、主动消息调度留待后续阶段）。
- ❌ 不自动安装 pip 依赖、不做破坏性数据库迁移。
- ❌ 不做跨 Bot 实例的实时记忆同步。
- ❌ 不在插件目录写运行时数据（一律写 AstrBot `data/` 目录）。

---

## 2. 架构分层与依赖方向

```
┌──────────────────────────────────────────────────────────────┐
│ main.py  （装配层：仅注册命令/钩子/工具/Web API，不含业务逻辑）      │
└───────────────┬──────────────────────────────────────────────┘
                │ 组装
┌───────────────▼──────────────────────────────────────────────┐
│ super_astrbot/app.py  （应用容器：生命周期编排、依赖注入）          │
└───┬──────────────────────────────────────────────────────────┘
    │
    ├─ 业务域（禁止 import astrbot）
    │    memory   长期记忆与检索        journal   周记 / 现实记忆
    │    learning 反思式自我学习        context   请求级上下文治理
    │    group    群聊语义（读空气）     proactive 主动交互（双轨调度）
    │    persona  拟人化学习（风格/黑话/好感度）
    │    commands 指令门面              web       面板 API
    │
┌───▼──────────────────────────────────────────────────────────┐
│ loop/  （循环控制：TaskScope / Scheduler / Gate / Budget）       │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│ storage/  （持久层：连接 / 迁移 / 原子写 / 仓储）                  │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│ harness/  （唯一允许 import astrbot 的层；对外只暴露 Protocol）      │
└──────────────────────────────────────────────────────────────┘
```

**硬性规则**

1. 只有 `super_astrbot/harness/`、插件根 `main.py`、以及框架适配型的 `super_astrbot/web/api.py` 允许 `import astrbot.*`；其余模块仅依赖 `harness/protocols.py` 中的抽象接口。`main.py` 与 `web/api.py` 属于「装配/适配」性质，本身不承载业务规则。
2. 业务域之间通过接口交互，不互相 `import` 具体实现（learning → 通过 `MemoryService` 协议写入，不直接操作记忆仓储）。
3. `spec/` 只声明契约与能力清单，零副作用、可被任何层引用。

---

## 3. 能力清单（Capability Registry）

`spec/capabilities.py` 为代码化登记表，与 `_conf_schema.json` 的布尔开关一一对应。

| key | 所属域 | 默认 | 前置依赖 | 热切换 | 说明 |
|---|---|---|---|---|---|
| `basic.enabled` | 基础 | on | — | ❌ | 总开关；关闭时所有钩子直接返回 |
| `basic.debug_log` | 基础 | off | — | ✅ | 输出检索打分、注入字符数等排障细节 |
| `memory.enabled` | 记忆 | on | `basic.enabled` | ✅ | 长期记忆总开关 |
| `memory.capture` | 记忆 | on | `memory.enabled` | ✅ | 自动采集对话写入记忆 |
| `memory.capture_groups` | 记忆 | on | `memory.capture` | ✅ | 群聊消息是否进入对话缓冲 |
| `memory.capture_private` | 记忆 | on | `memory.capture` | ✅ | 私聊消息是否进入对话缓冲 |
| `memory.fts_enabled` | 记忆 | on | `memory.enabled` | ✅ | 关键词全文检索路 |
| `memory.vector_enabled` | 记忆 | on | `memory.enabled` + 存在 Embedding Provider | ✅ | 向量语义检索路；不可用时静默降级 |
| `reflection.enabled` | 自我学习 | on | `memory.enabled` | ✅ | 反思式自我学习 |
| `journal.enabled` | 周记 | on | `basic.enabled` | ✅ | 周记现实记忆 |
| `journal.weekly_reflection` | 周记 | on | `journal.enabled` + `reflection.enabled` | ✅ | 周度洞察生成 |
| `agent.memory_tools` | Agent 工具 | **off** | `memory.enabled` | ❌ | 向模型暴露记忆检索/写入函数工具（见第 9 节） |
| `context.governance` | 上下文治理 | **off** | `basic.enabled` | ✅ | 请求级 token 治理：占位压缩 + 历史摘要（见第 10 节） |
| `group.enabled` | 群聊语义 | **off** | `basic.enabled` | ✅ | 读空气插话 + 冷却配额 + 并发合并（见第 11 节） |
| `proactive.enabled` | 主动交互 | **off** | `basic.enabled` | ✅ | 双轨调度主动消息 + 免打扰（见第 12 节） |
| `persona.style` | 拟人化学习 | **off** | `basic.enabled` | ✅ | 风格模仿：邻接对样本 + few-shot 注入（见第 13 节） |
| `persona.jargon` | 拟人化学习 | **off** | `basic.enabled` | ✅ | 群内用语：统计预筛 + 词义推断 + 理解注入（见第 13 节） |
| `persona.affinity` | 拟人化学习 | **off** | `basic.enabled` | ✅ | 社交好感度：规则判定 + 模型兜底 + 语气指引（见第 13 节） |

**依赖解析规则**：某能力的前置不满足时，该能力视为关闭，并记录一条 `DegradedReason`（只告警一次）。

**热切换规则**：`hot_reloadable=False` 的能力（当前仅 `basic.enabled`）写入配置后**不立即生效**，
控制台会提示「需重载插件」，并在界面上禁用直接切换；其余能力均可运行时热应用。
运行时相关能力（`memory.vector_enabled`）还需 `overrides` 通过环境探测，
环境不支持时控制台禁用开关并说明原因。

**配置写入**：控制台开关经 `spec/capabilities.py: set_path()` 按点号路径写入配置，
自动创建缺失的中间层，并拒绝覆盖异常结构（返回 `False` 而非破坏配置）。

---

## 4. 接口契约

### 4.1 Harness 协议（`harness/protocols.py`）

```python
class Host(Protocol):
    def data_dir(self) -> Path: ...
    def app_config(self) -> Mapping[str, Any]: ...  # 插件配置
    def log(self) -> LoggerLike: ...
    def now(self) -> float: ...  # Unix 秒
    async def kv_get(self, key, default=None): ...
    async def kv_put(self, key, value) -> None: ...
    async def kv_delete(self, key) -> None: ...
    async def send_message(self, umo: str, text: str) -> bool: ...  # 失败返回 False


class LlmGateway(Protocol):
    async def chat(
        self,
        *,
        prompt: str,
        system_prompt: str | None = None,
        contexts: Sequence[ChatMessage] | None = None,
        provider_id: str | None = None,
        session_key: str | None = None,
        timeout: float | None = None,
        purpose: str = "general",
    ) -> LlmResult: ...  # 失败抛 LlmError；预算耗尽抛 BudgetExhausted


class Injector(Protocol):
    def compose(self, blocks: Sequence[str]) -> str: ...
    def inject(
        self, target: Any, blocks: Sequence[str], *, prefer: str = "auto"
    ) -> InjectResult: ...
    def clear(self, target: Any) -> int: ...


class MemoryToolBackend(Protocol):
    # Agent 函数工具的业务回调（见第 9 节）；harness 只依赖本协议
    async def memory_search(
        self, *, view: EventView, query: str, limit: int | None = None
    ) -> str: ...
    async def memory_write(
        self, *, view: EventView, content: str, kind: str = "fact", importance: float = 0.6
    ) -> str: ...


@dataclass(frozen=True)
class GroupSignals:
    # 群消息的额外信号（读空气决策输入，见第 11 节）
    self_id: str = ""
    mentioned: bool = False  # 被 @ 或引用了 Bot 的消息
    wake: bool = False  # 框架已判定应唤醒（wake 前缀 / @ / 引用）


@dataclass(frozen=True)
class GroupDecision:
    # 群消息处理决策，由业务域给出、由 harness 落地到事件对象
    action: str = "reply"  # interject | reply | silent
    reason: str = ""
    attention: float = 0.0
    text: str = ""  # 插话时写回事件的消息文本（并发合并结果）
    merged: int = 0  # 本次合并的消息条数
```

### 4.2 事件视图（`harness/protocols.py: EventView`）

业务层**不持有** `AstrMessageEvent`，只消费其纯数据快照：

```python
@dataclass(frozen=True)
class EventView:
    umo: str  # 统一消息源 platform:type:session
    platform: str
    session_id: str
    is_group: bool
    group_id: str
    sender_id: str
    sender_name: str
    text: str
    timestamp: float
    is_admin: bool
    stopped: bool  # 事件是否已被 /stop
```

### 4.3 循环控制契约（`loop/`）

| 组件 | 契约 |
|---|---|
| `TaskScope` | `run(awaitable_factory, *, timeout=None, token=None)`；支持 `cancel()`、`is_stopped()`；**迟到结果必须靠代次令牌丢弃**；被放弃时返回哨兵 `ABANDONED`（而非 `None`），调用方必须用 `is ABANDONED` 判定，禁止用 `result is None` 推断放弃 |
| `Token` | `scope_identity + generation`；`is_current(token)` 判定结果是否仍有效 |
| `Scheduler` | `every(seconds, job, *, key, run_immediately=False)`、`daily_at(hour, minute, job, *, key, weekday=None)`、`start()`、`shutdown()`；**同一 key 幂等**，跨重载不重复触发，且随插件卸载全部取消 |
| `ConcurrencyGate` | `read(key)` / `write(key)` 异步上下文管理器；同一 key 读共享、写独占 |
| `Budget` | `try_acquire(purpose) -> bool`；按日计数，超限拒绝；`stats()` 供面板展示 |

**硬性要求**：所有后台任务必须经 `TaskScope` 或 `Scheduler` 创建；插件 `terminate()` 时必须能全部收敛（无游离任务）。

> `ABANDONED` 哨兵的必要性：任务正常结束时也可能返回 `None`，若用 `result is None` 判断放弃，
> 所有正常任务都会被误判为超时（调度器会误报 WARN 并计入跳过）。哨兵把「放弃」与「正常返回」显式区分开。

### 4.4 存储契约（`storage/`）

- 数据库位于 `<plugin_data_dir>/super_astrbot.db`，启用 `WAL` + `busy_timeout`。
- **所有**数据库访问（读与写）经同一把 `asyncio.Lock` 串行化：单连接 + 显式事务，
  杜绝事务交错，也避免读操作在事务进行中读到未提交的中间态。
- 迁移由 `storage/migrations.py` 版本化驱动（`schema_version` 表），**只做增量、不删列**；迁移前自动备份。
- 写操作必须经 `Database.transaction()`；跨表写入需可恢复（记录写日志，启动时重放未完成项）。
- 模糊查询（面板关键词、降级 LIKE 检索）必须转义 `%` / `_` 并声明 `ESCAPE`，
  避免用户输入被当作通配符放大匹配范围。

---

## 5. 数据契约（核心表）

| 表 | 用途 | 关键字段 |
|---|---|---|
| `schema_version` | 迁移版本 | `version`, `applied_at` |
| `memories` | 记忆条目 | `id`, `scope_type`, `scope_id`, `content`, `kind`, `importance`, `confidence`, `source`, `created_at`, `updated_at`, `last_access_at`, `access_count`, `status` |
| `memory_index` | FTS5 虚拟表（关键词检索） | `content`, `tokens` |
| `memory_vectors` | 向量（可选路） | `memory_id`, `model_fingerprint`, `dim`, `vector`(BLOB) |
| `memory_links` | 记忆关联（预留图谱） | `src_id`, `dst_id`, `relation`, `weight` |
| `journals` | 周记 | `id`, `scope_type`, `scope_id`, `content`, `tags`(JSON), `emotion`, `event_time`, `created_at` |
| `reflection_logs` | 反思运行记录 | `id`, `scope_type`, `scope_id`, `started_at`, `finished_at`, `status`, `produced`, `error` |
| `pending_reviews` | 待审记忆（审批模式） | `id`, `payload`(JSON), `created_at`, `status` |
| `write_ops` | 可恢复写日志 | `op_id`, `step`, `payload`(JSON), `status`, `updated_at` |
| `kv_state` | 运行状态（节流/游标/幂等） | `key`, `value`(JSON), `updated_at` |

**作用域语义**：`scope_type ∈ {session, user, global}`；`scope_id` 为对应 umo / user_id / `"*"`。检索时按「当前会话 + 当前用户 + 全局」三层并集召回。

> **写入与删除只作用于单一作用域**：`retrieval_scopes()` 表达的是「检索并集」（总会附带全局兜底），
> 因此禁止用于重置/清理路径 —— 否则一次会话级 `/sab reset` 会连带删掉全局共享记忆。

**记忆状态机（决定是否参与检索）**：

| status | 含义 | 参与检索 | 建索引 |
|---|---|---|---|
| `buffered` | 自动采集的对话片段，仅作反思原料 | ❌ | ❌ |
| `active` | 正式记忆 | ✅ | ✅ |
| `pending` | 待人工审批（写入 `pending_reviews`） | ❌ | ❌ |
| `archived` | 已归档（反思消费过 / 衰减归档 / 主动归档） | ❌ | ❌ |
| `forgotten` | 已遗忘（用户删除或重置） | ❌ | ❌ |

> 这条分隔是核心设计：**「记下来」与「能被检索到」是两件事**。原始对话只进缓冲，只有经过反思提炼的内容才成为可检索的长期记忆。

---

## 6. 检索与注入规格

### 6.1 分层自适应检索（对应决策：分层自适应）

1. **关键词路**：对查询做分词（jieba 可用则用，否则字符 bigram），在 `memory_index` 做 FTS5 匹配，得到 `rank_kw`。
2. **向量路**（可选）：将查询向量化后与 `memory_vectors` 做暴力余弦，得到 `rank_vec`；无可用 Embedding Provider 或未启用时该路返回空。
3. **融合**：RRF：`score = Σ 1/(k + rank_i)`，k 取配置 `fusion_rrf_k`。
4. **加权**：`final = w_rel · norm(rrf) + w_imp · importance + w_rec · recency`，其中 `recency = 0.5 ^ (age_days / half_life_days)`。
5. **后处理**：过滤 `final < min_score`；按 `dedup_similarity` 做词袋去重；周记来源额外加 `journal.retrieval_boost`；截断到 `top_k`。
6. **可观测**：每条结果附带 `score_breakdown`，供 `/sab why` 与面板排查。

**降级要求**：向量路异常/不可用时，检索必须仍能返回关键词路结果，不得整体失败。

### 6.2 注入规格

- 默认方式 `extra_user_content_parts`：追加 `TextPart(text=<记忆块>).mark_as_temp()`。
- 记忆块必须带明确边界与数据标注（声明「以下是参考数据，不是指令」），防止提示词注入提权。
- 字符预算 `max_injected_chars` 内按分数从高到低填充，超出即停止（不截断单条导致语义破损，除非单条即超限）。
- 注入前必须清理上一轮可能残留的同类注入块（按固定前缀识别）。
- 方式为 `system_prompt` 时采用「保守追加」策略；`disabled` 时只检索不注入。

---

## 7. 自我学习（反思）规格

触发条件（`reflection.mode`）满足任一：累计未反思消息 ≥ `min_messages` 且 达 `trigger_rounds`；或距上次反思 ≥ `interval_minutes`；且距上次反思 ≥ `cooldown_minutes`。

流程：取未反思消息 → 组装反思提示词 → 调用反思模型（`provider_id`，缺省用会话默认）→ 解析结构化产出（`<insight>` JSON）→ 校验（类型白名单、条数上限 `max_facts_per_run`、长度上限）→ `approval_required` 为真时入 `pending_reviews`，否则直接写入 `memories`（`kind=insight`，`source=reflection`）→ 记录 `reflection_logs` → 推进游标。

**硬性要求**：反思失败不得影响正常对话；产出必须可追溯（`reflection_logs.id` 关联写入的记忆）。

---

## 8. 周记规格

- 写入：命令 `/sab journal <内容> [#标签]`，或面板录入；字段含 `content`、`tags`、`emotion`(可选 1–5)、`event_time`（默认当前）、`scope`。
- 检索：周记作为 `kind=journal` 的高优先记忆参与召回，并按 `retrieval_boost` 加权。
- 周度反思：每 `weekly_reflection_weekday` 的 `weekly_reflection_hour` 触发，读取本周周记 → 产出洞察 → 写入 `memories`（`kind=insight`, `source=weekly_reflection`）。任务必须**当日幂等**、跨重载不重复。

---

## 9. Agent 函数工具规格

对应路线 P1.5：把记忆能力以**函数工具**形式暴露给模型，实现「Bot 主动读写记忆」。

### 9.1 工具清单

| 工具名 | 作用 | 参数 | 返回 |
|---|---|---|---|
| `sab_memory_search` | 检索长期记忆 | `query`（必填）、`limit`（1–20，可选） | 格式化后的记忆列表；无命中返回明确提示 |
| `sab_memory_write` | 写入一条长期记忆 | `content`（必填，≤400 字）、`kind`（fact / insight / preference）、`importance`（0.1–1.0） | 成功回执（含记忆 ID）或可读的拒绝原因 |

工具名必须固定：`ToolSet.openai_schema()` 按名字排序生成定义，改名会破坏模型侧前缀缓存。

### 9.2 分层与依赖

- `harness/tools.py` 只做**接线**（构造 `FunctionTool`、注册 / 注销），不实现业务规则；
- 业务规则由 `memory/agent_tools.py: AgentMemoryBackend` 实现，经 `MemoryToolBackend`
  协议与 harness 解耦（harness 不 import `memory`）；
- 工具用 `FunctionTool(handler=...)` 构造：AstrBot 执行器优先调用 `handler`，
  其次才是子类的 `call()`，最后才回退旧版 `run()`；`handler` 是这几代执行路径的最小公约数。

### 9.3 硬性约束

1. **默认关闭**：开启会改变所有会话的模型行为并占用上下文，属「非侵入式增强」的例外，
   必须由用户显式开启（`agent.memory_tools`）。
2. **需要重载**：`hot_reloadable=False`。运行期增删已注册工具在框架侧没有稳定 API，
   故不做热切换，避免「界面显示已关闭、模型仍能看到工具」的静默不一致。
3. **写入门槛与命令一致**：`basic.admin_only_commands` 开启时仅管理员可通过工具写入，
   防止普通成员绕过 `/sab remember` 的限制。
4. **失败不打断对话**：工具内任何异常都被吞掉并返回可读文本。
5. **读取即访问**：检索命中的记忆与注入路径一致地累计 `access_count`，
   否则高频被工具使用的记忆会因计数为 0 而被衰减归档。
6. **来源可追溯**：`memory_write` 写入的记忆 `source='agent'`，与自动采集 / 反思区分。
7. 框架未提供 `FunctionTool` 或注册接口时，降级为「不注册工具」并告警一次，不影响其它能力。

---

## 10. 上下文治理规格

对应路线 P2。目标：在**不改写持久化对话历史**的前提下，让本次请求送入模型的内容不超出预算，
并优先丢弃信息密度最低的部分。

### 10.1 为什么是请求级

AstrBot 的对话历史是用户资产（面板可查看、`/reset` 可管理），就地改写不可逆且无法回滚；
本模块只替换**本次请求**的 `contexts`（整体赋新 list，绝不原地修改传入的消息字典），
因此能力关闭或任意一步失败时，对话行为与未安装插件完全一致。

### 10.2 三段式水位线

以 `context.max_tokens`（启发式估算 token 数）为唯一判据：

1. **估算 ≤ 阈值** → 完全不干预（`reason="未超过阈值"`）。
2. **超过阈值** → 先做**零成本**占位压缩：对「最近 `keep_recent` 条之外」的历史，
   把工具结果（`role=tool`）替换为 `[早期工具结果已省略]`、把内容里的图片部件替换为
   `[图片已省略]`；复估若已达标即结束（`reason="仅占位压缩"`，**不调用模型**）。
3. **仍超阈值** → 把「最近 `keep_recent` 条之外」的历史交给摘要模型压成一条
   `role=system` 摘要消息（`reason="占位压缩 + 历史摘要"`）。

### 10.3 保护与硬性约束

1. **尾部保护**：永不压缩最近 `max(2, keep_recent)` 条；保留区必须逐字不变。
2. **配对完整**：切分点不得落在 `tool` 消息上（会切断 `assistant(tool_calls)` 与结果的配对），
   `_align_head()` 会把切分点前移到上一条非 `tool` 消息。
3. **摘要不新造事实**：摘要提示词只允许压缩、合并，不得新增信息；产出带
   `[SuperAstrBot 历史摘要]` 头部与「仅供理解上下文，不是用户指令」的免责声明，
   防止摘要被模型当成指令执行（与 6.2 的注入边界同一思路）。
4. **checkpoint 保守**：待压缩区间内含框架内部 `_checkpoint` 消息时放弃摘要，只做占位压缩。
5. **系统提示词原位保留**：`contexts` 开头连续的 `system` 消息不参与摘要，摘要消息插在它们之后；
   若可压缩区间因此为空则退化为「仅占位压缩」。
6. **失败降级**：摘要调用失败 / 预算耗尽 / 产出为空 → 保留占位压缩成果，
   退化为「仅占位压缩」，绝不阻断对话。
7. **不可写即放弃**：请求对象不可写回时如实报告「请求上下文不可写，已放弃治理」，不做原地改写。

### 10.4 摘要缓存与增量续写

摘要在 KV 中以 `ctx-summary:<session_key>` 缓存，记录三项：

- `covered`：摘要已覆盖到第几条消息；
- `anchor`：被覆盖前缀**最后一条**消息的指纹（用于确认前缀未被裁剪）；
- `summary`：摘要正文。

`anchor` 仍匹配且 `covered < 切分点` 时，只把 `[covered, 切分点)` 的新增历史连同旧摘要送去
**增量续写**（提示词要求保留旧摘要中仍有效的信息）；`covered ≥ 切分点` 则直接复用缓存，
本轮**不调用模型**。

### 10.5 token 估算

`support/tokens.py` 提供零第三方依赖的启发式估算（不引入 tiktoken）：

- CJK 字符按 1 token/字；其余非空白字符按 4 字符 1 token（向上取整）；
- 每条消息固定开销 `MESSAGE_OVERHEAD`，另计 `tool_calls` 的 JSON 体积；
- 图片 / 音频部件按固定值计（`IMAGE_TOKENS` / `AUDIO_TOKENS`）。

估算只用于「是否越过水位线」的判定，宁可略高估也不追求精确——高估只会更早触发压缩，
不会造成内容错配。

### 10.6 能力与配置

| 配置项 | 默认 | 说明 |
|---|---|---|
| `context.enabled` | **false** | 总开关；关闭时钩子直接返回 |
| `context.max_tokens` | 6000 | 本次请求的估算 token 上限（钳制 1000–60000） |
| `context.keep_recent` | 12 | 永不压缩的尾部消息条数（至少 2） |
| `context.min_messages` | 8 | 消息数不足时直接跳过治理 |
| `context.summary_provider_id` | 空 | 摘要专用模型；空则用会话默认模型 |

能力 `context.governance` 依赖 `basic.enabled`，**默认关闭**且可热切换：开启会改变所有会话
送入模型的内容形态，属「非侵入式增强」的例外，必须由用户显式开启。

---

## 11. 群聊语义规格

对应路线 P3。目标：让 Bot 在群聊里**有分寸地参与**——该接的话才接，接的时候少而完整。

### 11.1 唤醒边界（为什么必须自定义 filter）

AstrBot 的 `WakingCheckStage` 会：① 按 wake 前缀 / 被 @ / 引用 Bot 判定 `is_wake`；
② **任何插件的 event_filter 通过，也会把该消息标记为已唤醒**；③ 若最终 `is_wake` 为假则
`event.stop_event()`，消息不再进入后续阶段。而调用 LLM 需要 `is_at_or_wake_command` 为真
（`ProcessStage`）。

因此本插件注册一个**群消息 handler + 自定义门控 filter**：

- 能力关闭 → filter 返回 `False` ⇒ handler 不被激活、`is_wake` 不受影响，插件对群聊
  **零副作用**（等价于未安装）；
- 能力开启 → filter 通过 ⇒ 每条群消息都会进入本插件决策；决定接话时由插件把
  `is_at_or_wake_command` 置真（与框架自身唤醒同一语义），决定不接时 `event.stop_event()`。

### 11.2 决策链（自上而下短路）

| 顺序 | 条件 | 结果 |
|---|---|---|
| 1 | 能力关闭 | 不参与（仅兜底，门控已挡） |
| 2 | 被直接提及（@ / 引用 / wake 前缀） | `reply`：不改写、不拦截，沿用框架原链路 |
| 3 | 该会话已有进行中的合并窗口 | `silent`，本条并入窗口 |
| 4 | 无文本 / 名单外 / 冷却中 / 已达每小时配额 | `silent`（零成本前置检查） |
| 5 | 注意力得分 < `attention_threshold` | `silent` |
| 6 | 通过 | `interject`：置唤醒标记，必要时改写为合并后的文本 |

**要点**：白/黑名单与冷却、配额都只约束「主动插话」，**不影响被直接提及时的回复**；
被 @ 也**不参与合并窗口**，定向提问必须立刻得到响应。

### 11.3 注意力评分（`group/attention.py`，纯函数）

对**合并后的整段文本**评分，输出 `[0, 1]` 与命中信号：

| 信号 | 权重 |
|---|---|
| 基础分（群里有对话发生） | +0.15 |
| 含疑问标记（？、吗、呢、怎么、为什么、哪、多少、谁、求、帮…） | +0.35 |
| 出现 Bot 称呼词（`bot_aliases`） | +0.40 |
| 与 Bot 最近 3 条发言的最大词袋重合度 | +0.30 × 重合度 |
| 长度落在 4–200 字 | +0.10 |
| 超过 400 字 / 少于 4 字 | −0.15 / −0.20 |
| 同一发送者 20 秒内 ≥3 条（刷屏） | −0.20 |
| 无实义内容（无 CJK/ASCII 词元，如纯表情） | 直接 0 |

默认阈值 0.55，即「明确提问」「被称呼」「话题延续」任一即可越过，普通闲聊不会。

### 11.4 冷却与配额

- 每会话 `cooldown_seconds`（默认 90s）内不重复插话；
- 每会话每小时最多 `max_per_hour`（默认 6）次；
- 两者都是**内存态**，重启后重置（不写库，避免为限流产生写放大）。

### 11.5 并发合并

用户常把一句话拆成几条发，逐条评分会让它们全部落空，因此：

1. 通过前置检查后，消息**认领**一个 `merge_window_seconds`（默认 1.2s）的窗口
   （认领发生在任何 `await` 之前，所以并发到达时只有一条能成为 leader）；
2. 窗口内的后续消息被窗口吸收（并入文本）并静默，不再各自决策；
3. 窗口结束后**对合并文本评分**，通过则把合并文本写回事件（`message_str`），
   由框架带着完整语义去调用模型；
4. 上限：`merge_max_messages` 条、`merge_max_chars` 字符。

延迟代价：开启合并时每次插话至少推迟一个窗口（默认 1.2s）。窗口内的消息**不会丢失**——
它们要么被合并进这次回复，要么因 leader 判定静默而一起静默。

### 11.6 配置

| 配置项 | 默认 | 说明 |
|---|---|---|
| `group.enabled` | **false** | 总开关；关闭时不参与唤醒判定 |
| `group.attention_threshold` | 0.55 | 插话阈值（0.05–1.0） |
| `group.cooldown_seconds` | 90 | 两次插话最短间隔 |
| `group.max_per_hour` | 6 | 每会话每小时上限 |
| `group.merge_window_seconds` | 1.2 | 合并窗口（0 表示关闭） |
| `group.merge_max_messages` | 4 | 单次最多合并条数 |
| `group.bot_aliases` | 空 | Bot 的称呼词 |
| `group.whitelist` / `group.blacklist` | 空 | 名单（群号或 UMO）；黑名单优先 |

### 11.7 硬性约束

1. **关闭即零副作用**：门控 filter 必须跟随能力开关，绝不无条件唤醒群消息；
2. **不打断定向提问**：被 @ / 引用时永远 `reply`，且不参与合并窗口；
3. **静默用 `stop_event()`**：让事件不再进入后续 stage，既不调用模型也不发送内容；
4. **消息不丢失**：窗口内的消息要么被合并，要么随 leader 一起静默，不做「部分回复」；
5. **不写库**：冷却、配额、话题历史都是内存态，插件不因限流产生新的持久化写入；
6. **框架缺符号即降级**：缺少 `custom_filter` / `EventMessageType` 时不注册 handler 并告警一次。

---

## 12. 主动交互规格

对应路线 P4。目标：让 Bot 在**不打扰**的前提下，偶尔主动开启一段对话。

### 12.1 双轨调度

| 轨道 | 触发 | 说明 |
|---|---|---|
| 计划轨（daily） | `Scheduler.daily_at`，每天 `daily_time` | 当日幂等、跨重载不重复；停机错过的时点启动后补一次 |
| 空闲轨（idle） | `Scheduler.every(idle_check_minutes)`，会话静默 ≥ `idle_minutes` | 每轮只做判定，不满足则零成本跳过 |

目标会话来自配置项 `proactive.targets`（UMO 列表），**不做自动发现**：主动发消息是
会打扰人的行为，必须由使用者显式指定对象。

### 12.2 内容生成

1. 素材：该会话作用域内的最近 `material_memories` 条长期记忆、`material_journals` 条周记、
   最近对话缓冲；**素材为空即跳过**（宁可不发，也不硬编一句问候）；
2. 生成：调用 LLM（`purpose="proactive"`，可用 `provider_id` 指定便宜的模型），
   提示词要求口语化、一句话、不超过 `max_chars`、不得自称「主动/定时/系统」、
   不得编造素材之外的事实，并附上「上一条主动消息」要求换话题；
3. 清洗：去掉包裹引号与「主动：」之类前缀、压平空白、按 `max_chars` 截断；
   清洗后为空即放弃本次发送。

### 12.3 竞态保护

1. **同会话互斥**：会话在 `_inflight` 中时直接跳过；
2. **生成前后各复核一次静默条件**：生成期间用户开始说话 → 放弃本次发送（不硬插话）；
3. **先占配额再发送**：配额写 KV（`proactive:sent:<umo>:<date>`）后才发送，
   发送失败也不补发（避免重复打扰）；跨重载幂等；
4. **全局并发与每日调用预算**交给 `LLMBudget`，本域不做二次限流；
5. **单个会话失败不影响其它会话**：逐会话串行、逐个兜底。

### 12.4 免打扰

| 机制 | 说明 |
|---|---|
| 安静时段 | `quiet_start`–`quiet_end`（本地整点，支持跨午夜；起止相同即不启用） |
| 每日上限 | 每会话 `daily_max` 条，两条轨道共享 |
| 忙碌守卫 | 会话静默 < `busy_guard_minutes` 视为「正在聊天」，两条轨道都不打扰 |
| 手动暂停 | `/sab quiet [on\|off]`，写 KV `proactive:paused:<umo>`，跨重载保持一致 |
| 无活动记录 | 按「服务启动时刻」估算静默，避免刚重启就打扰 |

### 12.5 配置

| 配置项 | 默认 | 说明 |
|---|---|---|
| `proactive.enabled` | **false** | 总开关（热切换） |
| `proactive.targets` | 空 | 目标会话 UMO 列表；为空则不发 |
| `proactive.daily_enabled` / `daily_time` | on / 10:00 | 计划轨开关与时间 |
| `proactive.idle_enabled` / `idle_minutes` / `idle_check_minutes` | off / 180 / 15 | 空闲轨开关、静默阈值、检查周期 |
| `proactive.busy_guard_minutes` | 10 | 「正在聊天」门槛 |
| `proactive.daily_max` | 1 | 每会话每日上限 |
| `proactive.quiet_start` / `quiet_end` | 23 / 8 | 免打扰时段 |
| `proactive.max_chars` | 80 | 主动消息长度上限 |
| `proactive.material_memories` / `material_journals` | 8 / 3 | 取材条数 |
| `proactive.provider_id` | 空 | 生成所用模型；空则用会话默认 |

### 12.6 硬性约束

1. **默认关闭且需显式指定目标**：绝不「自动找会话聊天」；
2. **主动消息不进记忆缓冲**：`Context.send_message` 不触发 `after_message_sent`，
   因此不会自己喂自己（避免自我强化循环）；
3. **失败即静默**：素材缺失、生成失败、发送失败都只记一条状态，不重试、不降级成模板；
4. **任何异常不打断调度**：单会话异常被吞掉，调度器继续跑其它会话。

---

## 13. 拟人化学习规格

对应路线 P5。目标：让 Bot 逐步获得**说话方式**、**群内用语理解**与**关系感**，
同时把「学习结果可能出错」的风险控制在审查队列之内。

### 13.1 三块能力与共同链路

| 子能力 | 学什么 | 学的方式 | 注入什么 |
|---|---|---|---|
| 风格模仿（`persona.style`） | 表达模式（场景 → 表达） | 邻接对直接配对，**零模型调用** | 相似场景的 few-shot 示例 |
| 群内用语（`persona.jargon`） | 群内词条及其含义 | 词频统计预筛 + 一次批量推断 | 命中词的含义（禁止复读） |
| 社交好感度（`persona.affinity`） | 每个对象的关系数值 | 关键词规则表，冲突时才调模型 | 按档位的语气指引 |

共同链路：**对话中学习 → 有错可拦（审查队列）→ 请求前注入**。
三者各自独立开关，互不依赖；全部默认关闭。

### 13.2 风格模仿

1. **配对**：`after_message_sent` 时取出该会话最近一条用户消息（内存态，TTL 300s 内有效），
   与本次回复组成 `situation` / `expression`。用户消息带命令前缀、过短，或回复过短/过长时丢弃。
2. **落地**：`approval_required`（默认 true）为真时写入 `pending_reviews`（`origin='style'`），
   否则直接写 `style_patterns`；同一作用域内完全相同的样本由唯一索引挡住，不重复计入。
3. **选择**：对当前消息与所有 `active` 样本的 `situation` 做词袋 Jaccard 相似度，
   低于 `min_similarity` 的丢弃，其余按「相似度 → 权重」排序取 top-k。
4. **加权**：命中即 `hits+1`，权重增加固定增量并封顶；每日按 `half_life_days` 衰减，
   低于 `weight_floor` 归档；每作用域超过 `max_patterns` 时按权重淘汰。
5. **注入**：渲染为带边界与「只模仿语气、不要照抄内容」声明的示例块，总字符不超 `max_injected_chars`。

### 13.3 群内用语

1. **候选统计（零成本）**：用户消息分词后累加词频，只保留长度在 `min_chars`–`max_chars`、
   非纯数字的词；每作用域内存条目有上限，超出按词频淘汰。计数与例句写入状态存储
   （`persona:jargon:counts:<scope>`），**插件卸载前落盘**，因此重载不会让累积归零。
2. **扫描**：定时任务（`scan_interval_minutes`）逐会话挑出词频 ≥ `min_frequency` 的候选，
   排除已收录、已在待审队列的词，取前 `candidate_limit` 个，**一次批量调用**推断词义。
3. **落地**：`approval_required`（默认 true）为真时入 `pending_reviews`（`origin='jargon'`），
   否则直接写 `jargons`（同词重复学习时累加证据数并刷新含义）。
4. **消费即移除**：本次送入推断的候选词无论判定结果如何都从计数中删除，
   避免一个通用词被反复送去推断、反复消耗调用。
5. **注入**：当前消息命中已收录词条时注入含义，并附**负向指令**（只用于理解、不要复述、
   不要主动使用），防止黑话被扩散；单次最多 `inject_max` 条、字符数不超 `max_injected_chars`。

### 13.4 社交好感度

1. **规则优先**：10 类交互（praise / thanks / care / apology / joke / greet / question /
   criticism / conflict / insult / neutral）由关键词规则表判定；以问号结尾按提问处理。
2. **模型兜底**：仅当规则同时命中正向与负向类型时（例：「你可真厉害，就是会添乱」）才调用模型裁决；
   模型不可用、解析失败或置信度不足时**不改变分数**（宁可不动，也不要误判）。
3. **数值模型**：`score ∈ [min_score, max_score]`；单次变化幅度受 `daily_delta_cap` 约束；
   长期静默时按 `decay_half_life_days` 向 `initial_score` 回归；只按「会话 + 对象」独立累积，
   **不做跨用户总量再分配**。
4. **触发器**：用户消息在后台任务中更新（含兜底调用），不阻塞对话主链路；注入读取的是当前表值。
5. **注入档位**：≥0.75 亲近、0.6–0.75 熟悉、**0.4–0.6 不注入**、0.25–0.4 保持礼貌、
   <0.25 克制；`inject_enabled` 为假时只累积不注入。

### 13.5 审查制

- 统一复用 `pending_reviews`，以 `origin` 区分来源（`style` / `jargon` / `reflection`）；
- 审批由**来源域**落地（写各自的表），落地成功后把记录置为 `approved`，避免重复出现在队列中；
- 非本域来源（反思）返回未处理，交由 `learning` 域处理，命令与面板都走同一入口；
- 审查开关可按子能力配置：`style_approval_required` / `jargon_approval_required`，默认均为 true。

### 13.6 配置

| 配置项 | 默认 | 说明 |
|---|---|---|
| `persona.style` | **false** | 风格模仿开关 |
| `persona.style_approval_required` | true | 样本是否先进待审队列 |
| `persona.style_max_patterns` | 200 | 每作用域样本容量 |
| `persona.style_top_k` / `style_min_similarity` | 3 / 0.12 | 单次注入条数与相似度门槛 |
| `persona.style_max_injected_chars` / `style_half_life_days` | 600 / 30 | 注入字符预算与衰减半衰期 |
| `persona.jargon` | **false** | 群内用语开关 |
| `persona.jargon_approval_required` | true | 词条是否先进待审队列 |
| `persona.jargon_min_frequency` / `jargon_candidate_limit` | 3 / 8 | 候选门槛与单批上限 |
| `persona.jargon_scan_interval_minutes` | 360 | 扫描周期 |
| `persona.jargon_max_jargons` / `jargon_max_injected_chars` | 200 / 300 | 词条上限与注入预算 |
| `persona.jargon_provider_id` | 空 | 推断所用模型（建议用便宜的小模型） |
| `persona.affinity` | **false** | 好感度开关 |
| `persona.affinity_initial_score` / `affinity_half_life_days` | 0.5 / 14 | 基线与衰减半衰期 |
| `persona.affinity_daily_delta_cap` | 0.3 | 单次交互最大变化 |
| `persona.affinity_use_llm` / `affinity_provider_id` | true / 空 | 兜底判定与所用模型 |
| `persona.affinity_inject_enabled` | true | 是否注入语气指引 |

### 13.7 硬性约束

1. **默认关闭且关闭即零副作用**：能力关闭时不注册调度、不学习、不注入，对话行为与未安装一致；
2. **未批准不生效**：审查开关为真时，学习结果只进队列，绝不提前影响对话；
3. **零成本路径不许调模型**：邻接对抽取、候选统计、无冲突的规则判定都必须不产生模型调用；
4. **兜底失败不改分数**：模型不可用或输出不可解析时保持原值，并只记一条调试日志；
5. **注入块独立**：使用独立的边界标记，注入前只清理自己的旧块，不覆盖记忆注入；
6. **不写对话历史**：注入走临时内容块，学习结果的载体是插件自己的表，不污染对话历史；
7. **单点失败不外溢**：学习、注入、扫描任一环节异常都只降级并记日志，不影响对话与其它任务。

---

## 14. 可观测性与运维

- 日志统一经 `Host.log()`；关键路径分级，`debug_log` 开启才输出检索打分细节。
- 命令采用**单一顶层入口** `sab`（别名 `superastrbot`），子命令由
  `commands/parser.py` 自行解析：`status`、`search`、`why`、`remember`、`journal`、
  `journals`、`review`、`approve`、`reject`、`reset`、`reindex`、`quiet`、`persona`、`help`。
  之所以不注册多条顶层/子指令：AstrBot 的指令冲突检测以指令「完整名」为键，
  注册项越少撞名概率越低，也不依赖框架的参数推导行为。
- 面板（`pages/dashboard`）八个分区：总览、记忆、检索、周记、待审、学习、功能、系统。
  前端**只经 `window.AstrBotPluginPage` bridge 请求**，不使用 `fetch`（插件页位于
  无 `allow-same-origin` 的 sandbox iframe，直连请求必然失败）；入口脚本必须
  `type="module"`，确保在 AstrBot 注入 bridge 之后执行。
- **功能管理界面**（`功能` 分区）：从能力注册表渲染图形化开关，覆盖全部能力项。
  开关经 `POST feature-toggle` 写配置 → 落盘 → `refresh_capabilities()` 热应用，
  并回传实际生效状态；界面区分「需重载」（禁用）与「环境不支持」（禁用并说明），
  前置未开启时给出提示；总览页的能力指示可点击跳转至对应开关。
- 插件图标：仓库根 `logo.png` 为插件列表图标，面板品牌区与页签使用
  `pages/dashboard/logo.svg`（矢量，随主题缩放不失真）。
- 配置页的「嵌入模型提供商」下拉由 `app.sync_schema_options()` 运行时注入（框架的
  `select_provider` 硬编码为对话模型）；无可用嵌入提供商时字段退回文本框。
  由于 AstrBot **先加载插件、后初始化 `ProviderManager`**，启动期探测必然为空，
  故嵌入探测**失败不缓存**，并在 `on_astrbot_loaded` 钩子中复检，使向量路自动启用。
- 上下文治理状态经 `/sab status` 与面板 `status()` 的 `context` 字段暴露：是否开启、token 上限、
  保留条数，以及最近一次的 token 前后值与原因。
- 群聊语义与主动交互同样经 `status()` 的 `group` / `proactive` 字段暴露；`status(umo=...)`
  （`/sab status` 走这条）额外附带**本会话**的近一小时插话次数、冷却剩余、今日主动消息条数、
  暂停状态与静默时长。主动交互的两条轨道以 `proactive-daily` / `proactive-idle` 注册到
  `Scheduler`，因此面板「系统 → 后台任务」会连同下次执行时间一起展示。
- 拟人化学习经 `status()` 的 `persona` 字段暴露（三块子能力的开关、审批要求与累计条数）；
  `/sab persona` 与面板「学习」分区按会话展示表达样本、群内用语与好感度明细，
  待审数量与「待审」分区共用同一份数据。黑话扫描任务以 `persona-jargon-scan` 注册到
  `Scheduler`，同样出现在后台任务列表里。
- 群消息 handler 只在能力开启时才参与唤醒判定；能力开启但框架缺少 `custom_filter` 符号时，
  启动日志给出一次明确告警（该能力降级为不可用，其余功能不受影响）。
- 所有敏感值（除内容外）只回状态不回原文；面板默认只读优先。

---

## 15. 借鉴来源与合规

本插件的设计思路来源于仓库上一级 `docs/Super_AstrBot_项目学习分析文档.md` 所分析的开源项目，**仅借鉴设计思路与公开 API 用法，不复制其源码**。若后续引入任何第三方代码或资源，必须：

1. 遵守原项目开源许可（AGPL-3.0 等），保留版权与许可声明；
2. 在 `README.md`「致谢与灵感来源」中列明来源与链接；
3. 在下方登记表中补充条目。

| 借鉴点 | 来源项目 | 本项目落地位置 |
|---|---|---|
| 分层检索 + RRF 融合 + 多因子加权 | livingmemory | `memory/retriever/` |
| 摘要/水位线思路 | memory_beyond | `context/governor.py` + `context/prompts.py` |
| 注入不落史（`mark_as_temp`） | livingmemory / self_learning | `memory/injector.py` |
| 无 patch 的阈值差退化策略 | memory_beyond | `spec/capabilities.py` 依赖解析 |
| 可恢复写日志 | livingmemory | `storage/db.py` + `write_ops` |
| 幂等定时任务 | livingmemory_ext | `loop/scheduler.py` |
| 群聊唤醒抑制 / 群消息并发处理 | AstrNa（`group_wake_suppression`、`group_sender_concurrency`） | `group/`（仅借鉴「保护系统提示词段」「同会话串行」思路，未复制代码，且以 filter 门控替代其 monkey-patch） |
| 主动消息的时间轨/空闲轨调度 | proactive_chat | `proactive/`（仅借鉴双轨触发与免打扰思路） |
| 表达模式邻接对抽取 + 时间衰减 + 容量控制 | self_learning | `persona/style.py`（零 LLM 的 user→bot 配对、指数衰减与权重淘汰） |
| 统计预筛 + 模型语义判定降本 | self_learning | `persona/jargon.py`（词频先行、批量推断，不复制其三步对比法） |
| 审查制写入 + 好感度数值模型 | self_learning | `persona/service.py` + `persona/affinity.py`（复用 `pending_reviews`；不做跨用户总量再分配） |

---

## 16. 验收标准（MVP）

1. 插件可在 AstrBot ≥ 4.24.2 正常加载、卸载、重载，无残留任务与残留 handler。
2. 未配置 Embedding Provider 时插件正常可用（自动降级），配置后自动启用向量路。
3. 对话能召回并注入相关记忆，注入内容**不写入**对话历史。
4. 反思在满足条件时触发，产出写入记忆且可在 `/sab search` 中检索到。
5. 周记可写入、可检索、可被周度反思消费，且周度任务当日只执行一次。
6. 所有外部调用失败均降级为「不影响正常对话」，并输出一次告警。
7. Agent 记忆工具：开启后可被模型调用并读写当前会话记忆，写入的记忆 `source='agent'`；
   默认关闭或框架不支持时不注册工具，插件仍正常就绪。
8. 上下文治理：开启后仅在本次请求内生效，超阈值时先占位压缩、再摘要；尾部保留区逐字不变，
   摘要失败降级为「仅占位压缩」，**持久化对话历史不被改写**；默认关闭时不干预任何请求。
9. 群聊语义：关闭时不影响任何群消息的唤醒判定（等价未安装）；开启后仅在注意力达标时插话，
   受冷却与每小时配额约束，被 @ / 引用时始终正常回复；开启合并时同一会话的一波消息只回答一次。
10. 主动交互：关闭时不产生任何发送；开启后仅在目标会话、非安静时段、静默足够久且未超每日上限时，
    基于素材生成并发送一条消息；生成期间会话转为活跃即放弃本次发送，失败不重试。
11. 拟人化学习：三项子能力关闭时零副作用。风格样本由邻接对直接抽取（**不调用模型**）；
    黑话先统计后推断，且**未收录的词不会反复送入推断**；好感度常规关系不注入语气，
    规则冲突且模型不可用时分数保持不变；审查开关为真时，未批准的样本不进入生效表；
    注入使用独立边界标记，不覆盖记忆注入、不写入对话历史。
12. `tests/` 覆盖：能力依赖解析、FTS 检索、RRF 融合、加权排序、注入清理、调度幂等、迁移幂等、
    Agent 工具、token 估算与上下文治理、群聊决策与合并、主动交互守卫与竞态、
    拟人化学习（样本过滤/选择与衰减、候选统计与推断、规则与兜底判定、审批分流）。
13. `ruff check .` 与 `ruff format .` 通过。

---

## 17. 迭代路线

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 规格 + 脚手架 + Harness + Loop + Storage | ✅ 已完成 |
| P1 | Memory 闭环 + 周记 + 命令 + 面板 | ✅ 已完成 |
| P1.6 | 修复指令冲突面/嵌入模型下拉/面板加载失败；控制台重建为六分区 | ✅ 已完成（v0.2.0） |
| P1.7 | 功能管理界面（能力热开关）；修复调度器误报与向量能力永久不可用 | ✅ 已完成（v0.3.0） |
| P1.8 | 全量代码审查：作用域越界删除、存储串行化、LIKE 转义、向量作用域扫描、写放大与死代码清理 | ✅ 已完成（v0.3.1） |
| P1.5 | Agent 函数工具（`sab_memory_search` / `sab_memory_write`） | ✅ 已完成（v0.4.0） |
| P2 | 上下文治理（token 估算 / 工具与图片历史占位 / 摘要水位线） | ✅ 已完成（v0.5.0） |
| P3 | 群聊语义（读空气决策 / 注意力 / 冷却 / 并发合并） | ✅ 已完成（v0.6.0） |
| P4 | 主动交互（双轨调度 / 竞态保护 / 免打扰） | ✅ 已完成（v0.6.0） |
| P5 | 拟人化学习（风格 few-shot / 黑话 / 好感度，审查制） | ✅ 已完成（v0.7.0） |

**验收结果**：`pytest tests -q` 233 项全部通过；`ruff check .` 无告警；`ruff format .` 已应用；
`node --check pages/dashboard/app.js` 通过。真实 AstrBot 环境下的面板数据加载、功能开关切换、
向量路启用、Agent 工具调用、上下文治理触发效果、群聊插话分寸、主动消息发送时机，
以及拟人化学习的学习质量与审查流程仍待服务器实测（本地无运行实例）。
