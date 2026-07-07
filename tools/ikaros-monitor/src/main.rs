//! Ikaros Monitor — Rust + Slint dashboard
//!
//! Replaces the Python monitor_agent.py with a native, fast, low-memory binary.

// Windows GUI subsystem — no console window
#![windows_subsystem = "windows"]

mod monitor;

use std::path::PathBuf;
use std::rc::Rc;
use std::cell::RefCell;

use slint::{Model, ModelRc, SharedString, Color, VecModel};

slint::include_modules!();

fn main() {
    let app = MainWindow::new().expect("failed to create Ikaros Monitor app");

    // Resolve project root (tools/ikaros-monitor -> ../../)
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_default();

    // Try to find project root: prefer env var, fallback to relative path
    let root = if let Ok(r) = std::env::var("IKAROS_ROOT") {
        PathBuf::from(r)
    } else {
        // When running from cargo target/debug, root is ../../../..
        // When running from tools/ikaros-monitor, root is ../..
        let candidate = exe_dir.join("..").join("..");
        if candidate.join("data").exists() {
            candidate
        } else {
            exe_dir.join("..").join("..").join("..").join("..")
        }
    };
    let root = root.canonicalize().unwrap_or(root);

    let lock_path = root.join("data").join("logs").join("ikaros-pet.lock");
    let jsonl_path = root.join("data").join("logs").join("ikaros-monitor.jsonl");
    let exit_flag = root.join("data").join("logs").join("ikaros-pet.exit");
    let main_py = root.join("bin").join("ikaros-desktop-pet").join("main.py");

    // ── Setup monitor engine ──
    let engine = monitor::MonitorEngine::new(lock_path.clone(), jsonl_path.clone(), exit_flag, main_py);
    let engine = Rc::new(RefCell::new(engine));

    // ── Timer: poll JSONL events (500ms) ──
    let eng = engine.clone();
    let ui_weak = app.as_weak();
    let timer_500 = slint::Timer::default();
    timer_500.start(
        slint::TimerMode::Repeated,
        std::time::Duration::from_millis(500),
        move || {
            let ui = ui_weak.upgrade().unwrap();
            for ev in eng.borrow_mut().poll_events() {
                handle_event(&ui, &ev);
            }
        },
    );

    // ── Timer: health check (3s) ──
    let eng = engine.clone();
    let ui_weak = app.as_weak();
    let timer_3s = slint::Timer::default();
    timer_3s.start(
        slint::TimerMode::Repeated,
        std::time::Duration::from_secs(3),
        move || {
            let ui = ui_weak.upgrade().unwrap();
            let alive = eng.borrow().is_pet_alive();
            if alive {
                ui.set_alive_text(SharedString::from("🟢 在线"));
                ui.set_alive_color(Color::from_rgb_u8(0x3f, 0xb9, 0x50));
            } else {
                ui.set_alive_text(SharedString::from("🔴 离线"));
                ui.set_alive_color(Color::from_rgb_u8(0xf8, 0x51, 0x49));
            }
        },
    );

    // ── Timer: resource monitor (2s) ──
    let eng = engine.clone();
    let ui_weak = app.as_weak();
    let timer_res = slint::Timer::default();
    timer_res.start(
        slint::TimerMode::Repeated,
        std::time::Duration::from_secs(2),
        move || {
            let ui = ui_weak.upgrade().unwrap();
            if let Some((cpu, mem, uptime)) = eng.borrow_mut().get_resources() {
                let mut parts = Vec::new();
                if cpu > 0.0 {
                    parts.push(format!("CPU {:.0}%", cpu));
                }
                if mem > 0.0 {
                    parts.push(format!("MEM {:.0}MB", mem));
                }
                if uptime > 0.0 {
                    parts.push(format!("⬆ {}", fmt_uptime(uptime)));
                }
                let text = if parts.is_empty() {
                    "--".to_string()
                } else {
                    parts.join(" · ")
                };
                ui.set_resource_text(SharedString::from(text));

                // Update alive card with uptime
                if uptime > 0.0 && eng.borrow().is_pet_alive() {
                    ui.set_alive_text(SharedString::from(format!(
                        "🟢 在线 · {}",
                        fmt_uptime(uptime)
                    )));
                }
            }
        },
    );

    // ── Callbacks ──
    let eng = engine.clone();
    app.on_restart_pet(move || {
        eng.borrow().restart_pet();
    });

    let eng = engine.clone();
    app.on_toggle_auto_restart(move |checked| {
        eng.borrow_mut().set_auto_restart(checked);
    });

    let eng = engine.clone();
    app.on_set_filter(move |idx| {
        eng.borrow_mut().set_filter(idx as usize);
    });

    let eng = engine.clone();
    app.on_search_changed(move |text| {
        eng.borrow_mut().set_search(text.to_string());
    });

    let eng = engine.clone();
    let ui_weak = app.as_weak();
    app.on_clear_events(move || {
        eng.borrow_mut().clear_events();
        let ui = ui_weak.upgrade().unwrap();
        ui.set_events(ModelRc::new(VecModel::<EventItem>::from(vec![])));
    });

    // ── Initial health check ──
    if engine.borrow().is_pet_alive() {
        app.set_alive_text(SharedString::from("🟢 在线"));
        app.set_alive_color(Color::from_rgb_u8(0x3f, 0xb9, 0x50));
    }

    // Keep timers alive
    std::mem::forget(timer_500);
    std::mem::forget(timer_3s);
    std::mem::forget(timer_res);

    app.run().expect("failed to run app");
}

