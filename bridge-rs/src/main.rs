//! hermes-bridge-rs — Rust bridge PoC
//!
//! axum + tokio reverse proxy for llama-server.
//! Replaces the Python uvicorn bridge with:
//!   - Zero GIL contention (no Python runtime)
//!   - Native async I/O (tokio)
//!   - Proper SSE streaming (reqwest bytes_stream → axum Body)
//!   - WebSocket support for voice
//!
//! Port topology:
//!   :7860 — this bridge (replaces Python uvicorn bridge)
//!   :8080 — llama-server (upstream)

pub mod intent_router;
pub mod task_delegation;
pub mod signals;
pub mod health;

use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        Query, State,
    },
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use futures::TryStreamExt;
use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    io,
    net::SocketAddr,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};
use tokio::sync::{RwLock, Semaphore};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use base64::Engine;
use tracing::{error, info, warn};

// ---------------------------------------------------------------------------
// Project root resolution
// ---------------------------------------------------------------------------

/// Get the project root directory (HERMES_ROOT).
/// Priority: HERMES_ROOT env > walk up from exe looking for modules/ > current_dir
fn project_root() -> std::path::PathBuf {
    if let Ok(root) = std::env::var("HERMES_ROOT") {
        return std::path::PathBuf::from(root);
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut dir = exe.parent().expect("exe has no parent");
        // Walk up parent chain looking for a directory that contains "modules/"
        // Exe is at: bridge-rs/target/release/hermes-bridge-rs.exe
        // Project root is where modules/ lives (Hermes Agent/)
        for _ in 0..8 {
            if dir.join("modules").is_dir() {
                return dir.to_path_buf();
            }
            dir = match dir.parent() {
                Some(p) => p,
                None => break,
            };
        }
    }
    std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."))
}

/// WebUI SQLite DB path (for session context endpoints)
fn webui_db_path() -> std::path::PathBuf {
    std::env::var("HERMES_WEBUI_DB_PATH")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| project_root().join("data").join("webui").join("hermes-web-ui.db"))
}

/// Find portable-python executable path
fn find_portable_python() -> String {
    let python = project_root().join("portable-python").join("python.exe");
    if python.is_file() {
        python.to_string_lossy().to_string()
    } else {
        "python".to_string()
    }
}

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const BRIDGE_PORT: u16 = 7860;
const DEFAULT_LLAMA_UPSTREAM: &str = "http://127.0.0.1:8080";
const MAX_CONCURRENT_CHAT: usize = 3;
const CHAT_TIMEOUT_SECS: u64 = 300;
const HEALTH_CHECK_INTERVAL_SECS: u64 = 10;

/// Get upstream URLs from environment or default
fn get_upstream_urls() -> Vec<String> {
    std::env::var("HERMES_LLAMA_UPSTREAMS")
        .ok()
        .map(|v| v.split(',').map(|s| s.trim().trim_end_matches('/').to_string()).collect())
        .unwrap_or_else(|| vec![DEFAULT_LLAMA_UPSTREAM.to_string()])
}

// ---------------------------------------------------------------------------
// Signal Bus — in-process telemetry signal bus (mirrors bridge/telemetry.py)
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Serialize)]
struct SignalEnvelope {
    id: u64,
    topic: String,
    payload: serde_json::Value,
    ts: f64,
}

struct SignalBus {
    envelopes: RwLock<Vec<SignalEnvelope>>,
    next_id: AtomicU64,
    capacity: usize,
}

impl SignalBus {
    fn new(capacity: usize) -> Self {
        Self {
            envelopes: RwLock::new(Vec::with_capacity(capacity)),
            next_id: AtomicU64::new(1),
            capacity,
        }
    }

    async fn emit(&self, topic: &str, payload: serde_json::Value) -> SignalEnvelope {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let env = SignalEnvelope {
            id,
            topic: topic.to_string(),
            payload,
            ts: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs_f64(),
        };
        let mut buf = self.envelopes.write().await;
        buf.push(env.clone());
        if buf.len() > self.capacity {
            let excess = buf.len() - self.capacity;
            buf.drain(0..excess);
        }
        env
    }

