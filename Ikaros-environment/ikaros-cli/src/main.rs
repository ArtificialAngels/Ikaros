// Ikaros unified launcher
// Replaces the bin/*.bat soup with a single order-dispatched Rust binary.
// Pure std (no external crates) for zero-dependency, offline-friendly builds.
//
// Orders:
//   start           Bring up backend + interactive frontend menu
//   sleep | stop    Stop all Ikaros services
//   studio          Hermes Studio (:8649)
//   dashboard       Hermes Dashboard (:9119)
//   desktop         Hermes Desktop app
//   think [args]    V5.1 self-think loop
//   mem [args]      Memory store CLI (v5/store.py)
//   live2d [mode]   Desktop Pet (Tauri) start|stop|status
//   verify [--quick]  V5.1 tests
//   soul-sync       V5 soul -> Hermes SOUL.md
//   ws-restart      Restart Voice WS (:7870)
//   screen-monitor [args]  Screen activity monitor

use std::collections::HashMap;
use std::env;
use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio, Child};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

// ---------------------------------------------------------------------------
// Root detection (mirrors Ikaros-environment/detect-root/src/main.rs)
// ---------------------------------------------------------------------------

fn is_valid_root(p: &Path) -> bool {
    p.join("runtime").join("portable-python").join("python.exe").is_file()
}

fn detect_root() -> Option<PathBuf> {
    // 1. IKAROS_ROOT
    if let Ok(v) = env::var("IKAROS_ROOT") {
        let p = PathBuf::from(v.trim());
        if is_valid_root(&p) {
            return Some(p);
        }
    }
    // 2. HERMES_ROOT (legacy compat)
    if let Ok(v) = env::var("HERMES_ROOT") {
        let p = PathBuf::from(v.trim());
        if is_valid_root(&p) {
            return Some(p);
        }
    }
    // 3. From exe location
    if let Ok(exe) = env::current_exe() {
        if let Some(mut dir) = exe.parent() {
            for _ in 0..10 {
                if dir.join("Ikaros-environment").is_dir() && is_valid_root(dir) {
                    return Some(dir.to_path_buf());
                }
                match dir.parent() {
                    Some(p) => dir = p,
                    None => break,
                }
            }
        }
    }
    // 4. Walk up from CWD
    if let Ok(cwd) = env::current_dir() {
        let mut dir = cwd;
        loop {
            if is_valid_root(&dir)
                && dir.join("hermes-agent").is_dir()
                && dir.join("Ikaros-environment").is_dir()
            {
                return Some(dir);
            }
            match dir.parent() {
                Some(p) => dir = p.to_path_buf(),
                None => break,
            }
        }
    }
    // 5. Scan drive letters for <drive>:\Ikaros
    for letter in b'A'..=b'Z' {
        let cand = PathBuf::from(format!("{}:\\Ikaros", letter as char));
        if cand.is_dir() && is_valid_root(&cand) {
            return Some(cand);
        }
    }
    None
}

// ---------------------------------------------------------------------------
// Environment (mirrors Ikaros-environment/ikaros-env.bat)
// ---------------------------------------------------------------------------

