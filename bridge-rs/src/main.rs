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
use tokio::sync::{RwLock, Semaphore};
use tracing::{error, info, warn};

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
// Shared state
// ---------------------------------------------------------------------------

struct AppState {
    http_client: reqwest::Client,
    chat_semaphore: Arc<Semaphore>,
    request_counter: AtomicU64,
    start_time: Instant,
    worker_pool: Arc<WorkerPool>,
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

#[derive(Deserialize)]
struct ChatRequest {
    #[serde(default)]
    stream: bool,
    model: Option<String>,
    // Pass through all other fields as-is
    #[serde(flatten)]
    extra: serde_json::Value,
}

/// POST /v1/chat/completions — proxy to llama-server worker
async fn chat_completions(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(body): Json<serde_json::Value>,
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
        forward_streaming_response(resp)
    } else {
        match resp.text().await {
            Ok(body_text) => {
                info!(req_id = req_id, elapsed_ms = elapsed, bytes = body_text.len(), "json");
                (StatusCode::OK, [("content-type", "application/json")], body_text).into_response()
            }
            Err(e) => {
                error!(req_id = req_id, error = %e, "read body failed");
                StatusCode::INTERNAL_SERVER_ERROR.into_response()
            }
        }
    }
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

    // Message loop
    while let Some(msg) = socket.recv().await {
        match msg {
            Ok(Message::Text(text)) => {
                // Parse incoming message
                match serde_json::from_str::<serde_json::Value>(&text) {
                    Ok(parsed) => {
                        let msg_type = parsed
                            .get("type")
                            .and_then(|v| v.as_str())
                            .unwrap_or("unknown");

                        match msg_type {
                            "transcribe" => {
                                // TODO: forward audio to whisper endpoint
                                let resp = serde_json::json!({
                                    "type": "transcription",
                                    "text": "[Rust bridge PoC — not yet implemented]",
                                });
                                let _ = socket.send(Message::Text(resp.to_string().into())).await;
                            }
                            "tts" => {
                                // TODO: forward to TTS endpoint
                                let resp = serde_json::json!({
                                    "type": "tts",
                                    "status": "not_implemented",
                                });
                                let _ = socket.send(Message::Text(resp.to_string().into())).await;
                            }
                            "ping" => {
                                let resp = serde_json::json!({"type": "pong"});
                                let _ = socket.send(Message::Text(resp.to_string().into())).await;
                            }
                            _ => {
                                let resp = serde_json::json!({
                                    "type": "error",
                                    "message": format!("unknown message type: {}", msg_type),
                                });
                                let _ = socket.send(Message::Text(resp.to_string().into())).await;
                            }
                        }
                    }
                    Err(e) => {
                        let resp = serde_json::json!({
                            "type": "error",
                            "message": format!("invalid JSON: {}", e),
                        });
                        let _ = socket.send(Message::Text(resp.to_string().into())).await;
                    }
                }
            }
            Ok(Message::Binary(_data)) => {
                // TODO: handle audio binary data
                let resp = serde_json::json!({
                    "type": "ack",
                    "message": "binary data received (not yet processed)",
                });
                let _ = socket.send(Message::Text(resp.to_string().into())).await;
            }
            Ok(Message::Close(_)) => {
                info!("voice WebSocket closed by client");
                break;
            }
            Err(e) => {
                warn!(error = %e, "voice WebSocket error");
                break;
            }
            _ => {}
        }
    }

    info!("voice WebSocket session ended");
}

// ---------------------------------------------------------------------------
// Icarus endpoints (minimal stubs — Phase 1)
// ---------------------------------------------------------------------------

/// GET /v1/liveness — composite liveness check
async fn liveness(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    let workers = state.worker_pool.workers.read().await;
    let alive_count = workers.iter().filter(|w| w.alive).count();
    let status = if alive_count > 0 { "ok" } else { "dead" };
    Json(serde_json::json!({
        "status": status,
        "ts": chrono::Utc::now().timestamp(),
        "local": {
            "alive": alive_count > 0,
            "workers": alive_count,
        },
        "summary": format!("{}/{} workers alive", alive_count, workers.len()),
    }))
}

/// POST /v1/llama/restart — trigger llama-server restart via supervisor
async fn llama_restart() -> Json<serde_json::Value> {
    // TODO: shell out to supervisor to restart llama-server
    // For now, return a stub response
    Json(serde_json::json!({
        "status": "ok",
        "message": "llama restart not yet implemented in Rust bridge (Phase 1 stub)",
    }))
}

/// GET /v1/icarus/last-session — return most recent memory note
async fn icarus_last_session() -> Json<serde_json::Value> {
    // TODO: read from data/icarus-memory/*.md
    Json(serde_json::json!({
        "found": false,
        "reason": "Rust bridge Phase 1 — icarus memory endpoints not yet implemented",
    }))
}

/// GET /v1/icarus/memories — return memory index
async fn icarus_memories() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "count": 0,
        "memory_dir": "data/icarus-memory",
        "notes": [],
    }))
}

/// GET /v1/icarus/awake-briefing — return briefing stub
async fn icarus_awake_briefing() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "status": "ok",
        "briefing": "Rust bridge Phase 1 — awake briefing not yet implemented",
    }))
}

// ---------------------------------------------------------------------------
// Signals / Modules endpoints (minimal stubs — Phase 1)
// ---------------------------------------------------------------------------

/// GET /v1/signals — signals snapshot stub
async fn signals_snapshot() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "signals": [],
        "count": 0,
    }))
}

/// GET /v1/signals/recent — recent signals stub
async fn signals_recent() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "signals": [],
        "count": 0,
    }))
}

/// GET /v1/signals/stats — signal stats stub
async fn signals_stats() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "stats": {},
    }))
}

/// GET /v1/modules — list modules stub
async fn list_modules() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "modules": [],
        "count": 0,
    }))
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
        // Health check
        .route("/health", get(health))
        // OpenAI-compatible endpoints
        .route("/v1/chat/completions", post(chat_completions))
        .route("/v1/models", get(list_models))
        // Voice WebSocket
        .route("/v1/voice/ws", get(voice_ws))
        // Icarus endpoints
        .route("/v1/liveness", get(liveness))
        .route("/v1/llama/restart", post(llama_restart))
        .route("/v1/icarus/last-session", get(icarus_last_session))
        .route("/v1/icarus/memories", get(icarus_memories))
        .route("/v1/icarus/awake-briefing", get(icarus_awake_briefing))
        // Signals / Modules
        .route("/v1/signals", get(signals_snapshot))
        .route("/v1/signals/recent", get(signals_recent))
        .route("/v1/signals/stats", get(signals_stats))
        .route("/v1/modules", get(list_modules))
        // Catch-all for other /v1/* paths
        .fallback(proxy_v1)
        .with_state(state);

    let addr = SocketAddr::from(([127, 0, 0, 1], BRIDGE_PORT));

    info!("hermes-bridge-rs v{} starting", env!("CARGO_PKG_VERSION"));
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
