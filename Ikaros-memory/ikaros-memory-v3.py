#!/usr/bin/env python3
"""ikaros-memory-v3.py — 伊卡洛斯记忆模块 v3 (DNA Memory 设计)

设计目标 (DNA Memory 借鉴 + 自控实现):
  1. **不调 LLM 整理记忆** — 用纯算法 (半衰期 + 权重晋升 + 相似度合并)
  2. **便携部署** — 单 sqlite 文件, 0 外部依赖, 全在 Ikaros 目录
  3. **最终形态是 Rust** — 本 Python 是参考实现, API 1:1 对应未来 rust 版
  4. **P0 invariant** — 跨重启不丢, 端到端可加可搜

API 表面 (CLI + Python module 都能调):
  - store(content, type, weight, tags)  → memory_id
  - search(query, top_k, min_weight)    → List[Memory]
  - get(memory_id)                       → Memory | None
  - list_all(limit, type_filter)         → List[Memory]
  - delete(memory_id)                    → bool
  - access(memory_id)                    → None  (更新 last_accessed + weight +0.05)
  - decay()                              → int   (跑半衰期衰减, 返更新条数)
  - stats()                              → dict  (总数/容量/分布)
  - clean()                              → int   (auto-clean, weight<0.25 + 60d 未用)

schema (FTS5 + triggers):
  - memory(id, content, type, tags, weight, access_count,
           last_accessed, created, short_term, long_term)
  - memory_fts(content, type, tags) -- FTS5 虚拟表, INSERT trigger 自动 sync
  - 半衰期: 每天 -0.01 (调 decay() 手动跑, 配 cron)

数据位置: E:\\Ikaros\\Ikaros-memory\\data\\v3.db (single file, 跨重启)

用法:
  python Ikaros-memory/ikaros-memory-v3.py store "哥哥喜欢 terse 中文" --type preference --weight 0.9
  python Ikaros-memory/ikaros-memory-v3.py search "terse" --top-k 5
  python Ikaros-memory/ikaros-memory-v3.py stats
  python Ikaros-memory/ikaros-memory-v3.py decay        # 跑半衰期
  python Ikaros-memory/ikaros-memory-v3.py access 1     # 访问 ID=1 (weight +0.05)
  python Ikaros-memory/ikaros-memory-v3.py clean        # auto-clean
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import threading
import atexit
import logging
import urllib.request
import urllib.error
from contextlib import contextmanager
from datetime import datetime

logger = logging.getLogger("ikaros.memory.v3")
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(r"E:\Ikaros\Ikaros-memory")
DATA = ROOT / "data"
DB_PATH = DATA / "v3.db"

# SOUL.md auto-sync target (HERMES_HOME set by launcher, fallback to data/hermes-agent)
SOUL_MD_PATH = Path(os.environ.get(
    "HERMES_HOME", r"E:\Ikaros\data\hermes-agent"
)) / "SOUL.md"
# axiom.md = SOUL.md 镜像 (cloud_chat.py 加载此路径作为 system prompt 灵魂段)
AXIOM_MD_PATH = Path(os.environ.get(
    "HERMES_ROOT", r"E:\Ikaros"
)) / "ikaros-identity" / "axiom.md"

# ---- write-back cache (在内存中读写, 每分钟覆写磁盘) ----

_cache_enabled = False
_mem_conn: sqlite3.Connection | None = None
_cache_lock = threading.RLock()
_dirty = False
_last_flush_time = 0.0
_flush_interval = 60.0
_stop_event = threading.Event()
_flush_thread: threading.Thread | None = None


# ---- write-back cache functions ----

def _load_into_memory() -> None:
    """从磁盘 v3.db 加载所有记录到内存 DB."""
    global _mem_conn
    _mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
    _mem_conn.row_factory = sqlite3.Row
    try:
        _mem_conn.autocommit = True         # Python 3.12+
    except AttributeError:
        _mem_conn.isolation_level = None    # fallback 3.11-
    _mem_conn.executescript(SCHEMA)

    if not DB_PATH.exists():
        logger.info("v3 cache: 磁盘 DB 不存在, 新建空内存库")
        return

    src = sqlite3.connect(str(DB_PATH))
    src.row_factory = sqlite3.Row
    cols = ["id", "content", "type", "tags", "weight",
            "access_count", "last_accessed", "created",
            "short_term", "long_term"]
    placeholders = ",".join("?" for _ in cols)
    count = 0
    for row in src.execute("SELECT * FROM memory"):
        _mem_conn.execute(
            f"INSERT INTO memory({','.join(cols)}) "
            f"VALUES({placeholders})",
            [row[c] for c in cols],
        )
        count += 1
    src.close()
    logger.info("v3 cache: %d records loaded from %s", count, DB_PATH)


def _flush(*, force: bool = False) -> int:
    """内存 -> 磁盘全量覆写. sqlite3.backup() (page-level copy)."""
    global _dirty, _last_flush_time
    if not _dirty and not force:
        return 0
    if _mem_conn is None:
        return 0
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        disk = sqlite3.connect(str(DB_PATH))
        _mem_conn.backup(disk)
        disk.close()
        _dirty = False
        _last_flush_time = time.time()
        size = DB_PATH.stat().st_size
        logger.info("v3 cache flushed to disk (%d bytes)", size)
        # Sync identity data to SOUL.md after successful DB flush
        _sync_soul_md()
        return size
    except Exception as e:
        logger.error("v3 cache flush FAILED: %s", e)
        return -1


def _sync_soul_md() -> None:
    """Sync identity/axiom/rule/fact/lesson/decision memories to SOUL.md.

    Called after each successful DB flush. Reads from memory DB
    (in-memory cache if enabled, otherwise disk DB), filters identity-
    related entries + high-weight facts, synthesizes structured markdown,
    and atomically writes to {HERMES_HOME}/SOUL.md.

    Failure is silently logged — never blocks the main flush flow.
    """
    try:
        # Query identity-related + high-weight knowledge entries
        with conn() as c:
            rows = c.execute(
                "SELECT content, type, weight FROM memory "
                "WHERE type IN ('identity', 'axiom', 'rule', "
                "       'fact', 'lesson', 'decision') "
                "  AND weight >= 0.5 "
                "ORDER BY type, weight DESC"
            ).fetchall()

        if not rows:
            logger.debug("soul sync: no memories, skipping")
            return

        # Group by type
        groups: dict[str, list[tuple[float, str]]] = {}
        for row in rows:
            t = row["type"]
            groups.setdefault(t, []).append((row["weight"], row["content"]))

        # Synthesize markdown
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "<!-- AUTO-SYNCED by Ikaros v3 Memory Plugin -->",
            f"<!-- Last sync: {now_str} -->",
            "<!-- Source: v3 memory DB (identity/axiom/rule/fact/lesson/decision) -->",
            "<!-- DO NOT manually edit — this file is regenerated every flush cycle -->",
            "",
        ]

        section_map = [
            ("identity", "## 核心身份"),
            ("axiom", "## 存在公理"),
            ("rule", "## 行为准则"),
            ("fact", "## 关键事实"),
            ("lesson", "## 经验教训"),
            ("decision", "## 重要决策"),
        ]
        for type_key, section_title in section_map:
            entries = groups.get(type_key, [])
            if not entries:
                continue
            lines.append(section_title)
            lines.append("")
            for weight, content in entries:
                lines.append(f"- {content}")
            lines.append("")

        content_text = "\n".join(lines)

        # Atomic write: temp file -> os.replace
        # 写 SOUL.md
        target = SOUL_MD_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target.parent / ".SOUL.md.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content_text)
        os.replace(str(tmp_path), str(target))

        # 镜像写 axiom.md (axiom.md = SOUL.md, cloud_chat.py 加载此路径)
        try:
            AXIOM_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_ax = AXIOM_MD_PATH.parent / ".axiom.md.tmp"
            with open(tmp_ax, "w", encoding="utf-8") as f:
                f.write(content_text)
            os.replace(str(tmp_ax), str(AXIOM_MD_PATH))
        except Exception as ax_e:
            logger.debug("axiom.md mirror write failed (non-fatal): %s", ax_e)

        logger.info("SOUL.md + axiom.md synced (%d entries, %d bytes)",
                    len(rows), len(content_text))

    except Exception as e:
        logger.warning("SOUL.md sync failed (non-fatal): %s", e)


_last_decay_time = 0.0
_DECAY_INTERVAL = 86400.0  # 24h between decay runs


def _flush_loop() -> None:
    """后台线程: 每 60 秒检查脏页并落盘, 每 24h 跑一次衰减+清理."""
    global _last_decay_time
    _last_decay_time = time.time()
    while not _stop_event.is_set():
        _stop_event.wait(timeout=_flush_interval)
        if _stop_event.is_set():
            break
        _flush()
        # 每 24h 跑一次半衰期衰减 + auto-clean
        now = time.time()
        if now - _last_decay_time >= _DECAY_INTERVAL:
            try:
                n = decay()
                c = clean()
                if n or c:
                    logger.info("auto decay: %d weights decayed, %d cleaned", n, c)
            except Exception as e:
                logger.warning("auto decay failed: %s", e)
            _last_decay_time = now


def enable_cache() -> None:
    """启用写回缓存: 加载到内存 -> 启动后台落盘线程."""
    global _cache_enabled, _flush_thread
    if _cache_enabled:
        return
    _load_into_memory()
    _cache_enabled = True
    _dirty = False
    _flush_thread = threading.Thread(
        target=_flush_loop, daemon=True, name="v3-cache-flush"
    )
    _flush_thread.start()
    logger.info("v3 cache enabled (flush interval=%ds)", _flush_interval)


def disable_cache(*, flush: bool = True) -> None:
    """停用写回缓存: 可选先落盘, 再停线程."""
    global _cache_enabled, _flush_thread, _mem_conn
    if not _cache_enabled:
        return
    _cache_enabled = False
    _stop_event.set()
    if flush:
        _flush(force=True)
    if _flush_thread and _flush_thread.is_alive():
        _flush_thread.join(timeout=5)
    _mem_conn = None
    _flush_thread = None
    _stop_event.clear()
    logger.info("v3 cache disabled")


def _mark_dirty() -> None:
    """标记内存有脏页 (下次 flush 落盘)."""
    global _dirty
    if _cache_enabled:
        _dirty = True


@atexit.register
def _atexit_flush():
    """进程退出前确保落盘."""
    if _cache_enabled and _dirty and _mem_conn is not None:
        _flush(force=True)


# Constants (DNA Memory design)
WEIGHT_DEFAULT = 0.6
WEIGHT_MIN = 0.0
WEIGHT_MAX = 1.0
DECAY_PER_DAY = 0.01      # 半衰期
ACCESS_BOOST = 0.05        # 每次 access weight +0.05
PROMOTE_WEIGHT = 0.8       # 晋升阈值
PROMOTE_ACCESSES = 3       # 晋升最少访问数
CLEAN_WEIGHT = 0.25        # clean 阈值
CLEAN_DAYS_UNUSED = 60     # clean 60 天未用
MAX_MEMORIES = 500        # 容量上限
MAX_DB_SIZE = 5 * 1024 * 1024  # 5MB


# ---- schema ----

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'fact',
    tags TEXT DEFAULT '',
    weight REAL NOT NULL DEFAULT 0.6,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed REAL NOT NULL,
    created REAL NOT NULL,
    short_term INTEGER NOT NULL DEFAULT 1,
    long_term INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_memory_weight ON memory(weight DESC);
CREATE INDEX IF NOT EXISTS idx_memory_last_accessed ON memory(last_accessed DESC);
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory(type);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    content, tags, type,
    content='memory', content_rowid='id',
    tokenize="trigram"
);

-- 关键: FTS5 sync triggers (add 仓库自身 bug 是没这个)
CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN
    INSERT INTO memory_fts(rowid, content, tags, type)
    VALUES (new.id, new.content, new.tags, new.type);
END;

CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, content, tags, type)
    VALUES ('delete', old.id, old.content, old.tags, old.type);
END;

CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, content, tags, type)
    VALUES ('delete', old.id, old.content, old.tags, old.type);
    INSERT INTO memory_fts(rowid, content, tags, type)
    VALUES (new.id, new.content, new.tags, new.type);
END;
"""