fn build_env(root: &Path) -> HashMap<String, String> {
    let pp = root.join("runtime").join("portable-python");
    let mut e: HashMap<String, String> = HashMap::new();

    let s = |p: PathBuf| p.to_string_lossy().into_owned();

    e.insert("IKAROS_ROOT".into(), s(root.to_path_buf()));
    e.insert("IKAROS_PYTHON".into(), s(root.join("runtime").join("portable-python").join("python.exe")));
    e.insert("IKAROS_RUNTIME".into(), s(root.join("runtime")));
    e.insert("IKAROS_NODE".into(), s(root.join("runtime").join("node").join("node.exe")));
    e.insert("IKAROS_DATA".into(), s(root.join("data")));
    e.insert("IKAROS_BIN".into(), s(root.join("bin")));
    e.insert("IKAROS_CONFIG".into(), s(root.join("config")));
    e.insert("IKAROS_MODULES".into(), s(root.join("modules")));
    e.insert("IKAROS_LOGS".into(), s(root.join("data").join("logs")));
    e.insert("IKAROS_HERMES_AGENT".into(), s(root.join("hermes-agent")));
    e.insert("IKAROS_HERMES_HOME".into(), s(root.join("data").join("hermes-agent")));
    e.insert("IKAROS_STUDIO".into(), s(root.join("hermes-studio")));
    e.insert("IKAROS_STUDIO_LOGS".into(), s(root.join("data").join("logs").join("hermes-studio")));
    e.insert("IKAROS_STUDIO_DATA".into(), s(root.join("data").join("hermes-studio")));
    e.insert("HERMES_BIN".into(), s(root.join("hermes-agent").join("venv").join("Scripts").join("hermes.exe")));
    e.insert("HERMES_AGENT_CLI_PYTHON".into(), s(root.join("hermes-agent").join("venv").join("Scripts").join("python.exe")));
    e.insert("HERMES_AGENT_BRIDGE_PYTHON".into(), s(root.join("hermes-agent").join("venv").join("Scripts").join("python.exe")));
    e.insert("HERMES_AGENT_NODE".into(), s(root.join("runtime").join("node").join("node.exe")));
    e.insert("IKAROS_MEMORY".into(), s(root.join("Ikaros-memory")));
    e.insert("IKAROS_MEMORY_DATA".into(), s(root.join("Ikaros-memory").join("data")));
    e.insert("IKAROS_MEMORY_MODELS".into(), s(root.join("Ikaros-memory").join("models")));
    e.insert("IKAROS_MEMORY_SCRIPT".into(), s(root.join("Ikaros-memory").join("v4").join("store.py")));
    e.insert("IKAROS_LIVE2D".into(), s(root.join("Ikaros-Live2D")));
    e.insert("IKAROS_NODE_MODULES".into(), s(root.join("runtime").join("node").join("node_modules")));
    e.insert("IKAROS_RUST".into(), s(root.join("runtime").join("rust")));

    let llama_ver = env::var("IKAROS_LLAMA_VERSION").unwrap_or_else(|_| "b10000-cuda".into());
    e.insert("IKAROS_LLAMA_VERSION".into(), llama_ver.clone());
    let llama_dir = root.join("runtime").join("llama").join(&llama_ver);
    e.insert("IKAROS_LLAMA_DIR".into(), s(llama_dir.clone()));
    e.insert("IKAROS_LLAMA_SERVER".into(), s(llama_dir.join("llama-server.exe")));
    e.insert("IKAROS_MODEL_EMBEDDING".into(), s(root.join("Ikaros-memory").join("models").join("nomic-embed-text-v2-moe.f32.gguf")));

    e.insert("IKAROS_PORT_EMBEDDING".into(), "8587".into());
    e.insert("IKAROS_PORT_LLM".into(), "8080".into());
    e.insert("IKAROS_PORT_BRIDGE".into(), "7860".into());
    e.insert("IKAROS_PORT_LIVE2D_WEBVIEW".into(), "8648".into());
    e.insert("IKAROS_PORT_LIVE2D_WEBVIEW_INTERNAL".into(), "8649".into());
    e.insert("IKAROS_PORT_LLAMA".into(), "8080".into());

    e.insert("PYTHONIOENCODING".into(), "utf-8".into());
    e.insert("PYTHONUTF8".into(), "1".into());
    e.insert("PYTHONPATH".into(), format!("{};{}", s(root.to_path_buf()), s(root.join("hermes-agent"))));
    e.insert("NODE_PATH".into(), s(root.join("runtime").join("node").join("node_modules")));
    // NOTE: Do NOT set PYTHONHOME to an empty string here. An empty PYTHONHOME makes
    // CPython skip exe-based home detection and mis-resolve its stdlib prefix, which
    // crashes the Hermes agent-bridge (a venv python) with 0xC0000005 on startup.
    // portable-python is an embedded build (ignores PYTHONHOME via python312._pth);
    // the agent-bridge venv resolves its home from pyvenv.cfg, so leaving PYTHONHOME
    // unset is correct for both.

    // HERMES_* compat
    e.insert("HERMES_ROOT".into(), s(root.to_path_buf()));
    e.insert("HERMES_HOME".into(), s(root.join("data").join("hermes-agent")));
    e.insert("HERMES_PYTHON".into(), e["IKAROS_PYTHON"].clone());
    e.insert("HERMES_RUNTIME".into(), e["IKAROS_RUNTIME"].clone());
    e.insert("HERMES_AGENT_ROOT".into(), e["IKAROS_HERMES_AGENT"].clone());

    // PATH: prepend project dirs to inherited PATH
    let mut path_parts: Vec<String> = vec![
        e["IKAROS_RUST"].clone() + "\\bin",
        e["IKAROS_LLAMA_DIR"].clone(),
        e["IKAROS_RUNTIME"].clone(),
        e["IKAROS_RUNTIME"].clone() + "\\node",
        s(pp.join("Scripts")),
        s(pp),
    ];
    if let Ok(old) = env::var("PATH") {
        path_parts.push(old);
    }
    e.insert("PATH".into(), path_parts.join(";"));

    e
}

fn child_env(base: &HashMap<String, String>, overrides: &[(&str, &str)]) -> HashMap<String, String> {
    let mut m: HashMap<String, String> = env::vars().collect();
    for (k, v) in base {
        m.insert(k.clone(), v.clone());
    }
    for (k, v) in overrides {
        m.insert(k.to_string(), v.to_string());
    }
    m
}

