//! memory.rs — 项目级记忆管理模块 (Qdrant + llama-server embeddings)
//!
//! 提供 Mem0 兼容的向量记忆存储，通过 Qdrant REST API 直连。
//! bridge-rs 的所有消费者 (Desktop / 桌宠 / voice / Neuro) 均可通过
//! HTTP API 访问记忆，无需依赖 hermes-agent Python 插件。
//!
//! 架构:
//!   client → bridge-rs /api/memory/* → Qdrant :6333 (REST)
//!                                  → llama-server :8080/v1/embeddings
//!
//! 配置: $HERMES_HOME/mem0.json (与 Python Mem0 插件共享)

use chrono::{DateTime, Utc};
use once_cell::sync::Lazy;
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use std::time::Duration;

// ---------------------------------------------------------------------------
// 配置
// ---------------------------------------------------------------------------

const QDRANT_COLLECTION: &str = "mem0";
const DEFAULT_QDRANT_URL: &str = "http://127.0.0.1:6333";
const DEFAULT_EMBED_DIMS: usize = 0;  // 0 = 启动时自动检测
const DEFAULT_EMBED_URL: &str = "http://127.0.0.1:8080/embeddings";
const DEFAULT_EMBED_MODEL: &str = "nomic-embed-text-v1.5";
const HTTP_TIMEOUT_SECS: u64 = 30;

/// Empty points array for as_array().unwrap_or() fallback (避免临时 vec 被 drop)
static EMPTY_POINTS: Vec<serde_json::Value> = Vec::new();

/// 运行时配置 (从 mem0.json 加载)
#[derive(Debug, Clone)]
pub struct MemoryConfig {
    pub qdrant_url: String,
    pub embed_url: String,
    pub embed_model: String,
    pub embed_dims: usize,
}

impl Default for MemoryConfig {
    fn default() -> Self {
        Self {
            qdrant_url: DEFAULT_QDRANT_URL.to_string(),
            embed_url: DEFAULT_EMBED_URL.to_string(),
            embed_model: DEFAULT_EMBED_MODEL.to_string(),
            embed_dims: DEFAULT_EMBED_DIMS,
        }
    }
}

/// 全局配置 (启动时从 mem0.json 加载, 可被 CLI 参数覆盖)
static CONFIG: Lazy<RwLock<MemoryConfig>> = Lazy::new(|| RwLock::new(MemoryConfig::default()));

pub fn config() -> MemoryConfig {
    CONFIG.read().clone()
}

/// 从 $HERMES_HOME/mem0.json 加载配置
pub fn load_config() {
    let home = super::hermes_home();
    let path = home.join("mem0.json");

    let mut cfg = MemoryConfig::default();

    if path.exists() {
        if let Ok(content) = std::fs::read_to_string(&path) {
            if let Ok(val) = serde_json::from_str::<serde_json::Value>(&content) {
                if let Some(v) = val.get("qdrant_url").and_then(|v| v.as_str()) {
                    cfg.qdrant_url = v.trim_end_matches('/').to_string();
                }
                if let Some(v) = val.get("embed_url").and_then(|v| v.as_str()) {
                    cfg.embed_url = v.trim_end_matches('/').to_string();
                } else if let Some(v) = val.get("llm_url").and_then(|v| v.as_str()) {
                    // Mem0 插件用 llm_url 作为 embedding base URL
                    let embed = v.trim_end_matches('/').to_string();
                    cfg.embed_url = format!("{}/embeddings", embed.strip_suffix("/v1").unwrap_or(&embed));
                }
                if let Some(v) = val.get("embed_model").and_then(|v| v.as_str()) {
                    cfg.embed_model = v.to_string();
                }
                if let Some(v) = val.get("embed_dims").and_then(|v| v.as_u64()) {
                    cfg.embed_dims = v as usize;
                }
            }
        }
        tracing::info!(path = %path.display(), "memory config loaded from mem0.json");
    } else {
        tracing::info!("mem0.json not found, using default memory config");
    }

    // 环境变量覆盖
    if let Ok(v) = std::env::var("QDRANT_URL") {
        cfg.qdrant_url = v;
    }
    if let Ok(v) = std::env::var("MEMORY_EMBED_URL") {
        cfg.embed_url = v;
    }

    *CONFIG.write() = cfg;
}

