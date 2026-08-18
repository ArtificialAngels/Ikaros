//! detect-root — Ikaros root directory detector (native Rust replacement for detect-root.ps1)
//!
//! Priority:
//!   1. IKAROS_ROOT env var
//!   2. Derive from exe location (exe → scripts/ → Ikaros-environment/ → root)
//!   3. Walk up from CWD looking for marker files
//!   4. Drive scan for `<letter>:/Ikaros`
//!
//! Output: resolved root path to stdout (single line, no trailing newline issues).
//! 2026-08-18: removed HERMES_ROOT legacy compat and hermes-agent marker.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

/// Marker file that must exist in the Ikaros root to confirm detection.
const MARKER: &str = r"runtime\portable-python\python.exe";

fn is_valid_root(path: &Path) -> bool {
    path.join(MARKER).is_file()
}

/// Priority 1: IKAROS_ROOT env var.
fn from_env_ikaros() -> Option<PathBuf> {
    let val = env::var("IKAROS_ROOT").ok()?;
    let p = PathBuf::from(val);
    if is_valid_root(&p) { Some(p) } else { None }
}

/// Priority 2: Derive from exe location.
/// exe is at <root>/core/env/scripts/detect-root.exe
/// (or <root>/core/env/detect-root/target/release/detect-root.exe during dev)
fn from_exe_location() -> Option<PathBuf> {
    let exe = env::current_exe().ok()?;
    let mut dir = exe.parent()?.to_path_buf();
    // Walk up from exe dir, looking for the Ikaros marker
    loop {
        if dir.join("core/env").is_dir()
            && dir.join(MARKER).is_file()
        {
            return Some(dir);
        }
        if !dir.pop() {
            break;
        }
    }
    None
}

/// Priority 3: Walk up from CWD looking for marker files.
fn from_cwd() -> Option<PathBuf> {
    let cwd = env::current_dir().ok()?;
    let mut dir = Some(cwd.as_path());
    while let Some(d) = dir {
        if d.join(MARKER).is_file()
            && d.join("core/env").is_dir()
        {
            return Some(d.to_path_buf());
        }
        dir = d.parent();
    }
    None
}

/// Priority 4: Drive scan (slow, last resort).
fn from_drive_scan() -> Option<PathBuf> {
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ".chars() {
        let candidate = PathBuf::from(format!("{letter}:/Ikaros"));
        if candidate.is_dir() && is_valid_root(&candidate) {
            return Some(candidate);
        }
    }
    None
}

fn main() {
    let root = from_env_ikaros()
        .or_else(from_exe_location)
        .or_else(from_cwd)
        .or_else(from_drive_scan);

    match root {
        Some(root) => {
            let path = fs::canonicalize(&root).unwrap_or(root);
            println!("{}", path.display());
        }
        None => {
            eprintln!("IKAROS_ROOT not found. Set IKAROS_ROOT env var or ensure runtime\\portable-python\\python.exe exists.");
            std::process::exit(1);
        }
    }
}