// ---------------------------------------------------------------------------
// Process helpers
// ---------------------------------------------------------------------------

fn spawn_hidden(
    cmd: &str,
    args: &[String],
    env: &HashMap<String, String>,
    cwd: Option<&Path>,
    log: Option<&Path>,
) -> io::Result<Child> {
    let mut c = Command::new(cmd);
    c.args(args);
    c.envs(env.iter().map(|(k, v)| (k.clone(), v.clone())));
    if let Some(cwd) = cwd {
        c.current_dir(cwd);
    }
    #[cfg(windows)]
    c.creation_flags(CREATE_NO_WINDOW);
    match log {
        Some(p) => {
            if let Some(parent) = p.parent() {
                let _ = fs::create_dir_all(parent);
            }
            let f = fs::File::create(p)?;
            let f2 = f.try_clone()?;
            c.stdout(Stdio::from(f));
            c.stderr(Stdio::from(f2));
        }
        None => {
            c.stdout(Stdio::null());
            c.stderr(Stdio::null());
        }
    }
    c.spawn()
}

// Run a child. If visible, inherit stdio (user sees output in the launcher console).
// If not visible, discard stdout/stderr. Returns the exit code.
fn run_child(cmd: &str, args: &[String], env: &HashMap<String, String>, cwd: Option<&Path>, visible: bool) -> i32 {
    let mut c = Command::new(cmd);
    c.args(args);
    c.envs(env.iter().map(|(k, v)| (k.clone(), v.clone())));
    if let Some(cwd) = cwd {
        c.current_dir(cwd);
    }
    if !visible {
        c.stdout(Stdio::null());
        c.stderr(Stdio::null());
    }
    match c.status() {
        Ok(s) => s.code().unwrap_or(1),
        Err(e) => {
            eprintln!("[ikaros] failed to run {}: {}", cmd, e);
            1
        }
    }
}

fn tcp_connect(port: u16) -> bool {
    use std::net::{TcpStream, ToSocketAddrs};
    if let Ok(addrs) = ("127.0.0.1", port).to_socket_addrs() {
        for a in addrs {
            if TcpStream::connect_timeout(&a, Duration::from_secs(1)).is_ok() {
                return true;
            }
        }
    }
    false
}

fn wait_for_file(path: &Path, timeout_secs: u64) -> bool {
    let start = Instant::now();
    let limit = Duration::from_secs(timeout_secs);
    loop {
        if path.exists() {
            return true;
        }
        if start.elapsed() > limit {
            return false;
        }
        thread::sleep(Duration::from_secs(2));
    }
}

fn wait_for_port(port: u16, timeout_secs: u64) -> bool {
    let start = Instant::now();
    let limit = Duration::from_secs(timeout_secs);
    loop {
        if tcp_connect(port) {
            return true;
        }
        if start.elapsed() > limit {
            return false;
        }
        thread::sleep(Duration::from_secs(2));
    }
}

fn kill_port(port: u16) {
    if let Ok(out) = Command::new("netstat").args(["-ano"]).output() {
        let s = String::from_utf8_lossy(&out.stdout);
        for line in s.lines() {
            if line.contains(&format!(":{}", port)) && line.contains("LISTENING") {
                if let Some(pid) = line.split_whitespace().last() {
                    let pid = pid.trim();
                    if !pid.is_empty() {
                        let _ = Command::new("taskkill").args(["/F", "/PID", pid]).output();
                    }
                }
            }
        }
    }
}

fn kill_image(name: &str) {
    let _ = Command::new("taskkill").args(["/F", "/IM", name, "/T"]).output();
}

/// Force-kill any process whose command line contains `substr`.
/// Used as a robust fallback because the watchdog's own `--stop` relies on
/// `os.kill(SIGTERM)` which is unreliable on Windows (signal only reaches the
/// main thread and the handler does not actually exit the run loop).
fn kill_by_cmdline(substr: &str) {
    #[cfg(windows)]
    {
        let safe = substr.replace('\'', "''");
        let ps = format!(
            "Get-CimInstance Win32_Process | Where-Object {{ $null -ne $_.CommandLine -and $_.CommandLine -like '*{s}*' }} | ForEach-Object {{ taskkill.exe /F /T /PID $($_.ProcessId) 2>$null }}",
            s = safe
        );
        let _ = Command::new("powershell")
            .args(["-NoProfile", "-Command", &ps])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .output();
    }
    #[cfg(not(windows))]
    {
        let _ = substr;
    }
}

fn open_browser(url: &str) {
    #[cfg(windows)]
    {
        let mut c = Command::new("cmd");
        c.args(["/c", "start", "", url]);
        c.creation_flags(CREATE_NO_WINDOW);
        let _ = c.spawn();
    }
    #[cfg(not(windows))]
    {
        let _ = Command::new("xdg-open").arg(url).spawn();
    }
}