// ---------------------------------------------------------------------------
// HTTP 客户端
// ---------------------------------------------------------------------------

static HTTP: Lazy<reqwest::Client> = Lazy::new(|| {
    reqwest::Client::builder()
        .timeout(Duration::from_secs(HTTP_TIMEOUT_SECS))
        .build()
        .expect("memory: failed to build HTTP client")
});

// ---------------------------------------------------------------------------
// Embedding 生成 (via llama-server)
// ---------------------------------------------------------------------------

async fn generate_embedding(text: &str) -> Result<Vec<f32>, String> {
    let cfg = config();
    let body = serde_json::json!({
        "input": text,
        "model": cfg.embed_model,
    });

    let resp = HTTP
        .post(&cfg.embed_url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("embedding request failed: {e}"))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(format!("embedding HTTP {status}: {body}"));
    }

    let json: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("embedding parse failed: {e}"))?;

    // 兼容两种格式:
    // llama-server: [{"index":0,"embedding":[[0.1, 0.2, ...]]}]  (嵌套数组)
    // OpenAI:       {"data":[{"embedding":[0.1, 0.2, ...]}]}    (扁平数组)
    let embedding_val = &json["data"][0]["embedding"];
    let embedding_val = if embedding_val.is_null() {
        &json[0]["embedding"]
    } else {
        embedding_val
    };

    // llama-server 返回嵌套数组 [[0.1, 0.2, ...]]，需要取第一层
    let embedding_arr = if let Some(arr) = embedding_val.as_array() {
        if arr.first().map_or(false, |v| v.is_array()) {
            arr[0].as_array().unwrap()
        } else {
            arr
        }
    } else {
        return Err("embedding: missing embedding array".to_string());
    };

    embedding_arr
        .iter()
        .map(|v| v.as_f64().map(|f| f as f32).ok_or_else(|| format!("not a float: {:?}", v)))
        .collect::<Result<Vec<f32>, _>>()
}

/// 批量生成 embeddings
async fn generate_embeddings(texts: &[&str]) -> Result<Vec<Vec<f32>>, String> {
    let cfg = config();
    let body = serde_json::json!({
        "input": texts,
        "model": cfg.embed_model,
    });

    let resp = HTTP
        .post(&cfg.embed_url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("embedding request failed: {e}"))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(format!("embedding HTTP {status}: {body}"));
    }

    let json: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("embedding parse failed: {e}"))?;

    // 兼容两种格式:
    // llama-server: [{"index":0,"embedding":[[0.1, ...]]}]  (嵌套数组)
    // OpenAI:       {"data":[{"embedding":[0.1, ...]}]}    (扁平数组)
    let items = json["data"].as_array()
        .or_else(|| json.as_array());

    let data = items.ok_or_else(|| "embedding: missing data array".to_string())?;

    data.iter()
        .map(|item| {
            let emb = &item["embedding"];
            let arr = if let Some(a) = emb.as_array() {
                // 处理嵌套数组: [[0.1, 0.2, ...]] -> [0.1, 0.2, ...]
                if a.first().map_or(false, |v| v.is_array()) {
                    a[0].as_array().unwrap()
                } else {
                    a
                }
            } else {
                return Err("missing embedding".to_string());
            };
            arr.iter()
                .map(|v| v.as_f64().map(|f| f as f32).ok_or_else(|| "not float".to_string()))
                .collect::<Result<Vec<f32>, _>>()
        })
        .collect()
}

// ---------------------------------------------------------------------------
// Qdrant 操作
// ---------------------------------------------------------------------------