fn fmt_uptime(sec: f64) -> String {
    if sec <= 0.0 {
        return "--".to_string();
    }
    let total = sec as u64;
    let h = total / 3600;
    let m = (total % 3600) / 60;
    let s = total % 60;
    if h > 0 {
        format!("{}h{:02}m", h, m)
    } else if m > 0 {
        format!("{}m{:02}s", m, s)
    } else {
        format!("{}s", s)
    }
}

fn handle_event(ui: &MainWindow, ev: &monitor::MonitorEvent) {

    let etype = ev.event_type.as_str();

    // Update state card on state events
    if etype == "state" {
        let state = ev.text.to_lowercase();
        let (label, r, g, b) = match state.as_str() {
            "listening" => ("👂 听", 0x4f, 0xc3, 0xf7),
            "thinking" => ("🧠 思考", 0xff, 0xb7, 0x4d),
            "speaking" => ("🔊 说话", 0x81, 0xc7, 0x84),
            "idle" => ("💤 待机", 0x90, 0xa4, 0xae),
            _ => ("💤 待机", 0x8b, 0x94, 0x9e),
        };
        ui.set_state_text(SharedString::from(label));
        ui.set_state_color(Color::from_rgb_u8(r, g, b));
    }

    // Update model card
    if etype == "model_info" {
        let short = ev.text.rsplit('/').next().unwrap_or(&ev.text);
        ui.set_model_text(SharedString::from(short.to_string()));
    }

    // Update STT indicator on heartbeat
    if etype == "heartbeat" && ev.module.as_deref() == Some("stt") {
        ui.set_stt_detail(SharedString::from("FunASR paraformer-zh · 运行中"));
        ui.set_stt_dot(SharedString::from("🟢"));
        ui.set_stt_dot_color(Color::from_rgb_u8(0x3f, 0xb9, 0x50));
    }
    if etype == "heartbeat" && ev.module.as_deref() == Some("tts") {
        ui.set_tts_detail(SharedString::from("edge-tts · 运行中"));
        ui.set_tts_dot(SharedString::from("🟢"));
        ui.set_tts_dot_color(Color::from_rgb_u8(0x3f, 0xb9, 0x50));
    }

    // Add to event list (if passes filter)
    let engine = monitor::GLOBAL_ENGINE.lock().unwrap();
    if let Some(ref eng) = *engine {
        if !eng.passes_filter(etype) {
            return;
        }
    }
    drop(engine);

    let (bg, fg) = bubble_colors(etype);
    let icon = event_icon(etype);
    let time_str = chrono::DateTime::from_timestamp(
        ev.ts as i64,
        ((ev.ts % 1.0) * 1_000_000_000.0) as u32,
    )
    .map(|dt| dt.format("%H:%M:%S").to_string())
    .unwrap_or_default();

    let item = EventItem {
        time: SharedString::from(time_str),
        icon: SharedString::from(icon),
        etype: SharedString::from(etype),
        text: SharedString::from(ev.text.chars().take(300).collect::<String>()),
        bg_color: bg,
        fg_color: fg,
    };

    // Append to model
    let current = ui.get_events();
    if let Some(model) = current.as_any().downcast_ref::<VecModel<EventItem>>() {
        model.push(item);
        // Limit to 500 items
        while model.row_count() > 500 {
            model.remove(0);
        }
    }
}

fn bubble_colors(etype: &str) -> (Color, Color) {
    match etype {
        "stt" => (Color::from_rgb_u8(0x1a, 0x3a, 0x5c), Color::from_rgb_u8(0x4f, 0xc3, 0xf7)),
        "llm_reply" => (Color::from_rgb_u8(0x1a, 0x3c, 0x2a), Color::from_rgb_u8(0x81, 0xc7, 0x84)),
        "state" => (Color::from_rgb_u8(0x2a, 0x2a, 0x1a), Color::from_rgb_u8(0xff, 0xb7, 0x4d)),
        "error" => (Color::from_rgb_u8(0x3a, 0x1a, 0x1a), Color::from_rgb_u8(0xf4, 0x43, 0x36)),
        "voice_activity" => (Color::from_rgb_u8(0x1a, 0x3a, 0x5c), Color::from_rgb_u8(0x4f, 0xc3, 0xf7)),
        "model_info" => (Color::from_rgb_u8(0x1a, 0x2a, 0x2a), Color::from_rgb_u8(0x80, 0xcb, 0xc4)),
        _ => (Color::from_rgb_u8(0x1a, 0x1a, 0x2e), Color::from_rgb_u8(0x90, 0xa4, 0xae)),
    }
}

fn event_icon(etype: &str) -> &'static str {
    match etype {
        "stt" => "🎤",
        "llm_reply" => "💬",
        "state" => "🧠",
        "status" => "📋",
        "neuro_state" => "🧠",
        "error" => "⚠️",
        "voice_activity" => "🎙️",
        "model_info" => "🤖",
        _ => "📋",
    }
}
