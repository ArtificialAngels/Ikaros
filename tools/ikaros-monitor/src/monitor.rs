//! Monitor engine — JSONL tail, health check, resource monitoring.

use std::fs::File;
use std::io::{BufRead, BufReader, Seek, SeekFrom};
use std::path::PathBuf;
use std::process::Command;
use std::sync::Mutex;
#[cfg(windows)]
use std::os::windows::process::CommandExt;

use serde::Deserialize;
use sysinfo::System;

/// Global engine reference (for filter access from event handler).
pub static GLOBAL_ENGINE: Mutex<Option<MonitorEngine>> = Mutex::new(None);

#[derive(Debug, Clone, Deserialize)]
pub struct MonitorEvent {
    pub ts: f64,
    #[serde(rename = "type")]
    pub event_type: String,
    pub text: String,
    #[serde(default)]
    pub module: Option<String>,
    #[serde(default)]
    pub state: Option<String>,
}

pub struct MonitorEngine {
    lock_path: PathBuf,
    jsonl_path: PathBuf,
    exit_flag: PathBuf,
    main_py: PathBuf,
    last_read_pos: u64,
    auto_restart: bool,
    restart_count: u32,
    filter_idx: usize,
    search_text: String,
    sys: System,
}

// Filter definitions matching the Python version
const FILTERS: &[&[&str]] = &[
    &[],                              // 0: all (no filter)
    &["stt", "voice_activity"],       // 1: voice
    &["llm_reply"],                   // 2: llm
    &["state", "neuro_state"],        // 3: state
    &["error"],                       // 4: error
    &["status", "module_status", "heartbeat"], // 5: system
];

impl MonitorEngine {
    pub fn new(
        lock_path: PathBuf,
        jsonl_path: PathBuf,
        exit_flag: PathBuf,
        main_py: PathBuf,
    ) -> Self {
        let mut sys = System::new();
        sys.refresh_processes(sysinfo::ProcessesToUpdate::All, true);

        let mut engine = Self {
            lock_path,
            jsonl_path,
            exit_flag,
            main_py,
            last_read_pos: 0,
            auto_restart: true,
            restart_count: 0,
            filter_idx: 0,
            search_text: String::new(),
            sys,
        };

        // Initialize read position to end of file
        if let Ok(meta) = std::fs::metadata(&engine.jsonl_path) {
            engine.last_read_pos = meta.len();
        }

        // Store in global for filter access
        *GLOBAL_ENGINE.lock().unwrap() = Some(MonitorEngine {
            lock_path: engine.lock_path.clone(),
            jsonl_path: engine.jsonl_path.clone(),
            exit_flag: engine.exit_flag.clone(),
            main_py: engine.main_py.clone(),
            last_read_pos: engine.last_read_pos,
            auto_restart: engine.auto_restart,
            restart_count: engine.restart_count,
            filter_idx: engine.filter_idx,
            search_text: engine.search_text.clone(),
            sys: System::new(),
        });

        engine
    }

    /// Poll new events from JSONL file.
    pub fn poll_events(&mut self) -> Vec<MonitorEvent> {
        let mut events = Vec::new();

        let file = match File::open(&self.jsonl_path) {
            Ok(f) => f,
            Err(_) => return events,
        };
        let mut reader = BufReader::new(file);

        let file_len = match reader.get_ref().metadata() {
            Ok(m) => m.len(),
            Err(_) => return events,
        };

        if file_len <= self.last_read_pos {
            return events;
        }

        if reader.seek(SeekFrom::Start(self.last_read_pos)).is_err() {
            return events;
        }

        let mut line = String::new();
        while reader.read_line(&mut line).unwrap_or(0) > 0 {
            let trimmed = line.trim();
            if !trimmed.is_empty() {
                if let Ok(ev) = serde_json::from_str::<MonitorEvent>(trimmed) {
                    events.push(ev);
                }
            }
            line.clear();
        }

        self.last_read_pos = file_len;

        // Sync to global engine
        self.sync_global();

        events
    }

    /// Check if pet process is alive.
    pub fn is_pet_alive(&self) -> bool {
        if let Some(pid) = self.get_pet_pid() {
            self.is_pid_alive(pid)
        } else {
            false
        }
    }

    /// Get pet PID from lock file.
    /// Uses tasklist to find python process running main.py (avoids lock file sharing issues).
    fn get_pet_pid(&self) -> Option<u32> {
        // Strategy 1: Find via tasklist (most reliable, no lock contention)
        if let Some(pid) = self.find_pet_via_tasklist() {
            return Some(pid);
        }
        // Strategy 2: Try lock file (may fail if exclusively locked)
        self.read_pid_from_lock_file()
    }

