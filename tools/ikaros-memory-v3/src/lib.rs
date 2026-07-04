//! ikaros-memory-v3: 伊卡洛斯记忆模块 Rust 封装
//!
//! DNA Memory 借鉴设计, 1:1 对应 Python v3 (`bin/ikaros-memory-v3.py`):
//!   - sqlite + FTS5 + trigram + type 索引
//!   - 半衰期 (DECAY_PER_DAY = 0.01)
//!   - 权重晋升 (PROMOTE_WEIGHT = 0.8 + access >= 3)
//!   - 容量上限 (MAX_MEMORIES = 500 / 5MB)
//!   - 端到端持久化, 跨重启
//!
//! API 1:1 Python v3:
//!   - store(content, type, weight, tags) -> i64
//!   - get(id) -> Option<Memory>
//!   - list_all(limit, type_filter) -> Vec<Memory>
//!   - search(query, top_k) -> Vec<Memory>
//!   - access(id) -> ()
//!   - delete(id) -> bool
//!   - decay() -> usize
//!   - clean() -> usize
//!   - stats() -> Stats

use rusqlite::{params, Connection, OptionalExtension, Row};
use serde::{Deserialize, Serialize};
use std::path::Path;

pub const WEIGHT_DEFAULT: f32 = 0.6;
pub const WEIGHT_MIN: f32 = 0.0;
pub const WEIGHT_MAX: f32 = 1.0;
pub const DECAY_PER_DAY: f32 = 0.01;
pub const ACCESS_BOOST: f32 = 0.05;
pub const PROMOTE_WEIGHT: f32 = 0.8;
pub const PROMOTE_ACCESSES: i64 = 3;
pub const CLEAN_WEIGHT: f32 = 0.25;
pub const CLEAN_DAYS_UNUSED: i64 = 60;
pub const MAX_MEMORIES: i64 = 500;

const SCHEMA: &str = "
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
    tokenize='trigram'
);
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
";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Memory {
    pub id: i64,
    pub content: String,
    #[serde(rename = "type")]
    pub type_: String,
    pub tags: String,
    pub weight: f32,
    pub access_count: i64,
    pub last_accessed: f64,
    pub created: f64,
    pub short_term: bool,
    pub long_term: bool,
}

