// access.rs — Per-client model access control (哥哥 6-30 C1 拍板)
//
// 5 条硬约束:
//   1. 单一真实来源 (桥是唯一权威)
//   2. webui + 桌宠各占 1 个本地 slot (per-client lock)
//   3. 本地切换: 先 evict 旧, 再 load 新
//   4. 切云端: 必须先 evict 本地
//   5. 启动继承 last_successful_local
//
// 设计:
//   - X-Ikaros-Client header 决定 client (webui | pet | cron | mcp)
//   - 每个 client 最多占 1 个本地模型 slot
//   - 总共 ≤ 2 个本地活跃 (router mode --models-max 2, webui 1 + pet 1)
//   - 云端永远 alive, 不受 access control
//
// 存储: data/access-control.json (gitignored)
// {
//   "default_local": "Qwen_Qwen3.5-9B-Q4_K_M",
//   "client_slots": { "webui": "Qwen3.5-9B-Q4_K_M", "pet": "Phi-4-Mini-3.8B-Q4_K_L" },
//   "last_successful_local": "Qwen3.5-9B-Q4_K_M"
// }

use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::RwLock;

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct ClientId(pub String);

impl ClientId {
    /// 从 X-Ikaros-Client header 解析 client identity
    /// None / 空 / "webui" → 默认 webui (兼容没改的 webui)
    pub fn from_header(h: Option<&str>) -> Self {
        let normalized = h.map(|s| s.trim().to_lowercase());
        match normalized.as_deref() {
            None | Some("") | Some("webui") => ClientId("webui".into()),
            Some("pet") => ClientId("pet".into()),
            Some("cron") => ClientId("cron".into()),
            Some("mcp") => ClientId("mcp".into()),
            Some(other) => ClientId(other.to_string()),
        }
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, Default)]
pub struct PersistedState {
    pub default_local: Option<String>,
    pub client_slots: HashMap<String, String>,
    pub last_successful_local: Option<String>,
}

pub struct AccessControl {
    /// 每个 client 当前占的本地 slot (model id)
    /// pub(crate): main.rs 的 EvictAndRouteCloud 处理需要直接读
    pub(crate) client_local_slot: Arc<RwLock<HashMap<ClientId, String>>>,
    /// 桥启动时从 :8080 router 拉的 loaded 模型 (动态)
    loaded_locals: Arc<RwLock<HashSet<String>>>,
    /// 默认本地模型 (持久化)
    default_local: Arc<RwLock<Option<String>>>,
    /// 上次成功加载 (启动时继承)
    last_successful: Arc<RwLock<Option<String>>>,
    /// 持久化路径
    persist_path: PathBuf,
}

#[derive(Debug, Clone, PartialEq)]
pub enum AuthzDecision {
    /// 直接 serve (不需切换本地) — worker_url + model 已确认
    Serve { worker_url: String, model: String },
    /// 需要 evict 旧 + load 新 (per-client slot 切换)
    SwapLocal { client: ClientId, old_model: String, new_model: String },
    /// 切云端 (强制 evict 该 client 本地 slot, 然后路由云)
    EvictAndRouteCloud { client: ClientId, provider: String, model: String },
    /// 拒绝 (该 client 想用别人占的本地)
    Deny { reason: String },
}

impl AccessControl {
    pub fn new(persist_path: PathBuf) -> Self {
        Self {
            client_local_slot: Arc::new(RwLock::new(HashMap::new())),
            loaded_locals: Arc::new(RwLock::new(HashSet::new())),
            default_local: Arc::new(RwLock::new(None)),
            last_successful: Arc::new(RwLock::new(None)),
            persist_path,
        }
    }