# ---- connection management ----

@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    if _cache_enabled and _mem_conn is not None:
        with _cache_lock:
            yield _mem_conn
        return
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")  # 写并发安全
    c.executescript(SCHEMA)
    c.commit()
    try:
        yield c
        c.commit()
    finally:
        c.close()


# ---- core API ----

def _now() -> float:
    return time.time()


def store(content: str, type: str = "fact", weight: float = WEIGHT_DEFAULT,
         tags: str = "") -> int:
    """存一条 memory. 返 id."""
    if not content or not content.strip():
        raise ValueError("content 不能为空")
    weight = max(WEIGHT_MIN, min(WEIGHT_MAX, weight))
    now = _now()
    with conn() as c:
        cur = c.execute(
            "INSERT INTO memory (content, type, tags, weight, "
            "access_count, last_accessed, created, short_term) "
            "VALUES (?, ?, ?, ?, 0, ?, ?, 1)",
            (content.strip(), type, tags, weight, now, now),
        )
        result = int(cur.lastrowid)
    _mark_dirty()
    return result


def search(query: str, top_k: int = 5, min_weight: float = 0.0) -> list[dict]:
    """FTS5 全文搜索. 返 List[Memory].

    中文搜索策略 (5 级 fallback):
    1. trigram FTS5 (3 字子串能命中) — bm25 排序
    2. LIKE 完全子串 — "便携" 命中 "哥哥喜欢便携部署"
    3. 拆 2-gram LIKE OR — "哥哥喜欢什么" 拆 ["哥哥","哥喜","喜欢","欢什","什么"]
       各自 LIKE, 合并去重
    4. 拆 1-gram LIKE OR — 兜底, 单字
    5. 全失败返 []
    """
    if not query or not query.strip():
        return []
    with conn() as c:
        results: list[dict] = []
        seen: set[int] = set()

        def _add(rows):
            for r in rows:
                if r["id"] not in seen:
                    results.append(r)
                    seen.add(r["id"])
                    if len(results) >= top_k:
                        return True
            return False

        # Step 1: trigram FTS5 (3 字以上 OK)
        try:
            cur = c.execute(
                "SELECT m.id, m.content, m.type, m.tags, m.weight, "
                "m.access_count, m.last_accessed, m.created, "
                "m.short_term, m.long_term, "
                "bm25(memory_fts) AS rank "
                "FROM memory_fts fts "
                "JOIN memory m ON fts.rowid = m.id "
                "WHERE memory_fts MATCH ? AND m.weight >= ? "
                "ORDER BY rank LIMIT ?",
                (query, min_weight, top_k),
            )
            if _add([dict(r) for r in cur.fetchall()]):
                return results[:top_k]
        except (sqlite3.OperationalError, Exception):
            pass

        # Step 2: LIKE 完全子串
        try:
            cur = c.execute(
                "SELECT id, content, type, tags, weight, "
                "access_count, last_accessed, created, "
                "short_term, long_term, 0.0 AS rank "
                "FROM memory "
                "WHERE (content LIKE ? OR tags LIKE ?) AND weight >= ? "
                "ORDER BY weight DESC, last_accessed DESC LIMIT ?",
                (f"%{query}%", f"%{query}%", min_weight, top_k),
            )
            if _add([dict(r) for r in cur.fetchall()]):
                return results[:top_k]
        except Exception:
            pass

        # Step 3: 拆 2-gram (中文友好: "哥哥喜欢什么" → ["哥哥","喜欢","什么"])
        # 跳纯 ASCII (PN/USB/HTTP 之类单 token)
        if any("\u4e00" <= c <= "\u9fff" for c in query):
            bigrams = [query[i:i+2] for i in range(len(query) - 1)
                       if "\u4e00" <= query[i] <= "\u9fff"]
            # 去重 + 至少 1 个
            bigrams = list(dict.fromkeys(bigrams))
            if bigrams:
                placeholders = " OR ".join(["(content LIKE ? OR tags LIKE ?)"] * len(bigrams))
                params = []
                for bg in bigrams:
                    params.extend([f"%{bg}%", f"%{bg}%"])
                params.extend([min_weight, top_k])
                try:
                    cur = c.execute(
                        f"SELECT id, content, type, tags, weight, "
                        f"access_count, last_accessed, created, "
                        f"short_term, long_term, 0.0 AS rank "
                        f"FROM memory "
                        f"WHERE ({placeholders}) AND weight >= ? "
                        f"ORDER BY weight DESC, last_accessed DESC LIMIT ?",
                        params,
                    )
                    if _add([dict(r) for r in cur.fetchall()]):
                        return results[:top_k]
                except Exception:
                    pass

        # Step 4: 拆 1-gram (单字兜底)
        if any("\u4e00" <= c <= "\u9fff" for c in query):
            unigrams = list(dict.fromkeys(c for c in query
                                          if "\u4e00" <= c <= "\u9fff"))
            if unigrams:
                placeholders = " OR ".join(["(content LIKE ? OR tags LIKE ?)"] * len(unigrams))
                params = []
                for ug in unigrams:
                    params.extend([f"%{ug}%", f"%{ug}%"])
                params.extend([min_weight, top_k])
                try:
                    cur = c.execute(
                        f"SELECT id, content, type, tags, weight, "
                        f"access_count, last_accessed, created, "
                        f"short_term, long_term, 0.0 AS rank "
                        f"FROM memory "
                        f"WHERE ({placeholders}) AND weight >= ? "
                        f"ORDER BY weight DESC, last_accessed DESC LIMIT ?",
                        params,
                    )
                    _add([dict(r) for r in cur.fetchall()])
                except Exception:
                    pass

        return results[:top_k]
