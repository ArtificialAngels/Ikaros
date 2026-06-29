//! SessionStore — persistent chat session storage for the Ikaros desktop pet.
//!
//! Each session is stored as an atomic JSON file at:
//!   `{root}/data/hermes-agent/ikaros-sessions/{session_id}.json`
//!
//! In-memory cache: `parking_lot::RwLock<HashMap<String, Session>>`
//! Atomic writes: write to tempfile → `std::fs::rename` over target.
//!
//! All public types are `Send + Sync`.

use std::collections::HashMap;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use tracing::{info, warn};

// ---------------------------------------------------------------------------
// Data structures
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SessionMetadata {
    pub source: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pet_window_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Message {
    pub role: String, // "user" | "assistant" | "system"
    pub content: String,
    pub ts: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tokens: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Session {
    pub session_id: String,
    #[serde(default = "default_profile")]
    pub profile: String,
    pub created_at: f64,
    pub updated_at: f64,
    pub metadata: SessionMetadata,
    pub messages: Vec<Message>,
}

fn default_profile() -> String {
    "default".to_string()
}

#[derive(Clone, Debug, Serialize)]
pub struct SessionSummary {
    pub session_id: String,
    pub created_at: f64,
    pub updated_at: f64,
    pub message_count: usize,
    pub metadata: SessionMetadata,
}

// ---------------------------------------------------------------------------
// SessionStore
// ---------------------------------------------------------------------------

pub struct SessionStore {
    root: PathBuf,
    cache: RwLock<HashMap<String, Session>>,
}

impl SessionStore {
    /// Create a new SessionStore rooted at `root`.
    ///
    /// Scans `{root}/data/hermes-agent/ikaros-sessions/` for existing session
    /// files and loads them into the in-memory cache.
    pub fn new(root: &Path) -> Self {
        let sessions_dir = root.join("data").join("hermes-agent").join("ikaros-sessions");
        let cache = RwLock::new(HashMap::new());

        // Scan existing session files
        if sessions_dir.is_dir() {
            let mut loaded = 0usize;
            if let Ok(entries) = fs::read_dir(&sessions_dir) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.extension().and_then(|e| e.to_str()) != Some("json") {
                        continue;
                    }
                    match fs::read_to_string(&path) {
                        Ok(content) => {
                            match serde_json::from_str::<Session>(&content) {
                                Ok(session) => {
                                    let sid = session.session_id.clone();
                                    cache.write().insert(sid, session);
                                    loaded += 1;
                                }
                                Err(e) => {
                                    warn!("sessions: failed to parse {}: {}", path.display(), e);
                                }
                            }
                        }
                        Err(e) => {
                            warn!("sessions: failed to read {}: {}", path.display(), e);
                        }
                    }
                }
            }
            info!(
                "SessionStore: loaded {} existing session(s) from {}",
                loaded,
                sessions_dir.display()
            );
        } else {
            info!(
                "SessionStore: creating sessions dir at {}",
                sessions_dir.display()
            );
            if let Err(e) = fs::create_dir_all(&sessions_dir) {
                warn!("sessions: failed to create dir {}: {}", sessions_dir.display(), e);
            }
        }

        Self {
            root: root.to_path_buf(),
            cache,
        }
    }

    /// Directory where session JSON files are stored.
    fn sessions_dir(&self) -> PathBuf {
        self.root.join("data").join("hermes-agent").join("ikaros-sessions")
    }

    // ── Session file path ──

    fn session_path(&self, session_id: &str) -> PathBuf {
        self.sessions_dir().join(format!("{}.json", session_id))
    }

    // ── Atomic write ──

    fn atomic_write(&self, session: &Session) -> Result<(), String> {
        let path = self.session_path(&session.session_id);
        let dir = path.parent().unwrap();
        if let Err(e) = fs::create_dir_all(dir) {
            return Err(format!("create dir: {}", e));
        }

        // Write to temp file
        let tmp_path = dir.join(format!(
            ".{}.tmp.{}",
            session.session_id,
            std::process::id()
        ));
        let json = serde_json::to_string_pretty(session).map_err(|e| format!("serialize: {}", e))?;
        {
            let mut f = fs::File::create(&tmp_path).map_err(|e| format!("create tmp: {}", e))?;
            f.write_all(json.as_bytes())
                .map_err(|e| format!("write tmp: {}", e))?;
            f.sync_all().map_err(|e| format!("sync tmp: {}", e))?;
        }
        // Atomic rename
        fs::rename(&tmp_path, &path).map_err(|e| format!("rename: {}", e))?;
        Ok(())
    }

    // ── Helpers ──

    fn now() -> f64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64()
    }

    // ── Public API ──

    /// Create a new session with the given metadata.
    /// Returns the created session.
    pub fn create_session(
        &self,
        source: &str,
        pet_window_id: Option<&str>,
        model: Option<&str>,
    ) -> Session {
        let now = Self::now();
        // session_id = pet_<ts_ms>_<pid> (unique enough without extra deps)
        let session_id = format!(
            "pet_{}_{}",
            (now * 1000.0) as u64,
            std::process::id()
        );

        let session = Session {
            session_id: session_id.clone(),
            profile: "default".to_string(),
            created_at: now,
            updated_at: now,
            metadata: SessionMetadata {
                source: source.to_string(),
                pet_window_id: pet_window_id.map(String::from),
                model: model.map(String::from),
            },
            messages: Vec::new(),
        };

        // Write to disk (best-effort)
        if let Err(e) = self.atomic_write(&session) {
            warn!("sessions: create atomic_write failed: {}", e);
        }

        // Insert into cache
        self.cache.write().insert(session_id.clone(), session.clone());
        info!(
            "sessions: created {} (source={}, model={:?})",
            session_id, source, model
        );
        session
    }

    /// Append a message to a session. If the session does not exist in cache,
    /// tries to load from disk. Returns Ok(()) on success.
    pub fn append_message(
        &self,
        session_id: &str,
        role: &str,
        content: &str,
        model: Option<&str>,
    ) -> Result<(), String> {
        let mut cache = self.cache.write();
        let session = if let Some(s) = cache.get_mut(session_id) {
            s
        } else {
            // Try loading from disk
            let path = self.session_path(session_id);
            if path.exists() {
                let content =
                    fs::read_to_string(&path).map_err(|e| format!("read disk: {}", e))?;
                let s: Session =
                    serde_json::from_str(&content).map_err(|e| format!("parse: {}", e))?;
                cache.insert(session_id.to_string(), s);
                cache.get_mut(session_id).unwrap()
            } else {
                return Err(format!("session not found: {}", session_id));
            }
        };

        let msg = Message {
            role: role.to_string(),
            content: content.to_string(),
            ts: Self::now(),
            tokens: None,
            model: model.map(String::from),
        };
        session.messages.push(msg);
        session.updated_at = Self::now();

        // Persist to disk
        let session_clone = session.clone();
        drop(cache); // release lock before I/O
        self.atomic_write(&session_clone)
            .map_err(|e| format!("persist: {}", e))?;

        info!(
            "sessions: appended {} msg to {} ({} total)",
            role, session_id, {
                // count after append
                let c = self.cache.read();
                c.get(session_id).map(|s| s.messages.len()).unwrap_or(0)
            }
        );
        Ok(())
    }

    /// Get messages from a session, optionally limited.
    pub fn get_messages(
        &self,
        session_id: &str,
        limit: Option<usize>,
        from: Option<usize>,
    ) -> Result<(Vec<Message>, usize), String> {
        let cache = self.cache.read();
        let session = if let Some(s) = cache.get(session_id) {
            s
        } else {
            // Try loading from disk
            drop(cache);
            let path = self.session_path(session_id);
            if !path.exists() {
                return Err(format!("session not found: {}", session_id));
            }
            let content =
                fs::read_to_string(&path).map_err(|e| format!("read disk: {}", e))?;
            let s: Session =
                serde_json::from_str(&content).map_err(|e| format!("parse: {}", e))?;
            let total = s.messages.len();
            let from_idx = from.unwrap_or(0);
            let limit = limit.unwrap_or(usize::MAX);
            let msgs: Vec<Message> = s
                .messages
                .into_iter()
                .skip(from_idx)
                .take(limit)
                .collect();
            return Ok((msgs, total));
        };

        let total = session.messages.len();
        let from_idx = from.unwrap_or(0);
        let limit = limit.unwrap_or(usize::MAX);
        let msgs: Vec<Message> = session
            .messages
            .iter()
            .skip(from_idx)
            .take(limit)
            .cloned()
            .collect();
        Ok((msgs, total))
    }

    /// List all sessions, newest-first, optionally limited.
    pub fn list_sessions(&self, limit: Option<usize>) -> Vec<SessionSummary> {
        let cache = self.cache.read();
        let mut summaries: Vec<SessionSummary> = cache
            .values()
            .map(|s| SessionSummary {
                session_id: s.session_id.clone(),
                created_at: s.created_at,
                updated_at: s.updated_at,
                message_count: s.messages.len(),
                metadata: s.metadata.clone(),
            })
            .collect();
        // Sort: newest updated_at first
        summaries.sort_by(|a, b| b.updated_at.partial_cmp(&a.updated_at).unwrap_or(std::cmp::Ordering::Equal));
        let limit = limit.unwrap_or(usize::MAX);
        summaries.truncate(limit);
        summaries
    }

    /// Delete a session (remove from cache + disk).
    pub fn delete_session(&self, session_id: &str) -> Result<(), String> {
        // Remove from cache
        self.cache.write().remove(session_id);

        // Remove from disk
        let path = self.session_path(session_id);
        if path.exists() {
            fs::remove_file(&path).map_err(|e| format!("remove file: {}", e))?;
        }

        info!("sessions: deleted {}", session_id);
        Ok(())
    }

    /// Ensure the sessions directory exists, creating it if needed.
    pub fn ensure_dir(&self) -> Result<(), String> {
        let dir = self.sessions_dir();
        fs::create_dir_all(&dir).map_err(|e| format!("create dir: {}", e))?;
        Ok(())
    }
}