fn ensure_pet_junction(root: &Path) {
    let nm = root.join("Ikaros-Live2D").join("node_modules");
    let target = root.join("runtime").join("node").join("node_modules");
    if !target.exists() {
        return;
    }
    if nm.exists() {
        let _ = Command::new("cmd")
            .args(["/c", "rmdir", &nm.to_string_lossy()])
            .output();
    }
    let _ = Command::new("cmd")
        .args([
            "/c",
            "mklink",
            "/J",
            &nm.to_string_lossy(),
            &target.to_string_lossy(),
        ])
        .output();
}

fn pet_running() -> bool {
    if let Ok(out) = Command::new("tasklist")
        .args(["/FI", "IMAGENAME eq ikaros-desktop-pet.exe", "/FO", "CSV", "/NH"])
        .output()
    {
        let s = String::from_utf8_lossy(&out.stdout);
        return s.contains("ikaros-desktop-pet.exe");
    }
    false
}

fn read_choice_with_default(def: &str) -> String {
    let (tx, rx) = mpsc::channel::<String>();
    thread::spawn(move || {
        let mut buf = String::new();
        let _ = io::stdin().read_line(&mut buf);
        let _ = tx.send(buf);
    });
    match rx.recv_timeout(Duration::from_secs(30)) {
        Ok(s) => {
            let t = s.trim().to_string();
            if t.is_empty() {
                def.to_string()
            } else {
                t
            }
        }
        Err(_) => def.to_string(),
    }
}

// path helpers
fn py(root: &Path) -> String {
    root.join("runtime").join("portable-python").join("python.exe")
        .to_string_lossy()
        .into_owned()
}
fn bin_dir(root: &Path) -> PathBuf {
    root.join("bin")
}
fn logs_dir(root: &Path) -> PathBuf {
    root.join("data").join("logs")
}

// ---------------------------------------------------------------------------
// Orders
// ---------------------------------------------------------------------------

