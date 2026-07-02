//! auto_collector.rs — DNA Memory 借鉴: 自动记忆采集器 (Phase 2 P0, 2026-07-02)
//!
//! 设计: 移植自 AIPMAndy/dna-memory (Apache 2.0) 的 auto_memory_collector.py
//!   - 纯 Rust 正则实现, 0.5ms 级
//!   - 4 类 Detector: Preference / Decision / Error / Knowledge
//!   - ContentFilter 噪音过滤 (10 字符 / 噪音模式 / 纯代码块)
//!   - ImportanceScorer 4 维评分 (base + length + entity + context)
//!   - 不写库, 只返回 Option<CollectedMemory> 供上层 (memory_writer) 决定
//!
//! 跟 memory_writer.rs 协作模式:
//!   user 消息 → main.rs 提取 user_text → on_user_message (入队)
//!     ↓ 5min 窗口 / 显式触发
//!   flush_messages() → 先过 AutoCollector::detect()
//!     ↓ 命中? 才调 LLM 归约 → add_memory
//!     ↓ 未命中? 直接 skip (省 LLM GPU 时间)
//!
//! 不依赖 LLM, 跟 Phase 1.1 的 reduce_to_fact (LLM 归约) 并存.

use serde::{Deserialize, Serialize};

/// 采集到的记忆 (给上层用, 不直接写库)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CollectedMemory {
    pub mem_type: String,      // preference / fact / insight / error
    pub content: String,
    pub importance: f64,       // 0.0 - 1.0
    pub tags: Vec<String>,
    pub is_explicit: bool,     // 显式触发 (含"记住"等)
    pub halflife_days: u32,    // 0=永久, 30=普通
}

/// 上下文 (给 ImportanceScorer 用)
#[derive(Debug, Default, Clone)]
pub struct CollectorContext {
    pub current_task: Option<String>,  // 当前任务关键词
}

// ============================================================================
// ContentFilter - 噪音过滤 (DNA Memory ContentFilter 移植)
// ============================================================================

const MIN_LENGTH: usize = 10;

const NOISE_PATTERNS: &[&str] = &[
    r"^(好的|OK|知道了|明白|收到|谢谢|感谢|继续|下一步|开始吧)$",
    r"^(是的|对|没错|对的|嗯|啊|哦)$",
    r"^Tool results provided\.$",
];

fn is_noise(text: &str) -> bool {
    let t = text.trim();
    // 1. 太短
    if t.chars().count() < MIN_LENGTH {
        return true;
    }
    // 2. 噪音模式
    for pattern in NOISE_PATTERNS {
        if let Ok(re) = regex::Regex::new(pattern) {
            if re.is_match(t) {
                return true;
            }
        }
    }
    // 3. 纯代码块
    if t.starts_with("```") && t.ends_with("```") {
        return true;
    }
    false
}

// ============================================================================
// ExplicitKeywordDetector - 显式触发检测 (中文/英文 "记住" 类)
// ============================================================================

const EXPLICIT_KEYWORDS: &[&str] = &[
    "记住", "记下来", "don't forget", "save", "remember", "请记",
];

fn contains_explicit(text: &str) -> bool {
    let lower = text.to_lowercase();
    EXPLICIT_KEYWORDS.iter().any(|k| lower.contains(&k.to_lowercase()))
}

// ============================================================================
// PreferenceDetector - 偏好识别 (DNA Memory 移植)
// ============================================================================

const PREFERENCE_PATTERNS: &[(&str, f64)] = &[
    (r"我(喜欢|偏好|更喜欢)(.+)", 0.85),
    (r"我(不喜欢|讨厌|不要)(.+)", 0.85),
    (r"我(习惯|通常|一般)(.+)", 0.75),
    (r"(更喜欢|prefer)\s*(.+?)\s*(而不是|over)\s*(.+)", 0.80),
];

fn detect_preference(text: &str) -> Option<CollectedMemory> {
    for (pat, score) in PREFERENCE_PATTERNS {
        if let Ok(re) = regex::Regex::new(pat) {
            if let Some(_) = re.captures(text) {
                return Some(CollectedMemory {
                    mem_type: "preference".to_string(),
                    content: text.trim().to_string(),
                    importance: *score,
                    tags: vec!["preference".to_string()],
                    is_explicit: false,
                    halflife_days: 0, // 偏好永久
                });
            }
        }
    }
    None
}

// ============================================================================
// DecisionDetector - 决策识别 (DNA Memory 移植)
// ============================================================================

