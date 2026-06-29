//! Ikaros Supervisor — per-worker auto-restart + suspend + crash log.
//!
//! 设计: 跟 qhkm/zeptoclaw 的 `ChannelManager::start_supervisor` 同结构
//!   - 3 consts (POLL / COOLDOWN / MAX_RESTARTS)
//!   - SupervisorEntry { restart_count, last_restart, started, last_dead_at }
//!   - tokio::spawn 监督 task, 15s 轮询, 检测 worker.alive=false 后按策略重启
//!   - 跟 crate::health::REGISTRY 集成 (报告 restart_ok / exceeded_max)
//!   - 4 method: suspend_module / resume_module / on_crash / crash_log
//!
//! 哥哥 6-29 axiom: 1) 暂时取消局部模块监管 (suspend) 2) 模块崩溃 log 记录 (crash_log)
//! Quest 5-23 1a5f9c ship Part B + Path A 后, bridge-rs 无内置 supervisor, watchdog (commit 39d88c6 disabled).
//! 复制 zeptoclaw 设计 (src/channels/manager.rs L292-394), 但 zero 外部依赖, 100% 我们 owned.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use tokio::sync::watch;
use tracing::{debug, error, info, warn};

use crate::health::{HealthRegistry, REGISTRY};

/// 监督轮询间隔 (跟 zeptoclaw SUPERVISOR_POLL_SECS 一致).
const POLL_SECS: u64 = 15;
/// 重启冷却 — 同一 worker 两次重启之间最小间隔.
const COOLDOWN_SECS: u64 = 60;
/// 最大重启次数 — 超过后放弃, 标记为 Error.
const MAX_RESTARTS: u32 = 5;

/// Per-worker 监督状态.
#[derive(Debug, Clone)]
pub struct SupervisorEntry {
    /// 累计重启次数.
    pub restart_count: u32,
    /// 上次重启时间 (用于 cooldown 判断).
    pub last_restart: Option<Instant>,
    /// 是否被监督 (false = suspend, 跳过此 worker).
    pub started: bool,
    /// 上次检测到 dead 的时间 (用于 crash_log).
    pub last_dead_at: Option<Instant>,
    /// worker URL (用于 crash_log 报告, 不重复读 worker).
    pub url: String,
}

impl SupervisorEntry {
    fn new(url: String) -> Self {
        Self {
            restart_count: 0,
            last_restart: None,
            started: true,
            last_dead_at: None,
            url,
        }
    }
}

/// 崩溃事件 — 写 crash_log 返回.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CrashEvent {
    pub at: f64,            // unix timestamp
    pub url: String,
    pub restart_count: u32,
    pub restart_ok: Option<bool>,
    pub reason: String,     // "alive_false" / "restart_failed" / "exceeded_max"
}

/// 全局 Supervisor 状态 — 跟 WorkerPool.supervisor_state 字段对应.
#[derive(Default)]
pub struct SupervisorState {
    entries: RwLock<HashMap<String, SupervisorEntry>>,
    /// 崩溃 log 环形缓冲 (每个 worker 最多保留 100 条).
    crash_logs: RwLock<HashMap<String, Vec<CrashEvent>>>,
    /// suspend 暂时到期时间 — 任务里检查, 到期自动 resume.
    suspend_until: RwLock<HashMap<String, Instant>>,
}

impl SupervisorState {
    pub fn new() -> Self {
        Self::default()
    }

    /// 注册 worker (在 WorkerPool::new 之后调, 给每个 worker 建 SupervisorEntry).
    pub fn register(&self, name: &str, url: &str) {
        let mut entries = self.entries.write();
        entries.entry(name.to_string()).or_insert_with(|| SupervisorEntry::new(url.to_string()));
    }

    /// 列出所有 worker 的监督状态.
    pub fn list(&self) -> Vec<SupervisorEntryView> {
        let entries = self.entries.read();
        entries.iter().map(|(name, e)| SupervisorEntryView {
            name: name.clone(),
            url: e.url.clone(),
            restart_count: e.restart_count,
            started: e.started,
            last_restart_ago: e.last_restart.map(|t| t.elapsed().as_secs()),
            last_dead_ago: e.last_dead_at.map(|t| t.elapsed().as_secs()),
        }).collect()
    }

