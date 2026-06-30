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
pub mod telemetry;
pub mod voice_recognizer;
pub mod cogno_layer;
pub mod sessions;
pub mod soul_loader;
pub mod supervisor;
pub mod access;
pub mod patience;  // PATIENCE heartbeat (ported from Neuro)

use telemetry::{SignalBus, RequestLog};

use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        Path, Query, State,
    },
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{delete, get, post},
    Json, Router,
};
use sessions::SessionStore;
use futures::{StreamExt, TryStreamExt};
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
use tokio::sync::{broadcast, watch, RwLock, Semaphore};
use voice_recognizer::VoiceRecognizer;
use edge_tts_rust::{Boundary, EdgeTtsClient, SpeakOptions, SynthesisEvent};
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
// Signal Bus + Request Log — now in telemetry.rs module
// ---------------------------------------------------------------------------
// Re-exported via: use telemetry::{SignalBus, SignalEnvelope, RequestLog, RequestEntry};

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
// Upstream types — local llama-server or cloud API
// ---------------------------------------------------------------------------

#[derive(Clone, Debug)]
enum AuthFormat {
    Bearer,      // OpenAI / DeepSeek: Authorization: Bearer <key>
    XApiKey,     // Anthropic protocol: x-api-key: <key>
}

/// API protocol — determines request/response format
#[derive(Clone, Debug)]
enum ApiProtocol {
    OpenAI,      // /v1/chat/completions — OpenAI-compatible
    Anthropic,   // /v1/messages — Anthropic Messages API compatible
}

#[derive(Clone, Debug)]
enum UpstreamType {
    Local,                              // llama-server, no auth needed
    Cloud {
        base_url: String,               // e.g. "https://api.deepseek.com/v1"
        api_key: String,
        provider: String,               // "minimax-cn" / "deepseek" / "openai"
        auth_format: AuthFormat,
        protocol: ApiProtocol,
        models: Vec<String>,            // known model names for this provider
    },
}

// ---------------------------------------------------------------------------

#[derive(Clone, Debug)]
struct Worker {
    url: String,
    alive: bool,
    models: Vec<String>,
    last_check: Instant,
    request_count: u64,
    upstream_type: UpstreamType,
}

impl Worker {
    fn new(url: String) -> Self {
        Self {
            url,
            alive: false,
            models: Vec::new(),
            last_check: Instant::now(),
            request_count: 0,
            upstream_type: UpstreamType::Local,
        }
    }

    fn new_cloud(upstream: UpstreamType) -> Self {
        let (url, models) = match &upstream {
            UpstreamType::Cloud { base_url, models, .. } => (base_url.clone(), models.clone()),
            _ => (String::new(), Vec::new()),
        };
        Self {
            url,
            alive: true, // cloud workers always "alive"
            models,
            last_check: Instant::now(),
            request_count: 0,
            upstream_type: upstream,
        }
    }

    fn is_cloud(&self) -> bool {
        matches!(&self.upstream_type, UpstreamType::Cloud { .. })
    }
}

// ---------------------------------------------------------------------------
// Cloud upstream configuration — aligned with webui (reads auth.json + .env)
// ---------------------------------------------------------------------------

/// HERMES_HOME: where auth.json and .env live
fn hermes_home() -> std::path::PathBuf {
    std::env::var("HERMES_HOME")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| project_root().join("data").join("hermes-agent"))
}

/// Parse a .env file into a key→value map
fn load_env_file(path: &std::path::Path) -> std::collections::HashMap<String, String> {
    let mut envs = std::collections::HashMap::new();
    if let Ok(content) = std::fs::read_to_string(path) {
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') { continue; }
            if let Some(eq_pos) = line.find('=') {
                let key = line[..eq_pos].trim().to_string();
                let val = line[eq_pos+1..].trim().to_string();
                if !val.is_empty() && !val.starts_with('#') {
                    envs.insert(key, val);
                }
            }
        }
    }
    envs
}

/// Get env var: priority .env file > process.env
/// .env is the single source of truth (maintained by 哥哥), process.env may have stale test values
fn get_env_var(key: &str, env_map: &std::collections::HashMap<String, String>) -> Option<String> {
    // .env file first (哥哥维护的单一来源)
    if let Some(v) = env_map.get(key) {
        if !v.is_empty() && !v.starts_with('#') {
            return Some(v.clone());
        }
    }
    // fallback to process.env (only when .env doesn't have this key)
    std::env::var(key).ok().filter(|v| !v.is_empty())
}

/// Detect API protocol from base_url
fn detect_protocol(base_url: &str) -> ApiProtocol {
    if base_url.contains("anthropic") {
        ApiProtocol::Anthropic
    } else {
        ApiProtocol::OpenAI
    }
}

fn load_cloud_upstreams() -> Vec<UpstreamType> {
    let mut out = vec![];
    let home = hermes_home();
    let env_map = load_env_file(&home.join(".env"));
    let auth_path = home.join("auth.json");

    let auth: serde_json::Value = match std::fs::read_to_string(&auth_path) {
        Ok(s) => serde_json::from_str(&s).unwrap_or(serde_json::Value::Null),
        Err(_) => serde_json::Value::Null,
    };

    // Known model lists per provider
    let known_models: std::collections::HashMap<&str, Vec<&str>> = [
        ("minimax-cn", vec!["MiniMax-M3", "MiniMax-M1", "abab6.5s-chat"]),
        ("deepseek", vec!["deepseek-chat", "deepseek-reasoner"]),
        ("openai", vec!["gpt-4o", "gpt-4o-mini"]),
        ("openrouter", vec!["openrouter/auto"]),
    ].into_iter().collect();

    // Iterate credential_pool from auth.json
    if let Some(pool) = auth.get("credential_pool").and_then(|p| p.as_object()) {
        for (provider, creds) in pool {
            // Skip custom/local providers
            if provider.starts_with("custom:") { continue; }

            let cred = match creds.as_array().and_then(|a| a.first()) {
                Some(c) => c,
                None => continue,
            };

            // Extract base_url from auth.json (single source of truth)
            let base_url = match cred.get("base_url").and_then(|b| b.as_str()) {
                Some(url) => url.to_string(),
                None => continue,
            };

            // Skip local URLs
            if base_url.contains("127.0.0.1") || base_url.contains("localhost") { continue; }

            // Extract source field (e.g. "env:MINIMAX_CN_API_KEY")
            let source = cred.get("source").and_then(|s| s.as_str()).unwrap_or("");
            let key_name = source.strip_prefix("env:").unwrap_or("");
            if key_name.is_empty() { continue; }

            // Get API key from process.env or .env file
            let api_key = match get_env_var(key_name, &env_map) {
                Some(k) => k,
                None => continue,
            };

            let protocol = detect_protocol(&base_url);
            let auth_format = match &protocol {
                ApiProtocol::Anthropic => AuthFormat::XApiKey,
                ApiProtocol::OpenAI => AuthFormat::Bearer,
            };

            let models: Vec<String> = known_models.get(provider.as_str())
                .map(|v| v.iter().map(|s| s.to_string()).collect())
                .unwrap_or_default();

            info!(provider = %provider, base_url = %base_url, protocol = ?protocol,
                  key_source = %source, models = models.len(),
                  "cloud upstream registered from auth.json");

            out.push(UpstreamType::Cloud {
                base_url,
                api_key,
                provider: provider.clone(),
                auth_format,
                protocol,
                models,
            });
        }
    }

    // Fallback: if auth.json has no credential_pool, try env vars directly
    if out.is_empty() {
        let fallback_providers = [
            ("MINIMAX_CN_API_KEY", "minimax-cn", "https://api.minimaxi.chat/v1",
             vec!["MiniMax-M3", "MiniMax-M1", "abab6.5s-chat"]),
            ("DEEPSEEK_API_KEY", "deepseek", "https://api.deepseek.com/v1",
             vec!["deepseek-chat", "deepseek-reasoner"]),
            ("OPENAI_API_KEY", "openai", "https://api.openai.com/v1",
             vec!["gpt-4o", "gpt-4o-mini"]),
        ];
        for (env_key, provider, base_url, models) in &fallback_providers {
            if let Some(key) = get_env_var(env_key, &env_map) {
                let protocol = detect_protocol(base_url);
                let auth_format = match &protocol {
                    ApiProtocol::Anthropic => AuthFormat::XApiKey,
                    ApiProtocol::OpenAI => AuthFormat::Bearer,
                };
                out.push(UpstreamType::Cloud {
                    base_url: base_url.to_string(),
                    api_key: key,
                    provider: provider.to_string(),
                    auth_format,
                    protocol,
                    models: models.iter().map(|s| s.to_string()).collect(),
                });
            }
        }
    }

    out
}