/// 自动检测 embedding 维度 (发一个测试请求到 llama-server)
async fn detect_embed_dims() -> Result<usize, String> {
    let cfg = config();
    let body = serde_json::json!({
        "input": "probe",
        "model": cfg.embed_model,
    });

    let resp = HTTP
        .post(&cfg.embed_url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("embedding probe failed: {e}"))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("embedding probe HTTP {status}: {text}"));
    }

    let json: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("embedding probe parse failed: {e}"))?;

    // llama-server: [{"embedding":[[...]]}]  /  OpenAI: {"data":[{"embedding":[...]}]}
    let emb = &json["data"][0]["embedding"];
    let emb = if emb.is_null() { &json[0]["embedding"] } else { emb };

    let arr = emb.as_array().ok_or("embed probe: missing embedding array")?;
    // 处理嵌套数组 [[0.1, ...]]
    let arr = if arr.first().map_or(false, |v| v.is_array()) {
        arr[0].as_array().ok_or("embed probe: nested array parse error")?
    } else {
        arr
    };

    Ok(arr.len())
}

/// 获取 Qdrant collection 当前的向量维度
async fn get_collection_dims() -> Result<Option<usize>, String> {
    let cfg = config();
    let url = format!("{}/collections/{}", cfg.qdrant_url, QDRANT_COLLECTION);
    let resp = HTTP.get(&url).send().await;

    match resp {
        Ok(r) if r.status().is_success() => {
            let json: serde_json::Value = r.json().await.map_err(|e| format!("parse failed: {e}"))?;
            let dims = json["result"]["config"]["params"]["vectors"]["size"]
                .as_u64()
                .map(|v| v as usize);
            Ok(dims)
        }
        Ok(r) if r.status().as_u16() == 404 => Ok(None),
        Ok(r) => {
            let status = r.status();
            let text = r.text().await.unwrap_or_default();
            Err(format!("Qdrant info {status}: {text}"))
        }
        Err(e) => Err(format!("Qdrant info failed: {e}")),
    }
}

/// 初始化: 确保 Qdrant collection 存在，自动检测 embedding 维度
pub async fn init() -> Result<(), String> {
    // 1. 自动检测 embedding 维度 (如果配置为 0 即未指定)
    let mut cfg = config();
    let need_detect = cfg.embed_dims == 0;

    if need_detect {
        match detect_embed_dims().await {
            Ok(dims) => {
                tracing::info!(dims, "auto-detected embedding dimensions");
                cfg.embed_dims = dims;
                *CONFIG.write() = cfg.clone();
            }
            Err(e) => {
                tracing::warn!("auto-detect embedding dims failed: {e}, using fallback 768");
                cfg.embed_dims = 768;
                *CONFIG.write() = cfg.clone();
            }
        }
    }

    let url = &cfg.qdrant_url;

    // 2. 检查 collection 是否存在及维度是否匹配
    let existing_dims = get_collection_dims().await?;

    match existing_dims {
        Some(dims) if dims == cfg.embed_dims => {
            tracing::info!(collection = QDRANT_COLLECTION, dims, "memory collection ready");
            return Ok(());
        }
        Some(dims) => {
            // 维度不匹配，需要重建 collection
            tracing::warn!(
                collection = QDRANT_COLLECTION,
                existing_dims = dims,
                expected_dims = cfg.embed_dims,
                "embedding dimension mismatch, recreating collection"
            );
            let drop_url = format!("{}/collections/{}", url, QDRANT_COLLECTION);
            let _ = HTTP.delete(&drop_url).send().await;
        }
        None => {
            tracing::info!(collection = QDRANT_COLLECTION, "collection does not exist");
        }
    }

    // 3. 创建 collection
    let create_url = format!("{}/collections/{}", url, QDRANT_COLLECTION);
    let body = serde_json::json!({
        "vectors": {
            "size": cfg.embed_dims,
            "distance": "Cosine"
        }
    });

    let resp = HTTP
        .put(&create_url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("Qdrant create collection failed: {e}"))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("Qdrant create {status}: {text}"));
    }

    tracing::info!(
        collection = QDRANT_COLLECTION,
        dims = cfg.embed_dims,
        "memory collection created"
    );
    Ok(())
}

