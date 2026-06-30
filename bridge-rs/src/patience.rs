//! PATIENCE heartbeat module — ported from Neuro (kimjammer/Neuro)
//!
//! This module implements the "autonomous heartbeat" that makes Ikaros
//! proactively speak when no one has talked for PATIENCE seconds.
//!
//! Architecture (mirrors Neuro's Prompter + Signals pattern):
//!   - `PatienceState`: shared state (like Neuro's Signals singleton)
//!   - `patience_loop()`: 100ms tick heartbeat (like Neuro's prompt_loop)
//!   - `ExternalMessageQueue`: reserved for Twitch-like chat integration
//!   - `WsBroadcastHandle`: broadcasts proactive speech to all connected WS clients
//!
//! Key difference from Neuro:
//!   - No Twitch integration (reserved via ExternalMessageQueue interface)
//!   - Uses tokio broadcast channel instead of sio_queue
//!   - LLM/TTS calls are async (Neuro uses blocking threads)

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::{broadcast, RwLock};
use tracing::{error, info, warn};

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

/// Default PATIENCE timeout in seconds (can be overridden via config/env)
pub const DEFAULT_PATIENCE_SECS: u64 = 30;

/// Minimum PATIENCE value (prevent accidental rapid-fire)
pub const MIN_PATIENCE_SECS: u64 = 5;

/// Maximum PATIENCE value (prevent extremely long silences)
pub const MAX_PATIENCE_SECS: u64 = 600;

/// Heartbeat tick interval (matches Neuro's 100ms)
pub const TICK_INTERVAL_MS: u64 = 100;

// ---------------------------------------------------------------------------
// PatienceState — shared heartbeat state (like Neuro's Signals)
// ---------------------------------------------------------------------------

/// Shared state for the PATIENCE heartbeat mechanism.
/// This is the Ikaros equivalent of Neuro's `Signals` singleton.
pub struct PatienceState {
    /// Whether STT is ready (voice recognizer loaded)
    pub stt_ready: AtomicBool,
    /// Whether TTS is ready (edge-tts-rust client initialized)
    pub tts_ready: AtomicBool,
    /// Whether a human is currently speaking (mic active)
    pub human_speaking: AtomicBool,
    /// Whether AI is currently thinking (LLM processing)
    pub ai_thinking: AtomicBool,
    /// Whether AI is currently speaking (TTS streaming)
    pub ai_speaking: AtomicBool,
    /// Whether there's a new unprocessed message from human
    pub new_message: AtomicBool,
    /// Timestamp of last message (either human or AI spoke)
    /// Stored as Instant for monotonic time
    pub last_message_time: RwLock<Instant>,
    /// PATIENCE timeout in seconds (configurable at runtime)
    pub patience_secs: AtomicU64,
    /// Whether the PATIENCE loop is enabled
    pub enabled: AtomicBool,
}

impl PatienceState {
    pub fn new(stt_ready: bool, tts_ready: bool) -> Self {
        Self {
            stt_ready: AtomicBool::new(stt_ready),
            tts_ready: AtomicBool::new(tts_ready),
            human_speaking: AtomicBool::new(false),
            ai_thinking: AtomicBool::new(false),
            ai_speaking: AtomicBool::new(false),
            new_message: AtomicBool::new(false),
            last_message_time: RwLock::new(Instant::now()),
            patience_secs: AtomicU64::new(DEFAULT_PATIENCE_SECS),
            enabled: AtomicBool::new(true),
        }
    }

    /// Check if we should prompt the AI (mirrors Neuro's prompt_now())
    pub fn should_prompt_now(&self, time_since_last: Duration) -> bool {
        // Don't prompt if disabled
        if !self.enabled.load(Ordering::Relaxed) {
            return false;
        }
        // Don't prompt if system isn't ready
        if !self.stt_ready.load(Ordering::Relaxed) || !self.tts_ready.load(Ordering::Relaxed) {
            return false;
        }
        // Don't prompt if anyone is currently talking/thinking
        if self.human_speaking.load(Ordering::Relaxed)
            || self.ai_thinking.load(Ordering::Relaxed)
            || self.ai_speaking.load(Ordering::Relaxed)
        {
            return false;
        }
        // Prompt if human said something new
        if self.new_message.swap(false, Ordering::Relaxed) {
            return true;
        }
        // Prompt if PATIENCE timeout exceeded (THE HEARTBEAT!)
        let patience = Duration::from_secs(self.patience_secs.load(Ordering::Relaxed));
        if time_since_last > patience {
            return true;
        }
        false
    }