    async fn recent(&self, topic: Option<&str>, limit: usize) -> Vec<SignalEnvelope> {
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

/// Simple request log for /v1/signals/stats
struct RequestLog {
    entries: RwLock<Vec<RequestEntry>>,
    start_time: Instant,
    capacity: usize,
}

#[derive(Clone, Debug, Serialize)]
struct RequestEntry {
    path: String,
    status: u16,
    elapsed_ms: u64,
    ts: f64,
}

impl RequestLog {
    fn new(capacity: usize) -> Self {
        Self {
            entries: RwLock::new(Vec::with_capacity(capacity)),
            start_time: Instant::now(),
            capacity,
        }
    }

    async fn record(&self, path: &str, status: u16, elapsed_ms: u64) {
        let entry = RequestEntry {
            path: path.to_string(),
            status,
            elapsed_ms,
            ts: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs_f64(),
        };
        let mut buf = self.entries.write().await;
        buf.push(entry);
        if buf.len() > self.capacity {
            let excess = buf.len() - self.capacity;
            buf.drain(0..excess);
        }
    }

    async fn stats(&self) -> serde_json::Value {
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
}

// ---------------------------------------------------------------------------
// Ikaros memory reader
// ---------------------------------------------------------------------------

fn icarus_memory_dir() -> std::path::PathBuf {
    std::env::var("ICARUS_MEMORY_DIR")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| project_root().join("data").join("hermes-agent").join("memories").join("ikaros"))
}

/// Read the most recent N daily notes (sorted newest-first).
/// Skips dojo-*.md files.
fn read_memory_files(max_files: usize) -> Vec<serde_json::Value> {
    let dir = icarus_memory_dir();
    if !dir.is_dir() {
        return Vec::new();
    }
    let mut files: Vec<std::path::PathBuf> = match std::fs::read_dir(&dir) {
        Ok(entries) => entries
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| {
                p.extension().map_or(false, |ext| ext == "md")
                    && !p.file_name().map_or(false, |n| n.to_string_lossy().starts_with("dojo-"))
            })
            .collect(),
        Err(_) => return Vec::new(),
    };
    files.sort_by(|a, b| b.file_name().cmp(&a.file_name()));
    files.truncate(max_files);

    files
        .into_iter()
        .filter_map(|p| {
            let text = std::fs::read_to_string(&p).ok()?;
            let date = p.file_stem()?.to_string_lossy().to_string();
            // Extract headline = first non-empty line
            let headline = text
                .lines()
                .find(|l| !l.trim().is_empty() && !l.starts_with('#'))
                .unwrap_or("")
                .to_string();
            Some(serde_json::json!({
                "date": date,
                "path": p.to_string_lossy().to_string(),
                "headline": headline,
            }))
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Module scanner (mirrors _scan_module_json in Python bridge)
// ---------------------------------------------------------------------------

async fn tcp_alive(host: &str, port: u16) -> bool {
    let addr = format!("{}:{}", host, port);
    match tokio::net::TcpStream::connect(&addr).await {
        Ok(_) => true,
        Err(_) => false,
    }
}

async fn scan_modules(_client: &reqwest::Client) -> Vec<serde_json::Value> {
    let root = project_root();
    let modules_dir = root.join("modules");
    if !modules_dir.is_dir() {
        return Vec::new();
    }
    let mut out = Vec::new();
    let entries = match std::fs::read_dir(&modules_dir) {
        Ok(e) => e,
        Err(_) => return Vec::new(),
    };
    for entry in entries.filter_map(|e| e.ok()) {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        let json_path = path.join("module.json");
        if !json_path.is_file() {
            continue;
        }
        let data = match std::fs::read_to_string(&json_path) {
            Ok(s) => match serde_json::from_str::<serde_json::Value>(&s) {
                Ok(v) => v,
                Err(e) => {
                    out.push(serde_json::json!({
                        "name": path.file_name().unwrap_or_default().to_string_lossy(),
                        "error": format!("bad module.json: {}", e),
                        "alive": false,
                    }));
                    continue;
                }
            },
            Err(e) => {
                out.push(serde_json::json!({
                    "name": path.file_name().unwrap_or_default().to_string_lossy(),
                    "error": format!("read error: {}", e),
                    "alive": false,
                }));
                continue;
            }
        };
        let net = data.get("network").cloned().unwrap_or(serde_json::json!({}));
        let port = net.get("port").and_then(|v| v.as_u64()).map(|v| v as u16);
        let host = net.get("host").and_then(|v| v.as_str()).unwrap_or("127.0.0.1");
        let alive = if let Some(p) = port {
            tcp_alive(host, p).await
        } else {
            false
        };
        let health_ep = net.get("health_endpoint").and_then(|v| v.as_str()).unwrap_or("/health");
        let protocol = net.get("protocol").and_then(|v| v.as_str()).unwrap_or("http");
        let depends = data.get("depends_on").cloned().unwrap_or(serde_json::json!([]));
        let runtime = data.get("runtime").cloned().unwrap_or(serde_json::json!({}));
        out.push(serde_json::json!({
            "name": data.get("name").and_then(|v| v.as_str()).unwrap_or(""),
            "version": data.get("version").and_then(|v| v.as_str()).unwrap_or(""),
            "description": data.get("description").and_then(|v| v.as_str()).unwrap_or(""),
            "type": data.get("type").and_then(|v| v.as_str()).unwrap_or("service"),
            "runtime_kind": runtime.get("kind").and_then(|v| v.as_str()).unwrap_or(""),
            "port": port,
            "host": host,
            "protocol": protocol,
            "health_endpoint": health_ep,
            "health_url": port.map(|p| format!("{}://{}:{}{}", protocol, host, p, health_ep)),
            "depends_on": depends,
            "alive": alive,
        }));
    }
    out
}

// ---------------------------------------------------------------------------
// Worker — represents a single llama-server instance
// ---------------------------------------------------------------------------

#[derive(Clone, Debug)]
struct Worker {
    url: String,
    alive: bool,
    models: Vec<String>,
    last_check: Instant,
    request_count: u64,
}

impl Worker {
    fn new(url: String) -> Self {
        Self {
            url,
            alive: false,
            models: Vec::new(),
            last_check: Instant::now(),
            request_count: 0,
        }
    }
}

// ---------------------------------------------------------------------------
// WorkerPool — manages multiple llama-server workers
// ---------------------------------------------------------------------------

struct WorkerPool {
    workers: RwLock<Vec<Worker>>,
    model_map: RwLock<HashMap<String, usize>>, // model_name -> worker index
}

impl WorkerPool {
    fn new(urls: Vec<String>) -> Self {
        let workers = urls.into_iter().map(Worker::new).collect();
        Self {
            workers: RwLock::new(workers),
            model_map: RwLock::new(HashMap::new()),
        }
    }

    /// Find which worker has a given model
    async fn find_worker_for_model(&self, model: &str) -> Option<usize> {
        // Check model map first
        {
            let map = self.model_map.read().await;
            if let Some(&idx) = map.get(model) {
                let workers = self.workers.read().await;
                if idx < workers.len() && workers[idx].alive {
                    return Some(idx);
                }
            }
            // Try partial match (model name without .gguf suffix)
            let model_base = model.trim_end_matches(".gguf");
            for (name, &idx) in map.iter() {
                if name.contains(model_base) || model_base.contains(name.as_str()) {
                    let workers = self.workers.read().await;
                    if idx < workers.len() && workers[idx].alive {
                        return Some(idx);
                    }
                }
            }
        }
        // Fallback: first alive worker
        let workers = self.workers.read().await;
        workers.iter().position(|w| w.alive)
    }

    /// Get the first alive worker (for non-model-specific requests)
    async fn get_alive_worker(&self) -> Option<usize> {
        let workers = self.workers.read().await;
        workers.iter().position(|w| w.alive)
    }

    /// Refresh health and model list for all workers
    async fn refresh(&self, client: &reqwest::Client) {
        let mut workers = self.workers.write().await;
        let mut new_map = HashMap::new();

        for (idx, worker) in workers.iter_mut().enumerate() {
            // Health check
            let health_url = format!("{}/props", worker.url);
            match client
                .get(&health_url)
                .timeout(Duration::from_secs(3))
                .send()
                .await
            {
                Ok(resp) if resp.status().is_success() => {
                    worker.alive = true;
                }
                _ => {
                    worker.alive = false;
                    worker.models.clear();
                    continue;
                }
            }

            // Fetch models
            let models_url = format!("{}/v1/models", worker.url);
            match client
                .get(&models_url)
                .timeout(Duration::from_secs(5))
                .send()
                .await
            {
                Ok(resp) if resp.status().is_success() => {
                    if let Ok(json) = resp.json::<serde_json::Value>().await {
                        if let Some(data) = json.get("data").and_then(|d| d.as_array()) {
                            worker.models = data
                                .iter()
                                .filter_map(|m| m.get("id").and_then(|id| id.as_str()).map(String::from))
                                .collect();
                            for model in &worker.models {
                                new_map.insert(model.clone(), idx);
                            }
                        }
                    }
                }
                _ => {
                    worker.models.clear();
                }
            }
            worker.last_check = Instant::now();
        }

        *self.model_map.write().await = new_map;
    }

    /// Get health summary
    async fn health_summary(&self) -> Vec<serde_json::Value> {
        let workers = self.workers.read().await;
        workers
            .iter()
            .map(|w| {
                serde_json::json!({
                    "url": w.url,
                    "alive": w.alive,
                    "models": w.models,
                    "requests": w.request_count,
                })
            })
            .collect()
    }
}

// ---------------------------------------------------------------------------
// Neuro state — IkarosSignals 1:1 in Rust
// ---------------------------------------------------------------------------

#[derive(Clone, Debug, Serialize, Deserialize)]
struct HistoryEntry {
    role: String,
    content: String,
    ts: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct MemoryEntry {
    id: String,
    document: String,
    metadata: serde_json::Value,
    created_at: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct ProactiveEntry {
    reason: String,
    content: String,
    ts: f64,
}

struct NeuroState {
    stt_ready: bool,
    tts_ready: bool,
    llm_ready: bool,
    human_speaking: bool,
    ai_thinking: bool,
    ai_speaking: bool,
    new_message: bool,
    last_message_time: f64,
    patience: f64,
    history: Vec<HistoryEntry>,
    memories: Vec<MemoryEntry>,
    proactive_messages: Vec<ProactiveEntry>,
    next_memory_id: u64,
}

impl NeuroState {
    fn new() -> Self {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();
        let memories = load_memories_from_disk();
        Self {
            stt_ready: false,
            tts_ready: false,
            llm_ready: true,
            human_speaking: false,
            ai_thinking: false,
            ai_speaking: false,
            new_message: false,
            last_message_time: now,
            patience: 30.0,
            history: Vec::new(),
            memories,
            proactive_messages: Vec::new(),
            next_memory_id: 1000,
        }
    }

    fn time_since_last(&self) -> f64 {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();
        (now - self.last_message_time).max(0.0)
    }
}

fn neuro_memories_path() -> std::path::PathBuf {
    project_root().join("data").join("ikaros-coordination").join("neuro-memories.jsonl")
}

fn load_memories_from_disk() -> Vec<MemoryEntry> {
    let path = neuro_memories_path();
    if !path.exists() {
        return Vec::new();
    }
    let content = match std::fs::read_to_string(&path) {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    content.lines()
        .filter_map(|line| serde_json::from_str::<MemoryEntry>(line).ok())
        .collect()
}

fn append_memory_to_disk(entry: &MemoryEntry) {
    let path = neuro_memories_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(line) = serde_json::to_string(entry) {
        use std::io::Write;
        if let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
        {
            let _ = writeln!(file, "{}", line);
        }
    }
}

// ---------------------------------------------------------------------------
// Shared state
// ---------------------------------------------------------------------------

struct AppState {
    http_client: reqwest::Client,
    chat_semaphore: Arc<Semaphore>,
    request_counter: AtomicU64,
    start_time: Instant,
    worker_pool: Arc<WorkerPool>,
    signal_bus: Arc<SignalBus>,
    request_log: Arc<RequestLog>,
    /// Cached modules list (refreshed periodically)
    modules_cache: RwLock<(Instant, Vec<serde_json::Value>)>,
    /// Neuro/Prompter state (shared, RwLock)
    neuro_state: Arc<RwLock<NeuroState>>,
}

impl AppState {
    fn new() -> Self {
        let http_client = reqwest::Client::builder()
            .pool_max_idle_per_host(8)
            .connect_timeout(Duration::from_secs(5))
            .timeout(Duration::from_secs(CHAT_TIMEOUT_SECS))
            .build()
            .expect("failed to build HTTP client");

        let urls = get_upstream_urls();
        info!("configured {} upstream worker(s): {:?}", urls.len(), urls);

        let worker_pool = Arc::new(WorkerPool::new(urls));

        Self {
            http_client,
            chat_semaphore: Arc::new(Semaphore::new(MAX_CONCURRENT_CHAT)),
            request_counter: AtomicU64::new(0),
            start_time: Instant::now(),
            worker_pool,
            signal_bus: Arc::new(SignalBus::new(500)),
            request_log: Arc::new(RequestLog::new(1000)),
            modules_cache: RwLock::new((Instant::now(), Vec::new())),
            neuro_state: Arc::new(RwLock::new(NeuroState::new())),
        }
    }

    /// Start background health monitor
    fn start_health_monitor(&self) {
        let pool = self.worker_pool.clone();
        let client = self.http_client.clone();
        tokio::spawn(async move {
            loop {
                pool.refresh(&client).await;
                let workers = pool.workers.read().await;
                let alive_count = workers.iter().filter(|w| w.alive).count();
                let total_models: usize = workers.iter().map(|w| w.models.len()).sum();
                info!(
                    "health: {}/{} workers alive, {} models total",
                    alive_count,
                    workers.len(),
                    total_models
                );
                drop(workers);
                tokio::time::sleep(Duration::from_secs(HEALTH_CHECK_INTERVAL_SECS)).await;
            }
        });
    }
}

// ---------------------------------------------------------------------------
// Health endpoint
// ---------------------------------------------------------------------------

#[derive(Serialize)]
struct HealthResponse {
    status: &'static str,
    version: &'static str,
    uptime_secs: u64,
    workers: Vec<serde_json::Value>,
}

async fn health(State(state): State<Arc<AppState>>) -> Json<HealthResponse> {
    Json(HealthResponse {
        status: "ok",
        version: env!("CARGO_PKG_VERSION"),
        uptime_secs: state.start_time.elapsed().as_secs(),
        workers: state.worker_pool.health_summary().await,
    })
}

// ---------------------------------------------------------------------------
// Chat completions proxy (streaming + non-streaming)
// ---------------------------------------------------------------------------

#[allow(dead_code)]
#[derive(Deserialize)]
struct ChatRequest {
    #[serde(default)]
    stream: bool,
    model: Option<String>,
    // Pass through all other fields as-is
    #[serde(flatten)]
    extra: serde_json::Value,
}

/// Shared chat completions implementation (called by both streaming and non-streaming handlers)
async fn _chat_completions_impl(
    state: Arc<AppState>,
    headers: HeaderMap,
    body: serde_json::Value,
) -> Response {
    let req_id = state.request_counter.fetch_add(1, Ordering::Relaxed);
    let start = Instant::now();

    let is_stream = body.get("stream").and_then(|v| v.as_bool()).unwrap_or(false);
    let model = body
        .get("model")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown");

    info!(req_id = req_id, model = model, stream = is_stream, "chat request");

    // Find the right worker for this model
    let worker_idx = match state.worker_pool.find_worker_for_model(model).await {
        Some(idx) => idx,
        None => {
            warn!(model = model, "no alive worker for model");
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(serde_json::json!({"error": format!("no worker available for model: {}", model)})),
            ).into_response();
        }
    };

    let worker_url = {
        let workers = state.worker_pool.workers.read().await;
        workers[worker_idx].url.clone()
    };

    // Acquire semaphore
    let _permit = match state.chat_semaphore.acquire().await {
        Ok(permit) => permit,
        Err(_) => {
            warn!("semaphore closed — rejecting request");
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(serde_json::json!({"error": "bridge overloaded"})),
            ).into_response();
        }
    };

    // Forward to worker
    let upstream_url = format!("{}/v1/chat/completions", worker_url);
    let mut req_builder = state
        .http_client
        .post(&upstream_url)
        .header("content-type", "application/json")
        .json(&body);

    if let Some(auth) = headers.get("authorization") {
        req_builder = req_builder.header("authorization", auth);
    }

    let resp = match req_builder.send().await {
        Ok(r) => r,
        Err(e) => {
            let elapsed = start.elapsed().as_millis();
            error!(req_id = req_id, worker = %worker_url, elapsed_ms = elapsed, error = %e, "upstream failed");
            return (
                StatusCode::BAD_GATEWAY,
                Json(serde_json::json!({"error": format!("worker {} unreachable: {}", worker_url, e)})),
            ).into_response();
        }
    };

    let status = resp.status();
    let elapsed = start.elapsed().as_millis();

    if !status.is_success() {
        let body_text = resp.text().await.unwrap_or_default();
        warn!(req_id = req_id, status = status.as_u16(), elapsed_ms = elapsed, "upstream error: {}", body_text);
        return (StatusCode::from_u16(status.as_u16()).unwrap_or(StatusCode::BAD_GATEWAY), body_text).into_response();
    }

    // Increment worker request count
    {
        let mut workers = state.worker_pool.workers.write().await;
        if worker_idx < workers.len() {
            workers[worker_idx].request_count += 1;
        }
    }

    if is_stream {
        info!(req_id = req_id, worker = %worker_url, elapsed_ms = elapsed, "streaming");
        // Record in request log + emit signal
        state.request_log.record("/v1/chat/completions", 200, elapsed as u64).await;
        state.signal_bus.emit("chat.request", serde_json::json!({"model": model, "stream": true})).await;
        forward_streaming_response(resp)
    } else {
        match resp.text().await {
            Ok(body_text) => {
                info!(req_id = req_id, elapsed_ms = elapsed, bytes = body_text.len(), "json");
                state.request_log.record("/v1/chat/completions", 200, elapsed as u64).await;
                state.signal_bus.emit("chat.request", serde_json::json!({"model": model, "stream": false})).await;
                (StatusCode::OK, [("content-type", "application/json")], body_text).into_response()
            }
            Err(e) => {
                error!(req_id = req_id, error = %e, "read body failed");
                state.request_log.record("/v1/chat/completions", 500, elapsed as u64).await;
                StatusCode::INTERNAL_SERVER_ERROR.into_response()
            }
        }
    }
}

/// POST /v1/chat/completions — proxy to llama-server worker
async fn chat_completions(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
) -> Response {
    _chat_completions_impl(state, headers, body).await
}

/// POST /v1/chat/completions/sse — dedicated streaming SSE endpoint.
/// Identical to POST /v1/chat/completions with stream=true, but lives at
/// a distinct URL so CDN/proxy layers can route on path (no body parsing).
async fn chat_completions_sse(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(mut body): Json<serde_json::Value>,
) -> Response {
    body["stream"] = serde_json::Value::Bool(true);
    _chat_completions_impl(state, headers, body).await
}

/// Forward a streaming response from upstream (SSE passthrough)
fn forward_streaming_response(resp: reqwest::Response) -> Response {
    let status = resp.status();
    let headers = resp.headers().clone();

    // Convert reqwest bytes_stream → axum Body
    let stream = TryStreamExt::map_err(resp.bytes_stream(), io::Error::other);
    let body = axum::body::Body::from_stream(stream);

    let mut response = Response::new(body);
    *response.status_mut() = StatusCode::from_u16(status.as_u16()).unwrap_or(StatusCode::OK);

    // Copy headers (especially content-type: text/event-stream)
    for (name, value) in headers.iter() {
        if let (Ok(name), Ok(value)) = (
            axum::http::HeaderName::from_bytes(name.as_str().as_bytes()),
            axum::http::HeaderValue::from_bytes(value.as_bytes()),
        ) {
            response.headers_mut().insert(name, value);
        }
    }

    response
}

// ---------------------------------------------------------------------------
// Models proxy (pass-through)
// ---------------------------------------------------------------------------

/// GET /v1/models — aggregate from all workers
async fn list_models(State(state): State<Arc<AppState>>) -> Response {
    let workers = state.worker_pool.workers.read().await;
    let mut all_models = Vec::new();

    for worker in workers.iter() {
        if !worker.alive {
            continue;
        }
        for model in &worker.models {
            all_models.push(serde_json::json!({
                "id": model,
                "object": "model",
                "owned_by": worker.url,
            }));
        }
    }

    Json(serde_json::json!({
        "object": "list",
        "data": all_models,
    })).into_response()
}

// ---------------------------------------------------------------------------
// WebSocket /v1/voice/ws — skeleton
// ---------------------------------------------------------------------------

#[derive(Deserialize)]
struct VoiceWsQuery {
    #[serde(default = "default_voice_model")]
    model: String,
}

fn default_voice_model() -> String {
    "default".to_string()
}

/// WebSocket endpoint for voice processing
async fn voice_ws(
    ws: WebSocketUpgrade,
    Query(query): Query<VoiceWsQuery>,
) -> Response {
    info!(model = %query.model, "voice WebSocket connection");
    ws.on_upgrade(move |socket| handle_voice_socket(socket, query.model))
}

async fn handle_voice_socket(mut socket: WebSocket, model: String) {
    info!(model = %model, "voice WebSocket established");

    // Send welcome message
    let welcome = serde_json::json!({
        "type": "connected",
        "model": model,
        "bridge": "hermes-bridge-rs",
        "version": env!("CARGO_PKG_VERSION"),
    });
    if socket
        .send(Message::Text(welcome.to_string().into()))
        .await
        .is_err()
    {
        return;
    }

    // Start voice worker process
    let python = find_portable_python();
    let worker_script = project_root().join("bridge-rs").join("workers").join("voice_worker.py");
    if !worker_script.is_file() {
        warn!(path = %worker_script.display(), "voice_worker.py not found");
        let _ = socket.send(Message::Text(
            serde_json::json!({"type": "error", "message": "voice_worker.py not found"}).to_string().into()
        )).await;
        return;
    }

    let mut child = match tokio::process::Command::new(&python)
        .arg(worker_script.to_string_lossy().as_ref())
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::inherit())
        .spawn()
    {
        Ok(c) => c,
        Err(e) => {
            warn!("failed to start voice_worker.py: {}", e);
            let _ = socket.send(Message::Text(
                serde_json::json!({"type": "error", "message": format!("voice worker failed: {}", e)}).to_string().into()
            )).await;
            return;
        }
    };

    let mut worker_stdin = match child.stdin.take() {
        Some(s) => s,
        None => {
            warn!("voice worker: no stdin");
            return;
        }
    };
    let worker_stdout = match child.stdout.take() {
        Some(s) => s,
        None => {
            warn!("voice worker: no stdout");
            return;
        }
    };
    let reader = BufReader::new(worker_stdout);
    let mut worker_lines = reader.lines();

    // Send initial model config
    let init_msg = serde_json::json!({"type": "set_model", "model": model});
    let _ = worker_stdin
        .write_all((serde_json::to_string(&init_msg).unwrap() + "\n").as_bytes())
        .await;
    let _ = worker_stdin.flush().await;

    let mut request_id = 0u64;
    let mut audio_buffer: Vec<u8> = Vec::new();
    let mut last_audio_time = tokio::time::Instant::now();
    let mut is_audio_session = false;
    let base64_engine = base64::engine::general_purpose::STANDARD;

    // Message loop — handle WS and worker concurrently
    loop {
        tokio::select! {
            // ── Read from WebSocket ──
            ws_msg = socket.recv() => {
                match ws_msg {
                    Some(Ok(Message::Text(text))) => {
                        match serde_json::from_str::<serde_json::Value>(&text) {
                            Ok(parsed) => {
                                let msg_type = parsed.get("type")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("unknown");
                                match msg_type {
                                    "start" => {
                                        is_audio_session = true;
                                        audio_buffer.clear();
                                        let m = parsed.get("model")
                                            .and_then(|v| v.as_str())
                                            .unwrap_or("auto");
                                        let msg = serde_json::json!({"type": "set_model", "model": m});
                                        let _ = worker_stdin
                                            .write_all((serde_json::to_string(&msg).unwrap() + "\n").as_bytes())
                                            .await;
                                        let _ = worker_stdin.flush().await;
                                        let resp = serde_json::json!({"type": "status", "message": "listening"});
                                        let _ = socket.send(Message::Text(resp.to_string().into())).await;
                                    }
                                    "stop" => {
                                        // Flush accumulated audio to worker
                                        if !audio_buffer.is_empty() {
                                            request_id += 1;
                                            let b64 = base64_engine.encode(&audio_buffer);
                                            let msg = serde_json::json!({
                                                "type": "audio",
                                                "request_id": request_id,
                                                "data": b64,
                                                "final": true,
                                            });
                                            let _ = worker_stdin
                                                .write_all((serde_json::to_string(&msg).unwrap() + "\n").as_bytes())
                                                .await;
                                            let _ = worker_stdin.flush().await;
                                            audio_buffer.clear();
                                        }
                                        is_audio_session = false;
                                        // Tell worker to stop
                                        let msg = serde_json::json!({"type": "stop"});
                                        let _ = worker_stdin
                                            .write_all((serde_json::to_string(&msg).unwrap() + "\n").as_bytes())
                                            .await;
                                        let _ = worker_stdin.flush().await;
                                    }
                                    "set_model" => {
                                        let m = parsed.get("model")
                                            .and_then(|v| v.as_str())
                                            .unwrap_or("auto");
                                        let msg = serde_json::json!({"type": "set_model", "model": m});
                                        let _ = worker_stdin
                                            .write_all((serde_json::to_string(&msg).unwrap() + "\n").as_bytes())
                                            .await;
                                        let _ = worker_stdin.flush().await;
                                    }
                                    "ping" => {
                                        let resp = serde_json::json!({"type": "pong"});
                                        let _ = socket.send(Message::Text(resp.to_string().into())).await;
                                    }
                                    _ => {
                                        let resp = serde_json::json!({
                                            "type": "error",
                                            "message": format!("unknown: {}", msg_type),
                                        });
                                        let _ = socket.send(Message::Text(resp.to_string().into())).await;
                                    }
                                }
                            }
                            Err(e) => {
                                let resp = serde_json::json!({
                                    "type": "error",
                                    "message": format!("bad JSON: {}", e),
                                });
                                let _ = socket.send(Message::Text(resp.to_string().into())).await;
                            }
                        }
                    }
                    Some(Ok(Message::Binary(data))) => {
                        if is_audio_session {
                            audio_buffer.extend_from_slice(&data);
                            last_audio_time = tokio::time::Instant::now();
                        }
                    }
                    Some(Ok(Message::Close(_))) => {
                        info!("voice WS closed by client");
                        break;
                    }
                    Some(Err(e)) => {
                        warn!(error = %e, "voice WS error");
                        break;
                    }
                    None => {
                        break;
                    }
                    _ => {}
                }
            }

            // ── Check VAD silence timeout (every 500ms) ──
            _ = tokio::time::sleep(Duration::from_millis(500)) => {
                if is_audio_session && !audio_buffer.is_empty() {
                    let elapsed = last_audio_time.elapsed();
                    if elapsed > Duration::from_secs_f64(1.5) {
                        // Silence detected — flush to worker
                        info!("voice VAD: silence {:.1}s, flushing {}B",
                              elapsed.as_secs_f64(), audio_buffer.len());
                        request_id += 1;
                        let b64 = base64_engine.encode(&audio_buffer);
                        let msg = serde_json::json!({
                            "type": "audio",
                            "request_id": request_id,
                            "data": b64,
                            "final": true,
                        });
                        let _ = worker_stdin
                            .write_all((serde_json::to_string(&msg).unwrap() + "\n").as_bytes())
                            .await;
                        let _ = worker_stdin.flush().await;
                        audio_buffer.clear();
                    } else if audio_buffer.len() > 1_048_576 {
                        // 1MB max per utterance — emergency flush
                        info!("voice buffer: {}B, emergency flush", audio_buffer.len());
                        request_id += 1;
                        let b64 = base64_engine.encode(&audio_buffer);
                        let msg = serde_json::json!({
                            "type": "audio",
                            "request_id": request_id,
                            "data": b64,
                            "final": true,
                        });
                        let _ = worker_stdin
                            .write_all((serde_json::to_string(&msg).unwrap() + "\n").as_bytes())
                            .await;
                        let _ = worker_stdin.flush().await;
                        audio_buffer.clear();
                    }
                }
            }

            // ── Read from worker stdout ──
            worker_line = worker_lines.next_line() => {
                match worker_line {
                    Ok(Some(line)) => {
                        let trimmed = line.trim();
                        if trimmed.is_empty() {
                            continue;
                        }
                        match serde_json::from_str::<serde_json::Value>(trimmed) {
                            Ok(resp) => {
                                let resp_type = resp.get("type")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("");

                                if resp_type == "mp3" {
                                    // Decode base64 MP3, send as binary to WS
                                    if let Some(data_b64) = resp.get("data").and_then(|v| v.as_str()) {
                                        match base64_engine.decode(data_b64) {
                                            Ok(mp3_bytes) => {
                                                let _ = socket.send(Message::Binary(mp3_bytes.into())).await;
                                            }
                                            Err(e) => {
                                                warn!("voice worker: bad base64 in mp3: {}", e);
                                            }
                                        }
                                    }
                                } else {
                                    // Forward JSON directly to WebSocket
                                    let resp_json_str = serde_json::to_string(&resp).unwrap_or_default();
                                    let _ = socket.send(Message::Text(resp_json_str.into())).await;
                                }
                            }
                            Err(e) => {
                                warn!("voice worker: bad JSON line ({}): {}", e, trimmed);
                            }
                        }
                    }
                    Ok(None) => {
                        info!("voice worker: stdout ended");
                        break;
                    }
                    Err(e) => {
                        warn!("voice worker: stdout error: {}", e);
                        break;
                    }
                }
            }
        }
    }

    // Cleanup: kill worker process
    let _ = worker_stdin.shutdown().await;
    let _ = child.wait().await;
    info!("voice WebSocket session ended");
}

// ---------------------------------------------------------------------------
// Ikaros endpoints — Phase 2 (real file I/O)
// ---------------------------------------------------------------------------

/// GET /v1/liveness — composite liveness check
async fn liveness(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    let workers = state.worker_pool.workers.read().await;
    let alive_count = workers.iter().filter(|w| w.alive).count();
    let local_alive = alive_count > 0;

    // Probe llama-server /health directly
    let llama_alive = {
        if let Some(idx) = state.worker_pool.get_alive_worker().await {
            let url = { workers[idx].url.clone() };
            state.http_client.get(format!("{}/health", url))
                .timeout(Duration::from_secs(3))
                .send().await
                .map_or(false, |r| r.status().is_success())
        } else {
            false
        }
    };

    let status = if local_alive && llama_alive { "ok" }
                 else if local_alive { "degraded" }
                 else { "dead" };

    // Emit liveness signal
    state.signal_bus.emit(
        if local_alive && llama_alive { "liveness.ok" } else { "liveness.dead" },
        serde_json::json!({"local_alive": local_alive, "llama_alive": llama_alive, "workers": alive_count}),
    ).await;

    Json(serde_json::json!({
        "status": status,
        "ts": chrono::Utc::now().timestamp(),
        "local": {
            "alive": local_alive,
            "workers": alive_count,
        },
        "summary": format!("{}/{} workers alive", alive_count, workers.len()),
    }))
}

// ---------------------------------------------------------------------------
// Neuro endpoints
// ---------------------------------------------------------------------------

/// GET /v1/neuro/status — return current neuro state snapshot
async fn neuro_status(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    let ns = state.neuro_state.read().await;
    Json(serde_json::json!({
        "patience": ns.patience,
        "time_since_last_message": ns.time_since_last(),
        "history_len": ns.history.len(),
        "human_speaking": ns.human_speaking,
        "AI_thinking": ns.ai_thinking,
        "AI_speaking": ns.ai_speaking,
        "llm_ready": ns.llm_ready,
        "stt_ready": ns.stt_ready,
        "tts_ready": ns.tts_ready,
        "memories_len": ns.memories.len(),
        "pet_visible": true,
        "pet_mode": "continuous",
    }))
}

/// POST /v1/neuro/patience — set patience threshold
async fn neuro_set_patience(
    State(state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> Json<serde_json::Value> {
    let seconds = body.get("seconds")
        .and_then(|v| v.as_f64())
        .map(|s| s.max(5.0).min(600.0))
        .unwrap_or(30.0);
    {
        let mut ns = state.neuro_state.write().await;
        ns.patience = seconds;
    }
    state.signal_bus.emit(
        "neuro.patience_changed",
        serde_json::json!({"patience": seconds}),
    ).await;
    Json(serde_json::json!({"patience": seconds}))
}

/// POST /v1/neuro/patience/trigger — manually trigger PATIENCE (simulate timeout)
async fn neuro_trigger_patience(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    {
        let mut ns = state.neuro_state.write().await;
        // Set last_message_time far enough in the past to trigger patience
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();
        ns.last_message_time = now - ns.patience - 5.0;
        ns.new_message = true;
        ns.ai_thinking = true;
    }
    state.signal_bus.emit(
        "neuro.patience_triggered",
        serde_json::json!({"triggered": true}),
    ).await;
    Json(serde_json::json!({"triggered": true}))
}

/// POST /v1/neuro/reset — reset conversation history
async fn neuro_reset(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    {
        let mut ns = state.neuro_state.write().await;
        ns.history.clear();
        ns.human_speaking = false;
        ns.ai_thinking = false;
        ns.ai_speaking = false;
        ns.new_message = false;
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64();
        ns.last_message_time = now;
    }
    Json(serde_json::json!({"reset": true, "history_len": 0}))
}

/// GET /v1/neuro/memories — list stored memories
async fn neuro_list_memories(
    State(state): State<Arc<AppState>>,
    Query(params): Query<HashMap<String, String>>,
) -> Json<serde_json::Value> {
    let limit: usize = params.get("limit")
        .and_then(|v| v.parse().ok())
        .unwrap_or(10)
        .max(1)
        .min(100);
    let ns = state.neuro_state.read().await;
    let count = ns.memories.len();
    let memories: Vec<&MemoryEntry> = ns.memories.iter().rev().take(limit).collect();
    Json(serde_json::json!({
        "memories": memories,
        "count": count,
        "limit": limit,
    }))
}

/// POST /v1/neuro/memory/add — add a new memory
async fn neuro_add_memory(
    State(state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> Json<serde_json::Value> {
    let document = body.get("document")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if document.is_empty() {
        return Json(serde_json::json!({"error": "document is required"}));
    }
    let metadata = body.get("metadata").cloned().unwrap_or(serde_json::json!({}));
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64();

    let entry = {
        let mut ns = state.neuro_state.write().await;
        let id = format!("mem-{}", ns.next_memory_id);
        ns.next_memory_id += 1;
        let entry = MemoryEntry {
            id: id.clone(),
            document,
            metadata,
            created_at: now,
        };
        ns.memories.push(entry.clone());
        // Keep max 500 memories
        if ns.memories.len() > 500 {
            ns.memories.remove(0);
        }
        entry
    };

    // Persist to disk
    append_memory_to_disk(&entry);

    Json(serde_json::json!({
        "id": entry.id,
        "count": 1,
    }))
}

/// GET /v1/neuro/proactive — return recent proactive messages
async fn neuro_proactive(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    let ns = state.neuro_state.read().await;
    Json(serde_json::json!({
        "proactive_messages": ns.proactive_messages,
    }))
}

/// POST /v1/llama/restart — trigger llama-server restart via supervisor
async fn llama_restart(State(state): State<Arc<AppState>>) -> Response {
    let root = project_root();
    let supervisor = root.join("bin").join("hermes-supervisor.py");
    if !supervisor.is_file() {
        return (StatusCode::INTERNAL_SERVER_ERROR, Json(serde_json::json!({
            "status": "error",
            "detail": format!("supervisor not found at {}", supervisor.display()),
        }))).into_response();
    }

    // Find portable python
    let python = root.join("portable-python").join("python.exe");
    let py = if python.is_file() {
        python.to_string_lossy().to_string()
    } else {
        "python".to_string()
    };

    info!("llama/restart: calling supervisor");

    // Shell out to supervisor --restart llm_engine
    let result = tokio::process::Command::new(&py)
        .arg(supervisor.to_string_lossy().as_ref())
        .arg("--restart")
        .arg("llm_engine")
        .current_dir(&root)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .output()
        .await;

    match result {
        Ok(output) => {
            let stdout = String::from_utf8_lossy(&output.stdout).to_string();
            let stderr = String::from_utf8_lossy(&output.stderr).to_string();
            let exit_code = output.status.code().unwrap_or(-1);

            if exit_code != 0 {
                return (StatusCode::INTERNAL_SERVER_ERROR, Json(serde_json::json!({
                    "status": "error",
                    "supervisor_exit": exit_code,
                    "stderr": stderr.lines().rev().take(6).collect::<Vec<_>>(),
                }))).into_response();
            }

            // Wait for llama-server to come back
            let mut llama_ready = false;
            for _ in 0..30 {
                tokio::time::sleep(Duration::from_secs(1)).await;
                if let Some(idx) = state.worker_pool.get_alive_worker().await {
                    let url = { let workers = state.worker_pool.workers.read().await; workers[idx].url.clone() };
                    if let Ok(r) = state.http_client.get(format!("{}/health", url))
                        .timeout(Duration::from_secs(2)).send().await {
                        if r.status().is_success() {
                            llama_ready = true;
                            break;
                        }
                    }
                }
            }

            // Refresh worker pool
            state.worker_pool.refresh(&state.http_client).await;

            // Emit signal
            state.signal_bus.emit("llama.restart", serde_json::json!({
                "status": if llama_ready { "ok" } else { "degraded" },
                "exit_code": exit_code,
            })).await;

            Json(serde_json::json!({
                "status": if llama_ready { "ok" } else { "degraded" },
                "supervisor_exit": exit_code,
                "llama_ready": llama_ready,
                "stdout_tail": stdout.lines().rev().take(6).collect::<Vec<_>>(),
            })).into_response()
        }
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, Json(serde_json::json!({
            "status": "error",
            "detail": format!("supervisor failed: {}", e),
        }))).into_response()
    }
}

/// GET /v1/ikaros/last-session — return most recent memory note
async fn icarus_last_session() -> Json<serde_json::Value> {
    let notes = read_memory_files(1);
    if notes.is_empty() {
        return Json(serde_json::json!({
            "found": false,
            "reason": "no memory files in ikaros memory dir",
        }));
    }
    let latest = &notes[0];
    let date = latest["date"].as_str().unwrap_or("");
    let body = std::fs::read_to_string(icarus_memory_dir().join(format!("{}.md", date)))
        .unwrap_or_default();
    Json(serde_json::json!({
        "found": true,
        "date": latest["date"],
        "path": latest["path"],
        "body": body,
    }))
}

/// GET /v1/ikaros/memories — return memory index
async fn icarus_memories(Query(params): Query<std::collections::HashMap<String, String>>) -> Json<serde_json::Value> {
    let days: usize = params.get("days")
        .and_then(|s| s.parse().ok())
        .unwrap_or(7)
        .max(1).min(90);
    let notes = read_memory_files(days);
    Json(serde_json::json!({
        "count": notes.len(),
        "memory_dir": icarus_memory_dir().to_string_lossy(),
        "notes": notes,
    }))
}

/// GET /v1/ikaros/awake-briefing — return briefing
async fn icarus_awake_briefing() -> Json<serde_json::Value> {
    let notes = read_memory_files(3);
    let last = notes.first();
    let last_body = last.and_then(|n| {
        let date = n["date"].as_str()?;
        std::fs::read_to_string(icarus_memory_dir().join(format!("{}.md", date))).ok()
    }).unwrap_or_default();

    // Read recent heartbeat events
    let heartbeat_path = project_root().join("data").join("logs").join("ikaros-heartbeat.jsonl");
    let recent_events: Vec<serde_json::Value> = if heartbeat_path.is_file() {
        std::fs::read_to_string(&heartbeat_path)
            .unwrap_or_default()
            .lines()
            .rev()
            .take(5)
            .filter_map(|line| {
                let v: serde_json::Value = serde_json::from_str(line).ok()?;
                Some(serde_json::json!({"event": v.get("event"), "ts": v.get("ts")}))
            })
            .collect()
    } else {
        Vec::new()
    };

    Json(serde_json::json!({
        "last_session": {
            "date": last.map(|n| &n["date"]),
            "headline": last.map(|n| &n["headline"]),
            "body_excerpt": &last_body[..last_body.len().min(2000)],
        },
        "recent_three_dates": notes.iter().map(|n| &n["date"]).collect::<Vec<_>>(),
        "recent_three_headlines": notes.iter().map(|n| &n["headline"]).collect::<Vec<_>>(),
        "heartbeat_recent": recent_events,
        "current_ts": chrono::Utc::now().timestamp(),
        "memory_dir": icarus_memory_dir().to_string_lossy(),
    }))
}

// ---------------------------------------------------------------------------
// Ikaros session context — active-session, tail, resume-context
// (for UI restart conversation recovery)
// ---------------------------------------------------------------------------

/// Helper: execute a Python inline script against the webui SQLite DB.
/// Injects `db_path` as a Posix-style path ready for `sqlite3.connect()`.
/// Returns stdout on success, None on failure.
async fn _exec_webui_sql(script: &str) -> Option<String> {
    let db = webui_db_path();
    if !db.is_file() {
        warn!("webui db not found at {}", db.display());
        return None;
    }
    let py = find_portable_python();
    let escaped_db = db.to_string_lossy().replace('\\', "/");
    let full_script = format!(
        "import sqlite3,json,time;\n\
         c=sqlite3.connect('file:{}?mode=ro', uri=True, timeout=3);\n\
         c.row_factory=sqlite3.Row;\n\
         {}\n\
         c.close()",
        escaped_db, script,
    );
    let output = tokio::process::Command::new(&py)
        .arg("-c")
        .arg(&full_script)
        .current_dir(project_root())
        .output().await;
    match output {
        Ok(o) if o.status.success() => {
            let stdout = String::from_utf8_lossy(&o.stdout).to_string();
            Some(stdout)
        }
        Ok(o) => {
            let stderr = String::from_utf8_lossy(&o.stderr).to_string();
            warn!("webui SQL python failed: exit={:?} stderr={}", o.status.code(), stderr.lines().last().unwrap_or(""));
            None
        }
        Err(e) => {
            warn!("webui SQL python error: {}", e);
            None
        }
    }
}

/// GET /v1/ikaros/active-session — find the most recent un-ended session
async fn icarus_active_session(
    Query(params): Query<std::collections::HashMap<String, String>>,
) -> Json<serde_json::Value> {
    let max_age_secs: i64 = params.get("max_age_seconds")
        .and_then(|s| s.parse().ok())
        .unwrap_or(86400 * 3); // default 3 days

    let script = format!(
        concat!(
            "cutoff=int(time.time())-{};\n",
            "cur=c.execute('''\n",
            "    SELECT id,title,source,agent,profile,model,provider,\n",
            "           message_count,started_at,last_active,workspace\n",
            "    FROM sessions\n",
            "    WHERE ended_at IS NULL AND last_active >= ?\n",
            "    ORDER BY last_active DESC LIMIT 5\n",
            "''', (cutoff,));\n",
            "sessions=[dict(r) for r in cur.fetchall()];\n",
            "if sessions:\n",
            "    s=sessions[0];\n",
            "    cur.execute('''\n",
            "        SELECT role,content,timestamp\n",
            "        FROM messages WHERE session_id=?\n",
            "        ORDER BY timestamp DESC LIMIT 3\n",
            "    ''', (s['id'],));\n",
            "    msgs=[dict(r) for r in cur.fetchall()];\n",
            "    msgs.reverse();\n",
            "    s['last_messages']=msgs;\n",
            "print(json.dumps({{'found': bool(sessions),\n",
            "                   'sessions': sessions}}))",
        ),
        max_age_secs,
    );

    match _exec_webui_sql(&script).await {
        Some(output) => {
            match serde_json::from_str::<serde_json::Value>(&output) {
                Ok(val) => Json(val),
                Err(_) => Json(serde_json::json!({"found": false, "reason": "parse error"})),
            }
        }
        None => Json(serde_json::json!({"found": false, "reason": "webui db not accessible"})),
    }
}

/// GET /v1/ikaros/session/{id}/tail — get last N messages of a session
async fn icarus_session_tail(
    axum::extract::Path(session_id): axum::extract::Path<String>,
    Query(params): Query<std::collections::HashMap<String, String>>,
) -> Json<serde_json::Value> {
    let limit: usize = params.get("limit")
        .and_then(|s| s.parse().ok())
        .unwrap_or(10)
        .max(1).min(50);

    let escaped_sid = session_id.replace('\\', "\\\\").replace('\'', "\\'");

    let script = format!(
        concat!(
            "cur=c.execute('''\n",
            "    SELECT role,content,timestamp,tool_name\n",
            "    FROM messages WHERE session_id=?\n",
            "    ORDER BY timestamp DESC LIMIT {}\n",
            "''', (r'{}',));\n",
            "msgs=[dict(r) for r in cur.fetchall()];\n",
            "msgs.reverse();\n",
            "print(json.dumps({{'found': True,\n",
            "                   'session_id': r'{}',\n",
            "                   'messages': msgs}}))",
        ),
        limit,
        escaped_sid,
        escaped_sid,
    );

    match _exec_webui_sql(&script).await {
        Some(output) => {
            match serde_json::from_str::<serde_json::Value>(&output) {
                Ok(val) => Json(val),
                Err(_) => Json(serde_json::json!({"found": false, "reason": "parse error"})),
            }
        }
        None => Json(serde_json::json!({"found": false, "reason": "webui db not accessible"})),
    }
}

/// POST /v1/ikaros/session/{id}/resume-context — compose system prompt for resume
async fn icarus_resume_context(
    axum::extract::Path(session_id): axum::extract::Path<String>,
) -> Json<serde_json::Value> {
    let escaped_sid = session_id.replace('\\', "\\\\").replace('\'', "\\'");
    
    let script = format!(
        concat!(
            "sid=r'{}';\n",
            "cur=c.execute('SELECT title,message_count,last_active FROM sessions WHERE id=?', (sid,));\n",
            "sess=cur.fetchone();\n",
            "if not sess:\n",
            "    print(json.dumps({{'found': False, 'reason': 'session not found'}}));\n",
            "else:\n",
            "    title,msg_count,last_active=sess;\n",
            "    cur.execute('''\n",
            "        SELECT role,content,tool_name\n",
            "        FROM messages\n",
            "        WHERE session_id=? AND role IN (\"user\",\"assistant\")\n",
            "          AND content IS NOT NULL AND content != \"\"\n",
            "        ORDER BY timestamp DESC LIMIT 8\n",
            "    ''', (sid,));\n",
            "    rows=[dict(r) for r in cur.fetchall()];\n",
            "    rows.reverse();\n",
            "    lines=[];\n",
            "    if title:\n",
            "        lines.append(f\"# Previous session: {{title}} ({{msg_count}} messages)\");\n",
            "    else:\n",
            "        lines.append(f\"# Previous session ({{msg_count}} messages)\");\n",
            "    if last_active:\n",
            "        from datetime import datetime;\n",
            "        dt=datetime.fromtimestamp(int(last_active));\n",
            "        lines.append(f\"# Last active: {{dt.isoformat()}}\");\n",
            "    lines.append(\"\");\n",
            "    lines.append(\"The user wants to continue this conversation. Recap where you left off, then ask what they want to do next.\");\n",
            "    lines.append(\"\");\n",
            "    lines.append(\"## Last messages\");\n",
            "    for r in rows:\n",
            "        role=r['role'];\n",
            "        excerpt=(r['content'] or \"\")[:400].replace(\"\\n\",\" \");\n",
            "        lines.append(f\"- **{{role}}**: {{excerpt}}\");\n",
            "    system_prompt=chr(10).join(lines);\n",
            "    summary=\"\";\n",
            "    for r in reversed(rows):\n",
            "        if r['role']=='assistant' and r['content']:\n",
            "            summary=r['content'][:300];\n",
            "            break;\n",
            "    print(json.dumps({{'found': True,\n",
            "                       'session_id': sid,\n",
            "                       'system_prompt': system_prompt,\n",
            "                       'summary': summary,\n",
            "                       'message_count': msg_count}}))",
        ),
        escaped_sid,
    );

    match _exec_webui_sql(&script).await {
        Some(output) => {
            match serde_json::from_str::<serde_json::Value>(&output) {
                Ok(val) => Json(val),
                Err(_) => Json(serde_json::json!({"found": false, "reason": "parse error"})),
            }
        }
        None => Json(serde_json::json!({"found": false, "reason": "webui db not accessible"})),
    }
}

// ---------------------------------------------------------------------------
// Signals / Modules endpoints — Phase 2 (real implementations)
// ---------------------------------------------------------------------------

/// GET /v1/signals — signals snapshot (aggregated telemetry)
async fn signals_snapshot(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    let stats = state.request_log.stats().await;
    let recent = state.signal_bus.recent(None, 20).await;
    let workers = state.worker_pool.workers.read().await;
    let alive_count = workers.iter().filter(|w| w.alive).count();

    Json(serde_json::json!({
        "ts": chrono::Utc::now().timestamp(),
        "module": "bridge-rs",
        "pid": std::process::id(),
        "uptime_sec": state.start_time.elapsed().as_secs(),
        "upstream": {
            "llama_server": {
                "workers": workers.iter().map(|w| serde_json::json!({
                    "url": w.url,
                    "alive": w.alive,
                    "models_count": w.models.len(),
                })).collect::<Vec<_>>(),
                "alive_count": alive_count,
            },
        },
        "request_stats": stats,
        "recent_signals": recent,
    }))
}

/// GET /v1/signals/recent — recent signals
async fn signals_recent(
    State(state): State<Arc<AppState>>,
    Query(params): Query<std::collections::HashMap<String, String>>,
) -> Json<serde_json::Value> {
    let topic = params.get("topic").map(|s| s.as_str());
    let limit: usize = params.get("limit")
        .and_then(|s| s.parse().ok())
        .unwrap_or(50)
        .max(1).min(500);
    let items = state.signal_bus.recent(topic, limit).await;
    Json(serde_json::json!({
        "count": items.len(),
        "items": items,
        "topic": topic,
    }))
}

/// GET /v1/signals/stats — request log aggregates
async fn signals_stats(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    Json(state.request_log.stats().await)
}

/// POST /v1/signals/emit — publish a signal
async fn signals_emit(
    State(state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let topic = match body.get("topic").and_then(|v| v.as_str()) {
        Some(t) => t,
        None => {
            return (StatusCode::BAD_REQUEST, Json(serde_json::json!({
                "error": "`topic` field is required (string)",
            }))).into_response();
        }
    };
    let payload = body.get("payload").cloned().unwrap_or(serde_json::json!({}));
    let env = state.signal_bus.emit(topic, payload).await;
    Json(serde_json::json!({"ok": true, "envelope": env})).into_response()
}

/// GET /v1/modules — scan modules/*/module.json + TCP probe
async fn list_modules(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    // Check cache (5s TTL)
    {
        let cache = state.modules_cache.read().await;
        if cache.0.elapsed() < Duration::from_secs(5) && !cache.1.is_empty() {
            let up = cache.1.iter().filter(|m| m.get("alive").and_then(|v| v.as_bool()).unwrap_or(false)).count();
            let down = cache.1.iter().filter(|m| m.get("port").and_then(|v| v.as_u64()).is_some() && !m.get("alive").and_then(|v| v.as_bool()).unwrap_or(false)).count();
            return Json(serde_json::json!({
                "ts": chrono::Utc::now().timestamp(),
                "source": "bridge-rs",
                "summary": {"total": cache.1.len(), "up": up, "down": down},
                "modules": cache.1,
            }));
        }
    }

    let modules = scan_modules(&state.http_client).await;
    let up = modules.iter().filter(|m| m.get("alive").and_then(|v| v.as_bool()).unwrap_or(false)).count();
    let down = modules.iter().filter(|m| m.get("port").and_then(|v| v.as_u64()).is_some() && !m.get("alive").and_then(|v| v.as_bool()).unwrap_or(false)).count();

    // Update cache
    {
        let mut cache = state.modules_cache.write().await;
        *cache = (Instant::now(), modules.clone());
    }

    Json(serde_json::json!({
        "ts": chrono::Utc::now().timestamp(),
        "source": "bridge-rs",
        "summary": {"total": modules.len(), "up": up, "down": down},
        "modules": modules,
    }))
}

/// GET /v1/inspect/{name} — inspect a specific module
async fn inspect_module(
    State(_state): State<Arc<AppState>>,
    axum::extract::Path(name): axum::extract::Path<String>,
) -> Response {
    // Validate name
    if !name.chars().all(|c| c.is_alphanumeric() || c == '_' || c == '-') {
        return (StatusCode::BAD_REQUEST, Json(serde_json::json!({
            "error": format!("invalid module name: {}", name),
        }))).into_response();
    }

    let root = project_root();
    let json_path = root.join("modules").join(&name).join("module.json");
    if !json_path.is_file() {
        return (StatusCode::NOT_FOUND, Json(serde_json::json!({
            "error": format!("module not found: {}", name),
        }))).into_response();
    }

    let data = match std::fs::read_to_string(&json_path) {
        Ok(s) => match serde_json::from_str::<serde_json::Value>(&s) {
            Ok(v) => v,
            Err(e) => {
                return (StatusCode::INTERNAL_SERVER_ERROR, Json(serde_json::json!({
                    "error": format!("bad module.json: {}", e),
                }))).into_response();
            }
        },
        Err(e) => {
            return (StatusCode::INTERNAL_SERVER_ERROR, Json(serde_json::json!({
                "error": format!("read error: {}", e),
            }))).into_response();
        }
    };

    let net = data.get("network").cloned().unwrap_or(serde_json::json!({}));
    let port = net.get("port").and_then(|v| v.as_u64()).map(|v| v as u16);
    let host = net.get("host").and_then(|v| v.as_str()).unwrap_or("127.0.0.1");
    let alive = if let Some(p) = port { tcp_alive(host, p).await } else { false };
    let health_ep = net.get("health_endpoint").and_then(|v| v.as_str()).unwrap_or("/health");

    Json(serde_json::json!({
        "ts": chrono::Utc::now().timestamp(),
        "name": name,
        "module_json": data,
        "probe": {
            "port": port,
            "alive": alive,
            "health_url": port.map(|p| format!("http://{}:{}{}", host, p, health_ep)),
            "probe_kind": if port.is_some() { "tcp" } else { "none" },
        },
    })).into_response()
}

/// GET /api/bridge/health — lightweight health for WebUI heartbeat
async fn bridge_health(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    let workers = state.worker_pool.workers.read().await;
    let alive_count = workers.iter().filter(|w| w.alive).count();
    Json(serde_json::json!({
        "status": if alive_count > 0 { "ok" } else { "degraded" },
        "bridge": "rust",
        "version": env!("CARGO_PKG_VERSION"),
        "uptime_sec": state.start_time.elapsed().as_secs(),
        "workers_alive": alive_count,
        "workers_total": workers.len(),
        "requests_total": state.request_counter.load(Ordering::Relaxed),
    }))
}

/// GET /api/bridge/health/snapshot — full health snapshot
async fn health_snapshot(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    let stats = state.request_log.stats().await;
    let workers = state.worker_pool.health_summary().await;
    Json(serde_json::json!({
        "bridge": "rust",
        "version": env!("CARGO_PKG_VERSION"),
        "uptime_sec": state.start_time.elapsed().as_secs(),
        "pid": std::process::id(),
        "workers": workers,
        "request_stats": stats,
    }))
}

// ---------------------------------------------------------------------------
// Model management endpoints (proxy to llama-server)
// ---------------------------------------------------------------------------

/// Normalize model id: strip .gguf suffix if present
fn normalize_model_id(model: &str) -> &str {
    model.strip_suffix(".gguf").unwrap_or(model)
}

/// POST /v1/models/load — proxy to llama-server /models/load with fallback
async fn load_model(
    State(state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let model = match body.get("model").and_then(|v| v.as_str()) {
        Some(m) if !m.is_empty() => m,
        _ => {
            return (StatusCode::BAD_REQUEST, Json(serde_json::json!({
                "error": "`model` field is required",
            }))).into_response();
        }
    };

    // Build candidate list: original + fallback (alias <-> .gguf)
    let base = normalize_model_id(model);
    let mut candidates = vec![model.to_string()];
    if base != model {
        candidates.push(base.to_string());
    } else {
        candidates.push(format!("{}.gguf", model));
    }

    // Find alive worker
    let worker_idx = match state.worker_pool.get_alive_worker().await {
        Some(idx) => idx,
        None => {
            return (StatusCode::SERVICE_UNAVAILABLE, Json(serde_json::json!({
                "error": "no alive llama-server worker",
            }))).into_response();
        }
    };
    let worker_url = { state.worker_pool.workers.read().await[worker_idx].url.clone() };

    // Try each candidate
    for (i, candidate) in candidates.iter().enumerate() {
        let url = format!("{}/models/load", worker_url);
        match state.http_client.post(&url)
            .json(&serde_json::json!({"model": candidate}))
            .timeout(Duration::from_secs(30))
            .send().await
        {
            Ok(resp) => {
                let status = resp.status();
                if status.as_u16() == 404 && i + 1 < candidates.len() {
                    warn!("model-load: '{}' returned 404, trying fallback '{}'", candidate, candidates[i + 1]);
                    continue;
                }
                let body_text = resp.text().await.unwrap_or_default();
                let json_body: serde_json::Value = serde_json::from_str(&body_text).unwrap_or(serde_json::json!({"raw": body_text}));

                // Emit signal
                state.signal_bus.emit("model.loaded", serde_json::json!({
                    "model": candidate, "status": status.as_u16(), "fallback_index": i,
                })).await;

                // Refresh worker pool after model load
                state.worker_pool.refresh(&state.http_client).await;

                return (StatusCode::from_u16(status.as_u16()).unwrap_or(StatusCode::OK), Json(json_body)).into_response();
            }
            Err(e) => {
                return (StatusCode::BAD_GATEWAY, Json(serde_json::json!({
                    "error": format!("llama-server unreachable: {}", e),
                }))).into_response();
            }
        }
    }

    (StatusCode::NOT_FOUND, Json(serde_json::json!({
        "error": format!("model '{}' not found (tried: {})", model, candidates.join(", ")),
    }))).into_response()
}

/// POST /v1/models/swap — alias of /v1/models/load
async fn swap_model(
    state: State<Arc<AppState>>,
    body: Json<serde_json::Value>,
) -> Response {
    load_model(state, body).await
}

/// GET /v1/models/status — report loaded models + VRAM
async fn models_status(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    let worker_idx = state.worker_pool.get_alive_worker().await;
    if worker_idx.is_none() {
        return Json(serde_json::json!({
            "loaded": [], "available": [], "vram": null, "source": "no-worker",
        }));
    }
    let worker_url = { state.worker_pool.workers.read().await[worker_idx.unwrap()].url.clone() };

    let mut loaded = Vec::new();
    let mut available = Vec::new();
    let mut vram: Option<serde_json::Value> = None;
    let mut source = "unknown".to_string();

    // Probe /props
    if let Ok(resp) = state.http_client.get(format!("{}/props", worker_url))
        .timeout(Duration::from_secs(5)).send().await {
        if resp.status().is_success() {
            if let Ok(props) = resp.json::<serde_json::Value>().await {
                let resident = props.get("model_alias")
                    .or_else(|| props.get("model_path"))
                    .or_else(|| props.get("model"))
                    .and_then(|v| v.as_str());
                if let Some(id) = resident {
                    loaded.push(serde_json::json!({"id": id, "source": "props.model_alias"}));
                }
                vram = props.get("vram_total_size").cloned()
                    .or_else(|| props.get("vram_used_size").cloned());
                source = "llama-server-props".to_string();
            }
        }
    }

    // Probe /v1/models
    if let Ok(resp) = state.http_client.get(format!("{}/v1/models", worker_url))
        .timeout(Duration::from_secs(5)).send().await {
        if resp.status().is_success() {
            if let Ok(json) = resp.json::<serde_json::Value>().await {
                if let Some(data) = json.get("data").and_then(|d| d.as_array()) {
                    available = data.iter().map(|m| serde_json::json!({
                        "id": m.get("id").and_then(|v| v.as_str()).unwrap_or(""),
                        "owned_by": m.get("owned_by").and_then(|v| v.as_str()).unwrap_or("llama-cpp"),
                    })).collect();
                }
            }
        }
    }

    Json(serde_json::json!({
        "loaded": loaded,
        "available": available,
        "vram": vram,
        "source": source,
    }))
}

/// POST /v1/models/evict — evict model from llama-server cache
async fn evict_model(
    State(state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let worker_idx = match state.worker_pool.get_alive_worker().await {
        Some(idx) => idx,
        None => {
            return (StatusCode::SERVICE_UNAVAILABLE, Json(serde_json::json!({
                "error": "no alive llama-server worker",
            }))).into_response();
        }
    };
    let worker_url = { state.worker_pool.workers.read().await[worker_idx].url.clone() };

    // Get model from body or detect resident from /props
    let model = body.get("model").and_then(|v| v.as_str()).map(String::from);
    let model = if let Some(m) = model {
        m
    } else {
        // Detect resident model from /props
        match state.http_client.get(format!("{}/props", worker_url))
            .timeout(Duration::from_secs(5)).send().await {
            Ok(resp) if resp.status().is_success() => {
                if let Ok(props) = resp.json::<serde_json::Value>().await {
                    let resident = props.get("model_alias")
                        .or_else(|| props.get("model_path"))
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    if resident.is_empty() || resident == "llama-server" || resident == "none" {
                        return Json(serde_json::json!({
                            "status": "noop",
                            "reason": "no resident model — nothing to evict",
                        })).into_response();
                    }
                    resident.to_string()
                } else {
                    return Json(serde_json::json!({"status": "noop", "reason": "cannot parse /props"})).into_response();
                }
            }
            _ => {
                return Json(serde_json::json!({"status": "noop", "reason": "llama-server unreachable"})).into_response();
            }
        }
    };

    // Reload-resident trick: POST /models/load forces slot re-evaluation
    let url = format!("{}/models/load", worker_url);
    match state.http_client.post(&url)
        .json(&serde_json::json!({"model": model}))
        .timeout(Duration::from_secs(30))
        .send().await {
        Ok(resp) => {
            let status = resp.status();
            let body_text = resp.text().await.unwrap_or_default();
            state.signal_bus.emit("model.evicted", serde_json::json!({
                "model": model, "method": "reload-resident",
            })).await;
            (StatusCode::from_u16(status.as_u16()).unwrap_or(StatusCode::OK),
             Json(serde_json::json!({"status": "ok", "triggered_reload": model, "upstream": body_text}))
            ).into_response()
        }
        Err(e) => (StatusCode::BAD_GATEWAY, Json(serde_json::json!({
            "error": format!("llama-server unreachable: {}", e),
        }))).into_response()
    }
}

// ---------------------------------------------------------------------------
// API endpoints (chat sessions + agent run)
// ---------------------------------------------------------------------------

/// GET /api/chat/sessions — list sessions from state.db (SQLite)
async fn list_sessions(
    Query(params): Query<std::collections::HashMap<String, String>>,
) -> Json<serde_json::Value> {
    let limit: usize = params.get("limit").and_then(|s| s.parse().ok()).unwrap_or(50).max(1).min(200);
    let root = project_root();
    let db_path = root.join("data").join("hermes-agent").join("state.db");

    if !db_path.is_file() {
        return Json(serde_json::json!({
            "object": "list", "data": [], "count": 0,
            "warning": format!("state.db not found at {}", db_path.display()),
        }));
    }

    // Use portable-python to query SQLite (avoid adding rusqlite dependency)
    let python = root.join("portable-python").join("python.exe");
    let py = if python.is_file() { python.to_string_lossy().to_string() } else { "python".to_string() };

    let script = format!(
        "import sqlite3,json; c=sqlite3.connect(r'{}'); c.row_factory=sqlite3.Row; \
         rows=[dict(r) for r in c.execute('SELECT id,title,started_at,model,source FROM sessions ORDER BY started_at DESC LIMIT {}').fetchall()]; \
         print(json.dumps(rows))",
        db_path.to_string_lossy().replace('\\', "\\\\"),
        limit,
    );

    match tokio::process::Command::new(&py)
        .arg("-c").arg(&script)
        .output().await {
        Ok(output) if output.status.success() => {
            let stdout = String::from_utf8_lossy(&output.stdout);
            match serde_json::from_str::<Vec<serde_json::Value>>(&stdout) {
                Ok(sessions) => Json(serde_json::json!({
                    "object": "list", "data": sessions, "count": sessions.len(),
                })),
                Err(_) => Json(serde_json::json!({"object": "list", "data": [], "count": 0, "warning": "parse error"})),
            }
        }
        _ => Json(serde_json::json!({"object": "list", "data": [], "count": 0, "warning": "python query failed"})),
    }
}

/// POST /api/agent/run — run AIAgent for a single message
async fn agent_run(
    State(state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let message = match body.get("message").and_then(|v| v.as_str()) {
        Some(m) if !m.is_empty() => m,
        _ => {
            return (StatusCode::BAD_REQUEST, Json(serde_json::json!({
                "error": "`message` field is required",
            }))).into_response();
        }
    };
    if message.len() > 32768 {
        return (StatusCode::BAD_REQUEST, Json(serde_json::json!({
            "error": "`message` exceeds 32K char limit",
        }))).into_response();
    }

    let root = project_root();
    let python = root.join("portable-python").join("python.exe");
    let py = if python.is_file() { python.to_string_lossy().to_string() } else { "python".to_string() };
    let _agent_script = root.join("hermes-agent").join("run_agent.py");

    let model = body.get("model").and_then(|v| v.as_str()).unwrap_or("");
    let session_id = body.get("session_id").and_then(|v| v.as_str()).unwrap_or("");
    let max_iter = body.get("max_iterations").and_then(|v| v.as_u64()).unwrap_or(1);

    // Shell out to Python AIAgent
    let script = format!(
        "import sys; sys.path.insert(0, r'{}'); \
         from run_agent import AIAgent; \
         a = AIAgent(model=r'{}', session_id=r'{}' or None, quiet_mode=True, \
                     enabled_toolsets=[], max_iterations={}); \
         print(a.chat(r'''{}'''))",
        root.join("hermes-agent").to_string_lossy().replace('\\', "\\\\"),
        model, session_id, max_iter,
        message.replace('\'', "\\'").replace('\n', "\\n"),
    );

    state.signal_bus.emit("agent.run", serde_json::json!({"model": model})).await;

    match tokio::process::Command::new(&py)
        .arg("-c").arg(&script)
        .current_dir(&root)
        .output().await {
        Ok(output) if output.status.success() => {
            let response = String::from_utf8_lossy(&output.stdout).to_string();
            Json(serde_json::json!({
                "response": response,
                "session_id": session_id,
                "model": model,
            })).into_response()
        }
        Ok(output) => {
            let stderr = String::from_utf8_lossy(&output.stderr).to_string();
            (StatusCode::INTERNAL_SERVER_ERROR, Json(serde_json::json!({
                "error": format!("AIAgent failed: {}", stderr.lines().last().unwrap_or("unknown")),
            }))).into_response()
        }
        Err(e) => (StatusCode::SERVICE_UNAVAILABLE, Json(serde_json::json!({
            "error": format!("cannot spawn python: {}", e),
        }))).into_response()
    }
}

// ---------------------------------------------------------------------------
// Catch-all proxy for other /v1/* paths
// ---------------------------------------------------------------------------

/// Proxy any other /v1/* request to the first alive worker
async fn proxy_v1(
    State(state): State<Arc<AppState>>,
    method: axum::http::Method,
    uri: axum::http::Uri,
    headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    let worker_idx = match state.worker_pool.get_alive_worker().await {
        Some(idx) => idx,
        None => {
            return (StatusCode::SERVICE_UNAVAILABLE, Json(serde_json::json!({"error": "no alive worker"}))).into_response();
        }
    };

    let worker_url = {
        let workers = state.worker_pool.workers.read().await;
        workers[worker_idx].url.clone()
    };

    let path = uri.path();
    let query = uri.query().map(|q| format!("?{}", q)).unwrap_or_default();
    let url = format!("{}{}{}", worker_url, path, query);

    let mut req_builder = state.http_client.request(method, &url);

    for (name, value) in headers.iter() {
        if name != "host" && name != "content-length" {
            req_builder = req_builder.header(name, value);
        }
    }

    if !body.is_empty() {
        req_builder = req_builder.body(body.to_vec());
    }

    match req_builder.send().await {
        Ok(resp) => {
            let status = resp.status();
            let headers = resp.headers().clone();
            match resp.bytes().await {
                Ok(resp_body) => {
                    let mut response = Response::new(axum::body::Body::from(resp_body.to_vec()));
                    *response.status_mut() = StatusCode::from_u16(status.as_u16()).unwrap_or(StatusCode::OK);
                    for (name, value) in headers.iter() {
                        if let (Ok(n), Ok(v)) = (
                            axum::http::HeaderName::from_bytes(name.as_str().as_bytes()),
                            axum::http::HeaderValue::from_bytes(value.as_bytes()),
                        ) {
                            response.headers_mut().insert(n, v);
                        }
                    }
                    response
                }
                Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
            }
        }
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({"error": format!("upstream error: {}", e)})),
        ).into_response(),
    }
}

// ---------------------------------------------------------------------------
// Server startup
// ---------------------------------------------------------------------------

#[tokio::main]
async fn main() {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "hermes_bridge_rs=info,tower_http=info".into()),
        )
        .with_target(false)
        .with_thread_ids(false)
        .init();

    let state = Arc::new(AppState::new());

    // Start background health monitor
    state.start_health_monitor();

    // Initial health check (wait a moment for first refresh)
    {
        let pool = state.worker_pool.clone();
        let client = state.http_client.clone();
        pool.refresh(&client).await;
    }

    // Build router
    let app = Router::new()
        // Health checks
        .route("/health", get(health))
        .route("/api/bridge/health", get(bridge_health))
        .route("/api/bridge/health/snapshot", get(health_snapshot))
        // OpenAI-compatible endpoints
        .route("/v1/chat/completions", post(chat_completions))
        .route("/v1/chat/completions/sse", post(chat_completions_sse))
        .route("/v1/models", get(list_models))
        .route("/v1/models/load", post(load_model))
        .route("/v1/models/swap", post(swap_model))
        .route("/v1/models/status", get(models_status))
        .route("/v1/models/evict", post(evict_model))
        // Voice WebSocket
        .route("/v1/voice/ws", get(voice_ws))
        // Neuro endpoints
        .route("/v1/neuro/status", get(neuro_status))
        .route("/v1/neuro/patience", post(neuro_set_patience))
        .route("/v1/neuro/patience/trigger", post(neuro_trigger_patience))
        .route("/v1/neuro/reset", post(neuro_reset))
        .route("/v1/neuro/memories", get(neuro_list_memories))
        .route("/v1/neuro/memory/add", post(neuro_add_memory))
        .route("/v1/neuro/proactive", get(neuro_proactive))
        // Ikaros endpoints
        .route("/v1/liveness", get(liveness))
        .route("/v1/llama/restart", post(llama_restart))
        .route("/v1/ikaros/last-session", get(icarus_last_session))
        .route("/v1/ikaros/memories", get(icarus_memories))
        .route("/v1/ikaros/awake-briefing", get(icarus_awake_briefing))
        .route("/v1/ikaros/active-session", get(icarus_active_session))
        .route("/v1/ikaros/session/{id}/tail", get(icarus_session_tail))
        .route("/v1/ikaros/session/{id}/resume-context", post(icarus_resume_context))
        // Signals / Modules
        .route("/v1/signals", get(signals_snapshot))
        .route("/v1/signals/recent", get(signals_recent))
        .route("/v1/signals/stats", get(signals_stats))
        .route("/v1/signals/emit", post(signals_emit))
        .route("/v1/modules", get(list_modules))
        .route("/v1/inspect/{name}", get(inspect_module))
        // API endpoints
        .route("/api/chat/sessions", get(list_sessions))
        .route("/api/agent/run", post(agent_run))
        // Catch-all for other /v1/* paths
        .fallback(proxy_v1)
        .with_state(state);

    let addr = SocketAddr::from(([127, 0, 0, 1], BRIDGE_PORT));

    info!("hermes-bridge-rs v{} starting", env!("CARGO_PKG_VERSION"));
    info!("project root: {}", project_root().display());
    info!("ikaros memory dir: {}", icarus_memory_dir().display());
    info!("listening on {}", addr);
    info!("upstream workers: {:?}", get_upstream_urls());
    info!("max concurrent chat: {}", MAX_CONCURRENT_CHAT);

    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .expect("failed to bind port");

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .expect("server error");

    info!("bridge shutdown complete");
}

async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("failed to install Ctrl+C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("failed to install signal handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => { info!("received Ctrl+C"); }
        _ = terminate => { info!("received SIGTERM"); }
    }
}