impl Memory {
    fn from_row(row: &Row<'_>) -> rusqlite::Result<Self> {
        Ok(Memory {
            id: row.get(0)?,
            content: row.get(1)?,
            type_: row.get(2)?,
            tags: row.get(3)?,
            weight: row.get(4)?,
            access_count: row.get(5)?,
            last_accessed: row.get(6)?,
            created: row.get(7)?,
            short_term: row.get(8)?,
            long_term: row.get(9)?,
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Stats {
    pub total: i64,
    pub long_term: i64,
    pub avg_weight: f32,
    pub by_type: std::collections::HashMap<String, i64>,
    pub db_size_bytes: u64,
    pub max_memories: i64,
}

pub struct MemoryDB {
    conn: Connection,
}

impl MemoryDB {
    pub fn new(path: &Path) -> anyhow::Result<Self> {
        let conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA journal_mode=WAL;")?;
        conn.execute_batch(SCHEMA)?;
        Ok(MemoryDB { conn })
    }

    pub fn store(&self, content: &str, type_: &str,
                 weight: f32, tags: &str) -> anyhow::Result<i64> {
        let w = weight.clamp(WEIGHT_MIN, WEIGHT_MAX);
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?.as_secs_f64();
        self.conn.execute(
            "INSERT INTO memory (content, type, tags, weight, access_count,
                                 last_accessed, created, short_term)
             VALUES (?, ?, ?, ?, 0, ?, ?, 1)",
            params![content, type_, tags, w, now, now],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    pub fn get(&self, id: i64) -> anyhow::Result<Option<Memory>> {
        let row = self.conn.query_row(
            "SELECT id, content, type, tags, weight, access_count,
                    last_accessed, created, short_term, long_term
             FROM memory WHERE id = ?",
            params![id],
            Memory::from_row,
        ).optional()?;
        Ok(row)
    }

    pub fn list_all(&self, limit: i64, type_filter: Option<&str>)
                    -> anyhow::Result<Vec<Memory>> {
        let mut sql = String::from(
            "SELECT id, content, type, tags, weight, access_count,
                    last_accessed, created, short_term, long_term
             FROM memory WHERE 1=1");
        let mut args: Vec<Box<dyn rusqlite::ToSql>> = vec![];
        if let Some(t) = type_filter {
            sql.push_str(" AND type = ?");
            args.push(Box::new(t.to_string()));
        }
        sql.push_str(" ORDER BY weight DESC, last_accessed DESC LIMIT ?");
        args.push(Box::new(limit));
        let mut stmt = self.conn.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(args.iter().map(|b| b.as_ref())),
                                  Memory::from_row)?;
        Ok(rows.filter_map(|r| r.ok()).collect())
    }

    pub fn search(&self, query: &str, top_k: i64) -> anyhow::Result<Vec<Memory>> {
        // FTS5 with trigram, fallback LIKE on OperationalError
        let result = self.conn.prepare(
            "SELECT m.id, m.content, m.type, m.tags, m.weight, m.access_count,
                    m.last_accessed, m.created, m.short_term, m.long_term,
                    bm25(memory_fts) AS rank
             FROM memory_fts fts JOIN memory m ON fts.rowid = m.id
             WHERE memory_fts MATCH ?
             ORDER BY rank LIMIT ?",
        );
        match result {
            Ok(mut stmt) => {
                let rows = stmt.query_map(
                    params![query, top_k], Memory::from_row)?;
                Ok(rows.filter_map(|r| r.ok()).collect())
            }
            Err(_) => {
                // 兜底: LIKE
                let pattern = format!("%{}%", query);
                let mut stmt = self.conn.prepare(
                    "SELECT id, content, type, tags, weight, access_count,
                            last_accessed, created, short_term, long_term
                     FROM memory
                     WHERE content LIKE ? OR tags LIKE ?
                     ORDER BY weight DESC, last_accessed DESC LIMIT ?",
                )?;
                let rows = stmt.query_map(
                    params![pattern, pattern, top_k], Memory::from_row)?;
                Ok(rows.filter_map(|r| r.ok()).collect())
            }
        }
    }

    pub fn access(&self, id: i64) -> anyhow::Result<()> {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?.as_secs_f64();
        self.conn.execute(
            "UPDATE memory
             SET last_accessed = ?,
                 access_count = access_count + 1,
                 weight = MIN(1.0, weight + ?),
                 long_term = CASE
                     WHEN weight + ? >= ? AND access_count + 1 >= ?
                     THEN 1 ELSE long_term
                 END
             WHERE id = ?",
            params![now, ACCESS_BOOST, ACCESS_BOOST,
                    PROMOTE_WEIGHT, PROMOTE_ACCESSES, id],
        )?;
        Ok(())
    }

    pub fn delete(&self, id: i64) -> anyhow::Result<bool> {
        let n = self.conn.execute("DELETE FROM memory WHERE id = ?", params![id])?;
        Ok(n > 0)
    }

    pub fn decay(&self) -> anyhow::Result<usize> {
        let n = self.conn.execute(
            "UPDATE memory SET weight = MAX(0.0, weight - ?) WHERE weight > 0.0",
            params![DECAY_PER_DAY],
        )?;
        Ok(n)
    }

    pub fn clean(&self) -> anyhow::Result<usize> {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?.as_secs_f64();
        let cutoff = now - (CLEAN_DAYS_UNUSED as f64) * 86400.0;
        let n1 = self.conn.execute(
            "DELETE FROM memory WHERE weight < ? AND last_accessed < ?",
            params![CLEAN_WEIGHT, cutoff],
        )?;
        Ok(n1)
    }

    pub fn stats(&self) -> anyhow::Result<Stats> {
        let total: i64 = self.conn.query_row(
            "SELECT COUNT(*) FROM memory", [], |r| r.get(0))?;
        let long_term: i64 = self.conn.query_row(
            "SELECT COUNT(*) FROM memory WHERE long_term = 1", [], |r| r.get(0))?;
        let avg_weight: f32 = self.conn.query_row(
            "SELECT COALESCE(AVG(weight), 0.0) FROM memory", [], |r| r.get(0))?;
        let mut by_type = std::collections::HashMap::new();
        let mut stmt = self.conn.prepare(
            "SELECT type, COUNT(*) FROM memory GROUP BY type")?;
        let rows = stmt.query_map([], |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))
        })?;
        for r in rows {
            let (t, c) = r?;
            by_type.insert(t, c);
        }
        let db_size = std::fs::metadata(self.conn.path().unwrap_or(Path::new("")))
            .map(|m| m.len()).unwrap_or(0);
        Ok(Stats {
            total, long_term, avg_weight, by_type,
            db_size_bytes: db_size, max_memories: MAX_MEMORIES,
        })
    }
}