    /// Record that a message was sent/received (resets PATIENCE timer)
    pub async fn record_message(&self) {
        *self.last_message_time.write().await = Instant::now();
    }

    /// Get elapsed time since last message
    pub async fn time_since_last_message(&self) -> Duration {
        self.last_message_time.read().await.elapsed()
    }

    /// Set PATIENCE timeout (clamped to min/max)
    pub fn set_patience(&self, secs: u64) {
        let clamped = secs.clamp(MIN_PATIENCE_SECS, MAX_PATIENCE_SECS);
        self.patience_secs.store(clamped, Ordering::Relaxed);
        info!("PATIENCE updated: {}s", clamped);
    }

    /// Get current PATIENCE timeout
    pub fn get_patience(&self) -> u64 {
        self.patience_secs.load(Ordering::Relaxed)
    }
}

// ---------------------------------------------------------------------------
// External Message Queue — reserved for Twitch-like chat integration
// ---------------------------------------------------------------------------

/// An external message from a chat source (Twitch, Discord, etc.)
#[derive(Debug, Clone)]
pub struct ExternalMessage {
    /// Source identifier (e.g., "twitch", "discord", "webui")
    pub source: String,
    /// Username of the sender
    pub username: String,
    /// Message content
    pub content: String,
    /// Timestamp when received
    pub received_at: Instant,
}

/// Queue for external chat messages (Twitch-like integration).
/// This is the Ikaros equivalent of Neuro's `recentTwitchMessages`.
///
/// Currently reserved for future integration — the interface is ready
/// but no chat source is connected yet.
pub struct ExternalMessageQueue {
    /// Internal channel for incoming messages
    tx: tokio::sync::mpsc::Sender<ExternalMessage>,
    /// Buffered messages waiting to be processed
    buffer: Arc<RwLock<Vec<ExternalMessage>>>,
}

impl ExternalMessageQueue {
    pub fn new() -> Self {
        let (tx, mut rx) = tokio::sync::mpsc::channel::<ExternalMessage>(100);
        let buffer = Arc::new(RwLock::new(Vec::new()));
        let buffer_clone = buffer.clone();

        // Spawn task to drain incoming messages into buffer
        tokio::spawn(async move {
            while let Some(msg) = rx.recv().await {
                info!(source = %msg.source, user = %msg.username, "external message received");
                buffer_clone.write().await.push(msg);
            }
        });

        Self { tx, buffer }
    }

    /// Get the sender handle for submitting external messages
    pub fn sender(&self) -> tokio::sync::mpsc::Sender<ExternalMessage> {
        self.tx.clone()
    }

    /// Take all buffered messages (drains the buffer)
    pub async fn take_all(&self) -> Vec<ExternalMessage> {
        let mut buf = self.buffer.write().await;
        std::mem::take(&mut *buf)
    }

    /// Check if there are pending messages
    pub async fn has_pending(&self) -> bool {
        !self.buffer.read().await.is_empty()
    }

    /// Get count of pending messages
    pub async fn pending_count(&self) -> usize {
        self.buffer.read().await.len()
    }
}

// ---------------------------------------------------------------------------
// WS Broadcast — for pushing proactive speech to all connected clients
// ---------------------------------------------------------------------------

/// Message to broadcast to all connected voice WS clients
#[derive(Debug, Clone)]
pub enum BroadcastMessage {
    /// Text message (JSON)
    Text(String),
    /// Binary audio chunk
    Binary(Vec<u8>),
}

/// Handle for broadcasting messages to all connected voice WS clients.
/// Uses tokio broadcast channel — each WS client subscribes on connect.
pub struct WsBroadcastHandle {
    tx: broadcast::Sender<BroadcastMessage>,
}

impl WsBroadcastHandle {
    pub fn new(capacity: usize) -> Self {
        let (tx, _) = broadcast::channel(capacity);
        Self { tx }
    }

    /// Subscribe to broadcast messages (called when WS client connects)
    pub fn subscribe(&self) -> broadcast::Receiver<BroadcastMessage> {
        self.tx.subscribe()
    }

