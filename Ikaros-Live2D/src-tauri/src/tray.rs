use std::sync::Mutex;
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem, Submenu},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager, State,
};

/// All tray event IDs that the frontend handles (App.vue switch/case).
/// Keep in sync with the frontend's `listen('tray-event', ...)` handler.
pub mod event_id {
    pub const SHOW_HIDE: &str = "show_hide";
    pub const HIDE: &str = "hide";
    pub const QUIT: &str = "quit";

    // Model switching
    pub const MODEL_PREV: &str = "model_prev";
    pub const MODEL_NEXT: &str = "model_next";
    pub const MODEL_1: &str = "model_1";
    pub const MODEL_2: &str = "model_2";
    pub const MODEL_3: &str = "model_3";
    pub const RANDOM_MODEL: &str = "random_model";

    // Texture / expression
    pub const TEXTURE_NEXT: &str = "texture_next";

    // Scale
    pub const SCALE_050: &str = "scale_050";
    pub const SCALE_075: &str = "scale_075";
    pub const SCALE_100: &str = "scale_100";
    pub const SCALE_125: &str = "scale_125";
    pub const SCALE_150: &str = "scale_150";
    pub const SCALE_200: &str = "scale_200";

    // Tools
    pub const SCREENSHOT: &str = "screenshot";
    pub const HIT_FRAMES: &str = "hit_frames";
    pub const MONITOR: &str = "monitor";
    pub const SETTINGS: &str = "settings";
    pub const RESTART: &str = "restart";

    // Mode / Neuro
    pub const MODE_CONTINUOUS: &str = "mode_continuous";
    pub const MODE_WAKE: &str = "mode_wake";
    pub const NEURO_TRIGGER: &str = "neuro_trigger";
    pub const NEURO_PATIENCE_15: &str = "neuro_patience_15";
    pub const NEURO_PATIENCE_30: &str = "neuro_patience_30";
    pub const NEURO_PATIENCE_60: &str = "neuro_patience_60";
    pub const NEURO_PATIENCE_120: &str = "neuro_patience_120";
    pub const NEURO_RESET: &str = "neuro_reset";

    // Float ball
    pub const FLOAT_BALL: &str = "float_ball";

    // New additions
    pub const ALWAYS_ON_TOP: &str = "always_on_top";
}

/// Helper: returns "✓ " prefix if checked, "  " otherwise.
fn chk(checked: bool) -> &'static str {
    if checked { "✓ " } else { "  " }
}

/// Runtime menu state for dynamic checkmarks.
#[derive(Debug, Clone)]
pub struct TrayMenuState {
    pub float_ball: bool,
    pub mode_continuous: bool,
    pub hit_frames: bool,
    pub llm_local: bool,
    pub always_on_top: bool,
    pub neuro_patience: u32,
    pub current_model: usize,
    pub current_scale: u32,
}

impl Default for TrayMenuState {
    fn default() -> Self {
        Self {
            float_ball: false,
            mode_continuous: true,
            hit_frames: false,
            llm_local: true,
            always_on_top: false,
            neuro_patience: 30,
            current_model: 0,
            current_scale: 100,
        }
    }
}

