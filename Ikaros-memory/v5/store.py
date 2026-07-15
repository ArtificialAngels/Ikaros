"""
v5.store — V5 记忆存储层 (代码于 2026-07-12 由 v4/ 迁入 v5/, 数据目录仍为 data/v4/)

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

logger = logging.getLogger("ikaros.memory.v5.store")

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
    long_term INTEGER NOT NULL DEFAULT 0,
    -- V5 情感指纹 (PAD 模型: pleasure / arousal / dominance)
    pad_p REAL NOT NULL DEFAULT 0.0,
    pad_a REAL NOT NULL DEFAULT 0.0,
    pad_d REAL NOT NULL DEFAULT 0.0
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
    """获取 V4 db 连接 (每次新连接, 用完即关).

    V4 行为:
      - 每次操作开新连接 (不再缓存 thread-local, 避免读操作用完后
        挂着隐式读事务阻塞后续 write 操作 → "database is locked")
      - 首次调用建库
      - 出错时显式抛, 不吞
      - 上下文退出时自动 commit/rollback + close
    """
    c = getattr(_tls, "c", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass
        _tls.c = None

    V4_DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(V4_DB_PATH))
    c.row_factory = sqlite3.Row
    # 多进程并发: 看门狗(反思 op)与 cloud_chat(store) 可能同时访问 v4.db
    # busy_timeout 让写入方等待而非立刻 "database is locked"
    try:
        c.execute("PRAGMA busy_timeout=5000")
    except Exception:
        pass
    # WAL 模式: 写事务不阻塞读事务
    try:
        c.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    c.executescript(SCHEMA)
    # V5: 给已有表加 PAD 列 (幂等, 已存在则跳过)
    for col in ("pad_p", "pad_a", "pad_d"):
        try:
            c.execute(f"ALTER TABLE memory ADD COLUMN {col} REAL NOT NULL DEFAULT 0.0")
        except (sqlite3.OperationalError, sqlite3.ProgrammingError):
            pass  # 已存在: SQLite 报 duplicate column name
    c.commit()
    logger.info("V4 store: initialized at %s", V4_DB_PATH)
    try:
        yield c
    finally:
        try:
            c.rollback()  # 结束任何未完成的读事务
        except Exception:
            pass
        try:
            c.close()
        except Exception:
            pass


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
    V5 新增: pad_p / pad_a / pad_d (情感指纹, 默认 0.0).
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
    # V5 情感指纹
    pad_p: float = 0.0
    pad_a: float = 0.0
    pad_d: float = 0.0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Memory":
        def _r(key: str, default=0.0):
            try:
                return row[key]
            except (IndexError, KeyError):
                return default
        return cls(
            id=_r("id"),
            content=_r("content", ""),
            type=_r("type", "fact"),
            tags=_r("tags", "") or "",
            weight=float(_r("weight", 0.6)),
            access_count=int(_r("access_count", 0)),
            last_accessed=float(_r("last_accessed", 0.0)),
            created=float(_r("created", 0.0)),
            short_term=bool(_r("short_term", 1)),
            long_term=bool(_r("long_term", 0)),
            pad_p=float(_r("pad_p", 0.0)),
            pad_a=float(_r("pad_a", 0.0)),
            pad_d=float(_r("pad_d", 0.0)),
        )


def store(content: str, type: str = "fact", weight: float = 0.6,
          tags: str = "", *,  # V5: keyword-only args, 不破坏 V3 调用方
          pad_p: float = 0.0, pad_a: float = 0.0, pad_d: float = 0.0) -> int:
    """存一条记忆, 返 id.

    V3 兼容 API: 同样 4 个参数 + 同样返 int.
    V5 新增: pad_p/a/d, keyword-only, 默认 0.0 (不传则不记录情感).

    并发安全: 多进程(看门狗+cloud_chat+Hermes Agent)同时读写 v4.db,
    WAL 模式下写入者等待 busy_timeout=5000ms; 若仍被锁则重试 3 次
    (间隔 1s/3s/5s), 最后一次抛异常 (调用方 decide 是否 swallow).
    """
    import time as _time
    weight = max(0.0, min(1.0, weight))
    last_err = None
    for attempt in range(4):
        try:
            with conn() as c:
                # 写入前主动做 WAL checkpoint, 释放未决帧
                # (Hermes Agent 可能通过其他连接写了大量未 checkpoint 数据)
                try:
                    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass
                cur = c.execute(
                    "INSERT INTO memory (content, type, tags, weight, pad_p, pad_a, pad_d) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (content, type, tags, weight, pad_p, pad_a, pad_d),
                )
                c.commit()
                mid = int(cur.lastrowid)
            _sync_vector_best_effort(mid, content, type, tags, weight)
            return mid
        except sqlite3.OperationalError as e:
            last_err = e
            if "locked" in str(e).lower() and attempt < 3:
                backoff = [1, 3, 5][attempt]
                logger.warning("store: locked, retry %d/3 in %ds", attempt + 1, backoff)
                _time.sleep(backoff)
            else:
                break
    raise RuntimeError(f"store failed after retries: {last_err}") from last_err


def _sync_vector_best_effort(memory_id: int, content: str, type: str,
                             tags: str, weight: float) -> bool:
    """写库后 best-effort 把向量同步进 Chroma.

    - 仅在 chromadb 可用时生效 (否则静默跳过, 不报错)
    - :8587 不可用 / 嵌入失败 → 返回 False, 由后续 vector_sync 反思 op 兜底
    """
    try:
        from v5.search import VectorIndex
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


def _sanitize_fts5_query(query: str) -> str:
    """Sanitize a query string for FTS5 MATCH.

    FTS5 treats . : * " ( ) - AND OR NOT as special syntax.
    Wrap each whitespace-separated token in double quotes to make it a
    literal phrase, preventing syntax errors on real-world input
    (file paths, version numbers, etc.).
    """
    import re as _re
    # Split on whitespace, filter empties
    tokens = [t for t in _re.split(r"\s+", query.strip()) if t]
    if not tokens:
        return ""
    # Wrap each token in double-quotes (FTS5 phrase syntax)
    # Escape internal double-quotes by doubling them (FTS5 escape rule)
    quoted = []
    for t in tokens:
        escaped = t.replace('"', '""')
        quoted.append(f'"{escaped}"')
    return " ".join(quoted)


def search(query: str, top_k: int = 5, min_weight: float = 0.0) -> list[Memory]:
    """FTS5 keyword search (V5, V3-compatible API).

    Sanitizes the query to prevent FTS5 syntax errors on real-world input
    (dots, colons, hyphens, etc. in file paths and version numbers).
    """
    fts_query = _sanitize_fts5_query(query)
    if not fts_query:
        return []
    with conn() as c:
        rows = c.execute(
            "SELECT m.* FROM memory m "
            "JOIN memory_fts f ON m.id = f.rowid "
            "WHERE memory_fts MATCH ? "
            "  AND m.weight >= ? "
            "ORDER BY bm25(memory_fts) "
            "LIMIT ?",
            (fts_query, min_weight, top_k),
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


def search_by_time_range(start_ts: float, end_ts: float,
                         limit: int = 10) -> list[Memory]:
    """按时间范围检索记忆（支持 cloud_chat 时间指代解析）。

    created 列存的是 Unix epoch (strftime('%s','now'))。
    start_ts / end_ts 同为 Unix epoch float。
    """
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM memory "
            "WHERE created >= ? AND created <= ? "
            "ORDER BY weight DESC, last_accessed DESC "
            "LIMIT ?",
            (start_ts, end_ts, limit),
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
        avg_weight = float(c.execute("SELECT AVG(weight) FROM memory").fetchone()[0] or 0)
    return {
        "total": total,
        "long_term": long_term,
        "avg_weight": avg_weight,
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
