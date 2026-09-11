# Changelog

本文件记录 Super_AstrBot 的版本变更。版本号以 `metadata.yaml` 为唯一来源。

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
