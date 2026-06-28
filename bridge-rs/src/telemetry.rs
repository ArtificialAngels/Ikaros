//! telemetry.rs — in-process signal bus + ring-buffer request log.
//!
//! Extracted from main.rs (2026-06-28) to keep main.rs focused on routing.
//! Mirrors the deleted bridge/telemetry.py (SignalBus + RequestLog + Topics).

use serde::Serialize;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;
use tokio::sync::RwLock;

// ---------------------------------------------------------------------------
// Signal Bus
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Serialize)]
pub struct SignalEnvelope {
    pub id: u64,
    pub topic: String,
    pub payload: serde_json::Value,
    pub ts: f64,
}

pub struct SignalBus {
    envelopes: RwLock<Vec<SignalEnvelope>>,
    next_id: AtomicU64,
    capacity: usize,
}

impl SignalBus {
    pub fn new(capacity: usize) -> Self {
        Self {
            envelopes: RwLock::new(Vec::with_capacity(capacity)),
            next_id: AtomicU64::new(1),
            capacity,
        }
    }

    pub async fn emit(&self, topic: &str, payload: serde_json::Value) -> SignalEnvelope {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let env = SignalEnvelope {
            id,
            topic: topic.to_string(),
            payload,
            ts: now_unix(),
        };
        let mut buf = self.envelopes.write().await;
        buf.push(env.clone());
        if buf.len() > self.capacity {
            let excess = buf.len() - self.capacity;
            buf.drain(0..excess);
        }
        env
    }

    pub async fn recent(&self, topic: Option<&str>, limit: usize) -> Vec<SignalEnvelope> {
        let buf = self.envelopes.read().await;
        let mut items: Vec<SignalEnvelope> = buf
            .iter()
            .rev()
            .filter(|e| topic.map_or(true, |t| e.topic.starts_with(t)))
            .take(limit)
            .cloned()
            .collect();
        items.reverse();
        items
    }
}

// ---------------------------------------------------------------------------
// Request Log (ring buffer)
// ---------------------------------------------------------------------------

pub struct RequestLog {
    entries: RwLock<Vec<RequestEntry>>,
    start_time: Instant,
    capacity: usize,
}

#[derive(Clone, Debug, Serialize)]
pub struct RequestEntry {
    pub path: String,
    pub status: u16,
    pub elapsed_ms: u64,
    pub ts: f64,
}

impl RequestLog {
    pub fn new(capacity: usize) -> Self {
        Self {
            entries: RwLock::new(Vec::with_capacity(capacity)),
            start_time: Instant::now(),
            capacity,
        }
    }

    pub async fn record(&self, path: &str, status: u16, elapsed_ms: u64) {
        let entry = RequestEntry {
            path: path.to_string(),
            status,
            elapsed_ms,
            ts: now_unix(),
        };
        let mut buf = self.entries.write().await;
        buf.push(entry);
        if buf.len() > self.capacity {
            let excess = buf.len() - self.capacity;
            buf.drain(0..excess);
        }
    }

    pub async fn stats(&self) -> serde_json::Value {
        let buf = self.entries.read().await;
        let total = buf.len();
        let errors = buf.iter().filter(|e| e.status >= 400).count();
        let mut by_status: HashMap<String, usize> = HashMap::new();
        let mut by_path: HashMap<String, usize> = HashMap::new();
        for e in buf.iter() {
            *by_status.entry(e.status.to_string()).or_insert(0) += 1;
            *by_path.entry(e.path.clone()).or_insert(0) += 1;
        }
        serde_json::json!({
            "total": total,
            "errors": errors,
            "error_rate": if total > 0 { errors as f64 / total as f64 } else { 0.0 },
            "uptime_sec": self.start_time.elapsed().as_secs(),
            "by_status": by_status,
            "by_path": by_path,
        })
    }

    /// Flush telemetry snapshot to data/logs/telemetry.json (non-blocking).
    pub async fn flush(&self) {
        let snapshot = serde_json::json!({
            "ts": now_unix(),
            "stats": self.stats().await,
            "recent": self.recent_entries(100).await,
        });
        // Non-blocking write
        tokio::spawn(async move {
            let log_dir = std::env::var("HERMES_LOGS")
                .map(std::path::PathBuf::from)
                .unwrap_or_else(|_| {
                    let root = std::env::current_dir().unwrap_or_default();
                    root.join("data").join("logs")
                });
            let _ = std::fs::create_dir_all(&log_dir);
            let path = log_dir.join("telemetry.json");
            let tmp = path.with_extension("json.tmp");
            if let Ok(json_str) = serde_json::to_string_pretty(&snapshot) {
                if std::fs::write(&tmp, json_str).is_ok() {
                    let _ = std::fs::rename(&tmp, &path);
                }
            }
        });
    }

    async fn recent_entries(&self, limit: usize) -> Vec<RequestEntry> {
        let buf = self.entries.read().await;
        buf.iter().rev().take(limit).cloned().collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect()
    }
}

// ---------------------------------------------------------------------------
// Topics — canonical signal topic constants (mirrors bridge/telemetry.py Topics)
// ---------------------------------------------------------------------------

pub struct Topics;

impl Topics {
    // Module lifecycle
    pub const MODULE_BOOT: &'static str = "module.boot";
    pub const MODULE_READY: &'static str = "module.ready";
    pub const MODULE_SHUTDOWN: &'static str = "module.shutdown";
    pub const MODULE_ERROR: &'static str = "module.error";

    // Model lifecycle
    pub const MODEL_LOADED: &'static str = "model.loaded";
    pub const MODEL_EVICTED: &'static str = "model.evicted";
    pub const MODEL_SWAP: &'static str = "model.swap";
    pub const MODEL_WARMUP_START: &'static str = "model.warmup.start";
    pub const MODEL_WARMUP_PROGRESS: &'static str = "model.warmup.progress";
    pub const MODEL_WARMUP_DONE: &'static str = "model.warmup.done";

    // Chat
    pub const CHAT_REQUEST: &'static str = "chat.request";
    pub const CHAT_DELTA: &'static str = "chat.delta";
    pub const CHAT_DONE: &'static str = "chat.done";
    pub const CHAT_ERROR: &'static str = "chat.error";

    // Session
    pub const SESSION_OPENED: &'static str = "session.opened";
    pub const SESSION_CLOSED: &'static str = "session.closed";

    // Infrastructure
    pub const SUPERVISOR_HEARTBEAT: &'static str = "supervisor.heartbeat";
    pub const PORT_LISTEN: &'static str = "port.listen";
    pub const PORT_LOST: &'static str = "port.lost";
    pub const DISK_LOW: &'static str = "disk.low";

    // Liveness
    pub const LIVENESS_OK: &'static str = "liveness.ok";
    pub const LIVENESS_DEGRADED: &'static str = "liveness.degraded";
    pub const LIVENESS_DEAD: &'static str = "liveness.dead";
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn now_unix() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}
