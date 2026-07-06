/**
 * click_through.rs — Click-through for Tauri v2 on Windows.
 *
 * Uses Tauri's built-in set_ignore_cursor_events, which internally
 * handles all platform-specific details (WS_EX_TRANSPARENT, DWM
 * compositing, WebView2 child window hierarchy) correctly.
 *
 * The previous custom Win32 approach (manually toggling WS_EX_TRANSPARENT
 * on root + children via EnumChildWindows) broke WebView2 rendering
 * when removing the flag from internal compositor windows.
 */

/// Apply (or remove) cursor event pass-through.
/// `ignore=true` means mouse events pass through the window to whatever is behind it.
pub fn set_ignore_cursor_events(window: &tauri::Window, ignore: bool) -> Result<(), String> {
    window
        .set_ignore_cursor_events(ignore)
        .map_err(|e| format!("set_ignore_cursor_events failed: {}", e))
}