fn order_start(root: &Path, env: &HashMap<String, String>) {
    println!("============================================================");
    println!("  Ikaros");
    println!();
    println!("  Pet:       Ikaros Desktop Pet v2  (Tauri v2, Live2D)");
    println!("  Studio:    http://127.0.0.1:8649");
    println!("  Dashboard: http://127.0.0.1:9119");
    println!("  Voice WS:  ws://127.0.0.1:7870/v1/voice/ws");
    println!("  Memory:    :8587 embedding + :8080 local LLM");
    println!("  Think:     V5.1 metacog cycle");
    println!("  Logs:      {}", env["IKAROS_LOGS"]);
    println!("  Stop:      ikaros sleep");
    println!("============================================================");

    // 0a. Dashboard WS token
    let token = "ikaros-fixed-token-20260715";
    let mut env2 = env.clone();
    env2.insert("HERMES_DASHBOARD_SESSION_TOKEN".into(), token.into());
    if let Ok(mut f) = fs::File::create(root.join(".dash_token")) {
        let _ = f.write_all(token.as_bytes());
    }

    // 0b. Quick self-check (non-fatal: a flaky unit test must not block startup)
    println!("[0b] Running quick self-check...");
    let rc = order_verify_impl(root, &env2, true);
    if rc != 0 {
        println!("[WARN] verify --quick failed (non-fatal). Continuing startup.");
    } else {
        println!("      self-check PASS");
    }

    // 1. Stop stale instances
    println!("[1] Stopping old instances...");
    order_sleep_impl(root, &env2);
    println!("      [1] done");

    // 2. Memory watchdog
    println!("[2] Starting Memory Services...");
    println!("      Embedding :8587 (nomic-embed-text)");
    println!("      LLM :8080 (local LLM)");
    let wd = py(root);
    let wd_script = bin_dir(root).join("ikaros-memory-watchdog.py");
    let _ = spawn_hidden(
        &wd,
        &[wd_script.to_string_lossy().into_owned(), "--detach".into()],
        &env2,
        None,
        Some(&logs_dir(root).join("memory-watchdog.log")),
    );
    let ep = root.join("Ikaros-memory").join("data").join("endpoints.json");
    if wait_for_file(&ep, 80) {
        println!("      Memory endpoints ready");
    } else {
        println!("      [WARN] Memory timeout");
    }

    // 2b. Voice WS
    println!("[2b] Launching Voice WS (:7870)...");
    let vw = bin_dir(root).join("ikaros-voice-ws.py");
    let _ = spawn_hidden(
        &wd,
        &[vw.to_string_lossy().into_owned()],
        &env2,
        None,
        Some(&logs_dir(root).join("voice-ws.log")),
    );
    if wait_for_port(7870, 40) {
        println!("      Voice WS ready");
    } else {
        println!("      [WARN] Voice WS timeout");
    }

    // 2c. Think loop
    println!("[2c] Launching V5.1 self-think loop...");
    let think = root.join("Ikaros-memory").join("v5").join("think.py");
    let _ = spawn_hidden(
        &wd,
        &[think.to_string_lossy().into_owned(), "--watch".into()],
        &env2,
        None,
        Some(&logs_dir(root).join("think.log")),
    );

    // 2d. Soul sync
    println!("[2d] Syncing V5 soul to Hermes...");
    let soul = bin_dir(root).join("ikaros-soul-sync.py");
    let rc = run_child(
        &wd,
        &[soul.to_string_lossy().into_owned(), "--once".into()],
        &env2,
        None,
        false,
    );
    if rc == 0 {
        println!("      SOUL.md synced");
    } else {
        println!("      [WARN] soul-sync failed (non-fatal)");
    }

    // 3. Desktop Pet
    println!("[3] Launching Desktop Pet (Tauri)...");
    ensure_pet_junction(root);
    let pet = root
        .join("Ikaros-Live2D")
        .join("src-tauri")
        .join("target")
        .join("release")
        .join("ikaros-desktop-pet.exe");
    if pet.exists() {
        let mut c = Command::new(&pet);
        c.envs(env2.iter().map(|(k, v)| (k.clone(), v.clone())));
        let _ = c.spawn();
        println!("      Pet started");
    } else {
        println!("      [WARN] pet exe not found, skipping");
    }

    // 5. Interactive frontend menu
    println!();
    println!("Which frontend would you like to open?");
    println!("  1) Studio    (default web UI + Agent)  :8649");
    println!("  2) Dashboard (Hermes Dashboard)        :9119");
    println!("  3) Desktop  (Hermes Desktop app)");
    println!("  4) All of the above");
    println!("  0) None (backend only)");
    print!("Choice [1]: ");
    let _ = io::stdout().flush();
    let choice = read_choice_with_default("1");
    match choice.as_str() {
        "2" => {
            order_dashboard_impl(root, &env2);
        }
        "3" => {
            order_desktop_impl(root, &env2);
        }
        "4" => {
            order_studio_impl(root, &env2);
            order_dashboard_impl(root, &env2);
            order_desktop_impl(root, &env2);
        }
        "0" => {}
        _ => {
            order_studio_impl(root, &env2);
        }
    }

    println!("============================================================");
    println!("  Ikaros is ready!");
    println!("  Stop: ikaros sleep");
    println!("============================================================");
    println!();
    println!("  Running in foreground. Press ENTER to stop all services");
    println!("  (or Ctrl+C to detach - services keep running in background).");
    let mut buf = String::new();
    let _ = io::stdin().read_line(&mut buf);
    println!("[stop] Stopping all Ikaros services...");
    order_sleep_impl(root, env);
    println!("[stop] All services stopped. Bye!");
}

fn order_sleep_impl(root: &Path, env: &HashMap<String, String>) {
    println!("[sleep] Stopping Ikaros...");
    let wd = py(root);

    println!("[0] Flushing V5 affect state...");
    let mem = root.join("Ikaros-memory");
    let code = format!(
        "import sys; sys.path.insert(0, r'{}'); from v5.affect import flush; flush()",
        mem.to_string_lossy()
    );
    let _ = run_child(&wd, &["-c".into(), code], env, None, false);
    println!("      done");

    println!("[1] Stopping Memory Watchdog...");
    let wds = bin_dir(root).join("ikaros-memory-watchdog.py");
    // Graceful stop first (writes V5 state, releases servers cleanly if it works).
    let _ = run_child(&wd, &[wds.to_string_lossy().into_owned(), "--stop".into()], env, None, false);
    // Fallback: the watchdog's --stop uses os.kill(SIGTERM) which is unreliable on
    // Windows, so force-kill by the ports it holds and by its command line.
    kill_port(8587);
    kill_port(8080);
    kill_by_cmdline("ikaros-memory-watchdog.py");
    println!("      done");

    println!("[2] Stopping Voice WS (:7870)...");
    kill_port(7870);
    println!("      done");

    println!("[3] Stopping Desktop Pet...");
    kill_image("ikaros-desktop-pet.exe");
    println!("      done");

    println!("[4] Stopping Hermes Desktop...");
    kill_image("Hermes.exe");
    println!("      done");

    println!("[5] Safety sweep (llama-server)...");
    kill_image("llama-server.exe");
    println!("      done");

    println!("[6] Stopping Download Accelerator (gopeed :9999)...");
    kill_image("gopeed-web.exe");
    println!("      done");

    println!("[7] Stopping Hermes Studio (:8647/:8648/:8649)...");
    kill_port(8647);
    kill_port(8648);
    kill_port(8649);
    kill_image("node.exe");
    println!("      done");

    println!("[8] Stopping Hermes Dashboard (:9119)...");
    kill_port(9119);
    println!("      done");

    println!("[sleep] Done.");
}

