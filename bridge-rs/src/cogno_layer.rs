//! cogno_layer.rs — 5 维认知元数据 (Cognitive 5D metadata)
//!
//! Mirrors the Python cogno_layer.py logic.
//! Each chat request gets enriched with:
//!   [time] [machine_id] [geo] [emotion] [context]
//!
//! This is injected into the system prompt so the LLM knows
//! "when/where/who/how" of the conversation.

use once_cell::sync::Lazy;
use std::sync::atomic::{AtomicU64, Ordering};

/// Global conversation turn counter (reset on bridge restart)
static TURN_COUNTER: AtomicU64 = AtomicU64::new(1);

/// Last user message excerpt (for context compression)
static LAST_USER_TEXT: Lazy<parking_lot::Mutex<String>> = Lazy::new(Default::default);

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Enrich a user message with 5D cognitive metadata.
/// Returns a formatted string to be appended to the system prompt.
pub fn enrich(user_text: &str) -> String {
    let time = get_time_str();
    let machine_id = get_machine_id();
    let geo = get_geo_location();
    let emotion = infer_emotion(user_text);
    let context = compress_context(user_text);

    // Update stored context for next turn
    {
        let mut last = LAST_USER_TEXT.lock();
        *last = user_text.chars().take(80).collect();
    }
    TURN_COUNTER.fetch_add(1, Ordering::Relaxed);

    format!(
        "【当前认知 5D】\n\
         - 时间: {}\n\
         - 设备: {}\n\
         - 地理: {}\n\
         - 情绪推断: {}\n\
         - 上下文: {}",
        time, machine_id, geo, emotion, context
    )
}

/// Enrich an assistant reply with cogno metadata (for logging/tracing).
/// Currently a no-op passthrough — reserved for future use.
pub fn enrich_reply(reply: &str) -> String {
    reply.to_string()
}

// ---------------------------------------------------------------------------
// 5D components
// ---------------------------------------------------------------------------

/// Dimension 1: Time string
/// Format: "2026/6/29 08:13"
fn get_time_str() -> String {
    let now = chrono::Local::now();
    now.format("%Y/%-m/%-d %H:%M").to_string()
}

/// Dimension 2: Machine ID
/// Uses hostname + username to create a stable identifier.
fn get_machine_id() -> String {
    // Try cached value first
    static CACHED: Lazy<String> = Lazy::new(|| {
        let hostname = std::env::var("COMPUTERNAME")
            .or_else(|_| std::env::var("HOSTNAME"))
            .unwrap_or_else(|_| "unknown-pc".to_string());

        let username = std::env::var("USERNAME")
            .or_else(|_| std::env::var("USER"))
            .unwrap_or_else(|_| "unknown-user".to_string());

        format!("{}@{}", username, hostname)
    });

    CACHED.clone()
}

/// Dimension 3: Geo location
/// Uses a lightweight HTTP call to ip-api.com (free, no key needed).
/// Falls back to "未知" on failure.
fn get_geo_location() -> String {
    static CACHED: Lazy<String> = Lazy::new(|| {
        // Synchronous HTTPX call via std (not reqwest, since this runs at request time
        // and we don't want to block the async runtime).
        // Use a simple TCP connection to ip-api.com
        match _fetch_geo_sync() {
            Some(geo) => geo,
            None => "未知".to_string(),
        }
    });

    CACHED.clone()
}

