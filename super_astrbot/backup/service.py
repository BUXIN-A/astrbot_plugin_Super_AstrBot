"""全局备份：把插件的**全部业务表**连同配置与数据库快照打成一个 zip。

设计取舍：

- **为什么是全表而不是挑几张表**：早先的做法按「记忆 / 现实桥 / 每周总结 …」逐类导出，
  结果是风格样本、群内用语、好感度、知识图谱、待审队列、身份观测、反思记录这些数据
  要么只能留档、要么根本没进包——恢复时就成了「数据丢了」。全表导出把这件事一次抹平：
  包里有什么，恢复后库里就有什么，不依赖某张表有没有专门的导入管线。
- **为什么同时留一份数据库文件**：JSON 表导出用于在运行中的实例里回灌（不需要停插件），
  SQLite 快照用于离线/异地恢复；两者都在包里，选哪种由使用者的场景决定。
- **为什么还留一份「视图」**：``views/*.json`` 是可读的语义视图（记忆、现实桥、图谱…），
  供人工查看与跨插件同步使用；**恢复不读它**，避免两套表示互相漂移。
- **恢复语义**：按表 ``INSERT OR REPLACE``（备份优先）——备份里有的行一定在，备份之后
  新产生的行不动。覆盖前由调用方先做数据库快照，所以「覆盖错了」也可回滚。
- **派生数据**：``memory_index``（FTS 虚拟表）不进包，恢复后由正文重建；
  ``memory_vectors`` 是普通表且重建需要调嵌入接口，因此照常导出与回灌。
- **运行态不外带**：``kv_state`` / ``write_ops`` 是调度幂等、节流游标与崩溃恢复日志，
  换一个实例就没有意义，明确列为排除项而不是悄悄丢掉。

本模块不依赖 AstrBot：数据来源是注入的 ``Database``，视图由装配层以 ``views`` 映射注入。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

BACKUP_DIR_NAME = "backups"
DEFAULT_KEEP = 5
"""备份包保留份数（超出后按时间淘汰最旧的）。"""

MANIFEST_NAME = "manifest.json"
README_NAME = "README.txt"
DB_ENTRY = "database/super_astrbot.db"
TABLE_DIR = "tables"
VIEW_DIR = "views"

BACKUP_TABLES: tuple[str, ...] = (
    # 恢复顺序：记忆 → 派生关联 → 现实桥 → 图谱 → 拟人化学习 → 审批/留痕 → 观测/指标。
    # 表之间没有外键约束（迁移里刻意不建），顺序只影响可读性。
    "memories",
    "memory_vectors",
    "memory_links",
    "journals",
    "graph_entities",
    "graph_relations",
    "memory_entities",
    "style_patterns",
    "jargons",
    "affinity_state",
    "pending_reviews",
    "reflection_logs",
    "identity_seen",
    "metric_series",
)
"""参与全局备份与恢复的业务表。新增表时同步登记在这里，否则会被备份遗漏。"""

RESTORE_MERGE = "merge"
RESTORE_REPLACE = "replace"
RESTORE_MODES: tuple[str, ...] = (RESTORE_MERGE, RESTORE_REPLACE)
"""恢复模式：``merge`` 逐表合并（备份优先，默认）；``replace`` 完全覆盖（先清后灌）。"""


def normalize_mode(value: Any) -> str:
    """归一恢复模式；未知取值回退 ``merge``（保持既有行为，不因参数写错而清库）。"""
    token = str(value or "").strip().lower()
    return token if token in RESTORE_MODES else RESTORE_MERGE


EXCLUDED_TABLES: dict[str, str] = {
    "schema_version": "迁移账本：恢复会破坏版本一致性，必须由代码按迁移链推进",
    "memory_index": "FTS 虚拟表：恢复后由正文重建（正文已在 memories 里）",
    "kv_state": "运行态：调度幂等、节流游标、迁移留痕，换实例没有意义",
    "write_ops": "崩溃恢复日志：仅在写入过程中有效，跨实例无意义",
    "sqlite_sequence": "SQLite 内部自增值：由插入自动维护",
}
"""明确排除的表及原因（写进 manifest，避免「以为备份全了」）。"""


@dataclass
class BackupArtifact:
    """一次备份的结果。"""

    filename: str
    path: Path
    size: int
    created_at: float
    schema_version: int
    items: dict[str, int] = field(default_factory=dict)
    """包内各条目的字节数（便于界面直接展示「装了什么」）。"""

    tables: dict[str, int] = field(default_factory=dict)
    """表名 → 行数。"""

    def as_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "path": str(self.path),
            "size": self.size,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
            "items": self.items,
            "tables": self.tables,
        }


@dataclass
class RestoreOutcome:
    """一次恢复的结果。"""

    mode: str = RESTORE_MERGE
    tables: dict[str, int] = field(default_factory=dict)
    """表名 → 实际写入行数。"""

    skipped_tables: dict[str, int] = field(default_factory=dict)
    """表名 → 包内行数（因本版本无此列/无此表而整体跳过时记录，便于排查）。"""

    manifest: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    legacy_views: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    """旧格式（format 1）包里的 ``data/*.json`` 视图：没有 ``tables/`` 时按它们尽力恢复。"""

    existing: dict[str, int] = field(default_factory=dict)
    """预览时的现状：表名 → 当前库行数（估算「将删除多少行」用）。"""

    estimated_deleted: dict[str, int] = field(default_factory=dict)
    """预览时的估算：表名 → 预计被删掉的行数（下界，见 ``preview`` 文档）。"""

    @property
    def rows_written(self) -> int:
        return sum(self.tables.values())


class RestoreError(Exception):
    """备份包不可用（不是 zip、缺少清单等）。"""


def _readme(tables: Sequence[str]) -> str:
    table_lines = "\n".join(f"  tables/{name}.json" for name in tables)
    return f"""Super_AstrBot 全局备份包
========================

包内结构
--------
manifest.json              元信息（版本、schema、逐表行数、文件大小与 sha256、排除项及原因）
config/plugin_config.json  插件配置（可在面板「系统 → 配置维护」导入恢复）
database/super_astrbot.db  数据库一致性快照（离线整库恢复用）
tables/*.json              全部业务表的完整字段导出（**恢复用这份**）：
{table_lines}
views/*.json               可读视图（记忆 / 现实桥 / 图谱 …），仅供人工查看与跨插件同步，
                           恢复不读它，避免两套表示漂移

恢复方式
--------
1. 面板恢复（推荐，无需停插件）：系统 → 备份与导出 → 「导入备份 ZIP」。
   逐表 INSERT OR REPLACE（备份优先）：备份里有的行一定恢复，备份之后新产生的行不受影响；
   恢复前插件会自动做一次数据库快照。配置同时热应用，FTS 关键词索引自动重建。
2. 离线整库恢复：停用插件 → 用 database/super_astrbot.db 覆盖
   data/plugin_data/<插件名>/super_astrbot.db → 启用插件。覆盖前请先另存当前库。
3. 只恢复某类数据：用面板对应页面的「导入 JSON」（记忆 / 现实桥 / 每周总结），
   或从 tables/ 里挑表手工处理（需要自备 SQLite 工具）。
4. 校验完整性：对每个文件重算 sha256 与 manifest.json 比对。

未包含（manifest 的 excluded_tables 有完整原因）
----------------------------------------------
schema_version（迁移账本）、memory_index（FTS 虚拟表，恢复后按正文重建）、
kv_state / write_ops（运行态与崩溃恢复日志）、sqlite_sequence（SQLite 内部）。

注意：包内可能含私人对话与身份标识（sender_id / sender_name），请按隐私材料保管。
"""


class BackupService:
    """全局备份与恢复。"""

    MANIFEST_NAME = MANIFEST_NAME
    README_NAME = README_NAME
    DB_ENTRY = DB_ENTRY
    BACKUP_DIR_NAME = BACKUP_DIR_NAME
    """包内/目录固定名：导入侧（``app.panel_backup_import``）据此定位内容。"""

    def __init__(
        self,
        *,
        data_dir: Path,
        db: Any | None,
        views: Mapping[str, Callable[[], Awaitable[Any]]] | None = None,
        config_provider: Callable[[], Any] | None = None,
        plugin_version: str = "",
        schema_version: int = 0,
        tables: Sequence[str] = BACKUP_TABLES,
        keep: int = DEFAULT_KEEP,
        logger: Any | None = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._db = db
        self._views = dict(views or {})
        self._config_provider = config_provider
        self._plugin_version = plugin_version
        self._schema_version = schema_version
        self._tables = tuple(tables)
        self._keep = max(1, int(keep))
        self._logger = logger

    # ------------------------------------------------------------------ #
    # 属性
    # ------------------------------------------------------------------ #

    @property
    def backup_dir(self) -> Path:
        return self._data_dir / BACKUP_DIR_NAME

    @property
    def keep(self) -> int:
        return self._keep

    @property
    def tables(self) -> tuple[str, ...]:
        return self._tables

    def view_names(self) -> list[str]:
        """可读视图名（面板展示用）。"""
        return list(self._views)

    # ------------------------------------------------------------------ #
    # 打包
    # ------------------------------------------------------------------ #

    async def build(
        self,
        *,
        include_database: bool = True,
        include_data: bool = True,
        include_config: bool = True,
        notes: str = "",
        now: float | None = None,
    ) -> BackupArtifact:
        """生成全局备份包；返回可下载/可定位的结果。"""
        moment = now if now is not None else time.time()
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(moment))
        filename = f"super_astrbot_backup_{stamp}.zip"
        target_dir = self.backup_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        temp = target.with_suffix(".zip.part")

        items: dict[str, int] = {}
        manifest_files: dict[str, dict[str, Any]] = {}
        table_counts: dict[str, int] = {}
        view_counts: dict[str, int] = {}
        missing_tables: list[str] = []

        readme = _readme(self._tables if include_data else ())
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(README_NAME, readme)
            items[README_NAME] = len(readme.encode("utf-8"))

            if include_config and self._config_provider is not None:
                payload = self._safe_collect("配置", self._config_provider)
                if payload is not None:
                    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                    name = "config/plugin_config.json"
                    archive.writestr(name, data)
                    items[name] = len(data)
                    manifest_files[name] = _digest(data)

            if include_data:
                for table in self._tables:
                    if self._db is None:
                        missing_tables.append(table)
                        continue
                    try:
                        rows = await self._db.dump_table(table)
                    except Exception as exc:  # 单表失败不应让整包失败
                        self._warn("导出表 %s 失败：%s", table, exc)
                        missing_tables.append(table)
                        continue
                    data = json.dumps(encode_rows(rows), ensure_ascii=False).encode("utf-8")
                    name = f"{TABLE_DIR}/{table}.json"
                    archive.writestr(name, data)
                    items[name] = len(data)
                    manifest_files[name] = _digest(data)
                    table_counts[table] = len(rows)

                for view_name, loader in self._views.items():
                    payload = await self._safe_collect_async(view_name, loader)
                    if payload is None:
                        continue
                    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                    name = f"{VIEW_DIR}/{view_name}.json"
                    archive.writestr(name, data)
                    items[name] = len(data)
                    manifest_files[name] = _digest(data)
                    view_counts[view_name] = _count_of(payload)

            if include_database and self._db is not None:
                snapshot = await self._db.snapshot_to(target_dir / f".{filename}.db")
                if snapshot is not None and snapshot.exists():
                    data = snapshot.read_bytes()
                    archive.writestr(DB_ENTRY, data)
                    items[DB_ENTRY] = len(data)
                    manifest_files[DB_ENTRY] = _digest(data)
                    try:
                        snapshot.unlink()
                    except OSError:
                        pass
                else:
                    self._warn("数据库快照不可用，备份包将不含原始库")

            manifest = {
                "kind": "super_astrbot.backup",
                "format": 2,
                "created_at": moment,
                "plugin_version": self._plugin_version,
                "schema_version": self._schema_version,
                "include": {
                    "config": bool(include_config),
                    "database": bool(include_database),
                    "data": bool(include_data),
                },
                "notes": str(notes or ""),
                "tables": table_counts,
                "rows_total": sum(table_counts.values()),
                "views": view_counts,
                "missing_tables": missing_tables,
                "excluded_tables": dict(EXCLUDED_TABLES),
                "restore": {
                    "semantics": "insert_or_replace",
                    "source": f"{TABLE_DIR}/*.json",
                    "note": "备份优先：备份里有的行一定恢复，备份之后新产生的行不受影响；恢复前自动快照",
                },
                "files": manifest_files,
            }
            raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            archive.writestr(MANIFEST_NAME, raw)
            items[MANIFEST_NAME] = len(raw)

        temp.replace(target)
        artifact = BackupArtifact(
            filename=filename,
            path=target,
            size=target.stat().st_size,
            created_at=moment,
            schema_version=self._schema_version,
            items=items,
            tables=table_counts,
        )
        self._prune()
        self._info(
            "全局备份包已生成：%s（%d 张表 / %d 行 / %d 字节）",
            filename,
            len(table_counts),
            artifact.tables and sum(table_counts.values()),
            artifact.size,
        )
        return artifact

    # ------------------------------------------------------------------ #
    # 恢复
    # ------------------------------------------------------------------ #

    def read_manifest(self, raw: bytes) -> dict[str, Any]:
        """读取包内清单（不做写入）。"""
        with self._open(raw) as archive:
            return self._manifest_of(archive)

    async def restore(
        self, raw: bytes, *, dry_run: bool = False, mode: str = RESTORE_MERGE
    ) -> RestoreOutcome:
        """按表回灌备份包。

        - ``mode="merge"``：``INSERT OR REPLACE``，备份优先，不动备份之外的行；
        - ``mode="replace"``：先清空该表再灌，恢复后与备份逐表一致（多余行被删除）；
        - ``dry_run=True``：只统计（不写库、不留快照），并给出每张表的现状行数与
          「预计删除行数」下界——现状行数减去备份行数，备份与现状主键集合有差异时
          实际删除会更多，因此对外一律标注为估计值。
        """
        selected = normalize_mode(mode)
        outcome = RestoreOutcome(mode=selected)
        if self._db is None:
            raise RestoreError("数据库不可用，无法恢复")

        with self._open(raw) as archive:
            names = set(archive.namelist())
            outcome.manifest = self._manifest_of(archive)
            if not any(name.startswith(f"{TABLE_DIR}/") for name in names):
                outcome.legacy_views = self._read_legacy_views(archive, names)
                if outcome.legacy_views:
                    outcome.notes.append(
                        "这是旧格式（format 1）备份包：按 data/*.json 尽力恢复，"
                        "其余数据请用包内数据库快照做整库恢复"
                    )
                return outcome

            payloads: dict[str, list[dict[str, Any]]] = {}
            for table in self._tables:
                entry = f"{TABLE_DIR}/{table}.json"
                if entry not in names:
                    continue
                try:
                    rows = json.loads(archive.read(entry).decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    outcome.notes.append(f"{table}：解析失败（{exc}）")
                    continue
                if not isinstance(rows, list):
                    outcome.notes.append(f"{table}：结构不是数组，已跳过")
                    continue
                payloads[table] = decode_rows(rows)

            if not payloads:
                return outcome

            if dry_run:
                for table, rows in payloads.items():
                    outcome.tables[table] = len(rows)
                    try:
                        current = await self._db.count_rows(table)
                    except Exception:  # 当前版本没有这张表
                        continue
                    outcome.existing[table] = current
                    outcome.estimated_deleted[table] = max(0, current - len(rows))
                return outcome

            # 逐表写入放在一个事务里：任一表失败整体回滚，不留半清半灌的中间状态
            try:
                written = await self._db.restore_tables(
                    payloads, replace=selected == RESTORE_REPLACE
                )
            except Exception as exc:
                raise RestoreError(f"恢复失败，已整体回滚：{exc}") from exc

            for table, rows in payloads.items():
                outcome.tables[table] = int(written.get(table, 0))
                omitted = len(rows) - outcome.tables[table]
                if omitted > 0:
                    outcome.notes.append(f"{table}：{omitted} 行因缺少可用列未写入")

        if outcome.notes:
            for note in outcome.notes:
                self._warn("恢复提示：%s", note)
        return outcome

    def _read_legacy_views(
        self, archive: zipfile.ZipFile, names: set[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """读旧格式包的 ``data/*.json``（没有全表导出时的兜底）。

        旧包覆盖哪些类别是不确定的（当初也是按类挑着导），因此这里只挑出
        **有导入管线**的那几类，其余交给数据库快照。
        """
        found: dict[str, list[dict[str, Any]]] = {}
        for key in ("memories", "journals", "weeklies"):
            entry = f"data/{key}.json"
            if entry not in names:
                continue
            try:
                payload = json.loads(archive.read(entry).decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                self._warn("旧格式视图 %s 解析失败：%s", key, exc)
                continue
            items = payload.get("items") if isinstance(payload, dict) else payload
            if isinstance(items, list):
                found[key] = [item for item in items if isinstance(item, dict)]
        return found

    def _open(self, raw: bytes) -> zipfile.ZipFile:
        if not raw:
            raise RestoreError("上传内容为空")
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise RestoreError("不是有效的 zip 备份包") from exc
        names = set(archive.namelist())
        if MANIFEST_NAME not in names and DB_ENTRY not in names:
            archive.close()
            raise RestoreError("zip 内没有 super_astrbot 备份内容（缺少 manifest.json）")
        return archive

    @staticmethod
    def _manifest_of(archive: zipfile.ZipFile) -> dict[str, Any]:
        if MANIFEST_NAME not in set(archive.namelist()):
            return {}
        try:
            payload = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    # ------------------------------------------------------------------ #
    # 列表与保留
    # ------------------------------------------------------------------ #

    def list_backups(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """列出历史备份包（按时间倒序）。"""
        try:
            files = sorted(
                self.backup_dir.glob("super_astrbot_backup_*.zip"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return []
        result: list[dict[str, Any]] = []
        for item in files[:limit]:
            try:
                stat = item.stat()
            except OSError:
                continue
            result.append(
                {
                    "filename": item.name,
                    "path": str(item),
                    "size": stat.st_size,
                    "created_at": stat.st_mtime,
                }
            )
        return result

    def _prune(self) -> int:
        """只保留最近 ``keep`` 份备份包。"""
        removed = 0
        try:
            files = sorted(
                self.backup_dir.glob("super_astrbot_backup_*.zip"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return 0
        for stale in files[self._keep :]:
            try:
                stale.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #

    def _safe_collect(self, label: str, loader: Callable[[], Any]) -> Any:
        try:
            return loader()
        except Exception as exc:
            self._warn("收集%s失败：%s", label, exc)
            return None

    async def _safe_collect_async(self, label: str, loader: Callable[[], Awaitable[Any]]) -> Any:
        try:
            return await loader()
        except Exception as exc:
            self._warn("收集视图 %s 失败：%s", label, exc)
            return None

    def _info(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.info(message, *args)

    def _warn(self, message: str, *args: Any) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)


_BYTES_KEY = "$b64"
"""二进制列的 JSON 包装键：SQLite 的 BLOB（如 ``memory_vectors.vector``）不能直接进 JSON，
用 ``{"$b64": "..."}`` 自描述包装，回灌时再还原成 bytes。"""


def encode_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {_BYTES_KEY: base64.b64encode(bytes(value)).decode("ascii")}
    return value


def encode_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """把整行里不可 JSON 化的列（BLOB）包装成可序列化结构。"""
    return [{key: encode_value(value) for key, value in row.items()} for row in rows]


def decode_value(value: Any) -> Any:
    """只还原「形如单一 ``$b64`` 键」的字典，避免误伤业务里的普通嵌套对象。"""
    if isinstance(value, dict) and len(value) == 1 and _BYTES_KEY in value:
        encoded = value[_BYTES_KEY]
        if isinstance(encoded, str):
            try:
                return base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                return value
    return value


def decode_rows(rows: Sequence[Any]) -> list[dict[str, Any]]:
    decoded: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        decoded.append({key: decode_value(value) for key, value in row.items()})
    return decoded


def _digest(data: bytes) -> dict[str, Any]:
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _count_of(payload: Any) -> int:
    """尽力给出「这个文件装了多少条」；拿不到就记 0。"""
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("items", "count"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
            if isinstance(value, int):
                return value
        return len(payload)
    return 0