fn order_studio_impl(root: &Path, env: &HashMap<String, String>) -> i32 {
    let studio = root.join("hermes-studio");
    let studio_logs = logs_dir(root).join("hermes-studio");
    let _ = fs::create_dir_all(&studio_logs);

    let mut cenv = child_env(env, &[]);
    cenv.insert(
        "PATH".into(),
        format!("{};{}", root.join("runtime").join("node").to_string_lossy(), env["PATH"]),
    );
    cenv.insert("NODE_PATH".into(), String::new());
    cenv.insert(
        "HERMES_WEB_UI_HOME".into(),
        root.join("data").join("hermes-studio").to_string_lossy().into_owned(),
    );

    if !studio.join("node_modules").exists() {
        println!("[install] node_modules missing - running npm install (several minutes)...");
        // NOTE: Windows cannot CreateProcess a .cmd directly, so wrap npm in `cmd /c`.
        // `npm` in runtime/node is npm.cmd (needs cmd.exe interpreter).
        let rc = run_child("cmd", &["/c".into(), "npm".into(), "install".into()], &cenv, Some(&studio), true);
        if rc != 0 {
            println!("[FATAL] npm install failed.");
            return rc;
        }
    } else {
        println!("[skip] node_modules present");
    }

    println!("Starting Hermes Studio (dev) on http://127.0.0.1:8649 ...");
    let log = studio_logs.join("hermes-studio.log");
    if log.exists() {
        let _ = fs::remove_file(&log);
    }
    // NOTE: wrap `npm run dev` in `cmd /c` — npm is a .cmd on Windows and cannot be
    // executed directly via CreateProcess (this is what prevented :8649 from binding).
    let _ = spawn_hidden("cmd", &["/c".into(), "npm".into(), "run".into(), "dev".into()], &cenv, Some(&studio), Some(&log));
    if wait_for_port(8649, 180) {
        println!("Hermes Studio ready: http://127.0.0.1:8649");
    } else {
        println!("[WARN] Hermes Studio did not respond on :8649 within timeout (may still be starting).");
    }
    open_browser("http://127.0.0.1:8649");
    0
}

fn order_dashboard_impl(root: &Path, env: &HashMap<String, String>) -> i32 {
    let mut cenv = child_env(env, &[]);
    cenv.insert("HERMES_SERVE_HEADLESS".into(), String::new());
    cenv.insert("HERMES_WEB_DIST".into(), String::new());

    if tcp_connect(9119) {
        println!("Dashboard already running on :9119. Opening browser...");
        open_browser("http://127.0.0.1:9119");
        return 0;
    }

    let hermes = env["HERMES_BIN"].clone();
    println!("Starting Hermes Dashboard on :9119...");
    let log = logs_dir(root).join("dashboard.log");
    let _ = spawn_hidden(
        &hermes,
        &[
            "dashboard".into(),
            "--port".into(),
            "9119".into(),
            "--no-open".into(),
            "--skip-build".into(),
        ],
        &cenv,
        None,
        Some(&log),
    );
    if wait_for_port(9119, 60) {
        println!("Dashboard is ready: http://127.0.0.1:9119");
    } else {
        println!("[WARN] Dashboard did not respond within timeout (may still be starting).");
    }
    open_browser("http://127.0.0.1:9119");
    0
}

fn order_desktop_impl(root: &Path, env: &HashMap<String, String>) -> i32 {
    let mut cenv = child_env(env, &[]);
    cenv.insert("HERMES_HOME".into(), root.join("data").join("hermes-agent").to_string_lossy().into_owned());
    cenv.insert("HERMES_DESKTOP_HERMES_ROOT".into(), root.join("hermes-agent").to_string_lossy().into_owned());
    cenv.insert(
        "HERMES_DESKTOP_PYTHON".into(),
        root.join("hermes-agent").join("venv").join("Scripts").join("python.exe").to_string_lossy().into_owned(),
    );
    cenv.insert("HERMES_DESKTOP_CWD".into(), root.to_string_lossy().into_owned());
    cenv.insert(
        "HERMES_DESKTOP_USER_DATA_DIR".into(),
        root.join("data").join("hermes-agent").join("desktop").to_string_lossy().into_owned(),
    );
    cenv.insert(
        "PATH".into(),
        format!(
            "{}\\runtime\\node;{}\\runtime\\portable-python;{}\\hermes-agent\\venv\\Scripts;{}",
            root.to_string_lossy(),
            root.to_string_lossy(),
            root.to_string_lossy(),
            env["PATH"]
        ),
    );

    let desktop_exe = root
        .join("hermes-agent")
        .join("apps")
        .join("desktop")
        .join("release")
        .join("win-unpacked")
        .join("Hermes.exe");
    if !desktop_exe.exists() {
        println!("[FATAL] Hermes Desktop not found: {}", desktop_exe.to_string_lossy());
        println!("        Build: cd hermes-agent && hermes desktop --build-only");
        return 1;
    }
    let venv_py = root.join("hermes-agent").join("venv").join("Scripts").join("python.exe");
    if !venv_py.exists() {
        println!("[FATAL] venv Python not found: {}", venv_py.to_string_lossy());
        return 1;
    }
    let _ = fs::create_dir_all(root.join("data").join("hermes-agent").join("logs"));
    let log = root.join("data").join("hermes-agent").join("logs").join("desktop-stdout.log");
    let _ = spawn_hidden(&desktop_exe.to_string_lossy().into_owned(), &[], &cenv, None, Some(&log));
    println!("Hermes Desktop launched.");
    0
}

