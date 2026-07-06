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
            tray::sync_tray_menu,
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
