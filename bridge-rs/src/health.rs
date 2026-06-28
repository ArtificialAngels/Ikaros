//! Component Health Registry — singleton for cross-service liveness.
//! Auto-expires stale components after STALE_TIMEOUT_SEC.

use parking_lot::RwLock;
use serde::Serialize;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use once_cell::sync::Lazy;

pub const STALE_TIMEOUT_SEC: f64 = 30.0;

#[derive(Debug, Clone, Serialize)]
pub struct ComponentHealth {
    pub alive: bool,
    pub last_report: f64,
    pub latency_ms: f64,
    pub consecutive_failures: u32,
    pub extra: serde_json::Value,
}

#[derive(Default)]
struct ComponentState {
    alive: bool,
    last_report: f64,
    latency_ms: f64,
    consecutive_failures: u32,
    extra: serde_json::Value,
}

pub struct HealthRegistry {
    components: RwLock<HashMap<String, ComponentState>>,
}

impl HealthRegistry {
    pub fn new() -> Self {
        Self {
            components: RwLock::new(HashMap::new()),
        }
    }

    pub fn report(&self, name: &str, alive: bool, latency_ms: f64, extra: Option<serde_json::Value>) {
        let now = now();
        let mut comps = self.components.write();
        let comp = comps.entry(name.to_string()).or_default();
        comp.alive = alive;
        comp.last_report = now;
        comp.latency_ms = latency_ms;
        comp.extra = extra.unwrap_or(serde_json::json!({}));
        if alive {
            comp.consecutive_failures = 0;
        } else {
            comp.consecutive_failures += 1;
        }
    }

    pub fn report_failure(&self, name: &str, error: &str) {
        let extra = if error.is_empty() {
            serde_json::json!({})
        } else {
            serde_json::json!({"error": error})
        };
        self.report(name, false, 0.0, Some(extra));
    }

    pub fn status(&self, name: &str) -> Option<ComponentHealth> {
        let comps = self.components.read();
        comps.get(name).map(|c| ComponentHealth {
            alive: c.alive,
            last_report: c.last_report,
            latency_ms: c.latency_ms,
            consecutive_failures: c.consecutive_failures,
            extra: c.extra.clone(),
        })
    }

    pub fn snapshot(&self) -> HashMap<String, ComponentHealth> {
        let now = now();
        let comps = self.components.read();
        comps
            .iter()
            .map(|(name, c)| {
                let alive = c.alive && (now - c.last_report) <= STALE_TIMEOUT_SEC;
                (
                    name.clone(),
                    ComponentHealth {
                        alive,
                        last_report: c.last_report,
                        latency_ms: c.latency_ms,
                        consecutive_failures: c.consecutive_failures,
                        extra: c.extra.clone(),
                    },
                )
            })
            .collect()
    }
}

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

// Global singleton
pub static REGISTRY: Lazy<Arc<HealthRegistry>> = Lazy::new(|| Arc::new(HealthRegistry::new()));