    /// Find pet process via wmic by looking for python running main.py.
    fn find_pet_via_tasklist(&self) -> Option<u32> {
        // Use wmic to get command line (tasklist /V only shows window title)
        let output = Command::new("wmic")
            .args(["process", "where", "name='python.exe'", "get", "ProcessId,CommandLine", "/FORMAT:CSV"])
            .creation_flags(0x08000000) // CREATE_NO_WINDOW
            .output()
            .ok()?;

        let stdout = String::from_utf8_lossy(&output.stdout);
        for line in stdout.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with("Node") {
                continue; // skip header
            }
            // CSV: NodeName,CommandLine,PID
            // Look for main.py in command line
            if line.contains("main.py") {
                // PID is the last comma-separated field
                if let Some(pid_str) = line.rsplit(',').next() {
                    let pid_str = pid_str.trim();
                    if let Ok(pid) = pid_str.parse::<u32>() {
                        return Some(pid);
                    }
                }
            }
        }
        None
    }

    /// Try to read PID from lock file (may fail if exclusively locked).
    fn read_pid_from_lock_file(&self) -> Option<u32> {
        let content = std::fs::read_to_string(&self.lock_path).ok()?;
        for line in content.lines() {
            let line = line.trim();
            if let Some(pid_str) = line.strip_prefix("pid=") {
                return pid_str.parse().ok();
            }
        }
        None
    }

    /// Check if a PID is alive.
    fn is_pid_alive(&self, pid: u32) -> bool {
        // Check from last refresh (get_resources does the refresh)
        if self.sys.process(sysinfo::Pid::from(pid as usize)).is_some() {
            return true;
        }

        // Fallback: tasklist on Windows
        Command::new("tasklist")
            .args(["/FI", &format!("PID eq {}", pid), "/NH"])
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).contains(&pid.to_string()))
            .unwrap_or(false)
    }

    /// Get CPU%, memory MB, uptime seconds for pet process.
    pub fn get_resources(&mut self) -> Option<(f64, f64, f64)> {
        let pid = self.get_pet_pid()?;
        let sys_pid = sysinfo::Pid::from(pid as usize);

        self.sys.refresh_processes(sysinfo::ProcessesToUpdate::All, true);
        self.sys.refresh_cpu_usage();

        if let Some(proc) = self.sys.process(sys_pid) {
            let cpu = proc.cpu_usage();
            let mem = proc.memory() as f64 / (1024.0 * 1024.0);
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs_f64();
            let uptime = now - proc.start_time() as f64 / 1000.0;
            Some((cpu as f64, mem, uptime))
        } else {
            None
        }
    }

    /// Restart the pet process.
    pub fn restart_pet(&self) {
        // Remove exit flag
        let _ = std::fs::remove_file(&self.exit_flag);

        // Find python executable
        let python = std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|d| d.to_path_buf()))
            .map(|d| d.join("python.exe"))
            .filter(|p| p.exists())
            .unwrap_or_else(|| PathBuf::from("python"));

        let _ = Command::new(&python)
            .arg(&self.main_py)
            .current_dir(self.main_py.parent().unwrap_or(&self.main_py))
            .creation_flags(0x00000008) // DETACHED_PROCESS
            .spawn();
    }

    pub fn set_auto_restart(&mut self, enabled: bool) {
        self.auto_restart = enabled;
        self.sync_global();
    }

    pub fn set_filter(&mut self, idx: usize) {
        self.filter_idx = idx;
        self.sync_global();
    }

    pub fn set_search(&mut self, text: String) {
        self.search_text = text.to_lowercase();
        self.sync_global();
    }

    pub fn clear_events(&mut self) {
        // Just reset read position to current end
        if let Ok(meta) = std::fs::metadata(&self.jsonl_path) {
            self.last_read_pos = meta.len();
        }
        self.sync_global();
    }

    /// Check if an event type passes the current filter.
    pub fn passes_filter(&self, etype: &str) -> bool {
        if self.filter_idx == 0 {
            // "all" filter — still check search
            if !self.search_text.is_empty() {
                // Search is checked in handle_event via text match
                return true;
            }
            return true;
        }

        let filter_types = match FILTERS.get(self.filter_idx) {
            Some(types) => {
                if types.is_empty() {
                    return true; // all
                }
                types
            }
            None => return true,
        };

        filter_types.contains(&etype)
    }

    /// Sync state to global engine.
    fn sync_global(&self) {
        if let Ok(mut global) = GLOBAL_ENGINE.lock() {
            if let Some(ref mut eng) = *global {
                eng.filter_idx = self.filter_idx;
                eng.search_text = self.search_text.clone();
                eng.auto_restart = self.auto_restart;
            }
        }
    }
}