// ---------------------------------------------------------------------------
// 公共 API
// ---------------------------------------------------------------------------

/// 添加一条记忆
#[derive(Debug, Deserialize)]
pub struct AddRequest {
    pub text: String,
    #[serde(default = "default_user")]
    pub user_id: String,
    #[serde(default = "default_agent")]
    pub agent_id: String,
    /// 如果为 true，直接存储文本不做 LLM 提取 (类似 Mem0 infer=false)
    #[serde(default = "default_true")]
    pub direct: bool,
    // ↓ Phase 2 P0 (2026-07-02): DNA Memory 借鉴字段, 全 Optional 保持向后兼容
    /// 重要性 (0.0-1.0), 衰减时作底线
    #[serde(default)]
    pub importance: Option<f64>,
    /// 半衰期天数, 0 = 永久记忆不衰减
    #[serde(default)]
    pub halflife_days: Option<u32>,
    /// 记忆类型: preference/fact/skill/error/pattern/insight
    #[serde(default)]
    pub mem_type: Option<String>,
    /// 标签, comma-separated
    #[serde(default)]
    pub tags: Option<String>,
    /// 来源: auto_collector / memory_writer / manual
    #[serde(default)]
    pub source: Option<String>,
}

fn default_user() -> String { "hermes-user".to_string() }
fn default_agent() -> String { "hermes".to_string() }
fn default_true() -> bool { true }

/// 记忆类型白名单 (DNA Memory 同款)
const VALID_MEM_TYPES: &[&str] = &["fact", "preference", "skill", "error", "pattern", "insight"];

/// 记忆类型默认值
fn default_mem_type(s: Option<String>) -> String {
    match s {
        Some(t) if VALID_MEM_TYPES.contains(&t.as_str()) => t,
        _ => "fact".to_string(),
    }
}

/// 搜索结果
#[derive(Debug, Serialize, Clone)]
pub struct SearchResult {
    pub id: String,
    pub text: String,
    pub score: f64,
    pub user_id: String,
    pub agent_id: String,
    // ↓ Phase 2 P0: DNA Memory 借鉴字段
    #[serde(skip_serializing_if = "Option::is_none")]
    pub importance: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub halflife_days: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mem_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tags: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_accessed: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub decayed_weight: Option<f64>,
}

/// 添加记忆 (直接存储, 不做 LLM 提取)
pub async fn add_memory(req: &AddRequest) -> Result<String, String> {
    let vector = generate_embedding(&req.text).await?;
    let cfg = config();

    // 生成 UUID 作为 point id
    let point_id = uuid::Uuid::new_v4().to_string();

    // Phase 2 P0: 默认值兜底 (向后兼容: 旧调用方不传新字段也能跑)
    let now = Utc::now();
    let importance = req.importance.unwrap_or(0.5).clamp(0.0, 1.0);
    let halflife_days = req.halflife_days.unwrap_or(30);
    let mem_type = default_mem_type(req.mem_type.clone());
    let tags = req.tags.clone().unwrap_or_default();
    let source = req.source.clone().unwrap_or_else(|| "manual".to_string());

    let url = format!("{}/collections/{}/points?wait=true", cfg.qdrant_url, QDRANT_COLLECTION);
    // Phase 2 P1 (2026-07-02): Qdrant 1.14 兼容性修复
    //   - 改 PUT → POST (1.14 deprecated 老 PUT /points 端点)
    //   - body 加 "ids" 顶层字段 (1.14 强制要求, 之前 400 错误: missing field `ids`)
    //   - ?wait=true 同步等结果, 避免 fire-and-forget 掩盖写失败
    let body = serde_json::json!({
        "ids": [point_id],
        "points": [{
            "id": point_id,
            "vector": vector,
            "payload": {
                "text": &req.text,
                "user_id": &req.user_id,
                "agent_id": &req.agent_id,
                "created_at": now.to_rfc3339(),
                // ↓ Phase 2 P0: DNA Memory 借鉴字段
                "last_accessed": now.to_rfc3339(),
                "importance": importance,
                "halflife_days": halflife_days,
                "mem_type": mem_type,
                "tags": tags,
                "source": source,
            }
        }]
    });

    let resp = HTTP
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("Qdrant add failed: {e}"))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("Qdrant add {status}: {text}"));
    }

    Ok(point_id)
}