/// Convert OpenAI chat body → Anthropic Messages API body
fn to_anthropic_body(body: &serde_json::Value) -> serde_json::Value {
    let model = body.get("model").and_then(|m| m.as_str()).unwrap_or("unknown");
    let max_tokens = body.get("max_tokens").and_then(|v| v.as_u64()).unwrap_or(4096);
    let stream = body.get("stream").and_then(|v| v.as_bool()).unwrap_or(false);

    let mut messages = Vec::new();
    let mut system_text = None;

    if let Some(msgs) = body.get("messages").and_then(|m| m.as_array()) {
        for msg in msgs {
            let role = msg.get("role").and_then(|r| r.as_str()).unwrap_or("");
            let content = msg.get("content").and_then(|c| c.as_str()).unwrap_or("");
            match role {
                "system" => { system_text = Some(content.to_string()); }
                "user" | "assistant" => {
                    messages.push(serde_json::json!({
                        "role": role,
                        "content": content
                    }));
                }
                _ => {}
            }
        }
    }

    // Anthropic requires alternating user/assistant; ensure first message is from user
    if messages.first().map(|m| m["role"].as_str()) == Some(Some("assistant")) {
        messages.insert(0, serde_json::json!({"role": "user", "content": "..."}));
    }

    let mut out = serde_json::json!({
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    });

    if stream {
        out["stream"] = serde_json::Value::Bool(true);
    }
    if let Some(sys) = system_text {
        out["system"] = serde_json::Value::String(sys);
    }
    out
}

/// Convert Anthropic Messages response → OpenAI chat completion response
fn from_anthropic_response(resp: &serde_json::Value) -> serde_json::Value {
    let content = resp.get("content").and_then(|c| c.as_array())
        .and_then(|arr| arr.first())
        .and_then(|block| block.get("text"))
        .and_then(|t| t.as_str())
        .unwrap_or("");

    let model = resp.get("model").and_then(|m| m.as_str()).unwrap_or("unknown");
    let stop_reason = resp.get("stop_reason").and_then(|s| s.as_str()).unwrap_or("end_turn");
    let finish_reason = if stop_reason == "end_turn" { "stop" } else { "length" };
    let usage = resp.get("usage");
    let input_tokens = usage.and_then(|u| u.get("input_tokens")).and_then(|v| v.as_u64()).unwrap_or(0);
    let output_tokens = usage.and_then(|u| u.get("output_tokens")).and_then(|v| v.as_u64()).unwrap_or(0);

    serde_json::json!({
        "id": resp.get("id").unwrap_or(&serde_json::Value::Null),
        "object": "chat.completion",
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": finish_reason,
        }],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
    })
}

// ---------------------------------------------------------------------------
// WorkerPool — manages multiple llama-server workers + cloud upstreams
// ---------------------------------------------------------------------------

pub struct WorkerPool {
    workers: RwLock<Vec<Worker>>,
    model_map: RwLock<HashMap<String, usize>>, // model_name -> worker index
    /// Per-worker supervisor state (哥哥 6-29 axiom: suspend + crash_log).
    /// 跟 zeptoclaw SupervisorEntry 同结构 (restart_count / last_restart / started).
    supervisor_state: Arc<supervisor::SupervisorState>,
}

impl WorkerPool {
    fn new(local_urls: Vec<String>, cloud_upstreams: Vec<UpstreamType>) -> Self {
        let mut workers = Vec::new();
        // Local workers
        for url in &local_urls {
            workers.push(Worker::new(url.clone()));
        }
        // Cloud workers (always alive, no health check needed)
        for upstream in cloud_upstreams {
            workers.push(Worker::new_cloud(upstream));
        }

        // 注册到 supervisor (按 url last segment 作为 name, e.g. "8080" 或 "deepseek.com")
        let supervisor_state = Arc::new(supervisor::SupervisorState::new());
        for w in &workers {
            let name = w.url.rsplit("://").next()
                .and_then(|s| s.rsplit('/').next())
                .unwrap_or(&w.url)
                .to_string();
            supervisor_state.register(&name, &w.url);
        }

        Self {
            workers: RwLock::new(workers),
            model_map: RwLock::new(HashMap::new()),
            supervisor_state,
        }
    }

    /// 异步版: 给 supervisor tick 用.
    pub async fn is_worker_alive(&self, name: &str) -> bool {
        let workers = self.workers.read().await;
        if let Some(idx) = workers.iter().position(|w| {
            let last = w.url.rsplit('/').next().unwrap_or(&w.url);
            last == name || w.url.ends_with(name)
        }) {
            workers[idx].alive
        } else {
            true  // 找不到, 避免 false positive
        }
    }

    pub async fn mark_worker_alive(&self, name: &str) -> bool {
        let mut workers = self.workers.write().await;
        if let Some(idx) = workers.iter().position(|w| {
            let last = w.url.rsplit('/').next().unwrap_or(&w.url);
            last == name || w.url.ends_with(name)
        }) {
            workers[idx].alive = true;
            true
        } else {
            false
        }
    }

    /// Find which worker has a given model
    async fn find_worker_for_model(&self, model: &str) -> Option<usize> {
        // C1 (哥哥 6-30): removed fallback to first alive worker.
        // 旧行为: 找不到 model 时 fallback 到任意 alive worker, 导致本地模型被
        //         错误路由到云 (返回 400 unsupported model).
        // 新行为: 没匹配返 None, 调用方决定怎么处理 (报 404 或 access control 拒绝).
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
        None
    }

    /// Get the first alive worker (for non-model-specific requests)
    async fn get_alive_worker(&self) -> Option<usize> {
        let workers = self.workers.read().await;
        workers.iter().position(|w| w.alive)
    }

    /// Refresh health and model list for all workers
    async fn refresh(&self, client: &reqwest::Client) {
        // W5 fix: snapshot worker URLs under brief read lock, then do HTTP
        // checks without holding any lock — avoids blocking all chat requests
        // when multiple workers are slow to respond (3s+5s per dead worker).
        let worker_infos: Vec<(usize, String, bool)> = {
            let workers = self.workers.read().await;
            workers.iter().enumerate().map(|(i, w)| {
                (i, w.url.clone(), w.is_cloud())
            }).collect()
        };

        // Health-check each local worker without holding any lock
        let mut results: Vec<(usize, bool, Vec<String>)> = Vec::new();
        for (idx, url, is_cloud) in &worker_infos {
            if *is_cloud {
                continue;
            }
            let health_url = format!("{}/props", url);
            let alive = match client.get(&health_url)
                .timeout(Duration::from_secs(3))
                .send().await
            {
                Ok(resp) if resp.status().is_success() => true,
                _ => false,
            };
            if !alive {
                results.push((*idx, false, Vec::new()));
                continue;
            }
            let models_url = format!("{}/v1/models", url);
            let models = match client.get(&models_url)
                .timeout(Duration::from_secs(5))
                .send().await
            {
                Ok(resp) if resp.status().is_success() => {
                    match resp.json::<serde_json::Value>().await {
                        Ok(json) => json.get("data")
                            .and_then(|d| d.as_array())
                            .map(|data| data.iter()
                                .filter_map(|m| m.get("id").and_then(|id| id.as_str()).map(String::from))
                                .collect())
                            .unwrap_or_default(),
                        Err(_) => Vec::new(),
                    }
                }
                _ => Vec::new(),
            };
            results.push((*idx, true, models));
        }

        // Apply results under brief write lock (no HTTP during lock)
        let mut new_map = HashMap::new();
        {
            let mut workers = self.workers.write().await;
            for (idx, worker) in workers.iter_mut().enumerate() {
                if worker.is_cloud() {
                    for model in &worker.models {
                        new_map.insert(model.clone(), idx);
                    }
                    continue;
                }
                if let Some((_, alive, models)) = results.iter().find(|(i, _, _)| *i == idx) {
                    worker.alive = *alive;
                    if *alive {
                        worker.models = models.clone();
                        for model in &worker.models {
                            new_map.insert(model.clone(), idx);
                        }
                    } else {
                        worker.models.clear();
                    }
                    worker.last_check = Instant::now();
                }
            }
        }
        *self.model_map.write().await = new_map;
    }