const DECISION_PATTERNS: &[(&str, f64)] = &[
    (r"(决定|选择|确定)(用|使用|采用)?\s*(.+)", 0.90),
    (r"(使用|采用|引入)\s*(.+?)\s*(来|做|重构|实现)", 0.85),
    (r"(改为|切换到|迁移到)\s*(.+)", 0.85),
    (r"(不用|放弃|废弃)\s*(.+?)[,，](因为|原因是)(.+)", 0.80),
];

fn detect_decision(text: &str) -> Option<CollectedMemory> {
    for (pat, score) in DECISION_PATTERNS {
        if let Ok(re) = regex::Regex::new(pat) {
            if let Some(_) = re.captures(text) {
                return Some(CollectedMemory {
                    mem_type: "preference".to_string(), // DNA Memory 把决策也归 preference
                    content: text.trim().to_string(),
                    importance: *score,
                    tags: vec!["decision".to_string()],
                    is_explicit: false,
                    halflife_days: 30,
                });
            }
        }
    }
    None
}

// ============================================================================
// ErrorDetector - 错误+解决方案识别 (最有价值)
// ============================================================================

const ERROR_KEYWORDS: &[&str] = &[
    "错误", "报错", "失败", "error", "bug", "问题", "踩坑", "exception", "failed",
];
const SOLUTION_KEYWORDS: &[&str] = &[
    "解决", "修复", "改为", "fixed", "solved", "方案", "通过",
];

fn detect_error(text: &str) -> Option<CollectedMemory> {
    let lower = text.to_lowercase();
    let has_error = ERROR_KEYWORDS.iter().any(|k| lower.contains(k));
    let has_solution = SOLUTION_KEYWORDS.iter().any(|k| lower.contains(k));

    if has_error && has_solution {
        Some(CollectedMemory {
            mem_type: "error".to_string(),
            content: text.trim().to_string(),
            importance: 0.95,
            tags: vec!["error".to_string(), "solution".to_string()],
            is_explicit: false,
            halflife_days: 30,
        })
    } else if has_error && text.chars().count() > 30 {
        Some(CollectedMemory {
            mem_type: "error".to_string(),
            content: text.trim().to_string(),
            importance: 0.80,
            tags: vec!["error".to_string(), "problem".to_string()],
            is_explicit: false,
            halflife_days: 30,
        })
    } else {
        None
    }
}

// ============================================================================
// KnowledgeDetector - 知识/洞察识别
// ============================================================================

const KNOWLEDGE_PATTERNS: &[(&str, f64)] = &[
    (r"(原来|发现|了解到|才知道)(.+)", 0.75),
    (r"(.+?)(可以|能够|支持)(.+)", 0.70),
];

fn detect_knowledge(text: &str) -> Option<CollectedMemory> {
    if text.chars().count() < 20 {
        return None;
    }
    for (pat, score) in KNOWLEDGE_PATTERNS {
        if let Ok(re) = regex::Regex::new(pat) {
            if let Some(_) = re.captures(text) {
                return Some(CollectedMemory {
                    mem_type: "insight".to_string(),
                    content: text.trim().to_string(),
                    importance: *score,
                    tags: vec!["knowledge".to_string()],
                    is_explicit: false,
                    halflife_days: 30,
                });
            }
        }
    }
    None
}

// ============================================================================
// ImportanceScorer - 4 维评分 (DNA Memory 移植)
// ============================================================================

const TECH_KEYWORDS: &[&str] = &[
    "react", "next.js", "typescript", "python", "sqlite", "postgresql",
    "api", "mcp", "github", "git", "docker", "kubernetes",
    "fastapi", "flask", "django", "claude", "anthropic", "openai", "gpt", "llm",
    "embedding", "vector", "database", "cache", "redis", "mongodb",
    "rust", "tokio", "qdrant", "chromadb", "nomic",
];

fn score_importance(base: f64, content: &str, context: &CollectorContext) -> f64 {
    let chars = content.chars().count();

    // 长度加成 (20-100 字符最佳)
    let length_bonus = if (20..=100).contains(&chars) {
        0.05
    } else if chars > 100 {
        0.10
    } else {
        0.0
    };

    // 实体密度 (技术名词)
    let lower = content.to_lowercase();
    let tech_count = TECH_KEYWORDS.iter().filter(|k| lower.contains(*k)).count();
    let entity_bonus = ((tech_count as f64) * 0.02).min(0.10);

    // 上下文相关性
    let context_bonus = match &context.current_task {
        Some(task) => {
            let task_words: Vec<&str> = task.split_whitespace().collect();
            let content_lower = content.to_lowercase();
            let overlap = task_words.iter()
                .filter(|w| content_lower.contains(&w.to_lowercase()))
                .count();
            if overlap >= 2 { 0.10 } else { 0.0 }
        }
        None => 0.0,
    };

    (base + length_bonus + entity_bonus + context_bonus).min(1.0)
}

