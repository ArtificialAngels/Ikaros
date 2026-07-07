"""
v4.store — V4 记忆存储层

设计目标:
  - API 与 V3 store() 兼容 (Phase 4 切换期平滑过渡)
  - 显式错误: 不像 V3 内部 try/except 吞错
  - 写回缓存 (复用 V3 思路, 但 V4 子目录)
  - 短/长期 memory flag (与 V3 一致)

V3 参考: ikaros-memory-v3.py 第 1-30 行 API 表面
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

logger = logging.getLogger("ikaros.memory.v4.store")

# V4 状态文件: Ikaros-memory/data/v4/v4.db (与 V3 v3.db 并列)
# 注意: scheduler.py 用的是 data/v4/reflect_state.json, 这里用 v4.db
# 两者都在 data/v4/ 下, 但文件名不同 (一个 db, 一个 json)
# V4 db 位置: Ikaros-memory/data/v4/v4.db (与 V3 v3.db 并列, 不污染)
# 注意: __file__ = Ikaros-memory/v4/store.py
#   parent = Ikaros-memory/v4/
#   parent.parent = Ikaros-memory/  ← 这就是 V4_ROOT
#   V4_ROOT / "data" / "v4" = Ikaros-memory/data/v4/
V4_ROOT = Path(__file__).resolve().parent.parent
V4_DATA_DIR = V4_ROOT / "data" / "v4"
V4_DB_PATH = V4_DATA_DIR / "v4.db"

# V3 schema 直接复用 (Phase 4 切换期不需要 migrate)
SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'fact',
    tags TEXT DEFAULT '',
    weight REAL NOT NULL DEFAULT 0.6,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed REAL NOT NULL DEFAULT 0,
    created REAL NOT NULL DEFAULT (strftime('%s','now')),
    short_term INTEGER NOT NULL DEFAULT 1,
    long_term INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_memory_type ON memory(type);
CREATE INDEX IF NOT EXISTS idx_memory_weight ON memory(weight);
CREATE INDEX IF NOT EXISTS idx_memory_last_accessed ON memory(last_accessed);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    content, type, tags,
    content='memory',
    content_rowid='id'
);

-- V3 触发器: FTS5 同步 (V4 复用, Phase 4 切换期兼容)
CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN
    INSERT INTO memory_fts(rowid, content, type, tags)
    VALUES (new.id, new.content, new.type, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, content, type, tags)
    VALUES ('delete', old.id, old.content, old.type, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, content, type, tags)
    VALUES ('delete', old.id, old.content, old.type, old.tags);
    INSERT INTO memory_fts(rowid, content, type, tags)
    VALUES (new.id, new.content, new.type, new.tags);
END;
"""


# ─── 连接管理 (与 V3 思路一致, V4 简化) ────────────────────────

# V4 改进: _conn 不再是模块级全局, 改用 thread-local.
# 原因: 模块级 _conn 在测试间会污染 (前一个测试关不掉).
# V3 模块级 _conn 是历史包袱, V4 干净.
import threading

_tls = threading.local()


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    """获取 V4 db 连接 (thread-local, lazy init).

    V4 行为:
      - 每个线程独立连接 (V3 模块级共享, 跨线程不安全)
      - 首次调用建库
      - 出错时显式抛, 不吞
    """
    c = getattr(_tls, "c", None)
    if c is None:
        V4_DATA_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(V4_DB_PATH))
        c.row_factory = sqlite3.Row
        # 多进程并发: 看门狗(反思 op)与 cloud_chat(store) 可能同时访问 v4.db
        # busy_timeout 让写入方等待而非立刻 "database is locked"
        try:
            c.execute("PRAGMA busy_timeout=30000")
        except Exception:
            pass
        c.executescript(SCHEMA)
        c.commit()
        _tls.c = c
        logger.info("V4 store: initialized at %s", V4_DB_PATH)
    yield c