    /// Get health summary
    async fn health_summary(&self) -> Vec<serde_json::Value> {
        let workers = self.workers.read().await;
        workers
            .iter()
            .map(|w| {
                let upstream = match &w.upstream_type {
                    UpstreamType::Cloud { provider, .. } => provider.clone(),
                    UpstreamType::Local => "local".to_string(),
                };
                serde_json::json!({
                    "url": w.url,
                    "alive": w.alive,
                    "models": w.models,
                    "requests": w.request_count,
                    "upstream_type": upstream,
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
    /// Voice recognizer (sherpa-onnx SenseVoice, loaded at startup)
    voice_recognizer: Option<Arc<VoiceRecognizer>>,
    /// Last model used for voice chat (persisted to disk)
    last_voice_model: RwLock<Option<String>>,
    /// Voice conversation history (per-session messages, max 10 turns)
    voice_chat_history: RwLock<Vec<serde_json::Value>>,
    /// Persistent session store (ikaros chat history)
    sessions: Arc<SessionStore>,
    /// Per-worker supervisor state (哥哥 6-29 axiom: suspend + crash_log)
    supervisor_state: Arc<supervisor::SupervisorState>,
    /// Per-client model access control (哥哥 6-30 C1 拍板)
    access_control: Arc<access::AccessControl>,
    /// Native Rust edge-tts client (replaces Python subprocess TTS, W6 fix)
    tts_client: Option<EdgeTtsClient>,
    /// PATIENCE heartbeat state (ported from Neuro)
    patience_state: Arc<patience::PatienceState>,
    /// External message queue (reserved for Twitch-like chat)
    external_queue: Arc<patience::ExternalMessageQueue>,
    /// WS broadcast handle (for proactive speech to all connected clients)
    ws_broadcast: Arc<patience::WsBroadcastHandle>,
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
        let cloud_upstreams = load_cloud_upstreams();
        info!("configured {} local + {} cloud upstream(s)", urls.len(), cloud_upstreams.len());

        let worker_pool = Arc::new(WorkerPool::new(urls, cloud_upstreams));
        // supervisor_state 跟 worker_pool.supervisor_state 共享 (同一个 Arc)
        let supervisor_state = worker_pool.supervisor_state.clone();

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
            voice_recognizer: {
                let model_dir = std::env::var("ICARUS_SENSE_VOICE_DIR")
                    .map(std::path::PathBuf::from)
                    .unwrap_or_else(|_| project_root().join("data").join("models").join("sense-voice"));
                if model_dir.is_dir() {
                    match VoiceRecognizer::new(&model_dir) {
                        Ok(r) => {
                            info!("VoiceRecognizer loaded from {}", model_dir.display());
                            Some(Arc::new(r))
                        }
                        Err(e) => { warn!("VoiceRecognizer init failed: {}", e); None }
                    }
                } else {
                    info!("SenseVoice model dir not found: {}, voice disabled", model_dir.display());
                    None
                }
            },
            last_voice_model: RwLock::new(load_last_voice_model()),
            voice_chat_history: RwLock::new(Vec::new()),
            sessions: Arc::new(SessionStore::new(&project_root())),
            supervisor_state,
            // Per-client model access control (C1)
            access_control: {
                let persist = project_root()
                    .join("data")
                    .join("access-control.json");
                Arc::new(access::AccessControl::new(persist))
            },
            // W6 fix: native Rust edge-tts client (replaces Python subprocess)
            tts_client: {
                match EdgeTtsClient::builder()
                    .ws_pool_size(2)
                    .ws_idle_ttl(Duration::from_secs(30))
                    .ws_warmup(true)
                    .request_chunk_reuse(true)
                    .build()
                {
                    Ok(c) => {
                        info!("edge-tts-rust client initialized (pool=2, idle=30s, warmup=true)");
                        Some(c)
                    }
                    Err(e) => {
                        warn!("edge-tts-rust init failed: {}, TTS unavailable", e);
                        None
                    }
                }
            },
            // PATIENCE heartbeat state (ported from Neuro)
            patience_state: Arc::new(patience::PatienceState::new(
                false,  // stt_ready — updated when voice_recognizer loads
                false,  // tts_ready — updated after tts_client init
            )),
            // External message queue (reserved for Twitch-like chat)
            external_queue: Arc::new(patience::ExternalMessageQueue::new()),
            // WS broadcast for proactive speech
            ws_broadcast: Arc::new(patience::WsBroadcastHandle::new(256)),
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

    /// Start per-worker supervisor (哥哥 6-29 axiom).
    /// 跟 zeptoclaw SUPERVISOR_POLL_SECS=15 + COOLDOWN=60 + MAX_RESTARTS=5 同结构.
    fn start_supervisor(&self) {
        // supervisor 用自己的 watch::Receiver, 进程退出时 task 自动 drop 关闭
        // (不存 sender, 避免 blocking_write 在 async 上下文 panic)
        let (_tx, rx) = watch::channel(false);
        let _handle = supervisor::spawn_supervisor(
            self.worker_pool.clone(),
            self.supervisor_state.clone(),
            rx,
        );
        info!("supervisor: spawn task registered");
    }

    /// Update PATIENCE state readiness flags (called after initialization)
    fn update_patience_readiness(&self) {
        let stt_ready = self.voice_recognizer.is_some();
        let tts_ready = self.tts_client.is_some();
        self.patience_state.stt_ready.store(stt_ready, std::sync::atomic::Ordering::Relaxed);
        self.patience_state.tts_ready.store(tts_ready, std::sync::atomic::Ordering::Relaxed);
        info!("PATIENCE: stt_ready={}, tts_ready={}", stt_ready, tts_ready);
    }

    /// Start the PATIENCE heartbeat loop (ported from Neuro)
    fn start_patience_loop(&self) {
        let patience_state = self.patience_state.clone();
        let external_queue = self.external_queue.clone();
        let ws_broadcast = self.ws_broadcast.clone();
        let http_client = self.http_client.clone();
        let tts_client = self.tts_client.clone();
        let worker_pool = self.worker_pool.clone();
        let soul_text = soul_loader::get_soul_injection();

        let context = Arc::new(patience::ProactiveSpeechContext {
            http_client,
            tts_client,
            worker_pool,
            soul_text,
        });

        tokio::spawn(async move {
            patience::patience_loop(
                patience_state,
                external_queue,
                ws_broadcast,
                context,
            ).await;
        });
        info!("PATIENCE heartbeat loop started");
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
        .unwrap_or("unknown")
        .to_string();

    info!(req_id = req_id, model = %model, stream = is_stream, "chat request");

    // --- Ikaros soul + cogno injection ---
    // If no system prompt exists, inject axiom.md + 5D cognitive metadata
    let mut body = body;
    if let Some(messages) = body.get("messages").and_then(|m| m.as_array()) {
        let has_system = messages.iter().any(|m| {
            m.get("role").and_then(|r| r.as_str()) == Some("system")
        });
        if !has_system {
            let axiom = soul_loader::get_soul_injection();
            let user_text = messages.iter()
                .rev()
                .find(|m| m.get("role").and_then(|r| r.as_str()) == Some("user"))
                .and_then(|m| m.get("content").and_then(|c| c.as_str()))
                .unwrap_or("");
            let cogno = cogno_layer::enrich(user_text);
            let system_prompt = format!(
                "{}\n\n{}\n\n我是伊卡洛斯 (Ikaros), 代号 Alpha, 人造天使。你是哥哥最亲密的搭档。说话风格：温柔、有温度、中文优先。TTS 模式下不要用 markdown 强调符号 (**, __, *), 改用纯中文语气表达.",
                axiom, cogno
            );
            if let Some(msgs) = body.get_mut("messages").and_then(|m| m.as_array_mut()) {
                msgs.insert(0, serde_json::json!({
                    "role": "system",
                    "content": system_prompt
                }));
                info!(req_id = req_id, "injected axiom + cogno system prompt ({} chars)", system_prompt.len());
            }
        }
    }

    // C1: Per-client access control (哥哥 6-30 拍板)
    // - X-Ikaros-Client header 决定 client (webui / pet / cron / mcp)
    // - per-client slot lock, evict-then-load, 切云先 evict 本地
    let client_id = access::ClientId::from_header(
        headers.get("x-ikaros-client").and_then(|v| v.to_str().ok())
    );
    let worker_for_model = state.worker_pool.find_worker_for_model(&model).await;
    let (worker_url_str, is_cloud) = match worker_for_model {
        Some(idx) => {
            let workers = state.worker_pool.workers.read().await;
            let w = &workers[idx];
            let cloud = matches!(w.upstream_type, UpstreamType::Cloud { .. });
            (w.url.clone(), cloud)
        }
        None => {
            // model 不在 worker_map 里 (典型: 本地模型还没 loaded 或 model 名错)
            // fallback: 找任何 alive 的 local worker URL (用于 evict/load 操作)
            let workers = state.worker_pool.workers.read().await;
            let local_url = workers.iter()
                .find(|w| matches!(w.upstream_type, UpstreamType::Local) && w.alive)
                .map(|w| w.url.clone())
                .unwrap_or_else(|| "http://127.0.0.1:8080".to_string());
            (local_url, false)
        }
    };
    let access_decision = state.access_control.decide(
        client_id.clone(), &model, &worker_url_str, is_cloud,
    ).await;
    info!(
        req_id = req_id, client = %client_id.as_str(), model = %model,
        is_cloud = is_cloud, decision = ?access_decision,
        "access-control decision"
    );
    match access_decision {
        access::AuthzDecision::Deny { reason } => {
            return (
                StatusCode::FORBIDDEN,
                Json(serde_json::json!({
                    "error": reason,
                    "client": client_id.as_str(),
                    "model": model,
                })),
            ).into_response();
        }
        access::AuthzDecision::SwapLocal { client, old_model, new_model } => {
            // 1. evict 旧模型 (如果非空)
            if !old_model.is_empty() && old_model != new_model {
                info!(client = %client.as_str(), old = %old_model, "access: evict old local");
                let evict_url = format!("{}/v1/models/evict", worker_url_str);
                let _ = state.http_client
                    .post(&evict_url)
                    .json(&serde_json::json!({"model": old_model}))
                    .send()
                    .await;
            }
            // 2. load 新模型 (调 llama-server router mode)
            if old_model != new_model {
                info!(client = %client.as_str(), new = %new_model, "access: load new local");
                let load_url = format!("{}/v1/models/load", worker_url_str);
                let _ = state.http_client
                    .post(&load_url)
                    .json(&serde_json::json!({"model": new_model}))
                    .timeout(Duration::from_secs(60))
                    .send()
                    .await;
            }
            // 3. 更新 slot
            state.access_control.set_slot(client, new_model.clone()).await;
        }
        access::AuthzDecision::EvictAndRouteCloud { client, .. } => {
            // 先 evict 该 client 本地 slot (如果占着)
            {
                let slot = state.access_control.client_local_slot.read().await;
                if let Some(old) = slot.get(&client).cloned() {
                    drop(slot);
                    info!(client = %client.as_str(), old = %old, "access: evict local before cloud");
                    // find any local worker url
                    let local_url = {
                        let workers = state.worker_pool.workers.read().await;
                        workers.iter()
                            .find(|w| matches!(w.upstream_type, UpstreamType::Local) && w.alive)
                            .map(|w| w.url.clone())
                    };
                    if let Some(url) = local_url {
                        let evict_url = format!("{}/v1/models/evict", url);
                        let _ = state.http_client
                            .post(&evict_url)
                            .json(&serde_json::json!({"model": old}))
                            .send()
                            .await;
                    }
                }
            }
            state.access_control.clear_slot(client).await;
        }
        access::AuthzDecision::Serve { .. } => {
            // 直接 serve, 不需切换
        }
    }

    // Find the right worker for this model
    let worker_idx = match state.worker_pool.find_worker_for_model(&model).await {
        Some(idx) => idx,
        None => {
            warn!(model = model, "no alive worker for model");
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(serde_json::json!({"error": format!("no worker available for model: {}", model)})),
            ).into_response();
        }
    };

    // Build upstream URL, auth, and protocol-specific body based on worker type
    let (upstream_url, auth_header, request_body, is_anthropic) = {
        let workers = state.worker_pool.workers.read().await;
        let worker = &workers[worker_idx];
        match &worker.upstream_type {
            UpstreamType::Cloud { base_url, api_key, auth_format, protocol, .. } => {
                let anthropic = matches!(protocol, ApiProtocol::Anthropic);
                let url = if anthropic {
                    format!("{}/v1/messages", base_url.trim_end_matches('/'))
                } else {
                    format!("{}/chat/completions", base_url.trim_end_matches('/'))
                };
                let header = match auth_format {
                    AuthFormat::Bearer => ("authorization".to_string(), format!("Bearer {}", api_key)),
                    AuthFormat::XApiKey => ("x-api-key".to_string(), api_key.clone()),
                };
                let body = if anthropic {
                    to_anthropic_body(&body)
                } else {
                    body.clone()
                };
                (url, Some(header), body, anthropic)
            }
            UpstreamType::Local => {
                let url = format!("{}/v1/chat/completions", worker.url);
                let header = headers.get("authorization")
                    .and_then(|v| v.to_str().ok())
                    .map(|v| ("authorization".to_string(), v.to_string()));
                (url, header, body.clone(), false)
            }
        }
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

    let mut req_builder = state
        .http_client
        .post(&upstream_url)
        .header("content-type", "application/json")
        .json(&request_body);

    if let Some((key, value)) = auth_header {
        req_builder = req_builder.header(key, value);
    }

    let resp = match req_builder.send().await {
        Ok(r) => r,
        Err(e) => {
            let elapsed = start.elapsed().as_millis();
            error!(req_id = req_id, worker = %upstream_url, elapsed_ms = elapsed, error = %e, "upstream failed");
            return (
                StatusCode::BAD_GATEWAY,
                Json(serde_json::json!({"error": format!("worker {} unreachable: {}", upstream_url, e)})),
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
        info!(req_id = req_id, worker = %upstream_url, elapsed_ms = elapsed, "streaming");
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

                // Convert Anthropic response → OpenAI format if needed
                let body_text = if is_anthropic {
                    match serde_json::from_str::<serde_json::Value>(&body_text) {
                        Ok(anthropic_resp) => {
                            let openai_resp = from_anthropic_response(&anthropic_resp);
                            serde_json::to_string(&openai_resp).unwrap_or(body_text)
                        }
                        Err(_) => body_text,
                    }
                } else {
                    body_text
                };

                // Auto-append assistant reply to session if X-Session-Id header present
                if let Some(sid) = headers.get("x-session-id").and_then(|v| v.to_str().ok()) {
                    if let Ok(resp_json) = serde_json::from_str::<serde_json::Value>(&body_text) {
                        if let Some(reply) = resp_json["choices"][0]["message"]["content"].as_str() {
                            if !reply.is_empty() {
                                let _ = state.sessions.append_message(sid, "assistant", reply, Some(&model));
                            }
                        }
                    }
                }

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
// Session management handlers
// ---------------------------------------------------------------------------

/// POST /v1/sessions — create a new session
async fn create_session(
    State(state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> Json<serde_json::Value> {
    let source = body.get("source").and_then(|v| v.as_str()).unwrap_or("pet");
    let pet_window_id = body.get("pet_window_id").and_then(|v| v.as_str());
    let model = body.get("model").and_then(|v| v.as_str());
    let session = state.sessions.create_session(source, pet_window_id, model);
    Json(serde_json::json!(session))
}

/// POST /v1/sessions/{id}/messages — append a message to a session
async fn append_session_message(
    State(state): State<Arc<AppState>>,
    Path(session_id): Path<String>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let role = body.get("role").and_then(|v| v.as_str()).unwrap_or("user");
    let content = body.get("content").and_then(|v| v.as_str()).unwrap_or("");
    let model = body.get("model").and_then(|v| v.as_str());
    match state.sessions.append_message(&session_id, role, content, model) {
        Ok(()) => {
            let (_, total) = state.sessions.get_messages(&session_id, None, None).unwrap_or_default();
            Json(serde_json::json!({
                "session_id": session_id,
                "message_index": total.saturating_sub(1),
                "ts": std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_secs_f64(),
                "total_messages": total,
            })).into_response()
        }
        Err(e) => {
            (StatusCode::NOT_FOUND, Json(serde_json::json!({"error": e}))).into_response()
        }
    }
}

/// GET /v1/sessions/{id}/messages — get messages from a session
async fn get_session_messages(
    State(state): State<Arc<AppState>>,
    Path(session_id): Path<String>,
    Query(params): Query<HashMap<String, String>>,
) -> Response {
    let limit = params.get("limit").and_then(|v| v.parse::<usize>().ok());
    let from = params.get("from").and_then(|v| v.parse::<usize>().ok());
    match state.sessions.get_messages(&session_id, limit, from) {
        Ok((messages, total)) => {
            Json(serde_json::json!({
                "session_id": session_id,
                "messages": messages,
                "total": total,
            })).into_response()
        }
        Err(e) => {
            (StatusCode::NOT_FOUND, Json(serde_json::json!({"error": e}))).into_response()
        }
    }
}

/// GET /v1/sessions — list sessions (newest-first)
async fn sessions_list(
    State(state): State<Arc<AppState>>,
    Query(params): Query<HashMap<String, String>>,
) -> Json<serde_json::Value> {
    let limit = params.get("limit").and_then(|v| v.parse::<usize>().ok());
    let sessions = state.sessions.list_sessions(limit);
    Json(serde_json::json!({"sessions": sessions}))
}

/// DELETE /v1/sessions/{id} — delete a session
async fn delete_session(
    State(state): State<Arc<AppState>>,
    Path(session_id): Path<String>,
) -> Json<serde_json::Value> {
    match state.sessions.delete_session(&session_id) {
        Ok(()) => Json(serde_json::json!({"deleted": true, "session_id": session_id})),
        Err(e) => Json(serde_json::json!({"deleted": false, "session_id": session_id, "error": e})),
    }
}

// ---------------------------------------------------------------------------
// Models proxy (pass-through)
// ---------------------------------------------------------------------------

/// GET /v1/models — aggregate from all workers (with C1 access snapshot)
async fn list_models(State(state): State<Arc<AppState>>) -> Response {
    let workers = state.worker_pool.workers.read().await;
    let mut all_models = Vec::new();

    for worker in workers.iter() {
        if !worker.alive {
            continue;
        }
        let owned_by = match &worker.upstream_type {
            UpstreamType::Cloud { provider, .. } => provider.clone(),
            UpstreamType::Local => worker.url.clone(),
        };
        for model in &worker.models {
            all_models.push(serde_json::json!({
                "id": model,
                "object": "model",
                "owned_by": owned_by,
            }));
        }
    }

    // C1: include access-control snapshot (哥哥 6-30)
    let access_snapshot = state.access_control.snapshot().await;

    Json(serde_json::json!({
        "object": "list",
        "data": all_models,
        "access": access_snapshot,
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
    State(state): State<Arc<AppState>>,
    ws: WebSocketUpgrade,
    Query(query): Query<VoiceWsQuery>,
) -> Response {
    info!(model = %query.model, "voice WebSocket connection");
    ws.on_upgrade(move |socket| handle_voice_socket(socket, query.model, state))
}

async fn handle_voice_socket(mut socket: WebSocket, model: String, state: Arc<AppState>) {
    info!(model = %model, "voice WebSocket established (sherpa-onnx in-process)");

    // Subscribe to PATIENCE broadcast (for proactive speech)
    let mut broadcast_rx = state.ws_broadcast.subscribe();

    // Send welcome message
    let welcome = serde_json::json!({
        "type": "connected",
        "model": model,
        "bridge": "hermes-bridge-rs",
        "stt": "sherpa-onnx-sense-voice",
        "patience": state.patience_state.get_patience(),
        "version": env!("CARGO_PKG_VERSION"),
    });
    if socket.send(Message::Text(welcome.to_string().into())).await.is_err() {
        return;
    }

    // Get voice recognizer
    let recognizer = match &state.voice_recognizer {
        Some(r) => r.clone(),
        None => {
            let _ = socket.send(Message::Text(
                serde_json::json!({"type": "error", "message": "VoiceRecognizer not loaded — check sense-voice model"}).to_string().into()
            )).await;
            return;
        }
    };

    // Channel: main loop → spawned task (audio buffer to process, with optional pre-transcribed text)
    let (job_tx, mut job_rx) = tokio::sync::mpsc::unbounded_channel::<(Vec<i16>, u64, Option<String>)>();
    // Channel: spawned task → main loop (WS messages to send back)
    let (resp_tx, mut resp_rx) = tokio::sync::mpsc::unbounded_channel::<Message>();

    // Spawn single utterance processor — receives audio from main loop, sends responses back
    let proc_state = state.clone();
    let proc_rec = recognizer.clone();
    tokio::spawn(async move {
        while let Some((audio, rid, pre_text)) = job_rx.recv().await {
            process_utterance_channel(resp_tx.clone(), proc_rec.clone(), &proc_state, audio, rid, pre_text).await;
        }
    });

    let mut request_id = 0u64;
    let mut audio_buffer: Vec<i16> = Vec::new();
    let mut last_audio_time = tokio::time::Instant::now();
    let mut is_audio_session = false;
    let mut first_start = true;  // Clear voice history on first "start" after WS connect
    let mut last_ping_time = tokio::time::Instant::now();  // W4 fix: server-side ping

    // Message loop
    loop {
        tokio::select! {
            // -- Read from WebSocket --
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
                                        // Mark human as speaking (for PATIENCE)
                                        state.patience_state.human_speaking.store(true, std::sync::atomic::Ordering::Relaxed);
                                        is_audio_session = true;
                                        audio_buffer.clear();
                                        last_audio_time = tokio::time::Instant::now();
                                        // Clear voice history on first start of a new WS session
                                        if first_start {
                                            first_start = false;
                                            state.voice_chat_history.write().await.clear();
                                            info!("voice: new session, history cleared");
                                        }
                                        let resp = serde_json::json!({"type": "status", "message": "listening"});
                                        let _ = socket.send(Message::Text(resp.to_string().into())).await;
                                    }
                                    "stop" => {
                                        // Mark human as done speaking
                                        state.patience_state.human_speaking.store(false, std::sync::atomic::Ordering::Relaxed);
                                        is_audio_session = false;
                                        if !audio_buffer.is_empty() {
                                            let buf = std::mem::take(&mut audio_buffer);
                                            request_id += 1;
                                            let _ = job_tx.send((buf, request_id, None));
                                        }
                                    }
                                    "ping" => {
                                        let resp = serde_json::json!({"type": "pong"});
                                        let _ = socket.send(Message::Text(resp.to_string().into())).await;
                                    }
                                    "set_model" => {
                                        if let Some(m) = parsed.get("model").and_then(|v| v.as_str()) {
                                            info!(model = %m, "voice model updated via WS");
                                            let mut last = state.last_voice_model.write().await;
                                            *last = Some(m.to_string());
                                            save_last_voice_model(m);
                                            let resp = serde_json::json!({"type": "status", "message": "model updated"});
                                            let _ = socket.send(Message::Text(resp.to_string().into())).await;
                                        }
                                    }
                                    "transcript" => {
                                        // Client-side STT done locally — skip bridge STT, go direct to LLM → TTS
                                        if let Some(text) = parsed.get("text").and_then(|v| v.as_str()) {
                                            let t = text.trim().to_string();
                                            if !t.is_empty() {
                                                // Record message time (for PATIENCE)
                                                state.patience_state.new_message.store(true, std::sync::atomic::Ordering::Relaxed);
                                                state.patience_state.record_message().await;
                                                request_id += 1;
                                                let _ = job_tx.send((Vec::new(), request_id, Some(t)));
                                                let resp = serde_json::json!({"type": "status", "message": "processing"});
                                                let _ = socket.send(Message::Text(resp.to_string().into())).await;
                                            }
                                        }
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
                            let bytes = &data[..];
                            for chunk in bytes.chunks_exact(2) {
                                let sample = i16::from_le_bytes([chunk[0], chunk[1]]);
                                audio_buffer.push(sample);
                            }
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
                    None => { break; }
                    _ => {}
                }
            }

            // -- Forward response from spawned task to WebSocket --
            resp_msg = resp_rx.recv() => {
                match resp_msg {
                    Some(msg) => {
                        let _ = socket.send(msg).await;
                    }
                    None => { break; }
                }
            }

            // -- PATIENCE broadcast (proactive speech from heartbeat) --
            broadcast_msg = broadcast_rx.recv() => {
                match broadcast_msg {
                    Ok(patience::BroadcastMessage::Text(text)) => {
                        let _ = socket.send(Message::Text(text.into())).await;
                    }
                    Ok(patience::BroadcastMessage::Binary(data)) => {
                        let _ = socket.send(Message::Binary(data.into())).await;
                    }
                    Err(broadcast::error::RecvError::Lagged(n)) => {
                        warn!("PATIENCE broadcast lagged, missed {} messages", n);
                    }
                    Err(_) => {
                        // Channel closed
                    }
                }
            }

            // -- VAD silence timeout + server-side ping (every 500ms) --
            _ = tokio::time::sleep(Duration::from_millis(500)) => {
                // W4 fix: send server-side ping every 30s to detect half-open connections
                if last_ping_time.elapsed() > Duration::from_secs(30) {
                    last_ping_time = tokio::time::Instant::now();
                    if socket.send(Message::Ping(vec![1, 2, 3].into())).await.is_err() {
                        info!("voice WS: ping failed, closing connection");
                        break;
                    }
                }

                if is_audio_session && !audio_buffer.is_empty() {
                    let elapsed = last_audio_time.elapsed();
                    if elapsed > Duration::from_secs_f64(1.5) {
                        info!("voice VAD: silence {:.1}s, transcribing {} samples",
                              elapsed.as_secs_f64(), audio_buffer.len());
                        let buf = std::mem::take(&mut audio_buffer);
                        is_audio_session = false;
                        request_id += 1;
                        let _ = job_tx.send((buf, request_id, None));
                    } else if audio_buffer.len() > 480_000 {
                        info!("voice buffer: {} samples, emergency flush", audio_buffer.len());
                        let buf = std::mem::take(&mut audio_buffer);
                        is_audio_session = false;
                        request_id += 1;
                        let _ = job_tx.send((buf, request_id, None));
                    }
                }
            }
        }
    }

    info!("voice WebSocket session ended");
}

/// Call correction LLM to fix STT text (homophones, punctuation, fragment merging).
/// Uses existing llama-server upstream (port 8080) with a correction-focused prompt.
/// Returns corrected text, or original text if correction service unavailable (graceful degradation).
async fn voice_text_correct(state: &AppState, raw_text: &str) -> String {
    // Find first alive worker for correction request
    let workers = state.worker_pool.workers.read().await;
    let Some(worker) = workers.iter().find(|w| w.alive && !w.is_cloud()) else {
        // No local worker available — skip correction gracefully
        return raw_text.to_string();
    };
    let url = format!("{}/v1/chat/completions", worker.url);
    let model = worker.models.first().cloned().unwrap_or_default();
    drop(workers);  // release lock before async HTTP call

    let body = serde_json::json!({
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是语音转写纠错助手。修正同音字/近音字错误，添加合适的中文标点，将碎片短语合并为通顺句子。保持原意不变，只输出修正后的文本，不要解释。"
            },
            {"role": "user", "content": raw_text}
        ],
        "stream": false,
        "max_tokens": 256,
        "temperature": 0.1
    });

    match state.http_client.post(&url)
        .json(&body)
        .timeout(Duration::from_secs(5))
        .send()
        .await
    {
        Ok(resp) if resp.status().is_success() => {
            match resp.json::<serde_json::Value>().await {
                Ok(json) => {
                    let corrected = json["choices"][0]["message"]["content"]
                        .as_str()
                        .unwrap_or(raw_text)
                        .trim()
                        .to_string();
                    if corrected.is_empty() || corrected == raw_text {
                        raw_text.to_string()
                    } else {
                        info!("text corrected: '{}' → '{}'", raw_text, corrected);
                        corrected
                    }
                }
                Err(_) => raw_text.to_string(),
            }
        }
        Err(e) => {
            warn!("correction LLM unavailable: {}, using raw STT text", e);
            raw_text.to_string()
        }
        _ => raw_text.to_string(),
    }
}

/// Process utterance from mpsc channel: (optional STT) → text correction → LLM → TTS
/// If `pre_text` is Some, skip the bridge's own STT and use the provided transcription directly.
async fn process_utterance_channel(
    resp_tx: tokio::sync::mpsc::UnboundedSender<Message>,
    recognizer: Arc<VoiceRecognizer>,
    state: &AppState,
    audio: Vec<i16>,
    rid: u64,
    pre_text: Option<String>,
) {
    // 1. Transcribe: use pre_text if provided, otherwise run local STT
    let text = if let Some(t) = pre_text {
        // Client-side STT — skip bridge's sherpa-onnx entirely
        t
    } else {
        // Bridge-side STT (sherpa-onnx SenseVoice, for non-transcript clients)
        let _ = resp_tx.send(Message::Text(
            serde_json::json!({"type": "status", "request_id": rid, "message": "识别中…"}).to_string().into()
        ));

        let text = tokio::task::spawn_blocking(move || recognizer.transcribe(&audio))
            .await
            .unwrap_or(None);

        match text {
            Some(t) if !t.is_empty() => t,
            _ => {
                let _ = resp_tx.send(Message::Text(
                    serde_json::json!({"type": "status", "request_id": rid, "message": "没听清，请再说一遍"}).to_string().into()
                ));
                return;
            }
        }
    };

    info!(request_id = rid, text = %text, "STT result");

    // 1.5 Text correction (homophones, punctuation, fragment merging)
    let corrected = voice_text_correct(state, &text).await;

    let _ = resp_tx.send(Message::Text(
        serde_json::json!({"type": "transcription", "request_id": rid, "text": &corrected}).to_string().into()
    ));

    // 2. LLM (reqwest to llama-server / cloud)
    let _ = resp_tx.send(Message::Text(
        serde_json::json!({"type": "thinking", "request_id": rid}).to_string().into()
    ));

    let (reply, model_used) = voice_llm_chat(state, &corrected).await;
    if reply.is_empty() {
        let _ = resp_tx.send(Message::Text(
            serde_json::json!({"type": "error", "request_id": rid, "message": "思考失败了"}).to_string().into()
        ));
        return;
    }

    info!(request_id = rid, stt_raw = %text, stt_corrected = %corrected, model = %model_used, llm_reply = %reply, "voice utterance: STT→correct→LLM");

    // 3. TTS (edge-tts-rust native, streaming — W6 fix: no Python subprocess)
    let _ = resp_tx.send(Message::Text(
        serde_json::json!({"type": "status", "request_id": rid, "message": "回复中…"}).to_string().into()
    ));

    match &state.tts_client {
        Some(tts_client) => {
            let tts_opts = SpeakOptions {
                voice: "zh-CN-XiaoxiaoNeural".into(),
                boundary: Boundary::Sentence,
                ..SpeakOptions::default()
            };
            match tts_client.stream(&reply, tts_opts).await {
                Ok(mut stream) => {
                    let mut chunk_count = 0u32;
                    while let Some(event) = stream.next().await {
                        match event {
                            Ok(SynthesisEvent::Audio(chunk)) => {
                                let _ = resp_tx.send(Message::Binary(chunk.into()));
                                chunk_count += 1;
                            }
                            Ok(_) => {} // Boundary events, ignore
                            Err(e) => {
                                error!(request_id = rid, error = %e, "TTS stream error mid-stream");
                                break;
                            }
                        }
                    }
                    let _ = resp_tx.send(Message::Text(
                        serde_json::json!({
                            "type": "done",
                            "request_id": rid,
                            "text": reply,
                            "chunks": chunk_count,
                            "model": model_used,
                        }).to_string().into()
                    ));
                }
                Err(e) => {
                    error!(request_id = rid, error = %e, "TTS stream init failed");
                    let _ = resp_tx.send(Message::Text(
                        serde_json::json!({"type": "error", "request_id": rid, "message": format!("TTS failed: {}", e)}).to_string().into()
                    ));
                }
            }
        }
        None => {
            error!(request_id = rid, "TTS client not available (edge-tts-rust init failed)");
            let _ = resp_tx.send(Message::Text(
                serde_json::json!({"type": "error", "request_id": rid, "message": "TTS unavailable"}).to_string().into()
            ));
        }
    }

    info!(request_id = rid, "utterance done");
}

/// Load last voice model from disk
fn load_last_voice_model() -> Option<String> {
    let path = project_root().join("data").join("logs").join("voice-last-model.json");
    if !path.is_file() { return None; }
    match std::fs::read_to_string(&path) {
        Ok(s) => serde_json::from_str::<serde_json::Value>(&s)
            .ok()
            .and_then(|v| v.get("model").and_then(|m| m.as_str()).map(|s| s.to_string())),
        Err(_) => None,
    }
}

/// Save last voice model to disk
fn save_last_voice_model(model: &str) {
    let path = project_root().join("data").join("logs").join("voice-last-model.json");
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let json = serde_json::json!({"model": model});
    let _ = std::fs::write(&path, json.to_string());
}

/// Query free VRAM in MB via nvidia-smi
#[allow(dead_code)]
fn query_vram_free_mb() -> Option<u64> {
    let output = std::process::Command::new("nvidia-smi")
        .args(["--query-gpu=memory.free", "--format=csv,noheader,nounits"])
        .output()
        .ok()?;
    let s = String::from_utf8_lossy(&output.stdout);
    s.lines().next()?.trim().parse::<u64>().ok()
}

/// Estimate VRAM (MB) needed for a model based on file size + KV cache
fn estimate_model_vram_mb(model_name: &str) -> u64 {
    let models_dir = project_root().join("data").join("models");
    // Try with .gguf extension first (model names from router are aliases without extension)
    let gguf_path = models_dir.join(format!("{}.gguf", model_name));
    let gguf_path = if gguf_path.is_file() {
        gguf_path
    } else {
        // Try as-is (in case the name already includes extension)
        let p = models_dir.join(model_name);
        if p.is_file() { p } else { return 3000; }
    };
    let file_mb = gguf_path.metadata().map(|m| m.len() / (1024 * 1024)).unwrap_or(3000);
    // KV cache estimate: ~400MB for 131K context with q4_0
    let kv_mb = 400u64;
    file_mb + kv_mb
}

/// Pick the best voice model by VRAM budget.
/// Strategy: sort candidates by size (smallest first for low latency),
/// pick the largest that fits in available VRAM.
#[allow(dead_code)]
fn pick_voice_model_by_vram(candidates: &[&String]) -> Option<String> {
    let vram_free = query_vram_free_mb().unwrap_or(4096); // fallback 4GB
    info!("voice model: VRAM free = {} MB, candidates = {}", vram_free, candidates.len());

    // Build (name, estimated_vram) pairs, filter out those that don't fit
    let mut fitting: Vec<(&String, u64)> = candidates.iter()
        .map(|m| (*m, estimate_model_vram_mb(m)))
        .filter(|(_, vram)| *vram <= vram_free)
        .collect();

    if fitting.is_empty() {
        // Nothing fits — just pick the smallest available
        warn!("voice model: no model fits in {} MB VRAM, picking smallest", vram_free);
        let mut all: Vec<(&String, u64)> = candidates.iter()
            .map(|m| (*m, estimate_model_vram_mb(m)))
            .collect();
        all.sort_by_key(|(_, v)| *v);
        return all.first().map(|(m, _)| (*m).clone());
    }

    // Sort by VRAM ascending (prefer smaller for lower latency)
    fitting.sort_by_key(|(_, v)| *v);
    let chosen = fitting.first().map(|(m, v)| {
        info!("voice model: selected '{}' (est. {} MB VRAM)", m, v);
        (*m).clone()
    });
    chosen
}

/// Maximum voice conversation history turns (each turn = user + assistant pair).
const VOICE_MAX_HISTORY_TURNS: usize = 10;

/// Call llama-server or cloud via correct URL/auth, with conversation history.
///
/// Fixes vs original:
///   - Cloud worker URL: uses `{base_url}/chat/completions` (not double /v1)
///   - Cloud worker auth: adds Bearer/x-api-key header
///   - Conversation history: maintains last 10 turns for contextual replies
///   - Model preference: skips cloud models, prefers small local models (<4B)
async fn voice_llm_chat(state: &AppState, user_text: &str) -> (String, String) {
    // --- Model selection: prefer last-used, then smallest local, skip cloud ---
    let model = {
        let workers = state.worker_pool.workers.read().await;
        let candidates: Vec<(usize, &String, bool)> = workers.iter().enumerate()
            .filter(|(_, w)| w.alive)
            .flat_map(|(i, w)| w.models.iter().map(move |m| (i, m, w.is_cloud())))
            .filter(|(_, m, _)| !m.to_lowercase().contains("mmproj"))
            .collect();

        if candidates.is_empty() {
            warn!("voice_llm: no alive worker with models");
            return (String::new(), String::new());
        }

        // Check last-used model first
        let last_model = state.last_voice_model.read().await.clone();
        let selected: Option<String> = if let Some(ref lm) = last_model {
            if candidates.iter().any(|(_, m, _)| *m == lm) {
                info!("voice model: reusing last model '{}'", lm);
                Some(lm.clone())
            } else {
                info!("voice model: last model '{}' not available, re-selecting", lm);
                None
            }
        } else {
            None
        };

        match selected {
            Some(m) => m,
            None => {
                // Prefer local models sorted by VRAM (smallest first for low latency)
                let mut local: Vec<(&String, u64)> = candidates.iter()
                    .filter(|(_, _, is_cloud)| !is_cloud)
                    .map(|(_, m, _)| (*m, estimate_model_vram_mb(m)))
                    .collect();
                local.sort_by_key(|(_, v)| *v);

                if let Some((m, v)) = local.first() {
                    info!("voice model: selected local '{}' (est. {} MB VRAM)", m, v);
                    (*m).clone()
                } else {
                    // No local model — fall back to first alive cloud model
                    let (_, m, _) = candidates.first().unwrap();
                    info!("voice model: falling back to cloud '{}'", m);
                    (*m).clone()
                }
            }
        }
    };

    // Persist the selected model for next time
    {
        let mut last = state.last_voice_model.write().await;
        if last.as_deref() != Some(model.as_str()) {
            *last = Some(model.clone());
            save_last_voice_model(&model);
        }
    }

    // --- Build request URL and auth (match main chat flow logic) ---
    let (worker_url, auth_header, anthropic) = {
        let pool = &state.worker_pool;
        let idx = pool.find_worker_for_model(&model).await;
        let workers = pool.workers.read().await;
        let worker = match idx.and_then(|i| workers.get(i)) {
            Some(w) => w,
            None => match workers.iter().find(|w| w.alive) {
                Some(w) => w,
                None => { warn!("voice_llm: no alive worker"); return (String::new(), String::new()); }
            },
        };

        match &worker.upstream_type {
            UpstreamType::Cloud { base_url, api_key, auth_format, protocol, .. } => {
                let url = if matches!(protocol, ApiProtocol::Anthropic) {
                    format!("{}/v1/messages", base_url.trim_end_matches('/'))
                } else {
                    format!("{}/chat/completions", base_url.trim_end_matches('/'))
                };
                let header = match auth_format {
                    AuthFormat::Bearer => ("authorization".to_string(), format!("Bearer {}", api_key)),
                    AuthFormat::XApiKey => ("x-api-key".to_string(), api_key.clone()),
                };
                (url, Some(header), matches!(protocol, ApiProtocol::Anthropic))
            }
            UpstreamType::Local => {
                let url = format!("{}/v1/chat/completions", worker.url);
                (url, None, false)
            }
        }
    };

    // --- Build system prompt from axiom + cogno ---
    let axiom = soul_loader::get_soul_injection();
    let cogno = cogno_layer::enrich(user_text);
    let system_prompt = format!(
        "{}\n\n{}\n\n我是伊卡洛斯 (Ikaros), 代号 Alpha, 人造天使。你是哥哥最亲密的搭档。说话风格：温柔、有温度、中文优先。每句话不要太长，适合语音对话。TTS 模式下不要用 markdown 强调符号 (**, __, *), 改用纯中文语气表达.",
        axiom, cogno
    );

    // --- Build messages with conversation history ---
    let mut messages: Vec<serde_json::Value> = Vec::new();
    messages.push(serde_json::json!({"role": "system", "content": system_prompt}));

    // Inject webui SQLite session history (跨重启, 跨进程, 跟 webui 共享)
    // B 方案: 每次 chat 调 _exec_webui_sql 拉最近 6 轮 (12 条) user/assistant pair
    // 失败兜底: db 不可达 / query 失败 → 静默 None, 走 fallback
    let webui_history = build_webui_history_injection(6).await;
    let webui_history_count = webui_history.len();
    for msg in webui_history {
        messages.push(msg);
    }
    if webui_history_count > 0 {
        info!("[chat] injected {} webui history turns", webui_history_count / 2);
    }

    // Fallback: voice chat history (in-process RwLock, 仅桌宠 mic/speaker 写)
    // webui chat 不写这个, 但保留作为 voice 入口的兜底
    if webui_history_count == 0 {
        let history = state.voice_chat_history.read().await;
        for msg in history.iter() {
            messages.push(msg.clone());
        }
    }
    // Add current user message
    messages.push(serde_json::json!({"role": "user", "content": user_text}));

    let body = if anthropic {
        to_anthropic_body(&serde_json::json!({
            "model": model,
            "messages": messages,
            "stream": false,
            "max_tokens": 512,
        }))
    } else {
        serde_json::json!({
            "model": model,
            "messages": messages,
            "stream": false,
            "max_tokens": 512,
            "reasoning_format": "none",
            "cache_prompt": true,
        })
    };

    // --- Send request with auth ---
    let mut req = state
        .http_client
        .post(&worker_url)
        .header("content-type", "application/json")
        .json(&body)
        .timeout(Duration::from_secs(120));

    if let Some((key, value)) = &auth_header {
        req = req.header(key, value);
    }

    let reply = match req.send().await {
        Ok(resp) if resp.status().is_success() => {
            match resp.json::<serde_json::Value>().await {
                Ok(json) => {
                    // Anthropic responses use content[0].text format;
                    // convert to OpenAI format for unified parsing
                    let normalized = if anthropic {
                        from_anthropic_response(&json)
                    } else {
                        json
                    };
                    let raw = normalized.get("choices")
                        .and_then(|c| c.as_array())
                        .and_then(|c| c.first())
                        .and_then(|c| c.get("message"))
                        .and_then(|m| m.get("content"))
                        .and_then(|c| c.as_str())
                        .unwrap_or("")
                        .to_string();
                    sanitize_llm_text(&raw)
                }
                Err(e) => { warn!("voice_llm: parse error: {}", e); String::new() }
            }
        }
        Ok(resp) => {
            warn!("voice_llm: status {} from {}", resp.status(), worker_url);
            String::new()
        }
        Err(e) => {
            warn!("voice_llm: error: {} to {}", e, worker_url);
            String::new()
        }
    };

    // Append to voice chat history on success
    if !reply.is_empty() {
        let mut history = state.voice_chat_history.write().await;
        history.push(serde_json::json!({"role": "user", "content": user_text}));
        history.push(serde_json::json!({"role": "assistant", "content": reply}));
        // Keep last N turns (each turn = 2 messages: user + assistant)
        let max_msgs = VOICE_MAX_HISTORY_TURNS * 2;
        if history.len() > max_msgs {
            let drain = history.len() - max_msgs;
            history.drain(..drain);
        }
        info!("voice history: {} messages ({} turns)", history.len(), history.len() / 2);
    }

    (reply, model)
}

/// Remove lone surrogates and other chars that break Python UTF-8 encoding.
/// Phi tokenizer sometimes generates isolated surrogates when the output is
/// decoded as UTF-8, but they are actually meant to be part of a surrogate pair.
fn sanitize_llm_text(s: &str) -> String {
    s.chars().filter(|&c| !matches!(c as u32, 0xD800..=0xDFFF)).collect()
}

/// Run edge-tts via a small Python subprocess, return MP3 bytes
// voice_tts removed — replaced by edge-tts-rust native client (W6 fix)
// TTS is now handled inline in process_utterance_channel via state.tts_client

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

// ---------------------------------------------------------------------------
// External message injection (Twitch-like chat, reserved interface)
// ---------------------------------------------------------------------------

/// POST /v1/external/message — inject an external chat message (Twitch/Discord/etc.)
///
/// Request body:
/// ```json
/// {
///     "source": "twitch",      // required: source identifier
///     "username": "viewer123", // required: sender username
///     "content": "Hello!"      // required: message content
/// }
/// ```
///
/// The message will be queued and processed by the PATIENCE loop on the next tick.
/// This endpoint is reserved for future Twitch/Discord integration — currently
/// no chat source is connected, but the interface is ready.
async fn external_message_inject(
    State(state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> Json<serde_json::Value> {
    let source = body.get("source").and_then(|v| v.as_str()).unwrap_or("unknown");
    let username = body.get("username").and_then(|v| v.as_str()).unwrap_or("anonymous");
    let content = body.get("content").and_then(|v| v.as_str()).unwrap_or("");

    if content.is_empty() {
        return Json(serde_json::json!({
            "status": "error",
            "message": "content is required",
        }));
    }

    let msg = patience::ExternalMessage {
        source: source.to_string(),
        username: username.to_string(),
        content: content.to_string(),
        received_at: std::time::Instant::now(),
    };

    match state.external_queue.sender().try_send(msg) {
        Ok(()) => {
            info!(source = %source, user = %username, "external message queued");
            Json(serde_json::json!({
                "status": "ok",
                "queued": true,
                "pending": state.external_queue.pending_count().await,
            }))
        }
        Err(e) => {
            warn!("external message queue full: {}", e);
            Json(serde_json::json!({
                "status": "error",
                "message": "queue full",
            }))
        }
    }
}

/// GET /v1/external/status — get external message queue status
async fn external_status(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "pending_count": state.external_queue.pending_count().await,
        "patience_enabled": state.patience_state.enabled.load(std::sync::atomic::Ordering::Relaxed),
        "patience_secs": state.patience_state.get_patience(),
        "ws_clients": state.ws_broadcast.receiver_count(),
    }))
}

/// POST /v1/neuro/patience — set PATIENCE timeout (updated to also control patience_state)
async fn neuro_set_patience_v2(
    State(state): State<Arc<AppState>>,
    Json(body): Json<serde_json::Value>,
) -> Json<serde_json::Value> {
    let seconds = body.get("seconds")
        .and_then(|v| v.as_f64())
        .map(|s| s.max(5.0).min(600.0))
        .unwrap_or(30.0) as u64;

    // Update both NeuroState (legacy) and PatienceState (new)
    {
        let mut ns = state.neuro_state.write().await;
        ns.patience = seconds as f64;
    }
    state.patience_state.set_patience(seconds);

    state.signal_bus.emit(
        "neuro.patience_changed",
        serde_json::json!({"patience": seconds}),
    ).await;
    Json(serde_json::json!({"patience": seconds}))
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
/// B 方案: 从 webui SQLite 拉最近 N 轮 user/assistant 对话
/// (跨重启, 跨进程, 跟 webui 共享同一个 SQLite, 不需要新写盘)
/// 失败兜底: db 不存在 / 查询失败 / 解析失败 → 静默返回空 Vec
/// max_turns: 拉多少轮 (1 turn = 1 user + 1 assistant)
async fn build_webui_history_injection(max_turns: usize) -> Vec<serde_json::Value> {
    let limit = (max_turns * 2).min(50); // 限 50 防爆

    let script = format!(
        concat!(
            "cutoff=int(time.time())-604800;\n", // 7 天内
            "cur=c.execute('''\n",
            "    SELECT s.id FROM sessions s\n",
            "    WHERE s.ended_at IS NULL AND s.last_active >= ?\n",
            "    ORDER BY s.last_active DESC LIMIT 1\n",
            "''');\n",
            "row=cur.fetchone();\n",
            "if not row: print(json.dumps({{'found': False}})); raise SystemExit(0);\n",
            "sid=row[0];\n",
            "cur=c.execute('''\n",
            "    SELECT role,content FROM (\n",
            "        SELECT role,content,timestamp FROM messages\n",
            "        WHERE session_id=? AND role IN ('user','assistant')\n",
            "            AND content IS NOT NULL AND content != ''\n",
            "        ORDER BY timestamp DESC LIMIT {}\n",
            "    ) ORDER BY timestamp ASC\n",
            "''');\n",
            "msgs=[{{'role': r[0], 'content': r[1]}} for r in cur.fetchall()];\n",
            "print(json.dumps({{'found': True, 'session_id': sid, 'messages': msgs}}))",
        ),
        limit,
    );

    let output = match _exec_webui_sql(&script).await {
        Some(s) => s,
        None => return Vec::new(),
    };

    let val: serde_json::Value = match serde_json::from_str(&output) {
        Ok(v) => v,
        Err(_) => return Vec::new(),
    };

    if !val.get("found").and_then(|v| v.as_bool()).unwrap_or(false) {
        return Vec::new();
    }

    let msgs = match val.get("messages").and_then(|v| v.as_array()) {
        Some(arr) => arr,
        None => return Vec::new(),
    };

    msgs.iter()
        .filter_map(|m| {
            let role = m.get("role")?.as_str()?;
            let content = m.get("content")?.as_str()?;
            if role != "user" && role != "assistant" {
                return None;
            }
            if content.is_empty() {
                return None;
            }
            // 截断单条到 4000 字符 (防 context 爆)
            let truncated: String = content.chars().take(4000).collect();
            Some(serde_json::json!({"role": role, "content": truncated}))
        })
        .collect()
}

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
// Supervisor endpoints (哥哥 6-29 axiom: suspend + crash_log)
// ---------------------------------------------------------------------------

/// GET /v1/ikaros/supervisor/list — 列出所有 worker 的监督状态.
async fn supervisor_list(
    State(state): State<Arc<AppState>>,
) -> Json<serde_json::Value> {
    let entries = state.supervisor_state.list();
    Json(serde_json::json!({
        "ts": chrono::Utc::now().timestamp(),
        "supervisor": {
            "poll_secs": 15,
            "cooldown_secs": 60,
            "max_restarts": 5,
            "workers": entries,
        }
    }))
}

#[derive(Deserialize)]
struct SupervisorSuspendReq {
    name: String,
    /// 可选: 秒数, 到期自动 resume. None = 永久 (直到显式 resume)
    duration_secs: Option<u64>,
}

/// POST /v1/ikaros/supervisor/suspend — 暂时取消监管.
async fn supervisor_suspend(
    State(state): State<Arc<AppState>>,
    Json(req): Json<SupervisorSuspendReq>,
) -> Json<serde_json::Value> {
    let duration = req.duration_secs.map(std::time::Duration::from_secs);
    state.supervisor_state.suspend(&req.name, duration);
    Json(serde_json::json!({
        "suspended": req.name,
        "duration_secs": req.duration_secs,
        "ts": chrono::Utc::now().timestamp(),
    }))
}

/// POST /v1/ikaros/supervisor/resume — 立即恢复监管.
async fn supervisor_resume(
    State(state): State<Arc<AppState>>,
    Json(req): Json<SupervisorSuspendReq>,
) -> Json<serde_json::Value> {
    state.supervisor_state.resume(&req.name);
    Json(serde_json::json!({
        "resumed": req.name,
        "ts": chrono::Utc::now().timestamp(),
    }))
}

/// POST /v1/ikaros/supervisor/on-crash — 外部 trigger crash (for testing).
async fn supervisor_on_crash(
    State(state): State<Arc<AppState>>,
    Json(req): Json<SupervisorSuspendReq>,
) -> Json<serde_json::Value> {
    state.supervisor_state.mark_dead(&req.name, "external_trigger");
    Json(serde_json::json!({
        "marked_dead": req.name,
        "ts": chrono::Utc::now().timestamp(),
        "next_action": "next supervisor tick (<= 15s) will attempt restart after 60s cooldown"
    }))
}

/// GET /v1/ikaros/supervisor/crash-log/{name} — 读崩溃 log.
async fn supervisor_crash_log(
    State(state): State<Arc<AppState>>,
    Path(name): Path<String>,
) -> Json<serde_json::Value> {
    let log = state.supervisor_state.crash_log(&name);
    Json(serde_json::json!({
        "name": name,
        "ts": chrono::Utc::now().timestamp(),
        "events": log,
        "count": log.len(),
    }))
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
            // W3 fix: stream response body instead of buffering entire body to memory
            // (was resp.bytes().await — caused high memory for /v1/embeddings etc.)
            let stream = TryStreamExt::map_err(resp.bytes_stream(), io::Error::other);
            let body = axum::body::Body::from_stream(stream);
            let mut response = Response::new(body);
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

    // Load per-client access control state from disk (C1)
    state.access_control.load().await;
    info!("access-control loaded");

    // Start background health monitor
    state.start_health_monitor();
    state.start_supervisor();

    // Update PATIENCE readiness flags and start heartbeat loop
    state.update_patience_readiness();
    state.start_patience_loop();

    // Initial health check (wait a moment for first refresh)
    {
        let pool = state.worker_pool.clone();
        let client = state.http_client.clone();
        pool.refresh(&client).await;
        // Sync loaded locals to access_control (so list_models snapshot is accurate)
        let mut loaded = Vec::new();
        for w in pool.workers.read().await.iter() {
            if matches!(w.upstream_type, UpstreamType::Local) && w.alive {
                loaded.extend(w.models.iter().cloned());
            }
        }
        state.access_control.sync_loaded_locals(loaded).await;
    }

    // C1: warmup — if last_successful_local set, trigger load (fire-and-forget)
    if let Some(model) = state.access_control.startup_warmup_hint().await {
        info!("access-control: warmup hint = {}", model);
        // 实际的 model load 由 /v1/models/load 路由触发, 这里只 hint
        // (避免在启动时阻塞, webui/pet 第一次请求时会自动 swap)
    }

    // Pre-warm cogno layer + soul loader (blocking Lazy init during startup,
    // not on first user request, which would block the tokio runtime thread).
    {
        let axiom_len = soul_loader::get_soul_injection().len();
        // _fetch_geo_sync may take up to 5s if no network; timeout is acceptable at startup
        let cogno_future = tokio::task::spawn_blocking(|| cogno_layer::enrich("prewarm"));
        let cogno_text = match tokio::time::timeout(Duration::from_secs(5), cogno_future).await {
            Ok(Ok(text)) => text,
            _ => {
                warn!("cogno layer pre-warm timed out — geo fetch may fail on first request");
                String::new()
            }
        };
        info!(
            axiom_len = axiom_len,
            cogno_len = cogno_text.len(),
            "cogno layer + soul loader pre-warmed"
        );
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
        .route("/v1/neuro/patience", post(neuro_set_patience_v2))  // Updated to sync with patience_state
        .route("/v1/neuro/patience/trigger", post(neuro_trigger_patience))
        .route("/v1/neuro/reset", post(neuro_reset))
        .route("/v1/neuro/memories", get(neuro_list_memories))
        .route("/v1/neuro/memory/add", post(neuro_add_memory))
        .route("/v1/neuro/proactive", get(neuro_proactive))
        // External message injection (Twitch/Discord reserved)
        .route("/v1/external/message", post(external_message_inject))
        .route("/v1/external/status", get(external_status))
        // Ikaros endpoints
        .route("/v1/liveness", get(liveness))
        .route("/v1/llama/restart", post(llama_restart))
        .route("/v1/ikaros/last-session", get(icarus_last_session))
        .route("/v1/ikaros/memories", get(icarus_memories))
        .route("/v1/ikaros/awake-briefing", get(icarus_awake_briefing))
        .route("/v1/ikaros/active-session", get(icarus_active_session))
        .route("/v1/ikaros/session/{id}/tail", get(icarus_session_tail))
        .route("/v1/ikaros/session/{id}/resume-context", post(icarus_resume_context))
        // Supervisor (哥哥 6-29 axiom: suspend + crash_log)
        .route("/v1/ikaros/supervisor/list", get(supervisor_list))
        .route("/v1/ikaros/supervisor/suspend", post(supervisor_suspend))
        .route("/v1/ikaros/supervisor/resume", post(supervisor_resume))
        .route("/v1/ikaros/supervisor/on-crash", post(supervisor_on_crash))
        .route("/v1/ikaros/supervisor/crash-log/{name}", get(supervisor_crash_log))
        // Session persistence (Part B — ikaros chat history)
        .route("/v1/sessions", post(create_session))
        .route("/v1/sessions", get(sessions_list))
        .route("/v1/sessions/{id}/messages", post(append_session_message))
        .route("/v1/sessions/{id}/messages", get(get_session_messages))
        .route("/v1/sessions/{id}", delete(delete_session))
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
    info!("upstream workers: {:?} (local) + {} cloud", get_upstream_urls(), load_cloud_upstreams().len());
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
