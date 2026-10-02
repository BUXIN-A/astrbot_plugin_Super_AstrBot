"""数据库迁移定义。

硬性规则（写入 ``SPEC.md`` 并被本模块强制）：

1. **只做增量**：只允许 ``CREATE`` / ``ALTER TABLE ADD COLUMN`` / ``CREATE INDEX``；
   禁止删列、改类型等破坏性操作（参考 self_learning 的教训：破坏性迁移一旦出错不可逆）。
2. **版本化**：每次结构变更追加一个新 ``Migration``，``version`` 严格递增。
3. **迁移前备份**：由 ``Database.migrate`` 负责，本模块只描述结构。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    statements: tuple[str, ...]


_FTS_STATEMENTS: tuple[str, ...] = (
    # 仅索引 tokens 列（分词由 support.text 统一负责），避免 unicode61 对中文整段成一个词。
    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_index USING fts5(tokens, tokenize='unicode61')",
)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        description="初始结构：记忆、向量、周记、反思、审批、写日志、运行状态",
        statements=(
            # ---------------- 记忆 ----------------
            """
            CREATE TABLE IF NOT EXISTS memories (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type     TEXT    NOT NULL,
                scope_id       TEXT    NOT NULL,
                kind           TEXT    NOT NULL DEFAULT 'fact',
                content        TEXT    NOT NULL,
                importance     REAL    NOT NULL DEFAULT 0.5,
                confidence     REAL    NOT NULL DEFAULT 0.8,
                source         TEXT    NOT NULL DEFAULT 'capture',
                tags           TEXT    NOT NULL DEFAULT '[]',
                created_at     REAL    NOT NULL,
                updated_at     REAL    NOT NULL,
                last_access_at REAL    NOT NULL DEFAULT 0,
                access_count   INTEGER NOT NULL DEFAULT 0,
                status         TEXT    NOT NULL DEFAULT 'active'
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope_type, scope_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind, status)",
            # ---------------- 向量（可选检索路） ----------------
            """
            CREATE TABLE IF NOT EXISTS memory_vectors (
                memory_id   INTEGER PRIMARY KEY,
                fingerprint TEXT    NOT NULL,
                dim         INTEGER NOT NULL,
                vector      BLOB    NOT NULL,
                updated_at  REAL    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_vectors_fingerprint ON memory_vectors(fingerprint)",
            # ---------------- 记忆关联（为后续图谱预留） ----------------
            """
            CREATE TABLE IF NOT EXISTS memory_links (
                src_id     INTEGER NOT NULL,
                dst_id     INTEGER NOT NULL,
                relation   TEXT    NOT NULL,
                weight     REAL    NOT NULL DEFAULT 1.0,
                created_at REAL    NOT NULL,
                PRIMARY KEY (src_id, dst_id, relation)
            )
            """,
            # ---------------- 周记 ----------------
            """
            CREATE TABLE IF NOT EXISTS journals (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type TEXT    NOT NULL,
                scope_id   TEXT    NOT NULL,
                content    TEXT    NOT NULL,
                tags       TEXT    NOT NULL DEFAULT '[]',
                emotion    INTEGER,
                event_time REAL    NOT NULL,
                memory_id  INTEGER,
                created_at REAL    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_journals_scope_time ON journals(scope_type, scope_id, event_time DESC)",
            # ---------------- 反思记录 ----------------
            """
            CREATE TABLE IF NOT EXISTS reflection_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type  TEXT    NOT NULL,
                scope_id    TEXT    NOT NULL,
                started_at  REAL    NOT NULL,
                finished_at REAL,
                status      TEXT    NOT NULL DEFAULT 'running',
                produced    INTEGER NOT NULL DEFAULT 0,
                detail      TEXT    NOT NULL DEFAULT '',
                error       TEXT    NOT NULL DEFAULT ''
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_reflection_scope ON reflection_logs(scope_type, scope_id, started_at DESC)",
            # ---------------- 待审队列 ----------------
            """
            CREATE TABLE IF NOT EXISTS pending_reviews (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type TEXT    NOT NULL,
                scope_id   TEXT    NOT NULL,
                origin     TEXT    NOT NULL DEFAULT 'reflection',
                payload    TEXT    NOT NULL,
                status     TEXT    NOT NULL DEFAULT 'pending',
                created_at REAL    NOT NULL,
                decided_at REAL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_reviews_status ON pending_reviews(status, created_at DESC)",
            # ---------------- 可恢复写日志 ----------------
            """
            CREATE TABLE IF NOT EXISTS write_ops (
                op_id      TEXT    PRIMARY KEY,
                op_type    TEXT    NOT NULL,
                step       TEXT    NOT NULL,
                payload    TEXT    NOT NULL DEFAULT '{}',
                status     TEXT    NOT NULL DEFAULT 'running',
                retries    INTEGER NOT NULL DEFAULT 0,
                created_at REAL    NOT NULL,
                updated_at REAL    NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_write_ops_status ON write_ops(status, updated_at DESC)",
            # ---------------- 运行状态（调度幂等 / 游标 / 节流） ----------------
            """
            CREATE TABLE IF NOT EXISTS kv_state (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """,
        ),
    ),
    Migration(
        version=2,
        description="拟人化学习：表达模式、群组黑话、社交好感度",
        statements=(
            # ---------------- 表达模式（风格 few-shot） ----------------
            """
            CREATE TABLE IF NOT EXISTS style_patterns (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type TEXT    NOT NULL,
                scope_id   TEXT    NOT NULL,
                situation  TEXT    NOT NULL,
                expression TEXT    NOT NULL,
                weight     REAL    NOT NULL DEFAULT 1.0,
                hits       INTEGER NOT NULL DEFAULT 0,
                source     TEXT    NOT NULL DEFAULT 'pair',
                created_at REAL    NOT NULL,
                updated_at REAL    NOT NULL,
                status     TEXT    NOT NULL DEFAULT 'active'
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_style_scope ON style_patterns(scope_type, scope_id, status)",
            # 同一作用域内「同样的问题、同样的回答」只留一条，避免重复样本挤占容量。
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_style_unique ON style_patterns(scope_type, scope_id, situation, expression)",
            # ---------------- 群组黑话 ----------------
            """
            CREATE TABLE IF NOT EXISTS jargons (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type   TEXT    NOT NULL,
                scope_id     TEXT    NOT NULL,
                term         TEXT    NOT NULL,
                meaning      TEXT    NOT NULL DEFAULT '',
                confidence   REAL    NOT NULL DEFAULT 0.6,
                evidence     INTEGER NOT NULL DEFAULT 1,
                samples      TEXT    NOT NULL DEFAULT '[]',
                created_at   REAL    NOT NULL,
                updated_at   REAL    NOT NULL,
                last_seen_at REAL    NOT NULL DEFAULT 0,
                status       TEXT    NOT NULL DEFAULT 'active'
            )
            """,
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_jargon_term ON jargons(scope_type, scope_id, term)",
            "CREATE INDEX IF NOT EXISTS idx_jargon_scope ON jargons(scope_type, scope_id, status)",
            # ---------------- 好感度（社交建模） ----------------
            """
            CREATE TABLE IF NOT EXISTS affinity_state (
                scope_type       TEXT    NOT NULL,
                scope_id         TEXT    NOT NULL,
                target_id        TEXT    NOT NULL,
                score            REAL    NOT NULL DEFAULT 0.5,
                mood             TEXT    NOT NULL DEFAULT 'neutral',
                interactions     INTEGER NOT NULL DEFAULT 0,
                last_interaction REAL    NOT NULL DEFAULT 0,
                updated_at       REAL    NOT NULL,
                PRIMARY KEY (scope_type, scope_id, target_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_affinity_scope ON affinity_state(scope_type, scope_id)",
        ),
    ),
    Migration(
        version=3,
        description="知识图谱、运行监控时序、自动审核留痕",
        statements=(
            # ---------------- 知识图谱：实体 ----------------
            """
            CREATE TABLE IF NOT EXISTS graph_entities (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type     TEXT    NOT NULL,
                scope_id       TEXT    NOT NULL,
                name           TEXT    NOT NULL,
                canonical_name TEXT    NOT NULL,
                entity_type    TEXT    NOT NULL DEFAULT 'concept',
                weight         REAL    NOT NULL DEFAULT 1.0,
                confidence     REAL    NOT NULL DEFAULT 0.7,
                evidence       INTEGER NOT NULL DEFAULT 1,
                source         TEXT    NOT NULL DEFAULT 'deterministic',
                created_at     REAL    NOT NULL,
                updated_at     REAL    NOT NULL,
                status         TEXT    NOT NULL DEFAULT 'active'
            )
            """,
            # 同一作用域内同名实体只保留一条（归并证据与权重），避免同义实体把图撑散。
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_unique ON graph_entities(scope_type, scope_id, canonical_name)",
            "CREATE INDEX IF NOT EXISTS idx_entity_name ON graph_entities(canonical_name)",
            "CREATE INDEX IF NOT EXISTS idx_entity_scope ON graph_entities(scope_type, scope_id, status)",
            # ---------------- 知识图谱：实体关系 ----------------
            """
            CREATE TABLE IF NOT EXISTS graph_relations (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type    TEXT    NOT NULL,
                scope_id      TEXT    NOT NULL,
                src_entity_id INTEGER NOT NULL,
                dst_entity_id INTEGER NOT NULL,
                relation      TEXT    NOT NULL,
                weight        REAL    NOT NULL DEFAULT 1.0,
                confidence    REAL    NOT NULL DEFAULT 0.7,
                evidence      INTEGER NOT NULL DEFAULT 1,
                source        TEXT    NOT NULL DEFAULT 'cooccur',
                created_at    REAL    NOT NULL,
                updated_at    REAL    NOT NULL,
                status        TEXT    NOT NULL DEFAULT 'active'
            )
            """,
            # 复合唯一索引而非复合主键：SQLite 不允许同时存在 rowid 主键与复合主键，
            # 而 ``ON CONFLICT(...)`` 只需要一个唯一约束即可生效。
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_relation_unique ON graph_relations(scope_type, scope_id, src_entity_id, dst_entity_id, relation)",
            "CREATE INDEX IF NOT EXISTS idx_relation_src ON graph_relations(scope_type, scope_id, src_entity_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_relation_dst ON graph_relations(scope_type, scope_id, dst_entity_id, status)",
            # ---------------- 知识图谱：记忆 ↔ 实体 ----------------
            """
            CREATE TABLE IF NOT EXISTS memory_entities (
                memory_id  INTEGER NOT NULL,
                entity_id  INTEGER NOT NULL,
                weight     REAL    NOT NULL DEFAULT 1.0,
                created_at REAL    NOT NULL,
                PRIMARY KEY (memory_id, entity_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_memory_entities_entity ON memory_entities(entity_id)",
            # ---------------- 运行监控：小时桶时序 ----------------
            """
            CREATE TABLE IF NOT EXISTS metric_series (
                bucket_ts  INTEGER NOT NULL,
                metric     TEXT    NOT NULL,
                scope_type TEXT    NOT NULL DEFAULT '',
                scope_id   TEXT    NOT NULL DEFAULT '',
                count      INTEGER NOT NULL DEFAULT 0,
                total      REAL    NOT NULL DEFAULT 0,
                last_value REAL    NOT NULL DEFAULT 0,
                PRIMARY KEY (bucket_ts, metric, scope_type, scope_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_metric_lookup ON metric_series(metric, bucket_ts DESC)",
            "CREATE INDEX IF NOT EXISTS idx_metric_scope ON metric_series(scope_type, scope_id, bucket_ts DESC)",
            # ---------------- 自动审核留痕 ----------------
            "ALTER TABLE pending_reviews ADD COLUMN decided_by TEXT NOT NULL DEFAULT ''",
        ),
    ),
    Migration(
        version=4,
        description="现实桥：周记条目新增标题与文本类型（周记 / 日记 / 随笔）",
        statements=(
            # 仅增量：``ALTER TABLE ADD COLUMN`` + 索引，不动既有列与数据。
            # 既有行由 DEFAULT 补齐（title 留空 → 读取时按条目时间回填默认标题）。
            "ALTER TABLE journals ADD COLUMN title TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE journals ADD COLUMN entry_type TEXT NOT NULL DEFAULT 'weekly'",
            "CREATE INDEX IF NOT EXISTS idx_journals_type_time ON journals(entry_type, event_time DESC)",
        ),
    ),
    Migration(
        version=5,
        description="记忆身份：memories 增补发送者标识，新增身份观测表（跨会话识别用户）",
        statements=(
            # 为什么加在 memories 上：历史缺陷是「记忆只记作用域、不记说话者」，
            # 于是会话级记忆无法回溯归属（迁移报告里的核心约束）。补上这三列后，
            # 新记忆天然带身份，作用域迁移（session→user）才有依据。
            "ALTER TABLE memories ADD COLUMN sender_id TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE memories ADD COLUMN sender_name TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE memories ADD COLUMN origin_umo TEXT NOT NULL DEFAULT ''",
            "CREATE INDEX IF NOT EXISTS idx_memories_sender ON memories(sender_id, status)",
            # 身份观测表：同一条 umo 上出现过的发送者标识。
            # 用途是回答「本平台 sender_id 是否跨会话稳定」——若同一昵称对应多个
            # sender_id，说明平台标识不稳定，应改用 auto/nickname 策略。
            """
            CREATE TABLE IF NOT EXISTS identity_seen (
                umo         TEXT PRIMARY KEY,
                platform    TEXT NOT NULL DEFAULT '',
                sender_id   TEXT NOT NULL DEFAULT '',
                sender_name TEXT NOT NULL DEFAULT '',
                scope_type  TEXT NOT NULL DEFAULT '',
                scope_id    TEXT NOT NULL DEFAULT '',
                user_key    TEXT NOT NULL DEFAULT '',
                first_seen  REAL NOT NULL,
                last_seen   REAL NOT NULL,
                events      INTEGER NOT NULL DEFAULT 1
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_identity_sender ON identity_seen(sender_id)",
            "CREATE INDEX IF NOT EXISTS idx_identity_name ON identity_seen(sender_name)",
        ),
    ),
    Migration(
        version=6,
        description="拟人化融合层：群友策略、三层人格、演化事件、共情日志、世界书、时序图谱、回访队列",
        statements=(
            # ---------------- 群友差异化对话策略（members/） ----------------
            # 一位群友一份策略：底层人格恒定，这里只存「表层微调」——
            # 语气 / 称呼 / 话题 / 禁忌，注入时改写表达而不切换人格。
            """
            CREATE TABLE IF NOT EXISTS member_profiles (
                sender_id       TEXT PRIMARY KEY,
                stable_name     TEXT NOT NULL DEFAULT '',
                relation        TEXT NOT NULL DEFAULT '',
                tone            TEXT NOT NULL DEFAULT '',
                address_as      TEXT NOT NULL DEFAULT '',
                topics          TEXT NOT NULL DEFAULT '[]',
                taboo           TEXT NOT NULL DEFAULT '[]',
                source          TEXT NOT NULL DEFAULT 'online',
                notes           TEXT NOT NULL DEFAULT '',
                updated_at      REAL NOT NULL DEFAULT 0,
                created_at      REAL NOT NULL DEFAULT 0
            )
            """,
            # ---------------- 融合层通用 KV（三层人格画像等单例 JSON） ----------------
            """
            CREATE TABLE IF NOT EXISTS fusion_state (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL DEFAULT '{}',
                updated_at REAL NOT NULL DEFAULT 0
            )
            """,
            # ---------------- 人格演化事件（character-sim 影响向量留痕） ----------------
            """
            CREATE TABLE IF NOT EXISTS persona_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                kind        TEXT NOT NULL DEFAULT 'shift',
                experience  TEXT NOT NULL DEFAULT 'neutral',
                sender_id   TEXT NOT NULL DEFAULT '',
                scope_type  TEXT NOT NULL DEFAULT '',
                scope_id    TEXT NOT NULL DEFAULT '',
                summary     TEXT NOT NULL DEFAULT '',
                deltas      TEXT NOT NULL DEFAULT '{}',
                confidence  REAL NOT NULL DEFAULT 0,
                created_at  REAL NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_persona_events_time ON persona_events(created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_persona_events_kind ON persona_events(kind, created_at DESC)",
            # ---------------- 共情事件日志（CogEmp 三阶段留痕） ----------------
            """
            CREATE TABLE IF NOT EXISTS empathy_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type  TEXT NOT NULL DEFAULT '',
                scope_id    TEXT NOT NULL DEFAULT '',
                sender_id   TEXT NOT NULL DEFAULT '',
                sender_name TEXT NOT NULL DEFAULT '',
                emotion     TEXT NOT NULL DEFAULT '',
                intensity   REAL NOT NULL DEFAULT 0,
                cause       TEXT NOT NULL DEFAULT '',
                stage_mask  TEXT NOT NULL DEFAULT '',
                guidance    TEXT NOT NULL DEFAULT '',
                created_at  REAL NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_empathy_events_time ON empathy_events(created_at DESC)",
            # ---------------- 世界书 / Lorebook 条目（AMBRACE 重写） ----------------
            """
            CREATE TABLE IF NOT EXISTS worldbook_entries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                triggers    TEXT NOT NULL DEFAULT '[]',
                content     TEXT NOT NULL DEFAULT '',
                priority    INTEGER NOT NULL DEFAULT 5,
                scope_type  TEXT NOT NULL DEFAULT '',
                scope_id    TEXT NOT NULL DEFAULT '',
                enabled     INTEGER NOT NULL DEFAULT 1,
                hits        INTEGER NOT NULL DEFAULT 0,
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_worldbook_scope ON worldbook_entries(scope_type, scope_id, enabled)",
            # ---------------- 时序知识图谱（LATRACE 思想，进程内重写） ----------------
            # 节点/边都带时间语义：valid_from/valid_to 让「时间线回溯」可查询，
            # evidence 保存来源引用（对话 / 离线蒸馏 / 周记），sender 归属防串台。
            """
            CREATE TABLE IF NOT EXISTS tkg_nodes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_type  TEXT NOT NULL DEFAULT '',
                scope_id    TEXT NOT NULL DEFAULT '',
                name        TEXT NOT NULL,
                canonical   TEXT NOT NULL DEFAULT '',
                entity_type TEXT NOT NULL DEFAULT 'topic',
                sender_id   TEXT NOT NULL DEFAULT '',
                weight      REAL NOT NULL DEFAULT 1.0,
                mentions    INTEGER NOT NULL DEFAULT 1,
                evidence    TEXT NOT NULL DEFAULT '',
                first_seen  REAL NOT NULL,
                last_seen   REAL NOT NULL
            )
            """,
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tkg_node_unique ON tkg_nodes(scope_type, scope_id, canonical)",
            "CREATE INDEX IF NOT EXISTS idx_tkg_node_sender ON tkg_nodes(sender_id)",
            "CREATE INDEX IF NOT EXISTS idx_tkg_node_seen ON tkg_nodes(last_seen DESC)",
            """
            CREATE TABLE IF NOT EXISTS tkg_edges (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                src_id     INTEGER NOT NULL,
                dst_id     INTEGER NOT NULL,
                relation   TEXT NOT NULL DEFAULT 'co_occur',
                weight     REAL NOT NULL DEFAULT 1.0,
                confidence REAL NOT NULL DEFAULT 0.5,
                evidence   TEXT NOT NULL DEFAULT '',
                sender_id  TEXT NOT NULL DEFAULT '',
                valid_from REAL NOT NULL,
                valid_to   REAL NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )
            """,
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_tkg_edge_unique ON tkg_edges(src_id, dst_id, relation)",
            "CREATE INDEX IF NOT EXISTS idx_tkg_edge_src ON tkg_edges(src_id)",
            "CREATE INDEX IF NOT EXISTS idx_tkg_edge_dst ON tkg_edges(dst_id)",
            "CREATE INDEX IF NOT EXISTS idx_tkg_edge_time ON tkg_edges(valid_from DESC)",
            # ---------------- 主动关怀回访队列（AMBRACE 约定回访 + Sibyl 前瞻） ----------------
            """
            CREATE TABLE IF NOT EXISTS proactive_queue (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                umo        TEXT NOT NULL DEFAULT '',
                target     TEXT NOT NULL DEFAULT '',
                due_at     REAL NOT NULL DEFAULT 0,
                content    TEXT NOT NULL DEFAULT '',
                kind       TEXT NOT NULL DEFAULT 'callback',
                status     TEXT NOT NULL DEFAULT 'pending',
                attempts   INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_proactive_queue_due ON proactive_queue(status, due_at)",
            "CREATE INDEX IF NOT EXISTS idx_proactive_queue_umo ON proactive_queue(umo, status)",
        ),
    ),
)
"""迁移列表。当前 schema 版本 = 最后一项的 version。"""

CURRENT_VERSION: int = MIGRATIONS[-1].version if MIGRATIONS else 0

FTS_STATEMENTS: tuple[str, ...] = _FTS_STATEMENTS
"""FTS 建表语句单独管理：FTS5 可能不被某些 SQLite 构建支持，失败时需降级而非中断迁移。"""
