mod tray;
mod click_through;
mod input_poll;

use tauri::Manager;

#[tauri::command]
async fn drag_window(window: tauri::Window) -> Result<(), String> {
    window.start_dragging().map_err(|e| e.to_string())
}

#[tauri::command]
fn get_active_window() -> Result<(String, String), String> {
    #[cfg(target_os = "windows")]
    {
        use windows::Win32::UI::WindowsAndMessaging::GetForegroundWindow;
        use windows::Win32::UI::WindowsAndMessaging::GetWindowThreadProcessId;
        use windows::Win32::UI::WindowsAndMessaging::GetWindowTextW;
        use windows::Win32::UI::WindowsAndMessaging::GetWindowTextLengthW;

        unsafe {
            let hwnd = GetForegroundWindow();
            if hwnd.is_invalid() {
                return Ok(("unknown".to_string(), String::new()));
            }

            // Get window title
            let title_len = GetWindowTextLengthW(hwnd);
            let mut title_buf = vec![0u16; (title_len + 1) as usize];
            let copied = GetWindowTextW(hwnd, &mut title_buf);
            let title = String::from_utf16_lossy(&title_buf[..copied as usize]);

            // Get process name
            let mut pid: u32 = 0;
            GetWindowThreadProcessId(hwnd, Some(&mut pid));

            let proc_name = get_process_name(pid).unwrap_or_else(|_| "unknown.exe".to_string());

            Ok((proc_name, title))
        }
    }

    #[cfg(not(target_os = "windows"))]
    {
        Ok(("unknown".to_string(), String::new()))
    }
}

#[cfg(target_os = "windows")]
fn get_process_name(pid: u32) -> Result<String, String> {
    use windows::Win32::System::Threading::{OpenProcess, PROCESS_QUERY_INFORMATION, PROCESS_VM_READ};
    use windows::Win32::Foundation::CloseHandle;

    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, false, pid)
            .map_err(|e| e.to_string())?;

        let mut buf = [0u16; 260]; // MAX_PATH
        let mut size: u32 = 260;

        // Use QueryFullProcessImageNameW
        let result = windows::Win32::System::Threading::QueryFullProcessImageNameW(
            handle,
            windows::Win32::System::Threading::PROCESS_NAME_FORMAT(0),
            windows::core::PWSTR(buf.as_mut_ptr()),
            &mut size,
        );

        let _ = CloseHandle(handle);

        if result.is_err() {
            return Err("QueryFullProcessImageNameW failed".to_string());
        }

        let full_path = String::from_utf16_lossy(&buf[..size as usize]);
        // Extract just the filename
        let name = full_path
            .rsplit('\\')
            .next()
            .unwrap_or("unknown.exe")
            .to_string();

        Ok(name)
    }
}

#[tauri::command]
fn custom_set_ignore_cursor_events(window: tauri::Window, ignore: bool) -> Result<(), String> {
    click_through::set_ignore_cursor_events(&window, ignore)
}

#[tauri::command]
fn get_window_hwnd(window: tauri::Window) -> Result<i64, String> {
    #[cfg(target_os = "windows")]
    {
        let hwnd = window.hwnd().map_err(|e| e.to_string())?;
        Ok(hwnd.0 as i64)
    }
    #[cfg(not(target_os = "windows"))]
    {
        Ok(0)
    }
}

#[derive(serde::Serialize)]
struct CursorPosition {
    x: i32,
    y: i32,
}

#[tauri::command]
fn get_cursor_position() -> Result<CursorPosition, String> {
    #[cfg(target_os = "windows")]
    {
        use windows::Win32::UI::WindowsAndMessaging::GetCursorPos;
        use windows::Win32::Foundation::POINT;
        unsafe {
            let mut pt = POINT { x: 0, y: 0 };
            GetCursorPos(&mut pt).map_err(|e| e.to_string())?;
            Ok(CursorPosition { x: pt.x, y: pt.y })
        }
    }
    #[cfg(not(target_os = "windows"))]
    {
        Ok(CursorPosition { x: 0, y: 0 })
    }
}

#[tauri::command]
fn is_ctrl_pressed() -> bool {
    #[cfg(target_os = "windows")]
    {
        use windows::Win32::UI::Input::KeyboardAndMouse::GetAsyncKeyState;
        use windows::Win32::UI::Input::KeyboardAndMouse::VK_CONTROL;
        unsafe { (GetAsyncKeyState(VK_CONTROL.0 as i32) as u16) & 0x8000 != 0 }
    }
    #[cfg(not(target_os = "windows"))]
    {
        false
    }
}

#[tauri::command]
async fn save_screenshot(app: tauri::AppHandle, base64_data: String, filename: String) -> Result<String, String> {
    use tauri_plugin_dialog::DialogExt;

    // Decode base64 (strip data URL prefix if present)
    let b64 = if let Some(pos) = base64_data.find(",") {
        &base64_data[pos + 1..]
    } else {
        &base64_data
    };

    use base64::Engine;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(b64)
        .map_err(|e| format!("base64 decode failed: {}", e))?;

    // Show save dialog
    let path = app
        .dialog()
        .file()
        .set_file_name(&filename)
        .add_filter("PNG Image", &["png"])
        .blocking_save_file();

    let path = match path {
        Some(p) => p,
        None => return Err("cancelled".to_string()),
    };

    let path_str = match path {
        tauri_plugin_dialog::FilePath::Path(p) => p.to_string_lossy().to_string(),
        tauri_plugin_dialog::FilePath::Url(u) => u.to_string(),
    };
    std::fs::write(&path_str, &bytes).map_err(|e| format!("write failed: {}", e))?;

    Ok(path_str)
}