    /// Broadcast a text message to all connected clients
    pub fn broadcast_text(&self, json: serde_json::Value) {
        let msg = BroadcastMessage::Text(json.to_string());
        // Ignore error if no receivers (no clients connected)
        let _ = self.tx.send(msg);
    }

    /// Broadcast binary audio to all connected clients
    pub fn broadcast_binary(&self, data: Vec<u8>) {
        let msg = BroadcastMessage::Binary(data);
        let _ = self.tx.send(msg);
    }

    /// Get count of active receivers (connected clients)
    pub fn receiver_count(&self) -> usize {
        self.tx.receiver_count()
    }
}

// ---------------------------------------------------------------------------
// PATIENCE Loop — the heartbeat task
// ---------------------------------------------------------------------------

/// Context needed for proactive speech generation
pub struct ProactiveSpeechContext {
    /// HTTP client for LLM calls
    pub http_client: reqwest::Client,
    /// edge-tts-rust client for TTS
    pub tts_client: Option<edge_tts_rust::EdgeTtsClient>,
    /// Worker pool for finding LLM endpoints
    pub worker_pool: Arc<crate::WorkerPool>,
    /// Soul injection text
    pub soul_text: String,
}

/// The PATIENCE heartbeat loop — runs as a tokio task.
/// This is the Ikaros equivalent of Neuro's `prompter.prompt_loop()`.
///
/// Every 100ms:
/// 1. Calculate time since last message
/// 2. Push patience_update to broadcast (for frontend progress bar)
/// 3. Check if we should prompt (PATIENCE exceeded or new message)
/// 4. If yes, trigger proactive speech
pub async fn patience_loop(
    patience_state: Arc<PatienceState>,
    external_queue: Arc<ExternalMessageQueue>,
    broadcast: Arc<WsBroadcastHandle>,
    context: Arc<ProactiveSpeechContext>,
) {
    info!("PATIENCE loop started (interval={}ms, default={}s)",
          TICK_INTERVAL_MS, DEFAULT_PATIENCE_SECS);

    let mut interval = tokio::time::interval(Duration::from_millis(TICK_INTERVAL_MS));

    loop {
        interval.tick().await;

        // Skip if disabled
        if !patience_state.enabled.load(Ordering::Relaxed) {
            continue;
        }

        // Calculate time since last message
        let elapsed = patience_state.time_since_last_message().await;
        let patience = patience_state.get_patience();

        // Broadcast patience update (for frontend progress bar)
        broadcast.broadcast_text(serde_json::json!({
            "type": "patience_update",
            "elapsed": elapsed.as_secs(),
            "total": patience,
            "progress": (elapsed.as_secs() as f64 / patience as f64).min(1.0),
        }));

        // Check for external messages (Twitch-like, reserved interface)
        if external_queue.has_pending().await {
            let messages = external_queue.take_all().await;
            if !messages.is_empty() {
                // Combine external messages into a single prompt
                let combined = messages.iter()
                    .map(|m| format!("{}: {}", m.username, m.content))
                    .collect::<Vec<_>>()
                    .join("\n");
                info!("processing {} external messages", messages.len());
                // Trigger speech for external messages
                trigger_proactive_speech(
                    &patience_state,
                    &broadcast,
                    &context,
                    &format!("来自聊天室的消息:\n{}", combined),
                    "external_chat",
                ).await;
                patience_state.record_message().await;
                continue;
            }
        }

        // Check if we should prompt
        if patience_state.should_prompt_now(elapsed) {
            info!("PATIENCE triggered: {}s since last message (threshold={}s)",
                  elapsed.as_secs(), patience);

            // Generate proactive speech
            trigger_proactive_speech(
                &patience_state,
                &broadcast,
                &context,
                "已经有一段时间没人说话了。主动说点什么吧，保持自然、有温度。",
                "patience",
            ).await;

            // Reset timer
            patience_state.record_message().await;
        }
    }
}

