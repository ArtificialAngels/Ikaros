"""
v4.migrate_from_v3 — V3 → V4 数据迁移脚本

哥哥 (2026-07-05) 拍 C: 跳过低 weight (< 0.5), 只迁移 0.5+ 的记忆
(weight 是 V3 自评质量分, 低分记忆不值得带进 V4)

用法:
  python v4/migrate_from_v3.py            # 跑迁移 (默认路径)
  python v4/migrate_from_v3.py --dry-run  # 只统计, 不写入
  python v4/migrate_from_v3.py --src PATH --dst PATH  # 自定义路径

V3 db 默认: E:\\Ikaros\\Ikaros-memory\\data\\v3.db
V4 db 默认: E:\\Ikaros\\Ikaros-memory\\data\\v4\\v4.db

设计原则:
  - 1:1 迁移 (id, content, type, tags, weight, access_count, last_accessed, created 全保)
  - 跳过低 weight (< 0.5)
  - 跳过已存在 (按 content dedup)
  - 不动 V3 db (只读)
  - 显式错误 (失败时抛, 不静默)
  - dry-run 模式 (统计 + 不写入, 验证迁移范围)
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path

V4_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V4_ROOT.parent))

logger = logging.getLogger("ikaros.memory.v5.migrate")

# V3 db 与 V4 db 同在 E:\Ikaros\Ikaros-memory\data\ 下
# V4_ROOT = E:\Ikaros\Ikaros-memory\  (parent of v4/ dir)
# V3 db: V4_ROOT / "data" / "v3.db"  (不是 .parent! 那是上层)
# V4 db: V4_ROOT / "data" / "v4" / "v4.db"
V3_DB_PATH = V4_ROOT / "data" / "v3.db"
V4_DB_PATH = V4_ROOT / "data" / "v4" / "v4.db"

# 哥哥 (2026-07-05) 拍 C: 跳过低 weight
WEIGHT_THRESHOLD = 0.5

# 迁移字段 (V3 schema 与 V4 schema 一致, 直接复制)
MIGRATABLE_FIELDS = (
    "content", "type", "tags", "weight", "access_count",
    "last_accessed", "created",
)


# ─── helpers ──────────────────────────────────────────────────

def _connect_ro(path: Path) -> sqlite3.Connection:
    """只读连接 V3 (防止误改)."""
    if not path.exists():
        raise FileNotFoundError(f"V3 db not found: {path}")
    # 绝对路径 + forward slashes (sqlite3 URI 跨平台)
    abs_path = path.resolve()
    # Windows 路径 C:\foo\bar → file:C:/foo/bar?mode=ro
    posix_path = abs_path.as_posix()
    uri = f"file:{posix_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _fetch_v3_memories(v3_path: Path, threshold: float) -> list[sqlite3.Row]:
    """从 V3 db 读所有 weight >= threshold 的记忆."""
    with _connect_ro(v3_path) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            f"SELECT id, content, type, tags, weight, access_count, "
            f"last_accessed, created "
            f"FROM memory "
            f"WHERE weight >= ? "
            f"ORDER BY weight DESC, created DESC",
            (threshold,),
        ).fetchall()
    return rows


def _fetch_v4_existing_contents(v4_path: Path) -> set[str]:
    """读 V4 db 现有 content (用于 dedup)."""
    if not v4_path.exists():
        return set()
    with sqlite3.connect(str(v4_path)) as c:
        rows = c.execute("SELECT content FROM memory").fetchall()
    return {r[0] for r in rows}


def _insert_v4(v4_path: Path, rows: list[sqlite3.Row], dry_run: bool) -> int:
    """写 V4 db (跳过已存在)."""
    if dry_run:
        return len(rows)

    existing = _fetch_v4_existing_contents(v4_path)
    inserted = 0
    skipped_dup = 0

    # V4 db 不存在时, 先确保 schema (FTS5 + triggers)
    if not v4_path.exists():
        v4_path.parent.mkdir(parents=True, exist_ok=True)
        # 用 v4.store 的 conn() 创建 schema
        from v5 import store as v5_store
        v5_store.V4_DATA_DIR = v4_path.parent
        v5_store.V4_DB_PATH = v4_path
        v5_store.close()  # 清掉旧 thread-local
        with v5_store.conn() as c:
            pass  # conn() 内 lazy init schema

    with sqlite3.connect(str(v4_path)) as c:
        for row in rows:
            if row["content"] in existing:
                skipped_dup += 1
                continue
            c.execute(
                "INSERT INTO memory (content, type, tags, weight, "
                "access_count, last_accessed, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row["content"],
                    row["type"],
                    row["tags"] or "",
                    float(row["weight"]),
                    int(row["access_count"]),
                    float(row["last_accessed"]),
                    float(row["created"]),
                ),
            )
            existing.add(row["content"])
            inserted += 1
        c.commit()

    logger.info("inserted %d, skipped %d duplicate", inserted, skipped_dup)
    return inserted


# ─── main ─────────────────────────────────────────────────────

def migrate(
    v3_path: Path = V3_DB_PATH,
    v4_path: Path = V4_DB_PATH,
    threshold: float = WEIGHT_THRESHOLD,
    dry_run: bool = False,
) -> dict:
    """从 V3 迁移到 V4.

    Returns: {total_v3, above_threshold, skipped_low, inserted, skipped_dup, error}
    """
    t0 = time.time()
    try:
        # 1. V3 总览
        with _connect_ro(v3_path) as c:
            total_v3 = c.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
            by_type = c.execute(
                "SELECT type, COUNT(*), ROUND(AVG(weight), 3) FROM memory GROUP BY type"
            ).fetchall()

        # 2. 读待迁移
        rows = _fetch_v3_memories(v3_path, threshold)
        above_threshold = len(rows)
        skipped_low = total_v3 - above_threshold

        # 3. 写入 V4
        inserted = _insert_v4(v4_path, rows, dry_run)

        elapsed = time.time() - t0
        return {
            "v3_total": total_v3,
            "v3_by_type": {r[0]: {"count": r[1], "avg_weight": r[2]} for r in by_type},
            "threshold": threshold,
            "above_threshold": above_threshold,
            "skipped_low_weight": skipped_low,
            "inserted_to_v4": inserted,
            "dry_run": dry_run,
            "elapsed_sec": elapsed,
            "error": None,
        }
    except Exception as e:
        logger.exception("migration failed")
        return {"error": str(e), "elapsed_sec": time.time() - t0}


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="V3 → V4 memory migration")
    parser.add_argument("--src", help=f"V3 db path (default: {V3_DB_PATH})")
    parser.add_argument("--dst", help=f"V4 db path (default: {V4_DB_PATH})")
    parser.add_argument("--threshold", type=float, default=WEIGHT_THRESHOLD,
                        help=f"weight threshold (default: {WEIGHT_THRESHOLD})")
    parser.add_argument("--dry-run", action="store_true",
                        help="只统计, 不写入")
    args = parser.parse_args()

    src = Path(args.src) if args.src else V3_DB_PATH
    dst = Path(args.dst) if args.dst else V4_DB_PATH

    result = migrate(
        v3_path=src, v4_path=dst,
        threshold=args.threshold, dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    main()