#[tauri::command]
async fn restart_app(_app: tauri::AppHandle) -> Result<(), String> {
    // Spawn new process, then forcefully exit current one
    let exe = std::env::current_exe().map_err(|e| format!("current_exe failed: {}", e))?;
    std::process::Command::new(&exe)
        .spawn()
        .map_err(|e| format!("spawn failed: {}", e))?;
    // Schedule forced exit after a delay so the new process can start
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(1000));
        std::process::exit(0);
    });
    Ok(())
}

#[tauri::command]
fn set_always_on_top(window: tauri::Window, on_top: bool) -> Result<(), String> {
    window.set_always_on_top(on_top).map_err(|e| e.to_string())
}

/// Resolve the Ikaros root directory (containing `Ikaros-memory`, `data`, ...).
/// Order: $IKAROS_ROOT env → walk up from the exe looking for `Ikaros-memory`
/// → hard fallback `E:\Ikaros`.
fn ikaros_root() -> std::path::PathBuf {
    if let Ok(r) = std::env::var("IKAROS_ROOT") {
        let p = std::path::PathBuf::from(&r);
        if p.join("Ikaros-memory").is_dir() {
            return p;
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut cur = exe.parent();
        while let Some(dir) = cur {
            if dir.join("Ikaros-memory").is_dir() {
                return dir.to_path_buf();
            }
            cur = dir.parent();
        }
    }
    std::path::PathBuf::from("E:\\Ikaros")
}

/// Read Ikaros' live inner state (V5 affect/vitality/care/relationship) plus the
/// tail of the conversation log (ikaros-monitor.jsonl). Powers the monitor panel
/// so it mirrors the browser dashboard without needing the Python server.
#[tauri::command]
fn read_ikaros_state(count: Option<usize>) -> Result<serde_json::Value, String> {
    let root = ikaros_root();
    let v5 = root.join("Ikaros-memory").join("data").join("v5");

    let read_json = |name: &str| -> serde_json::Value {
        let p = v5.join(name);
        std::fs::read_to_string(&p)
            .ok()
            .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
            .unwrap_or(serde_json::Value::Null)
    };

    let affect = read_json("affect.json");
    let vitality = read_json("vitality.json");
    let care = read_json("care.json");
    let relationship = read_json("relationship.json");
    // V5 self-cognition layer: persistent "I" model + latest metacog thought
    let self_model = read_json("self_model.json");
    let latest_thought = read_json("latest_thought.json");

    // Tail the conversation log.
    let n = count.unwrap_or(60).min(500);
    let log_path = root.join("data").join("logs").join("ikaros-monitor.jsonl");
    let mut log_entries: Vec<serde_json::Value> = Vec::new();
    if let Ok(content) = std::fs::read_to_string(&log_path) {
        let lines: Vec<&str> = content.lines().filter(|l| !l.trim().is_empty()).collect();
        let start = lines.len().saturating_sub(n);
        for line in &lines[start..] {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
                log_entries.push(v);
            }
        }
    }

    Ok(serde_json::json!({
        "affect": affect,
        "vitality": vitality,
        "care": care,
        "relationship": relationship,
        "log": log_entries,
        "self_model": self_model,
        "latest_thought": latest_thought,
    }))
}

