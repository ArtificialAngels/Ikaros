//! memory_writer.rs — 自动监听 chat, 归约 user 消息, 写 mem0
//!
//! 设计 (Phase 1.1, 2026-07-01 伊卡洛斯 ship):
//!
//!   user 说话
//!     ↓
//!   chat_completions handler → enqueue (user_id, text)  ← tokio::spawn, 不阻塞
//!     ↓
//!   WriterState.queue (Mutex<VecDeque<PendingMessage>>)
//!     ↓
//!   显式触发 (含"记住"等词)? → 立即 process  → 调本地 LLM 归约 → 写 mem0
//!   隐式? → 等 5 分钟窗口累积 → process    → 调本地 LLM 归约 → 写 mem0
//!     ↓
//!   失败: 5xx / 超时 → log, 不重试 (避免风暴)
//!
//! 跟哥哥 7-1 拍板一致:
//!   - DeepSeek-R1-Distill-1.5B Q4 (本地, 8588, 隐私)
//!   - toggle 默认 OFF (控制权优先)
//!   - 5 分钟累积窗口 (隐式)
//!   - 显式触发词: 记住 / 记下来 / don't forget / save
//!
//! 不依赖: 跟 memory.rs (Qdrant 直连) 解耦, 通过 memory::add_memory() 写

use once_cell::sync::Lazy;
use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use std::time::{Duration, Instant};

// ---------------------------------------------------------------------------
// 配置
// ---------------------------------------------------------------------------

/// 本地 LLM 配置 (归约专用)
#[derive(Debug, Clone, Deserialize)]
pub struct LocalLlmConfig {
    pub enabled: bool,
    pub base_url: String,
    pub model: String,
    #[serde(default = "default_timeout")]
    pub timeout_ms: u64,
    #[serde(default = "default_window")]
    pub implicit_window_sec: u64,
    #[serde(default = "default_similarity")]
    pub merge_similarity_threshold: f64,
    #[serde(default)]
    pub fallback_to_cloud: bool,
    #[serde(default = "default_max_chars")]
    pub max_input_chars: usize,
    #[serde(default = "default_max_tokens")]
    pub max_output_tokens: u32,
    #[serde(default = "default_explicit_keywords")]
    pub explicit_keywords: Vec<String>,
}

fn default_timeout() -> u64 { 5000 }
fn default_window() -> u64 { 300 }      // 5 min
fn default_similarity() -> f64 { 0.92 }
fn default_max_chars() -> usize { 2000 }
fn default_max_tokens() -> u32 { 800 }  // P1.2: 提到 800, 给 R1 thinking + JSON 留空间
fn default_explicit_keywords() -> Vec<String> {
    vec!["记住".into(), "记下来".into(), "don't forget".into(), "save".into()]
}

impl Default for LocalLlmConfig {
    fn default() -> Self {
        // P1.2 (2026-07-01 23:39): port 8588 was orphaned by a Windows zombie listener (PID 11012, 拒绝访问).
        // 改用 :8589 (next free port). If you change, also update bin/memory-writer-llm-serve.bat.
        Self {
            enabled: false,  // 默认 OFF, 跟哥哥 6-30 axiom "控制权优先" 一致
            base_url: "http://127.0.0.1:8589/v1".to_string(),
            model: "DeepSeek-R1-Distill-Qwen-1.5B-q4".to_string(),
            timeout_ms: 5000,
            implicit_window_sec: 300,
            merge_similarity_threshold: 0.92,
            fallback_to_cloud: false,
            max_input_chars: 2000,
            max_output_tokens: 300,
            explicit_keywords: default_explicit_keywords(),
        }
    }
}

/// 全局配置 (load_config() 加载, 跟 memory::config() 同模式)
static CONFIG: Lazy<Mutex<LocalLlmConfig>> = Lazy::new(|| Mutex::new(LocalLlmConfig::default()));

pub fn config() -> LocalLlmConfig {
    CONFIG.lock().clone()
}

pub fn set_config(cfg: LocalLlmConfig) {
    *CONFIG.lock() = cfg;
}