// ============================================================================
// AutoCollector - 主入口
// ============================================================================

pub struct AutoCollector;

impl AutoCollector {
    /// 主入口: 检测消息, 返回 Some(CollectedMemory) 表示值得记, None 表示跳过
    ///
    /// 流程: ContentFilter → ExplicitKeyword (加分) → 4 Detector 顺序匹配 → ImportanceScorer
    pub fn detect(text: &str, context: &CollectorContext) -> Option<CollectedMemory> {
        // 1. 噪音过滤
        if is_noise(text) {
            return None;
        }

        // 2. 显式触发检测 (加分, 不改变 mem_type)
        let is_explicit = contains_explicit(text);

        // 3. 4 个 Detector 顺序匹配, 第一个命中即返回
        // 顺序很关键: error > preference > decision > knowledge
        //   - 错误+方案 (含 "错误" 词) 优先于 "我喜欢" 类的偏好
        //   - 跟 DNA Memory 原版测试集一致
        let mut detected = detect_error(text)
            .or_else(|| detect_preference(text))
            .or_else(|| detect_decision(text))
            .or_else(|| detect_knowledge(text));

        // Phase 2 P1 (2026-07-02) 修: 显式触发含"记住"但无 Detector 匹配时,
        //  兜底写入 fact 类型。否则 "记住: X" 但 X 不匹配任何 pattern 会被静默丢弃。
        if detected.is_none() && is_explicit {
            detected = Some(CollectedMemory {
                mem_type: "fact".to_string(),
                content: text.trim().to_string(),
                importance: 0.7,
                tags: vec!["explicit".to_string()],
                is_explicit: true,
                halflife_days: 0,
            });
        }

        let mut detected = match detected {
            Some(m) => m,
            None => return None,  // 无 Detector 匹配 + 非显式触发 → skip
        };

        // 4. 显式触发 → 永久 + 加分
        if is_explicit {
            detected.is_explicit = true;
            detected.halflife_days = 0;
        }

        // 5. 重要性评分
        detected.importance = score_importance(detected.importance, text, context);

        // 6. 分数过低 → 丢弃
        if detected.importance < 0.60 {
            return None;
        }

        Some(detected)
    }
}

// ============================================================================
// 单元测试 (DNA Memory 原版测试集移植)
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    fn ctx() -> CollectorContext { CollectorContext::default() }

    #[test]
    fn test_preference_detected() {
        let m = AutoCollector::detect("我喜欢用 TypeScript 而不是 JavaScript", &ctx());
        assert!(m.is_some(), "preference 应被识别");
        let m = m.unwrap();
        assert_eq!(m.mem_type, "preference");
        assert!(m.importance >= 0.85);
    }

    #[test]
    fn test_explicit_preference_is_permanent() {
        let m = AutoCollector::detect("记住: 我喜欢简洁中文", &ctx()).unwrap();
        assert_eq!(m.halflife_days, 0, "显式触发应永久");
        assert!(m.is_explicit);
    }

    #[test]
    fn test_error_with_solution_high_importance() {
        let m = AutoCollector::detect(
            "遇到数据库锁定错误, 改为单一事务解决了",
            &ctx(),
        ).unwrap();
        assert_eq!(m.mem_type, "error");
        assert!(m.importance >= 0.95, "错误+方案 重要性最高");
        assert!(m.tags.contains(&"solution".to_string()));
    }

    #[test]
    fn test_decision_detected() {
        let m = AutoCollector::detect("决定用 Next.js 重构前端", &ctx()).unwrap();
        assert_eq!(m.mem_type, "preference");
        assert!(m.tags.contains(&"decision".to_string()));
    }

    #[test]
    fn test_knowledge_detected() {
        let m = AutoCollector::detect("原来 SQLite 支持 FTS5 全文搜索", &ctx()).unwrap();
        assert_eq!(m.mem_type, "insight");
    }

    #[test]
    fn test_noise_skipped() {
        assert!(AutoCollector::detect("好的", &ctx()).is_none());
        assert!(AutoCollector::detect("继续", &ctx()).is_none());
        assert!(AutoCollector::detect("Tool results provided.", &ctx()).is_none());
    }

    #[test]
    fn test_too_short_skipped() {
        assert!(AutoCollector::detect("知道了", &ctx()).is_none());
    }

    #[test]
    fn test_code_block_skipped() {
        let code = "```python\nprint('hi')\n```";
        assert!(AutoCollector::detect(code, &ctx()).is_none());
    }

    #[test]
    fn test_unrelated_text_skipped() {
        // 无任何 detector 命中
        assert!(AutoCollector::detect("今天天气不错啊真的挺舒服的要不要出去走走", &ctx()).is_none());
    }
}