    /// 从持久化文件加载 (启动时, 同步 std::fs — 启动期不在 tokio runtime 内, 用阻塞 IO)
    pub async fn load(&self) {
        let content = match std::fs::read_to_string(&self.persist_path) {
            Ok(c) => c,
            Err(_) => return,  // 文件不存在 = 首次启动
        };
        let parsed: PersistedState = match serde_json::from_str(&content) {
            Ok(p) => p,
            Err(e) => {
                tracing::warn!("access-control: failed to parse {:?}: {}", self.persist_path, e);
                return;
            }
        };

        {
            let mut slot = self.client_local_slot.write().await;
            for (k, v) in parsed.client_slots {
                slot.insert(ClientId(k), v);
            }
        } // drop client_local_slot write lock before acquiring read locks below
        *self.default_local.write().await = parsed.default_local;
        *self.last_successful.write().await = parsed.last_successful_local;

        tracing::info!(
            "access-control: loaded default={:?} slots={:?} last_successful={:?}",
            self.default_local.read().await,
            self.client_local_slot.read().await,
            self.last_successful.read().await,
        );
    }

    /// 持久化到磁盘 (同步 std::fs + spawn_blocking 包装避免阻塞 tokio)
    pub async fn save(&self) -> Result<(), String> {
        let state = PersistedState {
            default_local: self.default_local.read().await.clone(),
            client_slots: {
                let slot = self.client_local_slot.read().await;
                slot.iter().map(|(k, v)| (k.0.clone(), v.clone())).collect()
            },
            last_successful_local: self.last_successful.read().await.clone(),
        };
        let path = self.persist_path.clone();

        tokio::task::spawn_blocking(move || -> Result<(), String> {
            let json = serde_json::to_string_pretty(&state).map_err(|e| e.to_string())?;
            // 原子写: 写 tmp + rename
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
            }
            let tmp = path.with_extension("json.tmp");
            std::fs::write(&tmp, json).map_err(|e| e.to_string())?;
            std::fs::rename(&tmp, &path).map_err(|e| e.to_string())?;
            Ok(())
        })
        .await
        .map_err(|e| e.to_string())?
    }

    /// 同步 :8080 router mode 当前 loaded 列表 (启动时 + 周期 tick)
    pub async fn sync_loaded_locals(&self, models: Vec<String>) {
        let mut loaded = self.loaded_locals.write().await;
        loaded.clear();
        for m in models {
            loaded.insert(m);
        }
    }

    /// 核心鉴权 + 决策
    ///
    /// - `client`: 请求方 (从 X-Ikaros-Client header 解析)
    /// - `requested_model`: 请求的 model id
    /// - `worker_map`: model_id → worker_url (桥已聚合)
    /// - `is_cloud`: requested_model 是否是云端 (桥 worker 已分类)
    pub async fn decide(
        &self,
        client: ClientId,
        requested_model: &str,
        worker_url: &str,
        is_cloud: bool,
    ) -> AuthzDecision {
        // 1. 云端: 先 evict 该 client 本地 slot (如果占了), 然后路由云
        if is_cloud {
            let slot = self.client_local_slot.read().await;
            if slot.contains_key(&client) {
                return AuthzDecision::EvictAndRouteCloud {
                    client,
                    provider: extract_provider_from_url(worker_url),
                    model: requested_model.into(),
                };
                // 注: caller 会负责先 evict 该 client slot 的旧本地, 再 Serve 云
            }
            return AuthzDecision::Serve {
                worker_url: worker_url.into(),
                model: requested_model.into(),
            };
        }

        // 2. 本地模型: per-client slot 检查
        // C1 (哥哥 6-30): 先检查跨 client 冲突 → Deny, 再看自己 slot
        let slot = self.client_local_slot.read().await;

        // 2a. 检查其他 client 是否占了 requested_model → Deny (不管自己当前 slot)
        for (other_client, other_model) in slot.iter() {
            if other_client != &client && other_model == requested_model {
                return AuthzDecision::Deny {
                    reason: format!(
                        "model '{}' is held by client '{}' (per-client slot lock)",
                        requested_model, other_client.0
                    ),
                };
            }
        }

        // 2b. 该 client 当前 slot == 请求模型 → 直接 serve
        if let Some(current) = slot.get(&client) {
            if current == requested_model {
                return AuthzDecision::Serve {
                    worker_url: worker_url.into(),
                    model: requested_model.into(),
                };
            }
            // 2c. 切换本地: evict 旧 + load 新
            return AuthzDecision::SwapLocal {
                client,
                old_model: current.clone(),
                new_model: requested_model.into(),
            };
        }

        // 2d. 没人占 (含自己也没占), 分配 slot
        AuthzDecision::SwapLocal {
            client,
            old_model: String::new(),  // 空 = 无需 evict
            new_model: requested_model.into(),
        }
    }

    /// SwapLocal 决策执行后调用: 更新 slot
    pub async fn set_slot(&self, client: ClientId, model: String) {
        let mut slot = self.client_local_slot.write().await;
        slot.insert(client, model.clone());
        *self.last_successful.write().await = Some(model);
        // 异步 fire-and-forget save
        let this = self.clone();
        tokio::spawn(async move {
            let _ = this.save().await;
        });
    }

    /// EvictAndRouteCloud 决策执行后调用: 清空 slot
    pub async fn clear_slot(&self, client: ClientId) {
        let mut slot = self.client_local_slot.write().await;
        slot.remove(&client);
        let this = self.clone();
        tokio::spawn(async move {
            let _ = this.save().await;
        });
    }

    /// 启动时给 caller 一个 hint: 应该预热哪个本地模型
    pub async fn startup_warmup_hint(&self) -> Option<String> {
        // 优先级: last_successful > default_local
        let last = self.last_successful.read().await.clone();
        if last.is_some() {
            return last;
        }
        self.default_local.read().await.clone()
    }

    /// 当前 snapshot (给 list_models 返给客户端看)
    pub async fn snapshot(&self) -> serde_json::Value {
        let slot = self.client_local_slot.read().await;
        let default = self.default_local.read().await.clone();
        let last = self.last_successful.read().await.clone();
        let loaded = self.loaded_locals.read().await;

        serde_json::json!({
            "default_local": default,
            "last_successful_local": last,
            "client_slots": slot.iter().map(|(k, v)| (k.0.clone(), v.clone())).collect::<HashMap<_, _>>(),
            "loaded_locals": loaded.iter().collect::<Vec<_>>(),
        })
    }
}