    /// 暂时取消监管 (哥哥 axiom #1).
    /// duration: None = 直到显式 resume; Some = duration 后自动 resume.
    pub fn suspend(&self, name: &str, duration: Option<Duration>) {
        let mut entries = self.entries.write();
        if let Some(e) = entries.get_mut(name) {
            e.started = false;
            info!(worker = name, "supervisor: suspended (duration={:?})", duration);
        }
        if let Some(d) = duration {
            let mut su = self.suspend_until.write();
            su.insert(name.to_string(), Instant::now() + d);
        }
    }

    /// 立即恢复监管.
    pub fn resume(&self, name: &str) {
        let mut entries = self.entries.write();
        if let Some(e) = entries.get_mut(name) {
            e.started = true;
            info!(worker = name, "supervisor: resumed");
        }
        let mut su = self.suspend_until.write();
        su.remove(name);
    }

    /// 标记 worker dead (内部用, 监督 loop 检测到 worker.alive=false 时调).
    /// 哥哥 axiom #2: 崩溃 log 记录.
    pub fn mark_dead(&self, name: &str, reason: &str) {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);

        let mut entries = self.entries.write();
        if let Some(e) = entries.get_mut(name) {
            e.last_dead_at = Some(Instant::now());
            let url = e.url.clone();
            let restart_count = e.restart_count;

            let mut logs = self.crash_logs.write();
            let log = logs.entry(name.to_string()).or_insert_with(Vec::new);
            log.push(CrashEvent {
                at: now,
                url: url.clone(),
                restart_count,
                restart_ok: None,
                reason: reason.to_string(),
            });
            // 环形 buffer, 保留最新 100 条
            if log.len() > 100 {
                log.remove(0);
            }
            debug!(worker = name, reason = reason, "supervisor: marked dead");
        }
    }

    /// 记录 restart 结果 (监督 loop 重启后调).
    pub fn record_restart(&self, name: &str, ok: bool) {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);

        let mut entries = self.entries.write();
        if let Some(e) = entries.get_mut(name) {
            e.restart_count += 1;
            e.last_restart = Some(Instant::now());
            let url = e.url.clone();

            let mut logs = self.crash_logs.write();
            let log = logs.entry(name.to_string()).or_insert_with(Vec::new);
            log.push(CrashEvent {
                at: now,
                url,
                restart_count: e.restart_count,
                restart_ok: Some(ok),
                reason: if ok { "restart_ok".to_string() } else { "restart_failed".to_string() },
            });
            if log.len() > 100 {
                log.remove(0);
            }
        }
    }

    /// 读 worker 的崩溃 log (哥哥 axiom #2).
    pub fn crash_log(&self, name: &str) -> Vec<CrashEvent> {
        let logs = self.crash_logs.read();
        logs.get(name).cloned().unwrap_or_default()
    }

    /// 监督 loop tick — 遍历所有 worker, 检查 dead, 决定 restart.
    /// 实际 restart 动作通过 callback 注入 (避免 supervisor.rs 直接依赖 worker_pool 字段写权限).
    pub fn tick<F>(&self, is_alive: F, on_restart: F) -> TickResult
    where
        F: Fn(&str) -> bool + Copy,
    {
        // 1. 检查 suspend_until 到期
        {
            let mut su = self.suspend_until.write();
            let now = Instant::now();
            let to_resume: Vec<String> = su.iter()
                .filter(|(_, until)| **until <= now)
                .map(|(name, _)| name.clone())
                .collect();
            for name in &to_resume {
                su.remove(name);
                if let Some(e) = self.entries.write().get_mut(name) {
                    e.started = true;
                    info!(worker = %name, "supervisor: suspend expired, auto-resumed");
                }
            }
        }

        // 2. 检查每个 worker
        let mut checked = 0;
        let mut found_dead = 0;
        let mut restarted = 0;
        let mut gave_up = 0;
        let entries_snapshot: Vec<(String, SupervisorEntry)> = {
            self.entries.read().iter().map(|(k, v)| (k.clone(), v.clone())).collect()
        };

        for (name, entry) in entries_snapshot {
            if !entry.started {
                continue;
            }
            checked += 1;
            if is_alive(&name) {
                continue;
            }
            // worker dead
            found_dead += 1;

            // cooldown check
            if let Some(last) = entry.last_restart {
                if last.elapsed().as_secs() < COOLDOWN_SECS {
                    debug!(worker = name, "supervisor: dead but in cooldown, skip");
                    continue;
                }
            }

            // max restart check
            if entry.restart_count >= MAX_RESTARTS {
                warn!(worker = name, count = entry.restart_count,
                      "supervisor: exceeded max restarts, giving up");
                REGISTRY.report_failure(&name, &format!("exceeded max restarts ({})", MAX_RESTARTS));
                gave_up += 1;
                continue;
            }

            // 尝试 restart
            info!(worker = name, count = entry.restart_count + 1,
                  "supervisor: dead, attempting restart");
            self.mark_dead(&name, "alive_false");
            let ok = on_restart(&name);
            self.record_restart(&name, ok);
            if ok {
                REGISTRY.report(&name, true, 0.0, Some(serde_json::json!({
                    "restarted_by": "supervisor",
                    "restart_count": entry.restart_count + 1,
                })));
                restarted += 1;
            } else {
                REGISTRY.report_failure(&name, "supervisor restart failed");
            }
        }

        TickResult { checked, found_dead, restarted, gave_up }
    }
}