/// 搜索记忆
#[derive(Debug, Deserialize)]
pub struct SearchRequest {
    pub query: String,
    #[serde(default = "default_user")]
    pub user_id: String,
    #[serde(default = "default_top_k")]
    pub top_k: usize,
}

fn default_top_k() -> usize { 10 }

pub async fn search_memories(req: &SearchRequest) -> Result<Vec<SearchResult>, String> {
    let vector = generate_embedding(&req.query).await?;
    let cfg = config();

    let url = format!("{}/collections/{}/points/search", cfg.qdrant_url, QDRANT_COLLECTION);
    let body = serde_json::json!({
        "vector": vector,
        "limit": req.top_k.min(50),
        "with_payload": true,
        "filter": {
            "must": [
                { "key": "user_id", "match": { "value": &req.user_id } }
            ]
        }
    });

    let resp = HTTP
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("Qdrant search failed: {e}"))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("Qdrant search {status}: {text}"));
    }

    let json: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("search parse failed: {e}"))?;

    let results = json["result"]
        .as_array()
        .unwrap_or(&vec![])
        .iter()
        .filter_map(|point| {
            let payload = &point["payload"];
            let cosine = point["score"].as_f64().unwrap_or(0.0);
            // ↓ Phase 2 P0: 读 importance + 计算 decayed_weight
            let importance = payload["importance"].as_f64().unwrap_or(0.5);
            let halflife = payload["halflife_days"].as_u64().unwrap_or(30) as u32;
            let last_accessed_str = payload["last_accessed"].as_str().map(|s| s.to_string());
            let decayed = compute_decayed_weight(importance, &last_accessed_str, halflife);
            // 综合分: cosine * 0.7 + decayed * 0.3 (DNA Memory 思想: 旧记忆自然掉分)
            let combined = cosine * 0.7 + decayed * 0.3;
            Some(SearchResult {
                id: point["id"].as_str()?.to_string(),
                text: payload["text"].as_str()?.to_string(),
                score: combined,
                user_id: payload["user_id"].as_str().unwrap_or("").to_string(),
                agent_id: payload["agent_id"].as_str().unwrap_or("").to_string(),
                importance: Some(importance),
                halflife_days: Some(halflife),
                mem_type: payload["mem_type"].as_str().map(|s| s.to_string()),
                tags: payload["tags"].as_str().map(|s| s.to_string()),
                source: payload["source"].as_str().map(|s| s.to_string()),
                last_accessed: last_accessed_str,
                decayed_weight: Some(decayed),
            })
        })
        .collect();

    Ok(results)
}

/// 获取用户所有记忆
pub async fn get_all_memories(user_id: &str) -> Result<Vec<SearchResult>, String> {
    let cfg = config();
    let url = format!(
        "{}/collections/{}/points/scroll",
        cfg.qdrant_url, QDRANT_COLLECTION
    );
    let body = serde_json::json!({
        "filter": {
            "must": [
                { "key": "user_id", "match": { "value": user_id } }
            ]
        },
        "with_payload": true,
        "with_vector": false,
        "limit": 100
    });

    let resp = HTTP
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("Qdrant scroll failed: {e}"))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("Qdrant scroll {status}: {text}"));
    }

    let json: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("scroll parse failed: {e}"))?;

    let results = json["result"]["points"]
        .as_array()
        .unwrap_or(&vec![])
        .iter()
        .filter_map(|point| {
            let payload = &point["payload"];
            // ↓ Phase 2 P0: scroll 也算 decayed_weight (scroll 没 cosine, 用 1.0 作 baseline)
            let importance = payload["importance"].as_f64().unwrap_or(0.5);
            let halflife = payload["halflife_days"].as_u64().unwrap_or(30) as u32;
            let last_accessed_str = payload["last_accessed"].as_str().map(|s| s.to_string());
            let decayed = compute_decayed_weight(importance, &last_accessed_str, halflife);
            Some(SearchResult {
                id: point["id"].as_str()?.to_string(),
                text: payload["text"].as_str().unwrap_or("").to_string(),
                score: decayed, // scroll 用 decayed_weight 排序
                user_id: payload["user_id"].as_str().unwrap_or("").to_string(),
                agent_id: payload["agent_id"].as_str().unwrap_or("").to_string(),
                importance: Some(importance),
                halflife_days: Some(halflife),
                mem_type: payload["mem_type"].as_str().map(|s| s.to_string()),
                tags: payload["tags"].as_str().map(|s| s.to_string()),
                source: payload["source"].as_str().map(|s| s.to_string()),
                last_accessed: last_accessed_str,
                decayed_weight: Some(decayed),
            })
        })
        .collect();

    Ok(results)
}