/// Synchronous geo fetch (runs on blocking thread if needed).
fn _fetch_geo_sync() -> Option<String> {
    use std::io::{Read, Write};
    use std::net::TcpStream;

    let stream = TcpStream::connect("ip-api.com:80").ok()?;
    stream.set_read_timeout(Some(std::time::Duration::from_secs(2))).ok()?;
    stream.set_write_timeout(Some(std::time::Duration::from_secs(2))).ok()?;

    let request = b"GET /json?fields=city,regionName,country HTTP/1.1\r\nHost: ip-api.com\r\nConnection: close\r\n\r\n";
    let mut stream = stream;
    stream.write_all(request).ok()?;

    let mut response = String::new();
    stream.read_to_string(&mut response).ok()?;

    // Parse JSON body from HTTP response
    let body = response.split("\r\n\r\n").nth(1)?;
    let json: serde_json::Value = serde_json::from_str(body).ok()?;

    let city = json.get("city").and_then(|v| v.as_str()).unwrap_or("");
    let region = json.get("regionName").and_then(|v| v.as_str()).unwrap_or("");
    let country = json.get("country").and_then(|v| v.as_str()).unwrap_or("");

    if city.is_empty() && region.is_empty() && country.is_empty() {
        return None;
    }

    let parts: Vec<&str> = [city, region, country]
        .iter()
        .filter(|s| !s.is_empty())
        .copied()
        .collect();

    Some(parts.join("/"))
}

/// Dimension 4: Emotion inference from user text
/// Simple keyword-based emotion detection (mirrors Python cogno_layer.py).
fn infer_emotion(text: &str) -> String {
    let text_lower = text.to_lowercase();

    // Happy / excited
    let happy_keywords = [
        "哈哈", "呵呵", "开心", "高兴", "太好了", "棒", "厉害", "赞", "牛",
        "喜欢", "爱", "😊", "😄", "🎉", "好耶", "nice", "great", "awesome",
        "wow", "wonderful", "优秀", "漂亮", "完美",
    ];
    for kw in &happy_keywords {
        if text_lower.contains(kw) {
            return "开心".to_string();
        }
    }

    // Curious
    let curious_keywords = [
        "为什么", "怎么", "如何", "什么", "能不能", "可以吗", "请问",
        "想知道", "好奇", "怎么回事", "why", "how", "what",
    ];
    for kw in &curious_keywords {
        if text_lower.contains(kw) {
            return "好奇".to_string();
        }
    }

    // Grateful
    let grateful_keywords = [
        "谢谢", "感谢", "多谢", "辛苦了", "麻烦了", "thank", "thanks",
        "thx", "appreciate",
    ];
    for kw in &grateful_keywords {
        if text_lower.contains(kw) {
            return "感谢".to_string();
        }
    }

    // Frustrated / annoyed
    let frustrated_keywords = [
        "烦", "讨厌", "垃圾", "bug", "错了", "不行", "失败", "崩溃",
        "搞什么", "为什么不能", "气死", "无语", "晕", "靠", "卧槽",
        "shit", "fuck", "damn", "crap",
    ];
    for kw in &frustrated_keywords {
        if text_lower.contains(kw) {
            return "烦躁".to_string();
        }
    }

    // Default: calm
    "平静".to_string()
}

/// Dimension 5: Context compression
/// Summarizes conversation state for the LLM.
fn compress_context(current_text: &str) -> String {
    let turn = TURN_COUNTER.load(Ordering::Relaxed);
    let last = LAST_USER_TEXT.lock().clone();

    if turn <= 1 {
        "新对话开始".to_string()
    } else {
        let excerpt = current_text.chars().take(40).collect::<String>();
        if last.is_empty() {
            format!("第{}轮, 问: {}", turn, excerpt)
        } else {
            format!("第{}轮, 上轮: {}…, 本轮: {}…", turn,
                    last.chars().take(30).collect::<String>(),
                    excerpt)
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_emotion_happy() {
        assert_eq!(infer_emotion("哈哈哈太棒了"), "开心");
    }

    #[test]
    fn test_emotion_curious() {
        assert_eq!(infer_emotion("这是怎么回事？"), "好奇");
    }

    #[test]
    fn test_emotion_grateful() {
        assert_eq!(infer_emotion("谢谢你帮我"), "感谢");
    }

    #[test]
    fn test_emotion_frustrated() {
        assert_eq!(infer_emotion("这个bug太烦了"), "烦躁");
    }

    #[test]
    fn test_emotion_calm() {
        assert_eq!(infer_emotion("帮我写个函数"), "平静");
    }

    #[test]
    fn test_enrich_format() {
        let result = enrich("你好");
        assert!(result.contains("【当前认知 5D】"));
        assert!(result.contains("时间:"));
        assert!(result.contains("设备:"));
    }
}