def close() -> None:
    """关闭当前线程的连接 (测试 / 切换 db 时用)."""
    c = getattr(_tls, "c", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
        _tls.c = None


# ─── 核心 API (V3 兼容) ──────────────────────────────────────

@dataclass(frozen=True)
class Memory:
    """V4 Memory 数据类 (V3 dict 风格 → V4 typed).

    V3 返 dict, 调用方易拼错字段名。
    V4 返 frozen dataclass, IDE 提示 + 不可变。
    """
    id: int
    content: str
    type: str
    tags: str
    weight: float
    access_count: int
    last_accessed: float
    created: float
    short_term: bool
    long_term: bool

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Memory":
        return cls(
            id=row["id"],
            content=row["content"],
            type=row["type"],
            tags=row["tags"] or "",
            weight=float(row["weight"]),
            access_count=int(row["access_count"]),
            last_accessed=float(row["last_accessed"]),
            created=float(row["created"]),
            short_term=bool(row["short_term"]),
            long_term=bool(row["long_term"]),
        )


def store(content: str, type: str = "fact", weight: float = 0.6,
          tags: str = "") -> int:
    """存一条记忆, 返 id.

    V3 兼容 API: 同样 4 个参数 + 同样返 int.
    V4 改进:
      - weight 显式 clamp 到 [0, 1]
      - 失败时抛, 不返 -1
    """
    weight = max(0.0, min(1.0, weight))
    with conn() as c:
        cur = c.execute(
            "INSERT INTO memory (content, type, tags, weight) VALUES (?, ?, ?, ?)",
            (content, type, tags, weight),
        )
        c.commit()
        mid = int(cur.lastrowid)
    # A1 修复: 写库后 best-effort 同步向量到 Chroma (失败不影响主流程)
    _sync_vector_best_effort(mid, content, type, tags, weight)
    return mid


def _sync_vector_best_effort(memory_id: int, content: str, type: str,
                             tags: str, weight: float) -> bool:
    """写库后 best-effort 把向量同步进 Chroma.

    - 仅在 chromadb 可用时生效 (否则静默跳过, 不报错)
    - :8587 不可用 / 嵌入失败 → 返回 False, 由后续 vector_sync 反思 op 兜底
    """
    try:
        from v4.search import VectorIndex
    except Exception as e:
        logger.debug("vector sync skipped (import): %s", e)
        return False
    try:
        idx = VectorIndex()
        ok = idx.add(memory_id, content, type=type, tags=tags, weight=weight)
        if not ok:
            logger.debug("vector sync returned False for id=%s", memory_id)
        return ok
    except Exception as e:
        logger.warning("vector sync failed for id=%s: %s", memory_id, e)
        return False


def get(memory_id: int) -> Memory | None:
    """按 id 取单条. 找不到返 None."""
    with conn() as c:
        row = c.execute("SELECT * FROM memory WHERE id = ?", (memory_id,)).fetchone()
        return Memory.from_row(row) if row else None


def search(query: str, top_k: int = 5, min_weight: float = 0.0) -> list[Memory]:
    """FTS5 关键词搜索 (V3 兼容).

    V4 行为: 返 typed Memory, 不返 dict.
    """
    with conn() as c:
        rows = c.execute(
            "SELECT m.* FROM memory m "
            "JOIN memory_fts f ON m.id = f.rowid "
            "WHERE memory_fts MATCH ? "
            "  AND m.weight >= ? "
            "ORDER BY bm25(memory_fts) "
            "LIMIT ?",
            (query, min_weight, top_k),
        ).fetchall()
    return [Memory.from_row(r) for r in rows]


def list_all(limit: int = 50, type_filter: str | None = None) -> list[Memory]:
    """列出记忆 (调试用)."""
    with conn() as c:
        if type_filter:
            rows = c.execute(
                "SELECT * FROM memory WHERE type = ? ORDER BY id DESC LIMIT ?",
                (type_filter, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM memory ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [Memory.from_row(r) for r in rows]


def delete(memory_id: int) -> bool:
    """删一条. 返 True/False."""
    with conn() as c:
        cur = c.execute("DELETE FROM memory WHERE id = ?", (memory_id,))
        c.commit()
        return cur.rowcount > 0


def access(memory_id: int) -> None:
    """记录访问 + weight +0.05 (与 V3 一致)."""
    with conn() as c:
        c.execute(
            "UPDATE memory SET "
            "  access_count = access_count + 1, "
            "  last_accessed = strftime('%s','now'), "
            "  weight = MIN(1.0, weight + 0.05) "
            "WHERE id = ?",
            (memory_id,),
        )
        c.commit()


def stats() -> dict:
    """v3.stats() 兼容 API."""
    with conn() as c:
        total = c.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
        by_type = c.execute(
            "SELECT type, COUNT(*), ROUND(AVG(weight), 3) FROM memory GROUP BY type"
        ).fetchall()
        long_term = c.execute(
            "SELECT COUNT(*) FROM memory WHERE long_term = 1"
        ).fetchone()[0]
    return {
        "total": total,
        "long_term": long_term,
        "avg_weight": float(c.execute("SELECT AVG(weight) FROM memory").fetchone()[0] or 0),
        "by_type": {r[0]: {"count": r[1], "avg_weight": r[2]} for r in by_type},
        "db_size_bytes": V4_DB_PATH.stat().st_size if V4_DB_PATH.exists() else 0,
        "db_path": str(V4_DB_PATH),
    }


# ─── CLI (走 ikaros-mem.bat v4) ──────────────────────────────

def main():
    import argparse
    import json
    parser = argparse.ArgumentParser(description="Ikaros Memory V4")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats").set_defaults(fn=lambda a: print(json.dumps(stats(), indent=2, ensure_ascii=False)))

    p_store = sub.add_parser("store")
    p_store.add_argument("content")
    p_store.add_argument("--type", default="fact")
    p_store.add_argument("--weight", type=float, default=0.6)
    p_store.add_argument("--tags", default="")
    p_store.set_defaults(fn=lambda a: print(store(a.content, a.type, a.weight, a.tags)))

    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_search.add_argument("--top-k", type=int, default=5)
    p_search.add_argument("--min-weight", type=float, default=0.0)
    p_search.set_defaults(fn=lambda a: print(json.dumps(
        [m.__dict__ for m in search(a.query, a.top_k, a.min_weight)],
        indent=2, ensure_ascii=False,
    )))

    p_get = sub.add_parser("get")
    p_get.add_argument("memory_id", type=int)
    p_get.set_defaults(fn=lambda a: print(json.dumps(
        vars(get(a.memory_id)) if get(a.memory_id) else None,
        indent=2, ensure_ascii=False,
    )))

    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    main()