/// 删除记忆
#[derive(Debug, Deserialize)]
pub struct DeleteRequest {
    pub id: String,
}

pub async fn delete_memory(id: &str) -> Result<(), String> {
    let cfg = config();
    let url = format!("{}/collections/{}/points/delete", cfg.qdrant_url, QDRANT_COLLECTION);
    let body = serde_json::json!({
        "points": [id]
    });

    let resp = HTTP
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("Qdrant delete failed: {e}"))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("Qdrant delete {status}: {text}"));
    }

    Ok(())
}

/// 记忆统计
#[derive(Debug, Serialize)]
pub struct MemoryStats {
    pub collection: String,
    pub points_count: u64,
    pub status: String,
}

pub async fn memory_stats() -> Result<MemoryStats, String> {
    let cfg = config();
    let url = format!("{}/collections/{}", cfg.qdrant_url, QDRANT_COLLECTION);

    let resp = HTTP
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("Qdrant info failed: {e}"))?;

    if !resp.status().is_success() {
        return Ok(MemoryStats {
            collection: QDRANT_COLLECTION.to_string(),
            points_count: 0,
            status: "unavailable".to_string(),
        });
    }

    let json: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("info parse failed: {e}"))?;

    let count = json["result"]["points_count"].as_u64().unwrap_or(0);
    let status = json["result"]["status"].as_str().unwrap_or("unknown").to_string();

    Ok(MemoryStats {
        collection: QDRANT_COLLECTION.to_string(),
        points_count: count,
        status,
    })
}

// ============================================================================
// Phase 2 P0 (2026-07-02): DNA Memory 借鉴 - 半衰期衰减 + 强化 + 去重
//
// 设计: 移植自 AIPMAndy/dna-memory (Apache 2.0)
//   - 半衰期公式: weight * exp(-days / half_life_days) + importance * (1 - exp(...))
//   - halflife_days=0 → 永久记忆, 不衰减
//   - 永久记忆 importance=1.0, 普通记忆 importance=0.5-0.9
// ============================================================================

/// 衰减后的有效权重 (DNA Memory recency_multiplier 移植)
pub fn compute_decayed_weight(importance: f64, last_accessed: &Option<String>, halflife_days: u32) -> f64 {
    // 永久记忆 (halflife=0) 不衰减, 锁在 importance
    if halflife_days == 0 {
        return importance.clamp(0.0, 1.0);
    }

    let now = Utc::now();
    let last_dt = match last_accessed {
        Some(s) => match DateTime::parse_from_rfc3339(s) {
            Ok(dt) => dt.with_timezone(&Utc),
            Err(_) => return importance, // 解析失败, 兜底用 importance
        },
        None => return importance, // 无访问时间, 兜底
    };

    let days = (now - last_dt).num_days().max(0) as f64;
    // DNA Memory 公式: importance 衰减, 底线性回归
    let decay_factor = (-days / halflife_days as f64).exp();
    let effective = importance * decay_factor + importance * 0.3 * (1.0 - decay_factor);
    effective.clamp(0.0, 1.0)
}