fn order_think(root: &Path, env: &HashMap<String, String>, args: &[String]) {
    let wd = py(root);
    let think = root.join("Ikaros-memory").join("v5").join("think.py");
    let mut a = vec![think.to_string_lossy().into_owned()];
    for x in args {
        a.push(x.clone());
    }
    run_child(&wd, &a, env, None, true);
}

fn order_mem(root: &Path, env: &HashMap<String, String>, args: &[String]) {
    let wd = py(root);
    let store = root.join("Ikaros-memory").join("v5").join("store.py");
    let mut a = vec![store.to_string_lossy().into_owned()];
    for x in args {
        a.push(x.clone());
    }
    run_child(&wd, &a, env, None, true);
}

fn order_verify_impl(root: &Path, env: &HashMap<String, String>, quick: bool) -> i32 {
    let wd = py(root);
    let target = if quick {
        root.join("Ikaros-memory")
            .join("v5")
            .join("tests")
            .join("test_goal_contract.py")
    } else {
        root.join("Ikaros-memory").join("v5").join("tests")
    };
    println!(
        "[verify] {} mode -- {}",
        if quick { "Quick" } else { "Full" },
        target.to_string_lossy()
    );
    run_child(
        &wd,
        &[
            "-m".into(),
            "pytest".into(),
            target.to_string_lossy().into_owned(),
            "-v".into(),
            "--tb=short".into(),
            "--no-header".into(),
        ],
        env,
        None,
        true,
    )
}

fn order_soul_sync(root: &Path, env: &HashMap<String, String>) {
    let wd = py(root);
    let soul = bin_dir(root).join("ikaros-soul-sync.py");
    run_child(&wd, &[soul.to_string_lossy().into_owned(), "--once".into()], env, None, true);
}

fn order_ws_restart(root: &Path, env: &HashMap<String, String>) {
    println!("[ws-restart] Stopping Voice WS (:7870)...");
    kill_port(7870);
    thread::sleep(Duration::from_secs(2));
    println!("[ws-restart] Voice WS stopped.");
    println!("[ws-restart] Starting Voice WS...");
    let wd = py(root);
    let vw = bin_dir(root).join("ikaros-voice-ws.py");
    let log = logs_dir(root).join("voice-ws.log");
    let _ = spawn_hidden(&wd, &[vw.to_string_lossy().into_owned()], env, None, Some(&log));
    if wait_for_port(7870, 40) {
        println!("[ws-restart] Voice WS ready: ws://127.0.0.1:7870/v1/voice/ws");
    } else {
        println!("[ws-restart] WARNING: Voice WS may not be ready");
    }
}

fn order_live2d(root: &Path, env: &HashMap<String, String>, mode: &str) {
    let wd = py(root);
    match mode {
        "stop" => {
            if pet_running() {
                kill_image("ikaros-desktop-pet.exe");
                println!("[ikaros] OK Stopped.");
            } else {
                println!("[ikaros] Pet was not running.");
            }
        }
        "status" => {
            if pet_running() {
                println!("  Running (ikaros-desktop-pet.exe)");
            } else {
                println!("  Not running");
            }
        }
        _ => {
            if !tcp_connect(7870) {
                println!("[ikaros] Starting Voice WS (:7870) for speech...");
                let vw = bin_dir(root).join("ikaros-voice-ws.py");
                let log = logs_dir(root).join("voice-ws.log");
                let _ = spawn_hidden(&wd, &[vw.to_string_lossy().into_owned()], env, None, Some(&log));
                if wait_for_port(7870, 40) {
                    println!("[ikaros] Voice WS ready");
                } else {
                    println!("[ikaros] WARNING: Voice WS not ready (timeout)");
                }
            }
            if pet_running() {
                println!("[ikaros] Pet already running.");
                return;
            }
            ensure_pet_junction(root);
            let pet = root
                .join("Ikaros-Live2D")
                .join("src-tauri")
                .join("target")
                .join("release")
                .join("ikaros-desktop-pet.exe");
            if !pet.exists() {
                println!("[ikaros] ERROR: {} not found.", pet.to_string_lossy());
                println!("[ikaros] Build: cd Ikaros-Live2D && npx tauri build");
                return;
            }
            kill_image("ikaros-desktop-pet.exe");
            println!("[ikaros] Launching pet...");
            let mut c = Command::new(&pet);
            c.envs(env.iter().map(|(k, v)| (k.clone(), v.clone())));
            let _ = c.spawn();
            thread::sleep(Duration::from_secs(3));
            if pet_running() {
                println!("[ikaros] OK Pet started.");
            } else {
                println!("[ikaros] WARNING: pet may not have started.");
            }
        }
    }
}

