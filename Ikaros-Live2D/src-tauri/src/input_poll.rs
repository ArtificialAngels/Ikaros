/// input_poll.rs — Input state management (no background thread).
///
/// Provides a single `poll_input_state` command that the JS frontend
/// calls at ~50ms intervals.  One IPC call returns all data:
/// Ctrl state, cursor position, window bounds, normalized tracking coords.
///
/// Click-through auto-management (corner hover, Ctrl toggle) runs inside
/// this command on every poll — all in Rust, zero extra IPC.
use std::sync::Mutex;
use std::time::Instant;
use serde::Serialize;
use tauri::{AppHandle, Manager, State};

// ── Dimensions ──
const CORNER_WIDTH: i32 = 105;
const CORNER_HEIGHT: i32 = 50;
const CORNER_EXIT_MS: u64 = 200;

// ── Shared state ──

pub struct PollState {
    pub ball_mode: bool,
    pub manual_override: bool,
    pub corner_override: bool,
    pub corner_exit_deadline: Option<Instant>,
    pub last_ignore: Option<bool>,
}

impl Default for PollState {
    fn default() -> Self {
        Self {
            ball_mode: false,
            manual_override: false,
            corner_override: true,
            corner_exit_deadline: None,
            last_ignore: None,
        }
    }
}

/// Cached window bounds — updated from main thread via on_window_event.
pub struct WindowBounds {
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
}

impl Default for WindowBounds {
    fn default() -> Self {
        Self { x: 0, y: 0, width: 400, height: 500 }
    }
}

// ── Windows helpers ──

#[cfg(target_os = "windows")]
fn is_ctrl_pressed_raw() -> bool {
    use windows::Win32::UI::Input::KeyboardAndMouse::{GetAsyncKeyState, VK_CONTROL};
    unsafe { (GetAsyncKeyState(VK_CONTROL.0 as i32) as u16) & 0x8000 != 0 }
}

#[cfg(not(target_os = "windows"))]
fn is_ctrl_pressed_raw() -> bool { false }

#[cfg(target_os = "windows")]
fn get_cursor_pos_raw() -> (i32, i32) {
    use windows::Win32::UI::WindowsAndMessaging::GetCursorPos;
    use windows::Win32::Foundation::POINT;
    unsafe {
        let mut pt = POINT { x: 0, y: 0 };
        if GetCursorPos(&mut pt).is_ok() { (pt.x, pt.y) } else { (0, 0) }
    }
}

#[cfg(not(target_os = "windows"))]
fn get_cursor_pos_raw() -> (i32, i32) { (0, 0) }

// ── Click-through helper ──

fn set_click_through(handle: &AppHandle, ignore: bool, last: &mut Option<bool>) {
    if *last == Some(ignore) { return; }
    if let Some(win) = handle.get_webview_window("main") {
        if win.set_ignore_cursor_events(ignore).is_ok() {
            *last = Some(ignore);
        }
    }
}

// ── Response type ──

#[derive(Clone, Serialize)]
pub struct PollResult {
    pub ctrl_held: bool,
    pub track_x: f64,
    pub track_y: f64,
    pub in_window: bool,
}

// ── Register state (called from setup) ──

pub fn register_state(app: &tauri::App) {
    app.manage(Mutex::new(PollState::default()));
    app.manage(Mutex::new(WindowBounds::default()));
}

// ── Main poll command (called from JS every ~50ms) ──

#[tauri::command]
pub fn poll_input_state(
    app: AppHandle,
    poll_state: State<'_, Mutex<PollState>>,
    bounds_state: State<'_, Mutex<WindowBounds>>,
) -> PollResult {
    // ── Read states ──
    let mut ps = match poll_state.lock() { Ok(s) => s, Err(_) => return PollResult { ctrl_held: false, track_x: 0.0, track_y: 0.0, in_window: false } };
    let bs = match bounds_state.lock() { Ok(b) => b, Err(_) => return PollResult { ctrl_held: false, track_x: 0.0, track_y: 0.0, in_window: false } };

    let ball_mode = ps.ball_mode;
    let manual_override = ps.manual_override;
    let skip_auto = ball_mode || manual_override;

    // ── Ctrl ──
    let ctrl = is_ctrl_pressed_raw();

    // ── Cursor position ──
    let (cx, cy) = get_cursor_pos_raw();

    // Window-relative coords
    let win_x = bs.x;
    let win_y = bs.y;
    let win_w = bs.width;
    let win_h = bs.height;

    let rel_x = cx - win_x;
    let rel_y = cy - win_y;
    let in_window = win_w > 0 && win_h > 0
        && rel_x >= 0 && rel_x <= win_w
        && rel_y >= 0 && rel_y <= win_h;

    let in_corner = in_window
        && rel_x >= win_w - CORNER_WIDTH
        && rel_y >= win_h - CORNER_HEIGHT;

    // ── Eye tracking coords (global — always compute, even outside window) ──
    let (tx, ty) = if !ball_mode {
        let cx_f = win_w as f64 / 2.0;
        let cy_f = win_h as f64 / 2.0;
        (
            ((rel_x as f64 - cx_f) / 150.0).clamp(-1.0, 1.0),
            ((rel_y as f64 - cy_f) / 150.0).clamp(-1.0, 1.0)
        )
    } else {
        (0.0, 0.0)
    };

    // ── Auto click-through management ──
    if !skip_auto {
        // Corner enter
        if in_corner && !ps.corner_override {
            ps.corner_override = true;
            ps.corner_exit_deadline = None;
            set_click_through(&app, false, &mut ps.last_ignore);
        }
        // Corner exit
        else if !in_corner && ps.corner_override && !ctrl {
            ps.corner_override = false;
            ps.corner_exit_deadline = Some(Instant::now());
        }

        // Corner exit timer
        if let Some(deadline) = ps.corner_exit_deadline {
            if deadline.elapsed().as_millis() as u64 >= CORNER_EXIT_MS {
                if !ps.corner_override && !ctrl {
                    set_click_through(&app, true, &mut ps.last_ignore);
                }
                ps.corner_exit_deadline = None;
            }
        }

        // Ctrl toggle (no prev tracking — changes handled on every poll)
        if ctrl {
            ps.corner_exit_deadline = None;
            set_click_through(&app, false, &mut ps.last_ignore);
        } else if !ps.corner_override {
            set_click_through(&app, true, &mut ps.last_ignore);
        }

        // Initial pass-through
        if ps.last_ignore.is_none() {
            set_click_through(&app, true, &mut ps.last_ignore);
        }
    }

    PollResult {
        ctrl_held: ctrl,
        track_x: tx,
        track_y: ty,
        in_window,
    }
}

// ── Control commands ──

#[tauri::command]
pub fn set_ball_mode(state: State<'_, Mutex<PollState>>, active: bool) {
    if let Ok(mut s) = state.lock() {
        s.ball_mode = active;
        if active {
            s.corner_exit_deadline = None;
            s.corner_override = false;
        }
    }
}

#[tauri::command]
pub fn set_manual_override(state: State<'_, Mutex<PollState>>, active: bool) {
    if let Ok(mut s) = state.lock() {
        s.manual_override = active;
    }
}

#[tauri::command]
pub fn force_click_through(app: AppHandle, state: State<'_, Mutex<PollState>>, ignore: bool) {
    if let Ok(mut s) = state.lock() {
        s.last_ignore = None;
        if ignore {
            s.corner_override = true;
        } else {
            s.corner_override = false;
        }
        s.corner_exit_deadline = None;
        set_click_through(&app, ignore, &mut s.last_ignore);
    }
}
