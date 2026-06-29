"""
Ikaros TTS Cache — SHA256(text+voice) → MP3 bytes LRU 200 条.

Axiom memory tier 5 (LRU 128 sessions) → here LRU 200 MP3.
Edge-tts 一次 ~500-1500ms, 命中 <1ms. 同 text 不重调.

2026-06-29 哥哥拍板 (A+B+C+D).
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger("ikaros.tts_cache")

CACHE_DIR = Path("E:/Ikaros/data/cache/tts")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = CACHE_DIR / "cache.db"
MAX_ENTRIES = 200
MAX_TEXT_LEN = 4000  # 截断防爆 (跟 axiom context_compression 一致)


class TtsCache:
    """SQLite-backed LRU cache for TTS MP3 bytes.

    Schema:
      cache(key TEXT PRIMARY KEY, text TEXT, voice TEXT,
            mp3 BLOB, size_bytes INTEGER, created_at REAL,
            last_hit_at REAL, hit_count INTEGER)
    """

    def __init__(self, max_entries: int = MAX_ENTRIES):
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                voice TEXT NOT NULL,
                mp3 BLOB NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at REAL NOT NULL,
                last_hit_at REAL NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        self._db.commit()

    @staticmethod
    def make_key(text: str, voice: str) -> str:
        """SHA256(text+voice) → 64 hex chars."""
        h = hashlib.sha256()
        h.update(voice.encode("utf-8"))
        h.update(b"\x00")
        # 截断长 text 防止 key 漂移
        h.update(text[:MAX_TEXT_LEN].encode("utf-8"))
        return h.hexdigest()

    def get(self, text: str, voice: str) -> bytes | None:
        """命中返 MP3 bytes, 未命中 None."""
        if not text:
            return None
        key = self.make_key(text, voice)
        with self._lock:
            row = self._db.execute(
                "SELECT mp3 FROM cache WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            # 更新 last_hit + hit_count
            self._db.execute(
                "UPDATE cache SET last_hit_at = ?, hit_count = hit_count + 1 WHERE key = ?",
                (time.time(), key),
            )
            self._db.commit()
            return bytes(row[0])

    def put(self, text: str, voice: str, mp3: bytes) -> None:
        """写入缓存. LRU eviction by hit_count + last_hit_at."""
        if not text or not mp3:
            return
        key = self.make_key(text, voice)
        now = time.time()
        with self._lock:
            self._db.execute(
                """INSERT OR REPLACE INTO cache
                   (key, text, voice, mp3, size_bytes, created_at, last_hit_at, hit_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(
                       (SELECT hit_count FROM cache WHERE key = ?), 0))""",
                (key, text[:MAX_TEXT_LEN], voice, mp3, len(mp3), now, now, key),
            )
            # LRU eviction — 只保留 max_entries 条
            self._db.execute("""
                DELETE FROM cache WHERE key IN (
                    SELECT key FROM cache ORDER BY last_hit_at DESC
                    LIMIT -1 OFFSET ?
                )
            """, (self.max_entries,))
            self._db.commit()

    def stats(self) -> dict:
        with self._lock:
            total = self._db.execute("SELECT count(*) FROM cache").fetchone()[0]
            hits = self._db.execute("SELECT COALESCE(SUM(hit_count), 0) FROM cache").fetchone()[0]
            size_kb = (self._db.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM cache").fetchone()[0]) / 1024
        return {"entries": total, "total_hits": hits, "size_kb": round(size_kb, 1)}

    def clear(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM cache")
            self._db.commit()


# Singleton
_instance: TtsCache | None = None
_instance_lock = threading.Lock()


def get_cache() -> TtsCache:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = TtsCache()
    return _instance