//! soul_loader.rs — axiom.md 缓存读取 (Soul / Identity loader)
//!
//! Reads the Ikaros axiom.md file at startup (lazy, cached forever).
//! Provides the soul injection text for system prompts.
//!
//! The axiom file defines Ikaros's core identity, values, and behavioral rules.
//! It is read once and cached in a `Lazy<String>` — to refresh after editing,
//! call `invalidate_cache()` (or restart the bridge).

use once_cell::sync::Lazy;
use parking_lot::Mutex;
use std::path::PathBuf;

/// Cached axiom content. Loaded once on first access, never reloaded
/// unless `invalidate_cache()` is called.
static AXIOM_CACHE: Lazy<Mutex<Option<String>>> = Lazy::new(|| Mutex::new(None));


// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Get the path to axiom.md.
/// Priority:
///   1. `IKAROS_AXIOM_PATH` env var
///   2. `<project_root>/data/hermes-agent/ikaros-identity/axiom.md`
pub fn axiom_path() -> PathBuf {
    if let Ok(p) = std::env::var("IKAROS_AXIOM_PATH") {
        return PathBuf::from(p);
    }
    // Use the same project_root logic as main.rs
    let root = super::project_root();
    root.join("data")
        .join("hermes-agent")
        .join("ikaros-identity")
        .join("axiom.md")
}

/// Get the cached axiom text. Returns `&str` reference to the cached value.
///
/// On first call, reads axiom.md from disk. If the file doesn't exist,
/// returns an empty string (graceful degradation).
pub fn get_soul_injection() -> String {
    let mut cache = AXIOM_CACHE.lock();
    if let Some(ref cached) = *cache {
        return cached.clone();
    }

    // Load from disk
    let path = axiom_path();
    let content = match std::fs::read_to_string(&path) {
        Ok(c) => {
            tracing::info!(path = %path.display(), bytes = c.len(), "axiom.md loaded");
            c
        }
        Err(e) => {
            tracing::warn!(path = %path.display(), error = %e, "axiom.md not found — soul injection empty");
            String::new()
        }
    };

    *cache = Some(content.clone());
    content
}

/// Invalidate the axiom cache. Next call to `get_soul_injection()` will re-read from disk.
/// Useful after manually editing axiom.md without restarting the bridge.
pub fn invalidate_cache() {
    let mut cache = AXIOM_CACHE.lock();
    *cache = None;
    tracing::info!("axiom cache invalidated — will reload on next access");
}

/// Get axiom file size in bytes (for diagnostics).
pub fn axiom_file_size() -> Option<u64> {
    std::fs::metadata(axiom_path()).ok().map(|m| m.len())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_axiom_path_not_empty() {
        let path = axiom_path();
        assert!(path.to_string_lossy().contains("axiom.md"));
    }

    #[test]
    fn test_get_soul_injection_returns_string() {
        // May be empty if axiom.md doesn't exist in test env
        let result = get_soul_injection();
        // Just verify it doesn't panic
        assert!(result.len() >= 0);
    }

    #[test]
    fn test_invalidate_cache() {
        invalidate_cache();
        // Should not panic
    }
}