pub fn is_enabled() -> bool {
    CONFIG.lock().enabled
}

pub fn set_enabled(b: bool) {
    CONFIG.lock().enabled = b;
}

// ---------------------------------------------------------------------------
// HTTP 客户端 (本地 LLM 调归约)
// ---------------------------------------------------------------------------

static HTTP: Lazy<reqwest::Client> = Lazy::new(|| {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .expect("memory_writer: failed to build HTTP client")
});

// ---------------------------------------------------------------------------
// 累积队列
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct PendingMessage {
    pub user_id: String,
    pub text: String,
    pub enqueued_at: Instant,
    pub is_explicit: bool,
}

/// per-user 累积状态
#[derive(Debug, Default)]
struct UserState {
    queue: VecDeque<PendingMessage>,
    #[allow(dead_code)]  // 留给未来 metric (距离上次 flush 多久)
    last_flush: Option<Instant>,
}

#[derive(Default)]
pub struct WriterState {
    users: HashMap<String, UserState>,
}

impl WriterState {
    pub fn new() -> Self { Self::default() }

    /// 入队新消息, 返回立即 flush 标记
    pub fn enqueue(&mut self, user_id: &str, text: &str) -> EnqueueDecision {
        let cfg = config();
        let is_explicit = contains_explicit_keyword(text, &cfg.explicit_keywords);

        let msg = PendingMessage {
            user_id: user_id.to_string(),
            text: text.to_string(),
            enqueued_at: Instant::now(),
            is_explicit,
        };

        let state = self.users.entry(user_id.to_string()).or_default();
        state.queue.push_back(msg);

        // 限制队列长度 (避免单 user 累积无限)
        const MAX_QUEUE_PER_USER: usize = 50;
        while state.queue.len() > MAX_QUEUE_PER_USER {
            state.queue.pop_front();
        }

        EnqueueDecision {
            is_explicit,
            queue_len: state.queue.len(),
            should_flush_now: is_explicit,  // 显式 → 立即 flush
        }
    }

    /// 取出所有过期隐式消息 (超过 implicit_window_sec) 准备 flush
    pub fn drain_implicit_due(&mut self) -> Vec<PendingMessage> {
        let cfg = config();
        let window = Duration::from_secs(cfg.implicit_window_sec);
        let now = Instant::now();
        let mut drained = Vec::new();

        for state in self.users.values_mut() {
            let mut keep = VecDeque::new();
            for msg in state.queue.drain(..) {
                if !msg.is_explicit && now.duration_since(msg.enqueued_at) >= window {
                    drained.push(msg);
                } else {
                    keep.push_back(msg);
                }
            }
            state.queue = keep;
        }
        drained
    }

    /// 清空某 user 的整个队列 (显式 flush 后)
    pub fn drain_user(&mut self, user_id: &str) -> Vec<PendingMessage> {
        if let Some(state) = self.users.get_mut(user_id) {
            state.queue.drain(..).collect()
        } else {
            Vec::new()
        }
    }