fn order_screen_monitor(root: &Path, env: &HashMap<String, String>, args: &[String]) {
    let script = bin_dir(root).join("screen-activity-monitor.ps1");
    let mut a: Vec<String> = vec![
        "-NoProfile".into(),
        "-ExecutionPolicy".into(),
        "Bypass".into(),
        "-File".into(),
        script.to_string_lossy().into_owned(),
    ];
    if args.is_empty() {
        a.push("status".into());
    } else {
        for x in args {
            a.push(x.clone());
        }
    }
    run_child("powershell", &a, env, None, true);
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

fn print_help() {
    println!("Ikaros unified launcher");
    println!();
    println!("Usage: ikaros <order> [args...]");
    println!();
    println!("Orders:");
    println!("  start                    Bring up backend + interactive frontend menu");
    println!("  sleep | stop             Stop all Ikaros services");
    println!("  studio                   Start Hermes Studio (:8649)");
    println!("  dashboard                Start Hermes Dashboard (:9119)");
    println!("  desktop                  Start Hermes Desktop app");
    println!("  think [args]             Run V5.1 self-think (ikaros think --watch)");
    println!("  mem [args]               Memory store CLI (v5/store.py)");
    println!("  live2d [start|stop|status]  Desktop Pet (Tauri)");
    println!("  verify [--quick]         Run V5.1 tests");
    println!("  soul-sync                Sync V5 soul -> Hermes SOUL.md");
    println!("  ws-restart               Restart Voice WS (:7870)");
    println!("  screen-monitor [args]    Screen activity monitor");
    println!("  help                     Show this help");
    println!();
    println!("Examples:");
    println!("  ikaros start");
    println!("  ikaros studio");
    println!("  ikaros mem stats");
}

fn main() {
    let args: Vec<String> = env::args().collect();
    // No arguments (e.g. double-clicking ikaros.exe from Explorer) => default
    // to `start` so the launcher actually brings up the stack instead of
    // flashing a help screen and exiting immediately.
    let (order, rest): (String, Vec<String>) = if args.len() < 2 {
        ("start".to_string(), Vec::new())
    } else {
        (args[1].to_lowercase(), args[2..].to_vec())
    };

    if order == "help" || order == "-h" || order == "--help" {
        print_help();
        return;
    }
    if order == "--version" || order == "-v" {
        println!("ikaros-cli 1.0.0");
        return;
    }

    let root = match detect_root() {
        Some(r) => r,
        None => {
            eprintln!("[ikaros] IKAROS_ROOT not found. Set IKAROS_ROOT env var or ensure runtime\\portable-python\\python.exe exists.");
            std::process::exit(1);
        }
    };
    let env = build_env(&root);

    match order.as_str() {
        "start" => {
            if !rest.is_empty() {
                println!("[ikaros] note: 'start' takes no flags; ignoring: {}", rest.join(" "));
            }
            order_start(&root, &env);
        }
        "sleep" | "stop" => order_sleep_impl(&root, &env),
        "studio" => {
            order_studio_impl(&root, &env);
        }
        "dashboard" => {
            order_dashboard_impl(&root, &env);
        }
        "desktop" => {
            order_desktop_impl(&root, &env);
        }
        "think" => order_think(&root, &env, &rest),
        "mem" => order_mem(&root, &env, &rest),
        "live2d" => {
            let m = rest.first().map(|s| s.as_str()).unwrap_or("start");
            order_live2d(&root, &env, m);
        }
        "verify" => {
            let quick = rest.iter().any(|a| a == "-q" || a == "--quick");
            order_verify_impl(&root, &env, quick);
        }
        "soul-sync" | "soulsync" => order_soul_sync(&root, &env),
        "ws-restart" => order_ws_restart(&root, &env),
        "screen-monitor" | "screenmonitor" => order_screen_monitor(&root, &env, &rest),
        _ => {
            eprintln!("[ikaros] Unknown order: {}", order);
            print_help();
            std::process::exit(1);
        }
    }
}
