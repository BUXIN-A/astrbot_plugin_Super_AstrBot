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
└───┬────────┬────────┬────────┬────────┬────────┬─────────────┘
    │        │        │        │        │        │
┌───▼──┐ ┌───▼──┐ ┌───▼───┐ ┌──▼───┐ ┌──▼───┐ ┌─▼──────┐
│memory│ │journal│ │learning│ │context│ │commands│ │ web    │  ← 业务域（禁止 import astrbot）
└───┬──┘ └───┬──┘ └───┬───┘ └──┬───┘ └──┬───┘ └─┬──────┘
    │        │        │        │        │        │
┌───▼────────▼────────▼────────▼────────▼────────▼─────────────┐
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

## 11. 可观测性与运维

- 日志统一经 `Host.log()`；关键路径分级，`debug_log` 开启才输出检索打分细节。
- 命令采用**单一顶层入口** `sab`（别名 `superastrbot`），子命令由
  `commands/parser.py` 自行解析：`status`、`search`、`why`、`remember`、`journal`、
  `journals`、`review`、`approve`、`reject`、`reset`、`reindex`、`help`。
  之所以不注册多条顶层/子指令：AstrBot 的指令冲突检测以指令「完整名」为键，
  注册项越少撞名概率越低，也不依赖框架的参数推导行为。
- 面板（`pages/dashboard`）七个分区：总览、记忆、检索、周记、待审、功能、系统。
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
- 所有敏感值（除内容外）只回状态不回原文；面板默认只读优先。

---

## 12. 借鉴来源与合规

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

---

## 13. 验收标准（MVP）

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
9. `tests/` 覆盖：能力依赖解析、FTS 检索、RRF 融合、加权排序、注入清理、调度幂等、迁移幂等、
   Agent 工具、token 估算与上下文治理。
10. `ruff check .` 与 `ruff format .` 通过。

---

## 14. 迭代路线

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 规格 + 脚手架 + Harness + Loop + Storage | ✅ 已完成 |
| P1 | Memory 闭环 + 周记 + 命令 + 面板 | ✅ 已完成 |
| P1.6 | 修复指令冲突面/嵌入模型下拉/面板加载失败；控制台重建为六分区 | ✅ 已完成（v0.2.0） |
| P1.7 | 功能管理界面（能力热开关）；修复调度器误报与向量能力永久不可用 | ✅ 已完成（v0.3.0） |
| P1.8 | 全量代码审查：作用域越界删除、存储串行化、LIKE 转义、向量作用域扫描、写放大与死代码清理 | ✅ 已完成（v0.3.1） |
| P1.5 | Agent 函数工具（`sab_memory_search` / `sab_memory_write`） | ✅ 已完成（v0.4.0） |
| P2 | 上下文治理（token 估算 / 工具与图片历史占位 / 摘要水位线） | ✅ 已完成（v0.5.0） |
| P3 | 群聊语义（读空气决策 / 注意力 / 冷却 / 并发合并） | 规划 |
| P4 | 主动交互（双轨调度 / 竞态保护 / 免打扰） | 规划 |
| P5 | 拟人化学习（风格 few-shot / 黑话 / 好感度，审查制） | 规划 |

**验收结果**：`pytest tests -q` 159 项全部通过；`ruff check .` 无告警；`ruff format .` 已应用；
`node --check pages/dashboard/app.js` 通过。真实 AstrBot 环境下的面板数据加载、功能开关切换、
向量路启用、Agent 工具调用与上下文治理触发效果仍待服务器实测（本地无运行实例）。