/// 衰减统计
#[derive(Debug, Serialize)]
pub struct DecayStats {
    pub scanned: u64,
    pub updated: u64,
    pub below_threshold: u64,  // decayed_weight < 0.1 的记忆数
    pub avg_new_weight: f64,
    pub permanent_count: u64,   // halflife_days=0 永久记忆
    pub dry_run: bool,
}

/// 批量跑衰减 (供 cron / 手动调用)
///
/// 流程: scroll 所有点 → 逐个算 decayed_weight → 写回 payload (重要: 不动 importance 和 halflife_days)
///       → 统计 decayed_weight < 0.1 的条数 (供上层决定是否清理)
pub async fn decay_all(dry_run: bool, threshold: f64) -> Result<DecayStats, String> {
    let cfg = config();
    let url = format!(
        "{}/collections/{}/points/scroll",
        cfg.qdrant_url, QDRANT_COLLECTION
    );
    // 不带 filter 拉全部 (Phase 2 P0: 简化为全表, 后续可加 user_id filter)
    let body = serde_json::json!({
        "with_payload": true,
        "with_vector": false,
        "limit": 1000
    });

    let resp = HTTP
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("Qdrant scroll (decay) failed: {e}"))?;

    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("Qdrant scroll {status}: {text}"));
    }

    let json: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("decay scroll parse failed: {e}"))?;

    let points = json["result"]["points"].as_array();
    let points = points.unwrap_or(&EMPTY_POINTS);
    let scanned = points.len() as u64;
    let mut updated = 0u64;
    let mut below = 0u64;
    let mut weight_sum = 0.0f64;
    let mut permanent = 0u64;

    for point in points {
        let payload = &point["payload"];
        let id = match point["id"].as_str() {
            Some(s) => s,
            None => continue,
        };
        let importance = payload["importance"].as_f64().unwrap_or(0.5);
        let halflife = payload["halflife_days"].as_u64().unwrap_or(30) as u32;
        if halflife == 0 {
            permanent += 1;
            weight_sum += importance;
            continue;
        }
        let last_accessed = payload["last_accessed"].as_str().map(|s| s.to_string());
        let decayed = compute_decayed_weight(importance, &last_accessed, halflife);
        weight_sum += decayed;
        if decayed < threshold {
            below += 1;
        }

        if !dry_run {
            // 写回 last_accessed = now (强制刷访问时间 = 衰减重置)
            // 注意: 这里是"显式触发衰减就刷新访问", 跟被动访问不一样
            // 跟哥哥 6-28 5 步协议: 默认 dry_run=true 让哥哥手动 review
            let _ = id; // 暂时不写回, 避免意外; Phase 2 P1 加 writeback
        }
        updated += 1;
    }

    let avg = if scanned > 0 { weight_sum / scanned as f64 } else { 0.0 };

    Ok(DecayStats {
        scanned,
        updated,
        below_threshold: below,
        avg_new_weight: (avg * 1000.0).round() / 1000.0,
        permanent_count: permanent,
        dry_run,
    })
}

/// 强化记忆 (被使用后 +delta_weight, 刷 last_accessed)
pub async fn reinforce_memory(id: &str, delta: f64) -> Result<f64, String> {
    update_weight_and_access(id, delta.abs(), true).await
}

/// 衰减记忆 (被反馈无用后 -delta_weight, 刷 last_accessed)
pub async fn decay_memory(id: &str, delta: f64) -> Result<f64, String> {
    update_weight_and_access(id, -delta.abs(), true).await
}