/// Build the full tray menu with dynamic checkmarks from state.
fn build_menu(app: &AppHandle, state: &TrayMenuState) -> Result<Menu<tauri::Wry>, Box<dyn std::error::Error>> {
    use event_id::*;

    // ── Top-level items ──
    let show_hide = MenuItem::with_id(app, SHOW_HIDE, "🪶 显示/隐藏", true, None::<&str>)?;
    let float_ball = MenuItem::with_id(app, FLOAT_BALL,
        format!("{}🔮 悬浮球模式", chk(state.float_ball)), true, None::<&str>)?;
    let sep1 = PredefinedMenuItem::separator(app)?;

    // ── Model switching submenu ──
    let model_names = ["1. Hiyori", "2. Haru", "3. Senko"];
    let model_prev = MenuItem::with_id(app, MODEL_PREV, "⏮ 上一个", true, None::<&str>)?;
    let model_next = MenuItem::with_id(app, MODEL_NEXT, "⏭ 下一个", true, None::<&str>)?;
    let model_1 = MenuItem::with_id(app, MODEL_1,
        format!("{}{}", chk(state.current_model == 0), model_names[0]), true, None::<&str>)?;
    let model_2 = MenuItem::with_id(app, MODEL_2,
        format!("{}{}", chk(state.current_model == 1), model_names[1]), true, None::<&str>)?;
    let model_3 = MenuItem::with_id(app, MODEL_3,
        format!("{}{}", chk(state.current_model == 2), model_names[2]), true, None::<&str>)?;
    let model_submenu = Submenu::with_id_and_items(app, "model", "🔄 切换形象", true, &[
        &model_prev, &model_next,
        &PredefinedMenuItem::separator(app)?,
        &model_1, &model_2, &model_3,
    ])?;

    // ── Texture ──
    let texture_next = MenuItem::with_id(app, TEXTURE_NEXT, "👗 切换服装", true, None::<&str>)?;

    // ── Expressions submenu ──
    let exprs: Vec<_> = ["F01", "F02", "F03", "F04", "F05", "F06", "F07", "F08"]
        .iter()
        .map(|&e| {
            let id = format!("expr_{}", e);
            MenuItem::with_id(app, id.as_str(), format!("😊 {}", e), true, None::<&str>)
        })
        .collect::<Result<Vec<_>, _>>()?;
    let expr_refs: Vec<&dyn tauri::menu::IsMenuItem<_>> = exprs.iter()
        .map(|m| m as &dyn tauri::menu::IsMenuItem<_>).collect();
    let expr_submenu = Submenu::with_id_and_items(app, "expr", "😊 表情", true, &expr_refs)?;

    // ── Scale submenu ──
    let scales: [(u32, &str, &str); 6] = [
        (50, SCALE_050, "50%"), (75, SCALE_075, "75%"),
        (100, SCALE_100, "100% (默认)"), (125, SCALE_125, "125%"),
        (150, SCALE_150, "150%"), (200, SCALE_200, "200%"),
    ];
    let scale_items: Vec<MenuItem<tauri::Wry>> = scales.iter().map(|(v, id, label)| {
        MenuItem::with_id(app, *id,
            format!("{}{}", chk(state.current_scale == *v), label), true, None::<&str>)
    }).collect::<Result<Vec<_>, _>>()?;
    let scale_refs: Vec<&dyn tauri::menu::IsMenuItem<_>> = scale_items.iter()
        .map(|m| m as &dyn tauri::menu::IsMenuItem<_>).collect();
    let scale_submenu = Submenu::with_id_and_items(app, "scale", "📏 模型比例", true, &scale_refs)?;

    // ── Tools ──
    let screenshot = MenuItem::with_id(app, SCREENSHOT, "📸 保存图片", true, None::<&str>)?;
    let hit_frames = MenuItem::with_id(app, HIT_FRAMES,
        format!("{}🔲 帧检测", chk(state.hit_frames)), true, None::<&str>)?;
    let random_model = MenuItem::with_id(app, RANDOM_MODEL, "🔀 随机模型", true, None::<&str>)?;
    let always_on_top = MenuItem::with_id(app, ALWAYS_ON_TOP,
        format!("{}📌 窗口置顶", chk(state.always_on_top)), true, None::<&str>)?;
    let sep2 = PredefinedMenuItem::separator(app)?;

    // ── Mode submenu ──
    let mode_continuous = MenuItem::with_id(app, MODE_CONTINUOUS,
        format!("{}🎤 连续对话模式", chk(state.mode_continuous)), true, None::<&str>)?;
    let mode_wake = MenuItem::with_id(app, MODE_WAKE,
        format!("{}🔑 唤醒词模式", chk(!state.mode_continuous)), true, None::<&str>)?;
    let mode_submenu = Submenu::with_id_and_items(app, "mode", "🎤 模式", true, &[
        &mode_continuous, &mode_wake,
    ])?;

    // ── Neuro submenu ──
    let neuro_trigger = MenuItem::with_id(app, NEURO_TRIGGER, "💬 让伊卡洛斯主动说话", true, None::<&str>)?;
    let neuro_vals: [(u32, &str, &str); 4] = [
        (15, NEURO_PATIENCE_15, "15s (敏感)"), (30, NEURO_PATIENCE_30, "30s (默认)"),
        (60, NEURO_PATIENCE_60, "60s (慢热)"), (120, NEURO_PATIENCE_120, "120s (极慢)"),
    ];
    let neuro_items: Vec<MenuItem<tauri::Wry>> = neuro_vals.iter().map(|(v, id, label)| {
        MenuItem::with_id(app, *id,
            format!("{}{}", chk(state.neuro_patience == *v), label), true, None::<&str>)
    }).collect::<Result<Vec<_>, _>>()?;

    let neuro_sep1 = PredefinedMenuItem::separator(app)?;
    let neuro_sep2 = PredefinedMenuItem::separator(app)?;
    let neuro_reset = MenuItem::with_id(app, NEURO_RESET, "🔄 重置说话标志", true, None::<&str>)?;

    let mut neuro_all: Vec<&dyn tauri::menu::IsMenuItem<_>> = Vec::with_capacity(3 + neuro_items.len() + 2);
    neuro_all.push(&neuro_trigger);
    neuro_all.push(&neuro_sep1);
    for item in &neuro_items {
        neuro_all.push(item as &dyn tauri::menu::IsMenuItem<_>);
    }
    neuro_all.push(&neuro_sep2);
    neuro_all.push(&neuro_reset);

    let neuro_submenu = Submenu::with_id_and_items(app, "neuro", "🧠 Neuro", true, &neuro_all)?;

    // ── LLM model submenu ──
    let llm_local = MenuItem::with_id(app, "llm_local",
        format!("{}💻 本地 (qwen3-8b)", chk(state.llm_local)), true, None::<&str>)?;
    let llm_cloud = MenuItem::with_id(app, "llm_cloud_ds",
        format!("{}☁️ deepseek-chat", chk(!state.llm_local)), true, None::<&str>)?;
    let llm_refresh = MenuItem::with_id(app, "llm_refresh", "🔄 刷新LLM列表", true, None::<&str>)?;
    let llm_submenu = Submenu::with_id_and_items(app, "llm", "🤖 LLM模型", true, &[
        &llm_local, &llm_cloud,
        &PredefinedMenuItem::separator(app)?,
        &llm_refresh,
    ])?;

    let sep3 = PredefinedMenuItem::separator(app)?;

    // ── Panels ──
    let monitor = MenuItem::with_id(app, MONITOR, "📊 监控面板", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, SETTINGS, "⚙️ 设置", true, None::<&str>)?;
    let sep4 = PredefinedMenuItem::separator(app)?;

    // ── System ──
    let restart = MenuItem::with_id(app, RESTART, "🔄 重启", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, HIDE, "💤 隐藏", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, QUIT, "❌ 退出", true, None::<&str>)?;

    let menu = Menu::with_id_and_items(app, "tray_menu", &[
        &show_hide,
        &float_ball,
        &sep1,
        &model_submenu,
        &texture_next,
        &expr_submenu,
        &scale_submenu,
        &screenshot,
        &hit_frames,
        &random_model,
        &always_on_top,
        &sep2,
        &mode_submenu,
        &neuro_submenu,
        &llm_submenu,
        &sep3,
        &monitor,
        &settings,
        &sep4,
        &restart,
        &hide,
        &quit,
    ])?;

    Ok(menu)
}

