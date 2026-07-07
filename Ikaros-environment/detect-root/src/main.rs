//! detect-root — Ikaros root directory detector (native Rust replacement for detect-root.ps1)
//!
//! Detection priorities:
//!   1. IKAROS_ROOT env var
//!   2. HERMES_ROOT env var (legacy compat)
//!   3. Derive from exe location (exe → scripts/ → Ikaros-environment/ → root)
//!   4. Walk up from CWD looking for marker files
//!   5. Scan drive letters for E:\Ikaros etc.
//!
//! Output: resolved root path to stdout (single line, no trailing newline issues).
//! Exit 0 on success, exit 1 with message to stderr on failure.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

/// Marker file that must exist in the Ikaros root to confirm detection.
const MARKER: &str = r"portable-python\python.exe";

fn is_valid_root(path: &Path) -> bool {
    path.join(MARKER).is_file()
}

/// Priority 1: IKAROS_ROOT env var.
fn from_env_ikaros() -> Option<PathBuf> {
    let val = env::var("IKAROS_ROOT").ok()?;
    let p = PathBuf::from(val.trim());
    if is_valid_root(&p) { Some(p) } else { None }
}

/// Priority 2: HERMES_ROOT env var (legacy compat).
fn from_env_hermes() -> Option<PathBuf> {
    let val = env::var("HERMES_ROOT").ok()?;
    let p = PathBuf::from(val.trim());
    if is_valid_root(&p) { Some(p) } else { None }
}

/// Priority 3: Derive from exe location.
/// exe is at <root>/Ikaros-environment/scripts/detect-root.exe
/// (or <root>/Ikaros-environment/detect-root/target/release/detect-root.exe during dev)
fn from_exe_location() -> Option<PathBuf> {
    let exe = env::current_exe().ok()?;
    // Walk up from exe dir, looking for the Ikaros-environment marker
    let mut dir = exe.parent()?;
    for _ in 0..10 {
        if dir.join("Ikaros-environment").is_dir()
            && dir.join(MARKER).is_file()
        {
            return Some(dir.to_path_buf());
        }
        dir = dir.parent()?;
    }
    None
}

/// Priority 4: Walk up from CWD looking for marker files.
fn from_cwd_walk() -> Option<PathBuf> {
    let mut dir = env::current_dir().ok()?;
    loop {
        if dir.join(MARKER).is_file()
            && dir.join("hermes-agent").is_dir()
            && dir.join("Ikaros-environment").is_dir()
        {
            return Some(dir);
        }
        if !dir.pop() {
            break;
        }
    }
    None
}

/// Priority 5: Scan available drive letters for <drive>:\Ikaros.
fn from_drive_scan() -> Option<PathBuf> {
    // Common drive letters
    for letter in b'A'..=b'Z' {
        let candidate = PathBuf::from(format!("{}:\\Ikaros", letter as char));
        if candidate.is_dir() && is_valid_root(&candidate) {
            return Some(candidate);
        }
    }
    None
}

fn detect() -> Option<PathBuf> {
    from_env_ikaros()
        .or_else(from_env_hermes)
        .or_else(from_exe_location)
        .or_else(from_cwd_walk)
        .or_else(from_drive_scan)
}

fn main() {
    match detect() {
        Some(root) => {
            // Canonicalize to resolve symlinks/junctions, then strip \\?\ prefix
            let path = fs::canonicalize(&root).unwrap_or(root);
            let display = path.display().to_string();
            let clean = display.strip_prefix(r"\\?\").unwrap_or(&display);
            println!("{}", clean);
        }
        None => {
            eprintln!("IKAROS_ROOT not found. Set IKAROS_ROOT env var or ensure portable-python\\python.exe exists.");
            std::process::exit(1);
        }
    }
}
