//! Intent Router — keyword/regex classifier
//! 0-risk port: pure logic, no IO. Returns "task" / "chat" / "ambiguous".

use once_cell::sync::Lazy;
use regex::Regex;

const TASK_KEYWORDS: &[&str] = &[
    "帮我", "创建", "做一下", "跑", "查一下",
    "计算", "分析", "汇总", "生成", "写一份",
    "安排", "建个", "帮我去",
];
const TASK_CHARS: &[char] = &['做', '跑'];
const CHAT_KEYWORDS: &[&str] = &[
    "你好", "早安", "晚安", "哈哈", "吃",
    "睡", "笑", "开心", "好吗", "怎么样",
    "你觉得", "想", "喜欢",
];

static QUESTION_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"[？?吗嘛吧呢]|怎么|什么|哪|谁|啥|何|是否|有没有|能不能|会不会|要不要").unwrap()
});

pub fn classify(text: &str) -> &'static str {
    if text.trim().is_empty() {
        return "ambiguous";
    }
    let stripped = text.trim();

    // Rule 1: task keywords take priority
    for kw in TASK_KEYWORDS {
        if stripped.contains(kw) {
            return "task";
        }
    }
    for ch in TASK_CHARS {
        if stripped.contains(*ch) {
            return "task";
        }
    }

    // Rule 2: chat/greeting keywords
    for kw in CHAT_KEYWORDS {
        if stripped.contains(kw) {
            return "chat";
        }
    }

    // Rule 3: question without action words → chat
    if QUESTION_RE.is_match(stripped) {
        return "chat";
    }

    // Rule 4: ambiguous (LLM decides)
    "ambiguous"
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_task() {
        assert_eq!(classify("帮我创建一个文件"), "task");
        assert_eq!(classify("跑一下测试"), "task");
    }

    #[test]
    fn test_chat() {
        assert_eq!(classify("你好"), "chat");
        assert_eq!(classify("今天怎么样？"), "chat");
    }

    #[test]
    fn test_ambiguous() {
        assert_eq!(classify(""), "ambiguous");
        assert_eq!(classify("伊卡洛斯"), "ambiguous");
    }
}
