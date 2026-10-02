#!/usr/bin/env python3
"""Super_AstrBot（拟人化版）存量重复记忆清理脚本。

运行期写前查重（``super_astrbot/memory/dedup.py`` + ``lifecycle``）只能阻止
新重复产生；本脚本处理线上已堆积的近义重复记忆。相似判定与运行期
``similar_enough`` 完全一致，对每个作用域内的正式记忆做近义聚类，
每组保留信息最全（重要度 > 访问次数 > 正文长度）的一条，其余标记为 forgotten
并清理 FTS / 向量 / 图谱关联（与 ``MemoryLifecycle.forget`` 落库行为一致）。

聚类采用**完全链接**（新成员须与簇内全部成员相似），不做传递闭包——
避免 A~B、B~C 但 A≁C 时把三条并成一组而误删。

周记（``kind='journal'``）是时间线记录，与运行期一样不参与合并，一律跳过。

仅依赖标准库。默认 dry-run 仅预览；确认无误后加 ``--apply`` 真正执行。
执行前请停用插件或备份数据库（``super_astrbot.db``）。

用法:
  python memory_dedup_cleanup.py --db <数据目录>/super_astrbot.db [--apply] [--threshold 0.5]
"""

from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import time
from pathlib import Path

_CLUSTER_BATCH = 2000
"""单作用域参与聚类的记忆条数上限（防止超大作用域 O(n²) 聚类失控）。"""


def load_similar_enough(plugin_root: Path, threshold: float):
    """按文件路径加载插件自带的 ``similar_enough``，保证与运行期逻辑一字不差。

    不走包导入：插件的包 ``__init__`` 会连带装配整个插件，独立脚本不该背这个依赖。
    """
    path = plugin_root / "super_astrbot" / "memory" / "dedup.py"
    if not path.is_file():
        print(f"[错误] 找不到去重模块：{path}")
        raise SystemExit(1)
    spec = importlib.util.spec_from_file_location("sab_memory_dedup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return lambda a, b: module.similar_enough(a, b, threshold)


def cluster_rows(rows, similar) -> list[list[int]]:
    """贪心完全链接聚类：新成员必须与簇内**全部**成员相似才并入。

    行元组约定：``(id, scope_type, scope_id, content, importance, access_count)``。
    """
    clusters: list[list[int]] = []
    for idx in range(len(rows)):
        text = rows[idx][3]
        for cluster in clusters:
            if all(similar(text, rows[other][3]) for other in cluster):
                cluster.append(idx)
                break
        else:
            clusters.append([idx])
    return clusters


def _keep_score(row) -> tuple[float, int, int, int]:
    """保留优先级：重要度 > 访问次数 > 正文长度；并列时取较新（id 更大）。"""
    return (float(row[4] or 0.0), int(row[5] or 0), len(row[3] or ""), int(row[0]))


def cleanup_scope(
    conn: sqlite3.Connection, scope: tuple[str, str], rows, similar, apply: bool
) -> int:
    """对一个作用域做近义聚类与合并，返回将被移除的重复条数。"""
    if len(rows) < 2:
        return 0
    if len(rows) > _CLUSTER_BATCH:
        print(
            f"    [{scope[0]}:{scope[1]}] 作用域内有 {len(rows)} 条，"
            f"仅前 {_CLUSTER_BATCH} 条参与本轮聚类，其余未处理"
        )
        rows = rows[:_CLUSTER_BATCH]

    removed = 0
    now = time.time()
    for group in cluster_rows(rows, similar):
        if len(group) <= 1:
            continue
        members = sorted((rows[i] for i in group), key=_keep_score)
        keep = members[-1]
        for dup in members[:-1]:
            print(
                f"    [{scope[0]}:{scope[1]}] 保留 #{keep[0]} ｜ 移除重复 #{dup[0]}：{dup[3][:40]}"
            )
            removed += 1
            if apply:
                # 与 forget() 的落库行为逐一对齐：状态 + FTS + 向量 + 图谱关联
                conn.execute(
                    "UPDATE memories SET status='forgotten', updated_at=? WHERE id=?",
                    (now, dup[0]),
                )
                conn.execute("DELETE FROM memory_index WHERE rowid=?", (dup[0],))
                conn.execute("DELETE FROM memory_vectors WHERE memory_id=?", (dup[0],))
                conn.execute("DELETE FROM memory_entities WHERE memory_id=?", (dup[0],))
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Super_AstrBot 存量重复记忆清理（默认 dry-run 仅预览）"
    )
    parser.add_argument("--db", required=True, help="super_astrbot.db 的路径")
    parser.add_argument(
        "--plugin-root",
        default=str(Path(__file__).resolve().parent),
        help="插件根目录（用于加载运行期同款去重逻辑），默认脚本所在目录",
    )
    parser.add_argument("--apply", action="store_true", help="真正执行清理（默认仅预览）")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="相似判重阈值（默认 0.5，与运行期一致）",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"[错误] 数据库不存在：{db_path}")
        raise SystemExit(1)

    similar = load_similar_enough(Path(args.plugin_root), args.threshold)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, scope_type, scope_id, content, importance, access_count FROM memories"
        " WHERE status='active' AND kind != 'journal' ORDER BY scope_type, scope_id, id"
    ).fetchall()

    if not rows:
        print("没有可处理的正式记忆。")
        conn.close()
        return

    scopes: dict[tuple[str, str], list] = {}
    for row in rows:
        scopes.setdefault((row["scope_type"], row["scope_id"]), []).append(
            (
                row["id"],
                row["scope_type"],
                row["scope_id"],
                row["content"],
                row["importance"],
                row["access_count"],
            )
        )

    print(f"[scope] 共 {len(scopes)} 个作用域，{len(rows)} 条正式记忆")
    total = 0
    for scope, scope_rows in scopes.items():
        total += cleanup_scope(conn, scope, scope_rows, similar, args.apply)

    if args.apply:
        conn.commit()
    conn.close()

    print(f"\n{'[DRY-RUN] ' if not args.apply else ''}共处理重复记忆：{total}")
    if not args.apply:
        print("（以上为预览，未做任何修改；加 --apply 执行真实清理）")


if __name__ == "__main__":
    main()