    pub fn stats(&self) -> WriterStats {
        let total_queued: usize = self.users.values().map(|s| s.queue.len()).sum();
        let users_with_queue = self.users.values().filter(|s| !s.queue.is_empty()).count();
        WriterStats {
            enabled: is_enabled(),
            users_with_queue,
            total_queued,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct EnqueueDecision {
    pub is_explicit: bool,
    pub queue_len: usize,
    pub should_flush_now: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct WriterStats {
    pub enabled: bool,
    pub users_with_queue: usize,
    pub total_queued: usize,
}

pub static STATE: Lazy<Arc<Mutex<WriterState>>> = Lazy::new(|| Arc::new(Mutex::new(WriterState::new())));

fn contains_explicit_keyword(text: &str, keywords: &[String]) -> bool {
    let lower = text.to_lowercase();
    keywords.iter().any(|k| lower.contains(&k.to_lowercase()))
}

// ---------------------------------------------------------------------------
// 归约 (调本地 LLM)
// ---------------------------------------------------------------------------

#[derive(Debug, Serialize, Deserialize)]
struct ChatMessage {
    role: String,
    content: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct ChatRequest {
    model: String,
    messages: Vec<ChatMessage>,
    max_tokens: u32,
    temperature: f32,
    stream: bool,
}

#[derive(Debug, Serialize, Deserialize)]
struct ChatChoice {
    message: ChatMessage,
}

#[derive(Debug, Serialize, Deserialize)]
struct ChatResponse {
    choices: Vec<ChatChoice>,
}

/// 调本地 LLM 把 N 条对话归约为 1 条事实 JSON
pub async fn reduce_to_fact(messages: &[PendingMessage]) -> Result<String, String> {
    let cfg = config();

    if messages.is_empty() {
        return Err("no messages to reduce".to_string());
    }

    // 拼接输入, 限制长度
    let combined: String = messages.iter()
        .map(|m| m.text.as_str())
        .collect::<Vec<_>>()
        .join("\n");
    let combined = if combined.len() > cfg.max_input_chars {
        combined[..cfg.max_input_chars].to_string()
    } else {
        combined
    };

    // P1.2 (2026-07-01 23:50): 优化 prompt 适配 R1 distill thinking mode.
    //   - max_tokens 提到 800, 给 thinking + JSON 留足空间
    //   - prompt 显式说"不要思考", R1 模型在 qwen 模板下默认开 thinking
    //   - 加 few-shot example 防止模型偷懒只输出部分字段
    let system_prompt = r#"你是一个事实提取器。从用户的对话片段中提取值得长期记忆的事实, 直接输出 JSON, 不要任何解释, 不要思考, 不要 <think> 块。

输出格式 (严格 JSON, 全部 4 字段必填):
{"fact": "简洁的事实陈述 (中文, 1-2 句)", "category": "L2 或 L3", "confidence": 0.0 到 1.0, "halflife_days": 30 或 0}

字段说明:
- fact: 1-2 句中文事实
- category: L2 = 哥哥明确说过的事实; L3 = 我推断的模式
- confidence: 1.0 = 哥哥明确说的; 0.5-0.7 = 推断
- halflife_days: 30 = 普通事实; 0 = 永久事实 (哥哥的设备/习惯/家庭)

示例输出:
{"fact": "哥哥喜欢简洁中文", "category": "L2", "confidence": 1.0, "halflife_days": 0}

现在处理以下对话片段:"#;

    let req = ChatRequest {
        model: cfg.model.clone(),
        messages: vec![
            ChatMessage { role: "system".to_string(), content: system_prompt.to_string() },
            ChatMessage { role: "user".to_string(), content: combined },
        ],
        max_tokens: cfg.max_output_tokens.max(800),  // P1.2: 至少 800, 防止 thinking 占满
        temperature: 0.1,
        stream: false,
    };

    let url = format!("{}/chat/completions", cfg.base_url.trim_end_matches("/v1"));

    let resp = HTTP.post(&url)
        .json(&req)
        .send()
        .await
        .map_err(|e| format!("local LLM request failed: {e}"))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("local LLM HTTP {status}: {text}"));
    }

    let parsed: ChatResponse = resp.json().await
        .map_err(|e| format!("local LLM parse failed: {e}"))?;

    let content = parsed.choices.first()
        .map(|c| c.message.content.clone())
        .ok_or_else(|| "local LLM returned no choices".to_string())?;

    Ok(content)
}

// ---------------------------------------------------------------------------
// 公开 API: enqueue (主入口, 给 chat_completions handler 调)
// ---------------------------------------------------------------------------

/// chat_completions handler 调这个: 把 user 消息入队
///
/// 显式触发 → 立即 flush (spawn)
/// 隐式 → 入队, 等 5 分钟窗口
pub fn on_user_message(user_id: &str, text: &str) -> EnqueueDecision {
    if !is_enabled() {
        return EnqueueDecision { is_explicit: false, queue_len: 0, should_flush_now: false };
    }
    STATE.lock().enqueue(user_id, text)
}

/// 显式 flush 某 user 队列 (spawn 后调)
pub async fn flush_user(user_id: &str) -> Result<u32, String> {
    let msgs = STATE.lock().drain_user(user_id);
    if msgs.is_empty() {
        return Ok(0);
    }
    flush_messages(msgs).await
}

/// 隐式累积到窗口的 flush (tokio::spawn 周期跑)
pub async fn flush_due_implicit() -> Result<u32, String> {
    let msgs = STATE.lock().drain_implicit_due();
    if msgs.is_empty() {
        return Ok(0);
    }
    flush_messages(msgs).await
}

async fn flush_messages(msgs: Vec<PendingMessage>) -> Result<u32, String> {
    if msgs.is_empty() { return Ok(0); }

    // 按 user_id 聚合
    let mut by_user: HashMap<String, Vec<PendingMessage>> = HashMap::new();
    for m in msgs {
        by_user.entry(m.user_id.clone()).or_default().push(m);
    }

    let mut total_written = 0u32;
    for (user_id, msgs) in by_user {
        let combined_text = msgs.iter().map(|m| m.text.as_str()).collect::<Vec<_>>().join("\n");

        // ↓ Phase 2 P0 (2026-07-02): AutoCollector 前置过滤
        // 命中 → 才调 LLM 归约 (省 GPU); 未命中 → 直接 skip
        let collected = crate::auto_collector::AutoCollector::detect(
            &combined_text,
            &crate::auto_collector::CollectorContext::default(),
        );
        let Some(collected) = collected else {
            tracing::debug!(
                user_id = %user_id,
                "memory_writer: auto_collector skip (noise / no pattern)"
            );
            continue;
        };

        // ↓ Phase 2 P0: 去重检查 (LCS ratio > 0.85 视为重复)
        match crate::memory::is_duplicate(&collected.content, 0.85).await {
            Ok(true) => {
                tracing::debug!(
                    user_id = %user_id,
                    content = %collected.content.chars().take(50).collect::<String>(),
                    "memory_writer: auto_collector skip (duplicate)"
                );
                continue;
            }
            Ok(false) => {}
            Err(e) => {
                tracing::warn!(
                    user_id = %user_id,
                    error = %e,
                    "memory_writer: dedupe check failed (continuing, write anyway)"
                );
            }
        }

        let text = reduce_to_fact(&msgs).await?;
        tracing::info!(
            user_id = %user_id,
            n = msgs.len(),
            mem_type = %collected.mem_type,
            importance = collected.importance,
            halflife_days = collected.halflife_days,
            "memory_writer: collected + reduced, writing to mem0"
        );
        // 通过 memory.rs 写, 把 auto_collector 提取的 metadata 一并写入
        let req = crate::memory::AddRequest {
            text,
            user_id: user_id.clone(),
            agent_id: "alpha".to_string(),
            direct: true,
            importance: Some(collected.importance),
            halflife_days: Some(collected.halflife_days),
            mem_type: Some(collected.mem_type.clone()),
            tags: Some(collected.tags.join(",")),
            source: Some("auto_collector".to_string()),
        };
        match crate::memory::add_memory(&req).await {
            Ok(id) => {
                tracing::info!(user_id = %user_id, id = %id, "memory_writer: wrote to mem0");
                total_written += 1;
            }
            Err(e) => {
                tracing::warn!(user_id = %user_id, error = %e, "memory_writer: mem0 write failed (continuing)");
            }
        }
    }
    Ok(total_written)
}

/// 启动后台 flush 循环 (5 秒 tick, 每次检查过期隐式)
pub fn spawn_flush_loop() {
    tokio::spawn(async {
        let mut interval = tokio::time::interval(Duration::from_secs(5));
        loop {
            interval.tick().await;
            if !is_enabled() { continue; }
            match flush_due_implicit().await {
                Ok(n) if n > 0 => tracing::info!(n, "memory_writer: implicit flush wrote N facts"),
                Ok(_) => {},
                Err(e) => tracing::warn!(error = %e, "memory_writer: implicit flush failed"),
            }
        }
    });
}