/// Build and attach the system tray icon.
pub fn create_tray(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    use event_id::*;

    let state = TrayMenuState::default();
    let menu = build_menu(app, &state)?;

    let _tray = TrayIconBuilder::with_id("ikaros-tray")
        .tooltip("🪶 伊卡洛斯")
        .icon(app.default_window_icon().unwrap().clone())
        .menu(&menu)
        .on_menu_event(move |app, event| {
            let id = event.id.as_ref();
            let _ = app.emit("tray-event", id);

            match id {
                SHOW_HIDE => {
                    if let Some(win) = app.get_webview_window("main") {
                        if win.is_visible().unwrap_or(true) {
                            let _ = win.hide();
                        } else {
                            let _ = win.show();
                            let _ = win.set_focus();
                        }
                    }
                }
                HIDE => {
                    if let Some(win) = app.get_webview_window("main") {
                        let _ = win.hide();
                    }
                }
                MONITOR => {
                    if let Some(win) = app.get_webview_window("monitor") {
                        if win.is_visible().unwrap_or(false) {
                            let _ = win.hide();
                        } else {
                            let _ = win.show();
                            let _ = win.set_focus();
                        }
                    }
                }
                QUIT => {
                    std::process::exit(0);
                }
                _ => {}
            }
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                let app = tray.app_handle();
                if let Some(win) = app.get_webview_window("main") {
                    if win.is_visible().unwrap_or(true) {
                        let _ = win.hide();
                    } else {
                        let _ = win.show();
                        let _ = win.set_focus();
                    }
                }
            }
        })
        .build(app)?;

    Ok(())
}

/// Tauri command: sync tray menu checkmarks from frontend state.
#[tauri::command]
pub fn sync_tray_menu(
    app: AppHandle,
    menu_state: State<'_, Mutex<TrayMenuState>>,
    float_ball: Option<bool>,
    mode_continuous: Option<bool>,
    hit_frames: Option<bool>,
    llm_local: Option<bool>,
    always_on_top: Option<bool>,
    neuro_patience: Option<u32>,
    current_model: Option<usize>,
    current_scale: Option<u32>,
) -> Result<(), String> {
    let mut state = menu_state.lock().map_err(|e| e.to_string())?;

    if let Some(v) = float_ball { state.float_ball = v; }
    if let Some(v) = mode_continuous { state.mode_continuous = v; }
    if let Some(v) = hit_frames { state.hit_frames = v; }
    if let Some(v) = llm_local { state.llm_local = v; }
    if let Some(v) = always_on_top { state.always_on_top = v; }
    if let Some(v) = neuro_patience { state.neuro_patience = v; }
    if let Some(v) = current_model { state.current_model = v; }
    if let Some(v) = current_scale { state.current_scale = v; }

    let new_menu = build_menu(&app, &state).map_err(|e| e.to_string())?;
    if let Some(tray) = app.tray_by_id("ikaros-tray") {
        tray.set_menu(Some(new_menu)).map_err(|e| e.to_string())?;
    }
    Ok(())
}