#[tauri::command]
fn toggle_monitor_window(app: tauri::AppHandle) -> Result<bool, String> {
    if let Some(win) = app.get_webview_window("monitor") {
        if win.is_visible().unwrap_or(false) {
            let _ = win.hide();
            Ok(false)
        } else {
            let _ = win.show();
            let _ = win.set_focus();
            Ok(true)
        }
    } else {
        Err("monitor window not found".to_string())
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_process::init())
        .invoke_handler(tauri::generate_handler![
            drag_window,
            get_active_window,
            save_screenshot,
            get_cursor_position,
            custom_set_ignore_cursor_events,
            get_window_hwnd,
            is_ctrl_pressed,
            restart_app,
            set_always_on_top,
            toggle_monitor_window,
            read_ikaros_state,
            tray::sync_tray_menu,
            tray::update_audio_devices,
            input_poll::set_ball_mode,
            input_poll::set_manual_override,
            input_poll::force_click_through,
            input_poll::poll_input_state,
        ])
        .setup(|app| {
            // Register tray menu state for dynamic checkmarks
            app.manage(std::sync::Mutex::new(tray::TrayMenuState::default()));

            // Register state (PollState + WindowBounds) for input management
            input_poll::register_state(app);

            // Sync window bounds cache from main thread (required for cursor tracking)
            if let Some(main_win) = app.get_webview_window("main") {
                // ── Grant microphone permission for WebView2 getUserMedia ──
                // wry only auto-allows the clipboard permission; microphone
                // requests would otherwise be denied by default, so we
                // explicitly allow them on the CoreWebView2 controller.
                #[cfg(windows)]
                {
                    if let Err(e) = main_win.with_webview(|pw| {
                        use webview2_com::Microsoft::Web::WebView2::Win32::*;
                        use webview2_com::PermissionRequestedEventHandler;
                        let controller = pw.controller();
                        if let Ok(webview) = unsafe { controller.CoreWebView2() } {
                            let mut token: i64 = 0;
                            let handler = PermissionRequestedEventHandler::create(Box::new(
                                |_sender: Option<ICoreWebView2>,
                                 args: Option<ICoreWebView2PermissionRequestedEventArgs>| {
                                    if let Some(args) = args {
                                        let mut kind = COREWEBVIEW2_PERMISSION_KIND::default();
                                        let is_mic = unsafe { args.PermissionKind(&mut kind).is_ok() }
                                            && kind == COREWEBVIEW2_PERMISSION_KIND_MICROPHONE;
                                        if is_mic {
                                            unsafe {
                                                let _ = args.SetState(COREWEBVIEW2_PERMISSION_STATE_ALLOW);
                                            }
                                        }
                                    }
                                    Ok(())
                                },
                            ));
                            let _ = unsafe { webview.add_PermissionRequested(&handler, &mut token) };
                        }
                    }) {
                        eprintln!("failed to install microphone permission hook: {e}");
                    }
                }

                // ── Place the pet on a real (physical) monitor ──
                // On machines with virtual display adapters (Todesk / GameViewer
                // remote-control drivers), `center: true` can drop this transparent,
                // borderless window onto a phantom monitor → it renders but is
                // invisible to the user ("pet won't open"). Explicitly move it to a
                // physical monitor's work-area center.
                if let Ok(mut monitors) = app.available_monitors() {
                    // Drop known virtual/remote displays if identifiable by name.
                    monitors.retain(|m| {
                        m.name()
                            .map(|n| {
                                let l = n.to_lowercase();
                                !(l.contains("virtual")
                                    || l.contains("todesk")
                                    || l.contains("gameviewer")
                                    || l.contains("remote")
                                    || l.contains("dummy"))
                            })
                            .unwrap_or(true)
                    });
                    if monitors.is_empty() {
                        monitors = app.available_monitors().unwrap_or_default();
                    }
                    // Prefer the monitor at virtual-desktop origin (0,0) = physical
                    // primary in the normal Windows layout; otherwise largest area.
                    monitors.sort_by_key(|m| {
                        let p = m.position();
                        let s = m.size();
                        let at_origin = if p.x == 0 && p.y == 0 { 1 } else { 0 };
                        (at_origin, (s.width as i64) * (s.height as i64))
                    });
                    if let Some(target) = monitors.last() {
                        let wa = target.work_area();
                        let win_size =
                            main_win.outer_size().unwrap_or(tauri::PhysicalSize::new(400, 500));
                        let x = wa.position.x
                            + ((wa.size.width as i32 - win_size.width as i32) / 2);
                        let y = wa.position.y
                            + ((wa.size.height as i32 - win_size.height as i32) / 2);
                        let _ = main_win.set_position(tauri::PhysicalPosition::new(x, y));
                    }
                }
                // Guarantee it is actually visible & focused (covers minimized /
                // off-screen / not-yet-shown states).
                let _ = main_win.unminimize();
                let _ = main_win.show();
                let _ = main_win.set_focus();

                // Initial bounds
                if let Ok(pos) = main_win.outer_position() {
                    if let Ok(size) = main_win.outer_size() {
                        if let Ok(mut bounds) = app.state::<std::sync::Mutex<input_poll::WindowBounds>>().lock() {
                            bounds.x = pos.x;
                            bounds.y = pos.y;
                            bounds.width = size.width as i32;
                            bounds.height = size.height as i32;
                        }
                    }
                }
                // Keep cache updated on move/resize
                let h = app.handle().clone();
                main_win.on_window_event(move |event| {
                    use tauri::WindowEvent;
                    match event {
                        WindowEvent::Moved(_) | WindowEvent::Resized(_) => {
                            if let Some(win) = h.get_webview_window("main") {
                                if let (Ok(p), Ok(s)) = (win.outer_position(), win.outer_size()) {
                                    if let Ok(mut bounds) = h.state::<std::sync::Mutex<input_poll::WindowBounds>>().lock() {
                                        bounds.x = p.x;
                                        bounds.y = p.y;
                                        bounds.width = s.width as i32;
                                        bounds.height = s.height as i32;
                                    }
                                }
                            }
                        }
                        _ => {}
                    }
                });
            }

            // Prevent monitor window from being destroyed on close — just hide it
            if let Some(monitor_win) = app.get_webview_window("monitor") {
                let win = monitor_win.clone();
                monitor_win.on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = win.hide();
                    }
                });
            }

            // Create system tray
            let handle = app.handle();
            if let Err(e) = tray::create_tray(handle) {
                eprintln!("Failed to create tray: {}", e);
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
