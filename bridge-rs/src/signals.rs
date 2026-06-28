//! Ikaros Signals — global signal bus singleton (Neuro 风格)
//! All modules read/write this; never directly coupled.

use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Message {
    pub role: String,
    pub content: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteMessage {
    pub source: String,
    pub user: String,
    pub content: String,
    pub timestamp: f64,
}

#[derive(Debug, Clone)]
pub struct IkarosSignals {
    // Control
    pub terminate: bool,
    // System ready
    pub stt_ready: bool,
    pub tts_ready: bool,
    pub llm_ready: bool,
    // Speaking state
    pub human_speaking: bool,
    pub ai_thinking: bool,
    pub ai_speaking: bool,
    // Message triggers
    pub new_message: bool,
    pub last_message_time: f64,
    // History (Neuro history)
    pub history: VecDeque<Message>,
    // Ikaros-specific
    pub context: serde_json::Value,
    pub patience: f64,
    pub sio_queue: VecDeque<serde_json::Value>,
    pub time_since_last_message: f64,
    // Pet state
    pub pet_visible: bool,
    pub pet_mode: String,
    // Remote messages
    pub recent_remote_messages: VecDeque<RemoteMessage>,
}

impl IkarosSignals {
    pub fn new() -> Self {
        Self {
            terminate: false,
            stt_ready: false,
            tts_ready: false,
            llm_ready: false,
            human_speaking: false,
            ai_thinking: false,
            ai_speaking: false,
            new_message: false,
            last_message_time: now(),
            history: VecDeque::with_capacity(100),
            context: serde_json::json!({}),
            patience: PATIENCE_DEFAULT,
            sio_queue: VecDeque::with_capacity(50),
            time_since_last_message: 0.0,
            pet_visible: true,
            pet_mode: "continuous".to_string(),
            recent_remote_messages: VecDeque::with_capacity(20),
        }
    }

    pub fn mark_new_message(&mut self, role: &str, content: &str) {
        if content.trim().is_empty() {
            return;
        }
        self.history.push_back(Message {
            role: role.to_string(),
            content: content.to_string(),
        });
        self.last_message_time = now();
        if role == "user" && !self.ai_speaking {
            self.new_message = true;
        }
    }

    pub fn update_time_since_last(&mut self) {
        self.time_since_last_message = now() - self.last_message_time;
    }
}

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

// Global singleton via lazy_static-like pattern
use once_cell::sync::Lazy;
pub static IKAROS: Lazy<Arc<Mutex<IkarosSignals>>> = Lazy::new(|| {
    Arc::new(Mutex::new(IkarosSignals::new()))
});

// === Neuro-style constants ===
pub const AI_NAME: &str = "伊卡洛斯";
pub const HOST_NAME: &str = "哥哥";
pub const PATIENCE_DEFAULT: f64 = 30.0;
pub const MEMORY_REFLECT_EVERY: usize = 20;
pub const MEMORY_RECALL_COUNT: usize = 5;

pub const MEMORY_PROMPT: &str = "\n请将以上对话中值得伊卡洛斯长期记住的信息总结为最多 3 个 Q&A 对。\n每对格式: {qa}Q: 提问\\nA: 回答{qa}\n只返回 Q&A 对，不要其他解释。\n";