/// 监督 tick 结果.
#[derive(Debug, Clone, Serialize)]
pub struct TickResult {
    pub checked: u64,
    pub found_dead: u64,
    pub restarted: u64,
    pub gave_up: u64,
}

/// 监督状态 view (HTTP 响应).
#[derive(Debug, Clone, Serialize)]
pub struct SupervisorEntryView {
    pub name: String,
    pub url: String,
    pub restart_count: u32,
    pub started: bool,
    pub last_restart_ago: Option<u64>,  // seconds ago
    pub last_dead_ago: Option<u64>,
}

/// 启动 supervisor 后台 task (async 版本, 用 WorkerPool::is_worker_alive / mark_worker_alive).
pub fn spawn_supervisor(
    state: Arc<crate::WorkerPool>,
    supervisor_state: Arc<SupervisorState>,
    mut shutdown_rx: watch::Receiver<bool>,
) -> tokio::task::JoinHandle<()> {
    info!(
        poll_secs = POLL_SECS,
        cooldown_secs = COOLDOWN_SECS,
        max_restarts = MAX_RESTARTS,
        "supervisor: starting"
    );

    tokio::spawn(async move {
        loop {
            // 等 poll 间隔 或 shutdown
            tokio::select! {
                _ = shutdown_rx.changed() => {
                    if *shutdown_rx.borrow() {
                        info!("supervisor: shutting down");
                        return;
                    }
                }
                _ = tokio::time::sleep(Duration::from_secs(POLL_SECS)) => {}
            }

            if *shutdown_rx.borrow() {
                return;
            }

            // tick: 遍历 entries, 调 async is_worker_alive + async mark_worker_alive
            let entries_snapshot: Vec<String> = {
                let entries = supervisor_state.entries.read();
                entries.keys().cloned().collect()
            };

            let mut found_dead = 0;
            let mut restarted = 0;
            let mut gave_up = 0;

            for name in entries_snapshot {
                let entry = supervisor_state.entries.read().get(&name).cloned();
                let entry = match entry {
                    Some(e) => e,
                    None => continue,
                };
                if !entry.started {
                    continue;
                }
                let alive = state.is_worker_alive(&name).await;
                if alive {
                    continue;
                }

                // worker dead
                found_dead += 1;

                // cooldown check
                if let Some(last) = entry.last_restart {
                    if last.elapsed().as_secs() < COOLDOWN_SECS {
                        debug!(worker = name, "supervisor: dead but in cooldown, skip");
                        continue;
                    }
                }

                // max restart check
                if entry.restart_count >= MAX_RESTARTS {
                    warn!(worker = name, count = entry.restart_count,
                          "supervisor: exceeded max restarts, giving up");
                    REGISTRY.report_failure(&name, &format!("exceeded max restarts ({})", MAX_RESTARTS));
                    gave_up += 1;
                    continue;
                }

                // 尝试 restart (设 alive=true, 让下次 refresh 重新 check)
                info!(worker = name, count = entry.restart_count + 1,
                      "supervisor: dead, attempting restart");
                supervisor_state.mark_dead(&name, "alive_false");
                let ok = state.mark_worker_alive(&name).await;
                supervisor_state.record_restart(&name, ok);
                if ok {
                    REGISTRY.report(&name, true, 0.0, Some(serde_json::json!({
                        "restarted_by": "supervisor",
                        "restart_count": entry.restart_count + 1,
                    })));
                    restarted += 1;
                } else {
                    REGISTRY.report_failure(&name, "supervisor restart failed");
                }
            }

            if found_dead > 0 {
                info!(
                    found_dead,
                    restarted,
                    gave_up,
                    "supervisor: tick complete"
                );
            }
        }
    })
}