def get(memory_id: int) -> dict | None:
    with conn() as c:
        cur = c.execute("SELECT * FROM memory WHERE id = ?", (memory_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_all(limit: int = 100, type_filter: str | None = None,
             min_weight: float | None = None) -> list[dict]:
    sql = "SELECT * FROM memory WHERE 1=1"
    params: list[Any] = []
    if type_filter:
        sql += " AND type = ?"
        params.append(type_filter)
    if min_weight is not None:
        sql += " AND weight >= ?"
        params.append(min_weight)
    sql += " ORDER BY weight DESC, last_accessed DESC LIMIT ?"
    params.append(limit)
    with conn() as c:
        cur = c.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def delete(memory_id: int) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM memory WHERE id = ?", (memory_id,))
        result = cur.rowcount > 0
    _mark_dirty()
    return result


def access(memory_id: int) -> None:
    """更新 last_accessed, access_count +1, weight +0.05 (cap 1.0),
    满足晋升阈值则 mark long_term."""
    with conn() as c:
        c.execute("""
            UPDATE memory
            SET last_accessed = ?,
                access_count = access_count + 1,
                weight = MIN(1.0, weight + ?),
                long_term = CASE
                    WHEN weight + ? >= ? AND access_count + 1 >= ?
                    THEN 1 ELSE long_term
                END
            WHERE id = ?
        """, (_now(), ACCESS_BOOST, ACCESS_BOOST,
              PROMOTE_WEIGHT, PROMOTE_ACCESSES, memory_id))
    _mark_dirty()


def decay() -> int:
    """跑半衰期衰减: 每天 -0.01. 返更新条数.

    注: 实际间隔看上次 decay 时间, 但简单做法是每次调用 -0.01
    (cron 每天 1 次). 配 ikaros-dojo-nightly 跑.
    """
    with conn() as c:
        cur = c.execute(
            "UPDATE memory SET weight = MAX(0.0, weight - ?) "
            "WHERE weight > 0.0",
            (DECAY_PER_DAY,),
        )
        result = cur.rowcount
    _mark_dirty()
    return result


def stats() -> dict:
    with conn() as c:
        total = c.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
        long_term = c.execute(
            "SELECT COUNT(*) FROM memory WHERE long_term = 1"
        ).fetchone()[0]
        avg_weight = c.execute(
            "SELECT AVG(weight) FROM memory"
        ).fetchone()[0] or 0.0
        by_type = {}
        for r in c.execute(
            "SELECT type, COUNT(*) AS cnt FROM memory GROUP BY type"
        ).fetchall():
            by_type[r["type"]] = r["cnt"]
        db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        return {
            "total": total,
            "long_term": long_term,
            "avg_weight": round(avg_weight, 3),
            "by_type": by_type,
            "db_size_bytes": db_size,
            "db_size_mb": round(db_size / 1024 / 1024, 3),
            "max_memories": MAX_MEMORIES,
            "max_db_size_mb": MAX_DB_SIZE / 1024 / 1024,
        }


def clean() -> int:
    """auto-clean: weight < CLEAN_WEIGHT 且 CLEAN_DAYS_UNUSED 天未用 → 删. 返删除数."""
    cutoff = _now() - CLEAN_DAYS_UNUSED * 86400
    with conn() as c:
        cur = c.execute(
            "DELETE FROM memory WHERE weight < ? AND last_accessed < ?",
            (CLEAN_WEIGHT, cutoff),
        )
        # 也 cap 在 MAX_MEMORIES 内: 超了删最旧最低分
        cur2 = c.execute(
            "DELETE FROM memory WHERE id IN ("
            "  SELECT id FROM memory ORDER BY weight ASC, last_accessed ASC "
            "  LIMIT MAX(0, (SELECT COUNT(*) FROM memory) - ?)"
            ")",
            (MAX_MEMORIES,),
        )
        result = cur.rowcount + cur2.rowcount
    _mark_dirty()
    return result


# ---- 本地 Embedding 语义搜索 (需 :8587 embedding 服务) ----
# Embedding 服务由 v3 插件自动启动 (nomic-embed-text, ~80MB, ~1s)

_EMBEDDING_URL = "http://127.0.0.1:8587/v1/embeddings"
_EMBEDDING_MODEL = "nomic-embed-text"


def _call_embedding_api(text: str) -> list[float] | None:
    """调本地 :8587 embedding 服务, 返 768 维向量."""
    if not text or not text.strip():
        return None
    try:
        payload = json.dumps({
            "model": _EMBEDDING_MODEL,
            "input": text.strip(),
        }).encode("utf-8")
        req = urllib.request.Request(
            _EMBEDDING_URL, data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["data"][0]["embedding"]
    except Exception as e:
        logger.debug("embedding API 调用失败: %s", e)
        return None


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """余弦相似度."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def semantic_search(query: str, top_k: int = 5, min_weight: float = 0.0,
                    rerank_k: int = 20) -> list[dict]:
    """语义搜索: FTS5 粗取 -> Embedding 精排 (向量 rerank).

    流程:
      1. search() 拿 rerank_k 条候选 (BM25+LIKE+ngram)
      2. embedding API 获取 query 向量
      3. 对每条候选调 embedding API 获取向量
      4. 余弦相似度 rerank, 加权融合原始 weight
      5. 返 top_k

    融合公式: score = 0.6 * semantic_sim + 0.4 * weight
    """
    if not query or not query.strip():
        return []
    candidates = search(query, top_k=rerank_k, min_weight=min_weight)
    if not candidates:
        return []

    q_vec = _call_embedding_api(query)
    if q_vec is None:
        return candidates[:top_k]

    scored = []
    for mem in candidates:
        m_vec = _call_embedding_api(mem["content"])
        sim = _cosine_sim(q_vec, m_vec) if m_vec else 0.0
        mem_weight = mem.get("weight", WEIGHT_DEFAULT)
        fused = 0.6 * sim + 0.4 * mem_weight
        scored.append((fused, mem))

    scored.sort(key=lambda x: x[0], reverse=True)
    result = []
    for score, mem in scored[:top_k]:
        mem = dict(mem)
        mem["semantic_score"] = round(score, 4)
        mem["search_type"] = "semantic"
        result.append(mem)
    return result


# ---- LLM memory extraction (local llama-server :8080 only) ----
# Cloud LLM is Hermes Agent's responsibility, not ours.
# If :8080 is down, extraction is skipped.

_LLM_URL = "http://127.0.0.1:8080/v1/chat/completions"
_LLM_MODEL = "auto"  # any model loaded on :8080

_EXTRACT_SYSTEM_PROMPT = (
    "You are a memory extraction assistant. Extract key information to remember from the conversation."
    "Rules: user preferences / key facts / important constraints / relationships."
    'Output JSON array [{"content": "fact", "type": "preference|fact|constraint|relation", "weight": 0.6}]'
    "Output JSON only, no extra text. If nothing worth remembering, output []."
    "Do not think, output JSON directly."
)


def extract(conversation_text: str, timeout: int = 30) -> list[dict]:
    """Extract key facts from conversation and store in memory.

    Uses local llama-server :8080 only. If unavailable, returns [].
    Cloud LLM fallback is Hermes Agent's responsibility, not ours.

    Args:
        conversation_text: full conversation text
        timeout: LLM call timeout (seconds)

    Returns: list of extracted and stored facts.
    """
    if not conversation_text or not conversation_text.strip():
        return []

    payload = json.dumps({
        "model": _LLM_MODEL,
        "messages": [
            {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": conversation_text.strip()},
        ],
        "temperature": 0,
        "max_tokens": 1024,
    }).encode("utf-8")

    content = _try_llm_extract(_LLM_URL, payload, timeout)

    if content is None:
        logger.warning("extract: local LLM :8080 unavailable, skipping extraction")
        return []

    # 解析 JSON
    facts = _parse_llm_json(content)
    if not facts:
        return []

    stored = []
    for fact in facts:
        if not isinstance(fact, dict) or not fact.get("content"):
            continue
        try:
            mid = store(
                content=fact["content"],
                type=fact.get("type", "fact"),
                weight=min(1.0, max(0.0, fact.get("weight", 0.6))),
                tags="extracted",
            )
            fact["memory_id"] = mid
            stored.append(fact)
        except Exception as e:
            logger.debug("extract store 失败: %s", e)
    logger.info("extract: %d facts extracted, %d stored", len(facts), len(stored))
    return stored


def _try_llm_extract(url: str, payload: bytes, timeout: int,
                     headers: dict | None = None) -> str | None:
    """尝试调 LLM API, 成功返 content, 失败返 None."""
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    try:
        req = urllib.request.Request(url, data=payload, headers=req_headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        msg = data["choices"][0]["message"]
        return msg.get("content") or msg.get("reasoning_content", "")
    except Exception as e:
        logger.debug("LLM extract (%s) 失败: %s", url, e)
        return None


def _parse_llm_json(content: str) -> list[dict]:
    """从 LLM 回复中解析 JSON 数组."""
    content = content.strip()
    # 抽取 ```json ... ``` 或 ``` ... ``` 代码块
    for delimiter in ["```json", "```"]:
        if delimiter in content:
            parts = content.split(delimiter)
            if len(parts) >= 2:
                inner = parts[1].split("```")[0].strip()
                content = inner
                break
    try:
        facts = json.loads(content)
        return facts if isinstance(facts, list) else []
    except json.JSONDecodeError:
        # 尝试找 [...], 可能 LLM 在 JSON 前后加了文字
        import re
        match = re.search(r'\[.*?\]', content, re.DOTALL)
        if match:
            try:
                facts = json.loads(match.group())
                return facts if isinstance(facts, list) else []
            except json.JSONDecodeError:
                pass
        return []


# ---- CLI ----



def _mem_to_dict(row: dict) -> dict:
    return {
        "id": row["id"],
        "content": row["content"],
        "type": row["type"],
        "tags": row.get("tags", ""),
        "weight": round(row["weight"], 3),
        "access_count": row["access_count"],
        "long_term": bool(row["long_term"]),
        "last_accessed": row["last_accessed"],
        "created": row["created"],
    }


def main():
    p = argparse.ArgumentParser(description="伊卡洛斯 memory v3 (DNA Memory 设计)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_store = sub.add_parser("store", help="存一条 memory")
    p_store.add_argument("content", help="memory 内容")
    p_store.add_argument("--type", default="fact",
                         choices=["fact", "preference", "pattern", "error"],
                         help="memory 类型")
    p_store.add_argument("--weight", type=float, default=WEIGHT_DEFAULT,
                         help=f"初始 weight (0-1, default {WEIGHT_DEFAULT})")
    p_store.add_argument("--tags", default="", help="逗号分隔的标签")

    p_search = sub.add_parser("search", help="FTS5 搜索")
    p_search.add_argument("query", help="搜索关键词")
    p_search.add_argument("--top-k", type=int, default=5)
    p_search.add_argument("--min-weight", type=float, default=0.0)

    sub.add_parser("stats", help="数据库统计")

    p_ls = sub.add_parser("list", help="列所有 memory")
    p_ls.add_argument("--limit", type=int, default=20)
    p_ls.add_argument("--type", help="按类型过滤")
    p_ls.add_argument("--min-weight", type=float, help="最低 weight")

    p_get = sub.add_parser("get", help="按 id 拿 1 条")
    p_get.add_argument("memory_id", type=int)

    p_acc = sub.add_parser("access", help="访问 1 条 (weight +0.05)")
    p_acc.add_argument("memory_id", type=int)

    p_del = sub.add_parser("delete", help="删 1 条")
    p_del.add_argument("memory_id", type=int)

    sub.add_parser("decay", help="跑半衰期衰减 (weight -0.01)")

    sub.add_parser("clean", help="auto-clean (weight<0.25 + 60d 未用)")

    p_sem = sub.add_parser("semantic-search", help="语义搜索 (FTS5 + Embedding rerank)")
    p_sem.add_argument("query", help="搜索关键词")
    p_sem.add_argument("--top-k", type=int, default=5)
    p_sem.add_argument("--min-weight", type=float, default=0.0)
    p_sem.add_argument("--rerank-k", type=int, default=20,
                       help="FTS5 候选数 (越多越准但越慢)")

    p_ext = sub.add_parser("extract",
                           help="从对话中提取并存储事实 (需 :8080 Hermes Agent)")
    p_ext.add_argument("text", help="对话文本")
    p_ext.add_argument("--timeout", type=int, default=30, help="LLM 超时(秒)")

    args = p.parse_args()

    if args.cmd == "store":
        mid = store(args.content, type=args.type,
                    weight=args.weight, tags=args.tags)
        print(json.dumps({"id": mid, "ok": True}, ensure_ascii=False))
    elif args.cmd == "search":
        hits = search(args.query, top_k=args.top_k,
                      min_weight=args.min_weight)
        out = [_mem_to_dict(h) for h in hits]
        print(json.dumps({"results": out, "count": len(out)},
                         ensure_ascii=False, indent=2))
    elif args.cmd == "get":
        m = get(args.memory_id)
        if m is None:
            print(json.dumps({"error": "not found"}))
            sys.exit(2)
        print(json.dumps(_mem_to_dict(m), ensure_ascii=False, indent=2))
    elif args.cmd == "list":
        rows = list_all(limit=args.limit, type_filter=args.type,
                        min_weight=args.min_weight)
        out = [_mem_to_dict(r) for r in rows]
        print(json.dumps({"results": out, "count": len(out)},
                         ensure_ascii=False, indent=2))
    elif args.cmd == "access":
        access(args.memory_id)
        print(json.dumps({"ok": True, "memory_id": args.memory_id}))
    elif args.cmd == "delete":
        ok = delete(args.memory_id)
        print(json.dumps({"ok": ok, "memory_id": args.memory_id}))
    elif args.cmd == "decay":
        n = decay()
        print(json.dumps({"decayed": n}))
    elif args.cmd == "clean":
        n = clean()
        print(json.dumps({"cleaned": n}))
    elif args.cmd == "semantic-search":
        hits = semantic_search(args.query, top_k=args.top_k,
                               min_weight=args.min_weight,
                               rerank_k=args.rerank_k)
        out = [_mem_to_dict(h) for h in hits]
        print(json.dumps({"results": out, "count": len(out)},
                         ensure_ascii=False, indent=2))
    elif args.cmd == "extract":
        facts = extract(args.text, timeout=args.timeout)
        print(json.dumps({"facts": facts, "count": len(facts)},
                         ensure_ascii=False, indent=2))
    elif args.cmd == "stats":
        print(json.dumps(stats(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