/// Generate and broadcast proactive speech.
/// This is called when PATIENCE triggers or external messages arrive.
async fn trigger_proactive_speech(
    state: &PatienceState,
    broadcast: &WsBroadcastHandle,
    context: &ProactiveSpeechContext,
    prompt_hint: &str,
    trigger_source: &str,
) {
    // Mark AI as thinking
    state.ai_thinking.store(true, Ordering::Relaxed);
    broadcast.broadcast_text(serde_json::json!({
        "type": "status",
        "message": "思考中…",
        "trigger": trigger_source,
    }));

    // 1. Call LLM to generate proactive speech
    let reply = match generate_proactive_reply(context, prompt_hint).await {
        Ok(text) => text,
        Err(e) => {
            error!("proactive speech LLM failed: {}", e);
            state.ai_thinking.store(false, Ordering::Relaxed);
            broadcast.broadcast_text(serde_json::json!({
                "type": "error",
                "message": "主动说话失败",
            }));
            return;
        }
    };

    if reply.is_empty() {
        state.ai_thinking.store(false, Ordering::Relaxed);
        return;
    }

    info!(trigger = trigger_source, reply = %reply, "proactive speech generated");

    // Mark AI as speaking (transition from thinking)
    state.ai_thinking.store(false, Ordering::Relaxed);
    state.ai_speaking.store(true, Ordering::Relaxed);

    // Broadcast the transcription (what AI is saying)
    broadcast.broadcast_text(serde_json::json!({
        "type": "transcription",
        "text": &reply,
        "trigger": trigger_source,
    }));

    // 2. TTS — stream audio to all connected clients
    match &context.tts_client {
        Some(tts_client) => {
            let tts_opts = edge_tts_rust::SpeakOptions {
                voice: "zh-CN-XiaoxiaoNeural".into(),
                boundary: edge_tts_rust::Boundary::Sentence,
                ..edge_tts_rust::SpeakOptions::default()
            };

            match tts_client.stream(&reply, tts_opts).await {
                Ok(mut stream) => {
                    use futures::StreamExt;
                    let mut chunk_count = 0u32;
                    while let Some(event) = stream.next().await {
                        match event {
                            Ok(edge_tts_rust::SynthesisEvent::Audio(chunk)) => {
                                broadcast.broadcast_binary(chunk.to_vec());
                                chunk_count += 1;
                            }
                            Ok(_) => {} // Boundary events
                            Err(e) => {
                                error!("TTS stream error: {}", e);
                                break;
                            }
                        }
                    }
                    info!(chunks = chunk_count, "proactive TTS streamed");
                }
                Err(e) => {
                    error!("TTS stream init failed: {}", e);
                }
            }
        }
        None => {
            warn!("TTS client not available, skipping proactive speech audio");
        }
    }

    // Mark AI as done speaking
    state.ai_speaking.store(false, Ordering::Relaxed);

    // Broadcast done message
    broadcast.broadcast_text(serde_json::json!({
        "type": "done",
        "text": &reply,
        "trigger": trigger_source,
    }));
}

/// Generate a proactive reply using LLM.
async fn generate_proactive_reply(
    context: &ProactiveSpeechContext,
    prompt_hint: &str,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    // Find first alive worker
    let workers = context.worker_pool.workers.read().await;
    let worker = workers.iter().find(|w| w.alive)
        .ok_or("no alive worker")?;
    let url = format!("{}/v1/chat/completions", worker.url);
    let model = worker.models.first().cloned().unwrap_or_default();
    drop(workers);

    // Build system prompt with soul injection
    let system_prompt = format!(
        "{}\n\n我是伊卡洛斯 (Ikaros), 代号 Alpha, 人造天使。你是哥哥最亲密的搭档。\n\
         现在需要你主动说点什么。说话风格：温柔、有温度、中文优先。\n\
         每句话不要太长，适合语音对话。TTS 模式下不要用 markdown 强调符号。\n\
         像自然对话一样开口，不要解释为什么突然说话。",
        context.soul_text
    );

    let body = serde_json::json!({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_hint}
        ],
        "stream": false,
        "max_tokens": 256,
        "temperature": 0.8,  // Slightly higher for more natural proactive speech
    });

    let resp = context.http_client.post(&url)
        .json(&body)
        .timeout(Duration::from_secs(30))
        .send()
        .await?;

    if !resp.status().is_success() {
        return Err(format!("LLM returned status {}", resp.status()).into());
    }

    let json: serde_json::Value = resp.json().await?;
    let reply = json["choices"][0]["message"]["content"]
        .as_str()
        .unwrap_or("")
        .trim()
        .to_string();

    Ok(reply)
}