// Cheap clone: Arc 共享 inner state
impl Clone for AccessControl {
    fn clone(&self) -> Self {
        Self {
            client_local_slot: self.client_local_slot.clone(),
            loaded_locals: self.loaded_locals.clone(),
            default_local: self.default_local.clone(),
            last_successful: self.last_successful.clone(),
            persist_path: self.persist_path.clone(),
        }
    }
}

fn extract_provider_from_url(url: &str) -> String {
    // crude: deepseek / minimax / anthropic / openai / local
    if url.contains("deepseek") {
        "deepseek".into()
    } else if url.contains("minimax") {
        "minimax-cn".into()
    } else if url.contains("anthropic") {
        "anthropic".into()
    } else if url.contains("127.0.0.1") || url.contains("localhost") {
        "local".into()
    } else {
        "openai".into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_client_id_from_header() {
        assert_eq!(ClientId::from_header(None).as_str(), "webui");
        assert_eq!(ClientId::from_header(Some("")).as_str(), "webui");
        assert_eq!(ClientId::from_header(Some("WEBUI")).as_str(), "webui");
        assert_eq!(ClientId::from_header(Some("pet")).as_str(), "pet");
        assert_eq!(ClientId::from_header(Some("Pet")).as_str(), "pet");
        assert_eq!(ClientId::from_header(Some("cron")).as_str(), "cron");
        assert_eq!(ClientId::from_header(Some("unknown")).as_str(), "unknown");
    }

    #[tokio::test]
    async fn test_decide_cloud_evicts_local() {
        let ac = AccessControl::new(PathBuf::from("/tmp/test-ac.json"));
        let client = ClientId("pet".into());

        // pet 占了一个本地 slot
        ac.set_slot(client.clone(), "Phi-4-Q4".into()).await;

        // pet 切云: 应该 EvictAndRouteCloud
        let d = ac.decide(client.clone(), "deepseek-chat", "https://api.deepseek.com/v1", true).await;
        assert!(matches!(d, AuthzDecision::EvictAndRouteCloud { .. }), "got {:?}", d);
    }

    #[tokio::test]
    async fn test_decide_local_same_slot_serves() {
        let ac = AccessControl::new(PathBuf::from("/tmp/test-ac.json"));
        let client = ClientId("webui".into());

        ac.set_slot(client.clone(), "Qwen3.5-9B".into()).await;

        let d = ac.decide(client.clone(), "Qwen3.5-9B", "http://127.0.0.1:8080", false).await;
        assert!(matches!(d, AuthzDecision::Serve { .. }), "got {:?}", d);
    }

    #[tokio::test]
    async fn test_decide_local_swap() {
        let ac = AccessControl::new(PathBuf::from("/tmp/test-ac.json"));
        let client = ClientId("webui".into());

        ac.set_slot(client.clone(), "Qwen3.5-9B".into()).await;

        let d = ac.decide(client.clone(), "Phi-4-Q4", "http://127.0.0.1:8080", false).await;
        match d {
            AuthzDecision::SwapLocal { old_model, new_model, .. } => {
                assert_eq!(old_model, "Qwen3.5-9B");
                assert_eq!(new_model, "Phi-4-Q4");
            }
            other => panic!("expected SwapLocal, got {:?}", other),
        }
    }

    #[tokio::test]
    async fn test_decide_deny_cross_client() {
        let ac = AccessControl::new(PathBuf::from("/tmp/test-ac.json"));

        ac.set_slot(ClientId("webui".into()), "Qwen3.5-9B".into()).await;

        // pet 想用 webui 的 slot
        let d = ac.decide(ClientId("pet".into()), "Qwen3.5-9B", "http://127.0.0.1:8080", false).await;
        assert!(matches!(d, AuthzDecision::Deny { .. }), "got {:?}", d);
    }

    #[tokio::test]
    async fn test_decide_local_assign_empty_slot() {
        let ac = AccessControl::new(PathBuf::from("/tmp/test-ac.json"));
        let client = ClientId("pet".into());

        // pet 之前没占 slot
        let d = ac.decide(client.clone(), "Phi-4-Q4", "http://127.0.0.1:8080", false).await;
        match d {
            AuthzDecision::SwapLocal { old_model, new_model, .. } => {
                assert_eq!(old_model, "");
                assert_eq!(new_model, "Phi-4-Q4");
            }
            other => panic!("expected SwapLocal, got {:?}", other),
        }
    }

    #[tokio::test]
    async fn test_persistence_roundtrip() {
        let tmp = std::env::temp_dir().join("test-ac-roundtrip.json");
        let _ = std::fs::remove_file(&tmp);

        let ac1 = AccessControl::new(tmp.clone());
        ac1.set_slot(ClientId("webui".into()), "Qwen3.5-9B".into()).await;
        ac1.set_slot(ClientId("pet".into()), "Phi-4-Q4".into()).await;
        let _ = ac1.save().await;
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;

        let ac2 = AccessControl::new(tmp.clone());
        ac2.load().await;
        let snap = ac2.snapshot().await;
        let slots = snap.get("client_slots").unwrap();
        assert_eq!(slots.get("webui").unwrap().as_str(), Some("Qwen3.5-9B"));
        assert_eq!(slots.get("pet").unwrap().as_str(), Some("Phi-4-Q4"));

        let _ = std::fs::remove_file(&tmp);
    }
}