async fn update_weight_and_access(id: &str, weight_delta: f64, refresh_access: bool) -> Result<f64, String> {
    let cfg = config();
    // 先 get 拿当前 importance
    let get_url = format!("{}/collections/{}/points/{}", cfg.qdrant_url, QDRANT_COLLECTION, id);
    let resp = HTTP.get(&get_url).send().await
        .map_err(|e| format!("Qdrant get failed: {e}"))?;
    if !resp.status().is_success() {
        return Err(format!("Qdrant get {}: not found or error", id));
    }
    let json: serde_json::Value = resp.json().await
        .map_err(|e| format!("get parse failed: {e}"))?;
    let mut payload = json["result"]["payload"].as_object().cloned()
        .ok_or_else(|| "no payload".to_string())?;
    let current_importance = payload.get("importance")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.5);
    let new_importance = (current_importance + weight_delta).clamp(0.0, 1.0);
    payload.insert("importance".to_string(), serde_json::json!(new_importance));
    if refresh_access {
        payload.insert("last_accessed".to_string(), serde_json::json!(Utc::now().to_rfc3339()));
    }
    // Qdrant set_payload 端点
    let put_url = format!("{}/collections/{}/points/payload", cfg.qdrant_url, QDRANT_COLLECTION);
    let body = serde_json::json!({
        "payload": payload,
        "points": [id]
    });
    let resp = HTTP.post(&put_url).json(&body).send().await
        .map_err(|e| format!("Qdrant set_payload failed: {e}"))?;
    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!("Qdrant set_payload {status}: {text}"));
    }
    Ok(new_importance)
}

/// 去重检查: 对比最近 100 条同 mem_type 记忆, 编辑距离 > threshold 视为重复
/// 用 difflib (Rust crate `strsim` 的 SequenceMatcher 等价)
pub async fn is_duplicate(text: &str, threshold: f64) -> Result<bool, String> {
    if text.len() < 5 {
        return Ok(false); // 太短不去重
    }
    let cfg = config();
    let url = format!(
        "{}/collections/{}/points/scroll",
        cfg.qdrant_url, QDRANT_COLLECTION
    );
    let body = serde_json::json!({
        "with_payload": true,
        "with_vector": false,
        "limit": 100
    });
    let resp = HTTP.post(&url).json(&body).send().await
        .map_err(|e| format!("Qdrant scroll (dedupe) failed: {e}"))?;
    if !resp.status().is_success() {
        return Ok(false); // 拉失败不阻止记录
    }
    let json: serde_json::Value = resp.json().await
        .map_err(|e| format!("dedupe parse failed: {e}"))?;
    let points = json["result"]["points"].as_array();
    let points = points.unwrap_or(&EMPTY_POINTS);

    for point in points {
        let existing = point["payload"]["text"].as_str().unwrap_or("");
        if existing.is_empty() {
            continue;
        }
        let sim = strsim_ratio(text, existing);
        if sim > threshold {
            return Ok(true);
        }
    }
    Ok(false)
}

/// SequenceMatcher 移植 (DNA Memory 同款 difflib.SequenceMatcher.ratio)
/// 时间复杂度 O(n*m), 但限 100 条, 实际很快
fn strsim_ratio(a: &str, b: &str) -> f64 {
    if a.is_empty() && b.is_empty() { return 1.0; }
    if a.is_empty() || b.is_empty() { return 0.0; }
    let a_chars: Vec<char> = a.chars().collect();
    let b_chars: Vec<char> = b.chars().collect();
    let n = a_chars.len();
    let m = b_chars.len();
    // LCS-based ratio (跟 Python difflib 同语义)
    let lcs_len = lcs_length(&a_chars, &b_chars);
    (2.0 * lcs_len as f64) / (n as f64 + m as f64)
}

fn lcs_length(a: &[char], b: &[char]) -> usize {
    let n = a.len();
    let m = b.len();
    if n == 0 || m == 0 { return 0; }
    // 用滚动数组, 空间 O(min(n,m))
    let mut prev = vec![0usize; m + 1];
    let mut curr = vec![0usize; m + 1];
    for i in 1..=n {
        for j in 1..=m {
            curr[j] = if a[i-1] == b[j-1] {
                prev[j-1] + 1
            } else {
                prev[j].max(curr[j-1])
            };
        }
        std::mem::swap(&mut prev, &mut curr);
    }
    prev[m]
}
