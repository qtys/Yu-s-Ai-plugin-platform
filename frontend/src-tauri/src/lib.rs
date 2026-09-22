use std::{fs::OpenOptions, io::Write, process::Command as StdCommand, sync::{Arc, Mutex}};
use std::sync::atomic::{AtomicBool, AtomicU8, AtomicU32, AtomicU64, Ordering};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use tauri::{
  AppHandle,
  menu::{Menu, MenuItem},
  tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
  Emitter, LogicalPosition, LogicalSize, Manager, RunEvent, State, WebviewWindow, WindowEvent,
};
use tauri_plugin_shell::{process::{CommandChild, CommandEvent}, ShellExt};
use tauri_plugin_autostart::ManagerExt;
use serde::Serialize;

const DATABASE_FILES: [&str; 3] = ["yus_ai.db", "yus_ai.db-wal", "yus_ai.db-shm"];
static CONTINUOUS_TRANSLATION: AtomicBool = AtomicBool::new(false);
static PET_INTERACTION_MODE: AtomicU8 = AtomicU8::new(0);
static PET_CURSOR_IGNORED: AtomicBool = AtomicBool::new(false);
static PET_ALIGN_LEFT: AtomicBool = AtomicBool::new(false);
static PET_PROACTIVE_HEIGHT: AtomicU32 = AtomicU32::new(0);
static PET_SCALE_MILLI: AtomicU32 = AtomicU32::new(1000);
static PET_DIALOG_WIDTH: AtomicU32 = AtomicU32::new(430);
static PET_DIALOG_HEIGHT: AtomicU32 = AtomicU32::new(520);
static PET_PLACEMENT_BELOW: AtomicBool = AtomicBool::new(false);
static PET_VISIBLE: AtomicBool = AtomicBool::new(false);
static PET_GAZE_LAST_EMIT_MS: AtomicU64 = AtomicU64::new(0);
static PET_AUTO_MOVE_SEQUENCE: AtomicU64 = AtomicU64::new(0);
static BACKEND_SHUTDOWN_STARTED: AtomicBool = AtomicBool::new(false);

struct DesktopState {
  backend: Mutex<Option<CommandChild>>,
  backend_exited: Arc<AtomicBool>,
  shutdown_file: Mutex<Option<std::path::PathBuf>>,
  always_on_top: Mutex<bool>,
  mini_mode: Mutex<bool>,
}

#[derive(Serialize)]
struct PetPosition {
  x: f64,
  y: f64,
}

#[derive(Clone, Serialize)]
struct PetGaze {
  x: f64,
  y: f64,
}

fn write_window_diagnostic(event: &str, details: &str) {
  let Ok(executable) = std::env::current_exe() else { return; };
  let Some(install_dir) = executable.parent() else { return; };
  let log_dir = install_dir.join("logs");
  if std::fs::create_dir_all(&log_dir).is_err() { return; }
  let timestamp = SystemTime::now().duration_since(UNIX_EPOCH).map(|value| value.as_millis()).unwrap_or(0);
  let safe_event: String = event.chars().filter(|character| character.is_ascii_alphanumeric() || matches!(character, '_' | '-')).take(64).collect();
  let safe_details = details.replace(['\r', '\n'], " ");
  if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(log_dir.join("window-diagnostics.log")) {
    let _ = writeln!(file, "{timestamp} | {safe_event} | {}", safe_details.chars().take(300).collect::<String>());
  }
}

#[tauri::command]
fn record_window_diagnostic(event: String, duration_ms: Option<f64>, details: Option<String>) {
  let message = format!("duration_ms={:.1} {}", duration_ms.unwrap_or(0.0), details.unwrap_or_default());
  write_window_diagnostic(&event, &message);
}

fn stop_backend(app: &AppHandle) {
  if BACKEND_SHUTDOWN_STARTED.swap(true, Ordering::AcqRel) { return; }
  if let Ok(mut backend) = app.state::<DesktopState>().backend.lock() {
    if let Some(child) = backend.take() {
      let state = app.state::<DesktopState>();
      let shutdown_file = state.shutdown_file.lock().ok().and_then(|path| path.clone());
      if let Some(path) = &shutdown_file {
        if std::fs::write(path, b"shutdown").is_ok() {
          for _ in 0..40 {
            if state.backend_exited.load(Ordering::Acquire) { break; }
            std::thread::sleep(std::time::Duration::from_millis(100));
          }
        }
      }
      if !state.backend_exited.load(Ordering::Acquire) {
        log::warn!("backend_graceful_shutdown_timeout; terminating backend");
        let _ = child.kill();
      }
      if let Some(path) = shutdown_file { let _ = std::fs::remove_file(path); }
    }
  }
}

fn prepare_install_data_dir(app: &AppHandle) -> Result<std::path::PathBuf, Box<dyn std::error::Error>> {
  let legacy_data_dir = app.path().app_data_dir()?;
  let executable = std::env::current_exe()?;
  let install_dir = executable.parent().ok_or("无法确定软件安装目录")?;
  let data_dir = install_dir.join("data");
  std::fs::create_dir_all(&data_dir)?;

  // 从 0.2.2 及更早版本使用的 AppData 目录迁移；保留源文件作为回退备份。
  if !data_dir.join("yus_ai.db").exists() {
    for file_name in DATABASE_FILES {
      let source = legacy_data_dir.join(file_name);
      let destination = data_dir.join(file_name);
      if source.exists() && !destination.exists() {
        std::fs::copy(source, destination)?;
      }
    }
  }

  Ok(data_dir)
}

#[tauri::command]
fn set_always_on_top(window: WebviewWindow, enabled: bool, state: State<DesktopState>) -> Result<bool, String> {
  window.set_always_on_top(enabled).map_err(|error| error.to_string())?;
  *state.always_on_top.lock().map_err(|_| "无法保存置顶状态")? = enabled;
  Ok(enabled)
}

#[tauri::command]
fn set_mini_mode(window: WebviewWindow, enabled: bool, state: State<DesktopState>) -> Result<bool, String> {
  let size = if enabled { LogicalSize::new(420.0, 640.0) } else { LogicalSize::new(1180.0, 760.0) };
  window.set_size(size).map_err(|error| error.to_string())?;
  if enabled {
    window.set_always_on_top(true).map_err(|error| error.to_string())?;
    *state.always_on_top.lock().map_err(|_| "无法保存置顶状态")? = true;
  }
  *state.mini_mode.lock().map_err(|_| "无法保存迷你模式")? = enabled;
  window.center().map_err(|error| error.to_string())?;
  Ok(enabled)
}

#[tauri::command]
fn enter_pet_mode(app: AppHandle) -> Result<(), String> {
  let pet = app.get_webview_window("pet").ok_or("找不到桌宠窗口")?;
  let _ = app.emit_to("pet", "pet-reset", ());
  pet.show().map_err(|error| error.to_string())?;
  PET_VISIBLE.store(true, Ordering::Release);
  let _ = pet.set_always_on_top(true);
  let _ = pet.set_focus();
  if let Some(main) = app.get_webview_window("main") {
    main.hide().map_err(|error| error.to_string())?;
  }
  Ok(())
}

fn resize_pet_window(window: &WebviewWindow, expanded: bool, pet_scale: f64, current_expanded: bool, current_placement: &str, dialog_width: f64, dialog_height: f64) -> Result<String, String> {
  let dpi_scale = window.scale_factor().map_err(|error| error.to_string())?;
  let old_position = window.outer_position().map_err(|error| error.to_string())?.to_logical::<f64>(dpi_scale);
  let old_size = window.outer_size().map_err(|error| error.to_string())?.to_logical::<f64>(dpi_scale);
  let old_base_height = if current_expanded { dialog_height.clamp(520.0, 760.0) } else { 320.0 };
  let old_factor = old_size.height / old_base_height;
  let old_base_size = LogicalSize::new(old_size.width / old_factor, old_base_height);
  let old_align_left = current_placement.ends_with("left");
  let old_below = current_expanded && current_placement.starts_with("below");
  let old_pet_x = if old_align_left { 21.0 } else { old_base_size.width - 191.0 };
  let old_pet_y = if current_expanded { if old_below { 12.0 } else { old_base_height - 234.0 } } else { 80.0 };
  let pet_left = old_position.x + old_pet_x * old_factor;
  let pet_top = old_position.y + old_pet_y * old_factor;
  let pet_right = pet_left + 170.0 * old_factor;

  let factor = pet_scale.clamp(0.7, 1.25);
  let base_size: LogicalSize<f64> = if expanded { LogicalSize::new(dialog_width.clamp(430.0, 720.0), dialog_height.clamp(520.0, 760.0)) } else { LogicalSize::new(250.0, 320.0) };
  let new_size = LogicalSize::new(base_size.width * factor, base_size.height * factor);
  let mut placement = "above-right".to_string();
  let mut new_position = LogicalPosition::new(pet_right - (base_size.width - 21.0) * factor, pet_top - 80.0 * factor);
  if let Some(monitor) = window.current_monitor().map_err(|error| error.to_string())? {
    let monitor_scale = monitor.scale_factor();
    let monitor_position = monitor.position().to_logical::<f64>(monitor_scale);
    let monitor_size = monitor.size().to_logical::<f64>(monitor_scale);
    let monitor_right = monitor_position.x + monitor_size.width;
    let monitor_bottom = monitor_position.y + monitor_size.height;
    let align_left = pet_right - (base_size.width - 21.0) * factor < monitor_position.x;
    let below = expanded && pet_top - (base_size.height - 234.0) * factor < monitor_position.y;
    placement = format!("{}-{}", if below { "below" } else { "above" }, if align_left { "left" } else { "right" });
    let new_pet_x = if align_left { 21.0 } else { base_size.width - 191.0 };
    let new_pet_y = if expanded { if below { 12.0 } else { base_size.height - 234.0 } } else { 80.0 };
    new_position = LogicalPosition::new(pet_left - new_pet_x * factor, pet_top - new_pet_y * factor);
    new_position.x = new_position.x.clamp(monitor_position.x, (monitor_right - new_size.width).max(monitor_position.x));
    new_position.y = new_position.y.clamp(monitor_position.y, (monitor_bottom - new_size.height).max(monitor_position.y));
  }
  window.set_size(new_size).map_err(|error| error.to_string())?;
  window.set_position(new_position).map_err(|error| error.to_string())?;
  Ok(placement)
}

#[tauri::command]
fn set_pet_layout(window: WebviewWindow, expanded: bool, scale: f64, current_expanded: bool, current_placement: String, dialog_width: f64, dialog_height: f64) -> Result<String, String> {
  PET_SCALE_MILLI.store((scale.clamp(0.7, 1.25) * 1000.0).round() as u32, Ordering::Relaxed);
  PET_DIALOG_WIDTH.store(dialog_width.clamp(430.0, 720.0).round() as u32, Ordering::Relaxed);
  PET_DIALOG_HEIGHT.store(dialog_height.clamp(520.0, 760.0).round() as u32, Ordering::Relaxed);
  let placement = resize_pet_window(&window, expanded, scale, current_expanded, &current_placement, dialog_width, dialog_height)?;
  PET_PLACEMENT_BELOW.store(placement.starts_with("below"), Ordering::Relaxed);
  Ok(placement)
}

#[tauri::command]
fn resize_pet_dialog(window: WebviewWindow, width: f64, height: f64, scale: f64, placement: String) -> Result<String, String> {
  PET_SCALE_MILLI.store((scale.clamp(0.7, 1.25) * 1000.0).round() as u32, Ordering::Relaxed);
  PET_DIALOG_WIDTH.store(width.clamp(430.0, 720.0).round() as u32, Ordering::Relaxed);
  PET_DIALOG_HEIGHT.store(height.clamp(520.0, 760.0).round() as u32, Ordering::Relaxed);
  PET_PLACEMENT_BELOW.store(placement.starts_with("below"), Ordering::Relaxed);
  let dpi_scale = window.scale_factor().map_err(|error| error.to_string())?;
  let old_position = window.outer_position().map_err(|error| error.to_string())?.to_logical::<f64>(dpi_scale);
  let old_size = window.outer_size().map_err(|error| error.to_string())?.to_logical::<f64>(dpi_scale);
  let factor = scale.clamp(0.7, 1.25);
  let old_base_width = old_size.width / factor;
  let old_base_height = old_size.height / factor;
  let align_left = placement.ends_with("left");
  let below = placement.starts_with("below");
  let old_pet_x = if align_left { 21.0 } else { old_base_width - 191.0 };
  let old_pet_y = if below { 12.0 } else { old_base_height - 234.0 };
  let pet_left = old_position.x + old_pet_x * factor;
  let pet_top = old_position.y + old_pet_y * factor;
  let new_base_width = width.clamp(430.0, 720.0);
  let new_base_height = height.clamp(520.0, 760.0);
  let new_size = LogicalSize::new(new_base_width * factor, new_base_height * factor);
  let new_pet_x = if align_left { 21.0 } else { new_base_width - 191.0 };
  let new_pet_y = if below { 12.0 } else { new_base_height - 234.0 };
  let mut new_position = LogicalPosition::new(pet_left - new_pet_x * factor, pet_top - new_pet_y * factor);
  if let Some(monitor) = window.current_monitor().map_err(|error| error.to_string())? {
    let monitor_scale = monitor.scale_factor();
    let monitor_position = monitor.position().to_logical::<f64>(monitor_scale);
    let monitor_size = monitor.size().to_logical::<f64>(monitor_scale);
    new_position.x = new_position.x.clamp(monitor_position.x, (monitor_position.x + monitor_size.width - new_size.width).max(monitor_position.x));
    new_position.y = new_position.y.clamp(monitor_position.y, (monitor_position.y + monitor_size.height - new_size.height).max(monitor_position.y));
  }
  window.set_size(new_size).map_err(|error| error.to_string())?;
  window.set_position(new_position).map_err(|error| error.to_string())?;
  Ok(placement)
}

#[tauri::command]
fn start_pet_drag(window: WebviewWindow) -> Result<(), String> {
  PET_AUTO_MOVE_SEQUENCE.fetch_add(1, Ordering::AcqRel);
  window.start_dragging().map_err(|error| error.to_string())
}

#[tauri::command]
fn cancel_pet_auto_move() -> u64 {
  PET_AUTO_MOVE_SEQUENCE.fetch_add(1, Ordering::AcqRel) + 1
}

#[tauri::command]
fn move_pet_by(window: WebviewWindow, delta_x: f64, delta_y: f64, duration_ms: u64) -> Result<u64, String> {
  let dpi_scale = window.scale_factor().map_err(|error| error.to_string())?;
  let start = window.outer_position().map_err(|error| error.to_string())?.to_logical::<f64>(dpi_scale);
  let size = window.outer_size().map_err(|error| error.to_string())?.to_logical::<f64>(dpi_scale);
  let Some(monitor) = window.current_monitor().map_err(|error| error.to_string())? else {
    return Err("无法确定桌宠所在屏幕".to_string());
  };
  let monitor_scale = monitor.scale_factor();
  let bounds = monitor.position().to_logical::<f64>(monitor_scale);
  let monitor_size = monitor.size().to_logical::<f64>(monitor_scale);
  let max_x = (bounds.x + monitor_size.width - size.width).max(bounds.x);
  let max_y = (bounds.y + monitor_size.height - size.height).max(bounds.y);
  let target = LogicalPosition::new(
    (start.x + delta_x.clamp(-140.0, 140.0)).clamp(bounds.x, max_x),
    (start.y + delta_y.clamp(-100.0, 100.0)).clamp(bounds.y, max_y),
  );
  let token = PET_AUTO_MOVE_SEQUENCE.fetch_add(1, Ordering::AcqRel) + 1;
  let duration = duration_ms.clamp(240, 1600);
  std::thread::spawn(move || {
    let steps = (duration / 16).max(1);
    for step in 1..=steps {
      if PET_AUTO_MOVE_SEQUENCE.load(Ordering::Acquire) != token { return; }
      let progress = step as f64 / steps as f64;
      let eased = 1.0 - (1.0 - progress).powi(3) + (progress * std::f64::consts::PI * 3.0).sin() * (1.0 - progress) * 0.035;
      let next = LogicalPosition::new(start.x + (target.x - start.x) * eased, start.y + (target.y - start.y) * eased);
      let _ = window.set_position(next);
      std::thread::sleep(std::time::Duration::from_millis(16));
    }
    if PET_AUTO_MOVE_SEQUENCE.load(Ordering::Acquire) == token {
      let _ = window.set_position(target);
    }
  });
  Ok(token)
}

#[tauri::command]
fn snap_pet_to_edge(window: WebviewWindow, threshold: f64, expanded: bool, current_placement: String) -> Result<String, String> {
  if expanded {
    return Ok(current_placement);
  }
  let dpi_scale = window.scale_factor().map_err(|error| error.to_string())?;
  let position = window.outer_position().map_err(|error| error.to_string())?.to_logical::<f64>(dpi_scale);
  let size = window.outer_size().map_err(|error| error.to_string())?.to_logical::<f64>(dpi_scale);
  let Some(monitor) = window.current_monitor().map_err(|error| error.to_string())? else {
    return Ok(current_placement);
  };
  let monitor_scale = monitor.scale_factor();
  let monitor_position = monitor.position().to_logical::<f64>(monitor_scale);
  let monitor_size = monitor.size().to_logical::<f64>(monitor_scale);
  let monitor_right = monitor_position.x + monitor_size.width;
  let monitor_bottom = monitor_position.y + monitor_size.height;
  let snap_distance = threshold.clamp(12.0, 96.0);
  let mut next = position;
  if (position.x - monitor_position.x).abs() <= snap_distance {
    next.x = monitor_position.x;
  } else if (monitor_right - position.x - size.width).abs() <= snap_distance {
    next.x = monitor_right - size.width;
  }
  if (position.y - monitor_position.y).abs() <= snap_distance {
    next.y = monitor_position.y;
  } else if (monitor_bottom - position.y - size.height).abs() <= snap_distance {
    next.y = monitor_bottom - size.height;
  }
  if next.x != position.x || next.y != position.y {
    window.set_position(next).map_err(|error| error.to_string())?;
  }
  let horizontal = if next.x <= monitor_position.x + snap_distance { "left" } else { "right" };
  Ok(format!("above-{horizontal}"))
}

#[tauri::command]
fn get_pet_position(window: WebviewWindow, expanded: bool, scale: f64, placement: String) -> Result<PetPosition, String> {
  let dpi_scale = window.scale_factor().map_err(|error| error.to_string())?;
  let position = window.outer_position().map_err(|error| error.to_string())?.to_logical::<f64>(dpi_scale);
  let factor = scale.clamp(0.7, 1.25);
  let width = if expanded {
    window.outer_size().map_err(|error| error.to_string())?.to_logical::<f64>(dpi_scale).width / factor
  } else { 250.0 };
  let pet_x = if placement.ends_with("left") { 21.0 } else { width - 191.0 };
  let height = if expanded {
    window.outer_size().map_err(|error| error.to_string())?.to_logical::<f64>(dpi_scale).height / factor
  } else { 320.0 };
  let pet_y = if expanded { if placement.starts_with("below") { 12.0 } else { height - 234.0 } } else { 80.0 };
  Ok(PetPosition { x: position.x + pet_x * factor, y: position.y + pet_y * factor })
}

#[tauri::command]
fn set_pet_position(window: WebviewWindow, x: f64, y: f64, scale: f64) -> Result<String, String> {
  PET_SCALE_MILLI.store((scale.clamp(0.7, 1.25) * 1000.0).round() as u32, Ordering::Relaxed);
  let factor = scale.clamp(0.7, 1.25);
  let align_left = x - 59.0 * factor < 0.0;
  let pet_x = if align_left { 21.0 } else { 59.0 };
  window.set_position(LogicalPosition::new(x - pet_x * factor, y - 80.0 * factor)).map_err(|error| error.to_string())?;
  Ok(if align_left { "above-left" } else { "above-right" }.to_string())
}

#[tauri::command]
fn hide_pet_window(window: WebviewWindow) -> Result<(), String> {
  CONTINUOUS_TRANSLATION.store(false, Ordering::Relaxed);
  window.hide().map_err(|error| error.to_string())?;
  PET_VISIBLE.store(false, Ordering::Release);
  Ok(())
}

#[tauri::command]
fn show_pet_window(window: WebviewWindow) -> Result<(), String> {
  window.show().map_err(|error| error.to_string())?;
  PET_VISIBLE.store(true, Ordering::Release);
  window.set_always_on_top(true).map_err(|error| error.to_string())
}

#[tauri::command]
fn set_continuous_translation(enabled: bool) -> bool {
  CONTINUOUS_TRANSLATION.store(enabled, Ordering::Relaxed);
  enabled
}

#[tauri::command]
fn set_pet_interaction_mode(window: WebviewWindow, mode: u8, align_left: bool, proactive_height: u32) -> Result<(), String> {
  PET_INTERACTION_MODE.store(mode.min(3), Ordering::Relaxed);
  PET_ALIGN_LEFT.store(align_left, Ordering::Relaxed);
  PET_PROACTIVE_HEIGHT.store(proactive_height.min(250), Ordering::Relaxed);
  PET_CURSOR_IGNORED.store(false, Ordering::Relaxed);
  window.set_ignore_cursor_events(false).map_err(|error| error.to_string())
}

#[tauri::command]
fn get_autostart_status(app: AppHandle) -> Result<bool, String> {
  app.autolaunch().is_enabled().map_err(|error| error.to_string())
}

#[tauri::command]
fn set_autostart(app: AppHandle, enabled: bool) -> Result<bool, String> {
  let manager = app.autolaunch();
  if enabled {
    manager.enable().map_err(|error| error.to_string())?;
  } else {
    manager.disable().map_err(|error| error.to_string())?;
  }
  let _ = app.emit("autostart-changed", enabled);
  Ok(enabled)
}

#[cfg(target_os = "windows")]
fn start_selection_monitor(app: AppHandle) {
  std::thread::spawn(move || {
    use rdev::{listen, simulate, Button, Event, EventType, Key};
    let (selection_sender, selection_receiver) = std::sync::mpsc::sync_channel::<()>(1);
    let (gaze_sender, gaze_receiver) = std::sync::mpsc::sync_channel::<PetGaze>(1);
    let selection_app = app.clone();
    std::thread::spawn(move || {
      while selection_receiver.recv().is_ok() {
        if !CONTINUOUS_TRANSLATION.load(Ordering::Relaxed) { continue; }
        std::thread::sleep(std::time::Duration::from_millis(45));
        let previous = clipboard_win::get_clipboard_string().ok();
        let sequence = clipboard_win::seq_num();
        let _ = simulate(&EventType::KeyPress(Key::ControlLeft));
        let _ = simulate(&EventType::KeyPress(Key::KeyC));
        let _ = simulate(&EventType::KeyRelease(Key::KeyC));
        let _ = simulate(&EventType::KeyRelease(Key::ControlLeft));
        std::thread::sleep(std::time::Duration::from_millis(120));
        if clipboard_win::seq_num() == sequence { continue; }
        let selected = clipboard_win::get_clipboard_string().ok();
        if let Some(text) = previous { let _ = clipboard_win::set_clipboard_string(&text); }
        if let Some(text) = selected {
          let text = text.trim();
          if !text.is_empty() && text.chars().count() <= 20_000 {
            let _ = selection_app.emit_to("pet", "screen-text-selected", text.to_string());
          }
        }
      }
    });
    let gaze_app = app.clone();
    std::thread::spawn(move || {
      while let Ok(gaze) = gaze_receiver.recv() {
        let _ = gaze_app.emit_to("pet", "global-cursor-gaze", gaze);
      }
    });
    let callback = move |event: Event| {
      if let EventType::MouseMove { x, y } = &event.event_type {
        // Never call into the window event loop from the global mouse hook while the
        // pet is hidden. During native title-bar tracking, a synchronous is_visible()
        // call here can block the low-level mouse hook and make the whole drag stutter.
        if !PET_VISIBLE.load(Ordering::Acquire) { return; }
        let callback_started = Instant::now();
        if let Some(pet) = app.get_webview_window("pet") {
          let mode = PET_INTERACTION_MODE.load(Ordering::Relaxed);
          let geometry = if let (Ok(position), Ok(dpi_scale)) = (pet.outer_position(), pet.scale_factor()) {
            let local_x = (*x - position.x as f64) / dpi_scale;
            let local_y = (*y - position.y as f64) / dpi_scale;
            let factor = (PET_SCALE_MILLI.load(Ordering::Relaxed) as f64 / 1000.0).clamp(0.7, 1.25);
            Some((local_x / factor, local_y / factor))
          } else {
            None
          };
          if let Some((base_x, base_y)) = geometry {
            let placement_left = PET_ALIGN_LEFT.load(Ordering::Relaxed);
            let base_width = if mode == 2 { PET_DIALOG_WIDTH.load(Ordering::Relaxed) as f64 } else { 250.0 };
            let base_height = if mode == 2 { PET_DIALOG_HEIGHT.load(Ordering::Relaxed) as f64 } else { 320.0 };
            let pet_left = if placement_left { 36.0 } else { base_width - 176.0 };
            let pet_top = if mode == 2 {
              if PET_PLACEMENT_BELOW.load(Ordering::Relaxed) { 32.0 } else { base_height - 214.0 }
            } else {
              100.0
            };
            let delta_x = base_x - (pet_left + 70.0);
            let delta_y = base_y - (pet_top + 75.0);
            let distance = (delta_x * delta_x + delta_y * delta_y).sqrt();
            let (gaze_x, gaze_y) = if distance < 8.0 {
              (0.0, 0.0)
            } else {
              ((delta_x / distance).clamp(-1.0, 1.0), (delta_y / distance).clamp(-1.0, 1.0))
            };
            let now_ms = SystemTime::now().duration_since(UNIX_EPOCH).map(|value| value.as_millis() as u64).unwrap_or(0);
            let previous = PET_GAZE_LAST_EMIT_MS.load(Ordering::Relaxed);
            if now_ms.saturating_sub(previous) >= 40
              && PET_GAZE_LAST_EMIT_MS.compare_exchange(previous, now_ms, Ordering::Relaxed, Ordering::Relaxed).is_ok() {
              let _ = gaze_sender.try_send(PetGaze { x: gaze_x, y: gaze_y });
            }
          }
          let interactive = if mode == 2 {
            true
          } else if let Some((base_x, base_y)) = geometry {
              let placement_left = PET_ALIGN_LEFT.load(Ordering::Relaxed);
              let pet_left = if placement_left { 36.0 } else { 74.0 };
              let over_pet = base_x >= pet_left && base_x <= pet_left + 140.0 && base_y >= 100.0 && base_y <= 250.0;
              if mode == 0 {
                over_pet
              } else if mode == 3 {
                let bubble_left = if placement_left { 18.0 } else { 27.0 };
                let bubble_bottom = 10.0 + PET_PROACTIVE_HEIGHT.load(Ordering::Relaxed) as f64;
                over_pet || (base_x >= bubble_left && base_x <= bubble_left + 205.0 && base_y >= 10.0 && base_y <= bubble_bottom)
              } else {
                let orbs = if placement_left {
                  [(85.0, 229.0), (159.0, 198.0), (190.0, 124.0), (159.0, 50.0), (85.0, 19.0)]
                } else {
                  [(124.0, 229.0), (50.0, 198.0), (19.0, 124.0), (50.0, 50.0), (124.0, 19.0)]
                };
                over_pet || orbs.iter().any(|(left, top)| base_x >= *left && base_x <= *left + 42.0 && base_y >= *top && base_y <= *top + 42.0)
              }
          } else {
            true
          };
          let ignored = !interactive;
          if PET_CURSOR_IGNORED.swap(ignored, Ordering::Relaxed) != ignored {
            let _ = pet.set_ignore_cursor_events(ignored);
          }
        }
        let elapsed = callback_started.elapsed();
        if elapsed.as_millis() >= 16 {
          write_window_diagnostic("slow_mouse_hook", &format!("duration_ms={:.1} pet_visible=true", elapsed.as_secs_f64() * 1000.0));
        }
        return;
      }
      if !CONTINUOUS_TRANSLATION.load(Ordering::Relaxed)
        || !matches!(event.event_type, EventType::ButtonRelease(Button::Left)) {
        return;
      }
      let _ = selection_sender.try_send(());
    };
    if let Err(error) = listen(callback) {
      log::error!("selection_monitor_failed error={error:?}");
    }
  });
}

#[tauri::command]
fn export_character_card(character_name: String, content: String) -> Result<String, String> {
  let executable = std::env::current_exe().map_err(|error| error.to_string())?;
  let install_dir = executable.parent().ok_or("无法确定软件安装目录")?;
  let export_dir = install_dir.join("data").join("character-exports");
  std::fs::create_dir_all(&export_dir).map_err(|error| error.to_string())?;
  let safe_name: String = character_name.chars()
    .map(|character| if r#"<>:"/\|?*"#.contains(character) || character.is_control() { '_' } else { character })
    .collect();
  let timestamp = SystemTime::now().duration_since(UNIX_EPOCH).map_err(|error| error.to_string())?.as_secs();
  let path = export_dir.join(format!("{}-{}.yus-character.json", safe_name.trim(), timestamp));
  std::fs::write(&path, content).map_err(|error| error.to_string())?;
  Ok(path.to_string_lossy().into_owned())
}

#[tauri::command]
fn copy_backup_file(source: String, destination: String) -> Result<String, String> {
  let executable = std::env::current_exe().map_err(|error| error.to_string())?;
  let install_dir = executable.parent().ok_or("无法确定软件安装目录")?;
  let backup_dir = install_dir.join("data").join("backups");
  let source_path = std::fs::canonicalize(&source).map_err(|error| error.to_string())?;
  let backup_root = std::fs::canonicalize(&backup_dir).map_err(|error| error.to_string())?;
  if !source_path.starts_with(&backup_root) || source_path.extension().and_then(|value| value.to_str()) != Some("yus-backup") {
    return Err("只能导出由 Yu's AI 创建的备份".to_string());
  }
  let mut destination_path = std::path::PathBuf::from(destination);
  if destination_path.extension().and_then(|value| value.to_str()) != Some("yus-backup") {
    destination_path.set_extension("yus-backup");
  }
  if let Some(parent) = destination_path.parent() {
    std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
  }
  std::fs::copy(&source_path, &destination_path).map_err(|error| error.to_string())?;
  Ok(destination_path.to_string_lossy().into_owned())
}

fn spawn_after_exit(path: &std::path::Path) -> Result<(), String> {
  StdCommand::new("powershell.exe")
    .args(["-NoProfile", "-WindowStyle", "Hidden", "-Command", "Start-Sleep -Milliseconds 900; Start-Process -FilePath $args[0]"])
    .arg(path)
    .spawn()
    .map_err(|error| error.to_string())?;
  Ok(())
}

#[tauri::command]
fn install_update(app: AppHandle, installer_path: String) -> Result<(), String> {
  let executable = std::env::current_exe().map_err(|error| error.to_string())?;
  let install_dir = executable.parent().ok_or("无法确定软件安装目录")?;
  let update_dir = install_dir.join("data").join("updates");
  let installer = std::fs::canonicalize(installer_path).map_err(|error| error.to_string())?;
  let update_root = std::fs::canonicalize(update_dir).map_err(|error| error.to_string())?;
  if !installer.starts_with(update_root) || installer.extension().and_then(|value| value.to_str()).map(|value| value.eq_ignore_ascii_case("exe")) != Some(true) {
    return Err("安装包不在受信任的更新目录中".to_string());
  }
  stop_backend(&app);
  spawn_after_exit(&installer)?;
  app.exit(0);
  Ok(())
}

#[tauri::command]
fn restart_application(app: AppHandle) -> Result<(), String> {
  let executable = std::env::current_exe().map_err(|error| error.to_string())?;
  stop_backend(&app);
  spawn_after_exit(&executable)?;
  app.exit(0);
  Ok(())
}

#[tauri::command]
fn show_main_window(app: AppHandle) -> Result<(), String> {
  let _ = app.emit_to("pet", "pet-reset", ());
  if let Some(pet) = app.get_webview_window("pet") {
    let _ = pet.hide();
    PET_VISIBLE.store(false, Ordering::Release);
  }
  let main = app.get_webview_window("main").ok_or("找不到主窗口")?;
  main.show().map_err(|error| error.to_string())?;
  let _ = app.emit_to("main", "main-sync", ());
  main.set_focus().map_err(|error| error.to_string())?;
  Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  let application = tauri::Builder::default()
    .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
      if let Some(main) = app.get_webview_window("main") {
        if main.is_visible().unwrap_or(false) {
          let _ = main.unminimize();
          let _ = main.set_focus();
          return;
        }
      }
      if let Some(pet) = app.get_webview_window("pet") {
        let _ = pet.show();
        PET_VISIBLE.store(true, Ordering::Release);
        let _ = pet.set_always_on_top(true);
        let _ = pet.set_focus();
      }
    }))
    .plugin(tauri_plugin_shell::init())
    .plugin(tauri_plugin_dialog::init())
    .plugin(tauri_plugin_log::Builder::default().level(log::LevelFilter::Info).build())
    .plugin(tauri_plugin_autostart::Builder::new().app_name("Yus AI").build())
    .manage(DesktopState {
      backend: Mutex::new(None),
      backend_exited: Arc::new(AtomicBool::new(true)),
      shutdown_file: Mutex::new(None),
      always_on_top: Mutex::new(false),
      mini_mode: Mutex::new(false),
    })
    .invoke_handler(tauri::generate_handler![set_always_on_top, set_mini_mode, enter_pet_mode, show_main_window, set_pet_layout, resize_pet_dialog, start_pet_drag, cancel_pet_auto_move, move_pet_by, snap_pet_to_edge, get_pet_position, set_pet_position, hide_pet_window, show_pet_window, set_continuous_translation, set_pet_interaction_mode, get_autostart_status, set_autostart, export_character_card, copy_backup_file, install_update, restart_application, record_window_diagnostic])
    .setup(|app| {
      let legacy_data_dir = app.path().app_data_dir()?;
      let data_dir = prepare_install_data_dir(app.handle())?;
      let fallback_logs = legacy_data_dir.join("logs");
      let install_logs = data_dir.parent().unwrap_or(&data_dir).join("logs");
      let log_dir = if std::fs::create_dir_all(&install_logs).is_ok() { install_logs } else {
        std::fs::create_dir_all(&fallback_logs)?;
        fallback_logs
      };
      let sidecar_log = log_dir.join("sidecar.log");
      let translation_runtime_dir = data_dir.join("translation-runtime");
      std::fs::create_dir_all(data_dir.join("temp"))?;
      let shutdown_file = data_dir.join("backend.shutdown");
      let _ = std::fs::remove_file(&shutdown_file);
      *app.state::<DesktopState>().shutdown_file.lock().unwrap() = Some(shutdown_file.clone());
      let backend_exited = app.state::<DesktopState>().backend_exited.clone();
      backend_exited.store(false, Ordering::Release);
      let (mut output, child) = app.shell().sidecar("yus-ai-backend")?
        .env("YUS_AI_SHUTDOWN_FILE", &shutdown_file)
        .env("TEMP", data_dir.join("temp"))
        .env("TMP", data_dir.join("temp"))
        .env("YUS_AI_DATA_DIR", &data_dir)
        .env("YUS_AI_LOG_DIR", &log_dir)
        .env("ARGOS_PACKAGES_DIR", data_dir.join("translation-models"))
        .env("ARGOS_CHUNK_TYPE", "MINISBD")
        .env("XDG_DATA_HOME", translation_runtime_dir.join("data"))
        .env("XDG_CONFIG_HOME", translation_runtime_dir.join("config"))
        .env("XDG_CACHE_HOME", translation_runtime_dir.join("cache"))
        .env("YUS_AI_PARENT_PID", std::process::id().to_string())
        .spawn()?;
      *app.state::<DesktopState>().backend.lock().unwrap() = Some(child);
      #[cfg(target_os = "windows")]
      start_selection_monitor(app.handle().clone());
      tauri::async_runtime::spawn(async move {
        while let Some(event) = output.recv().await {
          if matches!(&event, CommandEvent::Terminated(_)) { backend_exited.store(true, Ordering::Release); }
          let line = match event {
            CommandEvent::Stdout(bytes) => Some(("stdout", bytes)),
            CommandEvent::Stderr(bytes) => Some(("stderr", bytes)),
            CommandEvent::Error(message) => Some(("error", message.into_bytes())),
            CommandEvent::Terminated(payload) => Some(("terminated", format!("{:?}", payload).into_bytes())),
            _ => None,
          };
          if let Some((kind, bytes)) = line {
            if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&sidecar_log) {
              let _ = writeln!(file, "{} | {}", kind, String::from_utf8_lossy(&bytes).trim());
            }
          }
        }
        backend_exited.store(true, Ordering::Release);
      });

      let show = MenuItem::with_id(app, "show", "显示主界面", true, None::<&str>)?;
      let pet_toggle = MenuItem::with_id(app, "pet_toggle", "显示/隐藏桌宠", true, None::<&str>)?;
      let pet_size_up = MenuItem::with_id(app, "pet_size_up", "桌宠放大", true, None::<&str>)?;
      let pet_size_down = MenuItem::with_id(app, "pet_size_down", "桌宠缩小", true, None::<&str>)?;
      let pet_opacity_up = MenuItem::with_id(app, "pet_opacity_up", "桌宠更清晰", true, None::<&str>)?;
      let pet_opacity_down = MenuItem::with_id(app, "pet_opacity_down", "桌宠更透明", true, None::<&str>)?;
      let autostart_enabled = app.handle().autolaunch().is_enabled().unwrap_or(false);
      let autostart = MenuItem::with_id(app, "autostart", if autostart_enabled { "关闭开机自启" } else { "开启开机自启" }, true, None::<&str>)?;
      let pin = MenuItem::with_id(app, "pin", "切换窗口置顶", true, None::<&str>)?;
      let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
      let menu = Menu::with_items(app, &[&show, &pet_toggle, &pet_size_up, &pet_size_down, &pet_opacity_up, &pet_opacity_down, &autostart, &pin, &quit])?;
      let autostart_menu = autostart.clone();
      TrayIconBuilder::new()
        .icon(app.default_window_icon().unwrap().clone())
        .menu(&menu)
        .on_menu_event(move |app, event| match event.id.as_ref() {
          "show" => {
            let _ = app.emit_to("pet", "pet-reset", ());
            if let Some(pet) = app.get_webview_window("pet") {
              let _ = pet.hide();
              PET_VISIBLE.store(false, Ordering::Release);
            }
            if let Some(window) = app.get_webview_window("main") { let _ = window.show(); let _ = app.emit_to("main", "main-sync", ()); let _ = window.set_focus(); }
          },
          "pet_toggle" => if let Some(pet) = app.get_webview_window("pet") {
            if pet.is_visible().unwrap_or(false) { let _ = pet.hide(); PET_VISIBLE.store(false, Ordering::Release); }
            else { let _ = pet.show(); PET_VISIBLE.store(true, Ordering::Release); let _ = pet.set_focus(); }
          },
          "pet_size_up" => { let _ = app.emit_to("pet", "pet-control", "size-up"); },
          "pet_size_down" => { let _ = app.emit_to("pet", "pet-control", "size-down"); },
          "pet_opacity_up" => { let _ = app.emit_to("pet", "pet-control", "opacity-up"); },
          "pet_opacity_down" => { let _ = app.emit_to("pet", "pet-control", "opacity-down"); },
          "autostart" => {
            let manager = app.autolaunch();
            let enabled = manager.is_enabled().unwrap_or(false);
            let next = !enabled;
            let result = if next { manager.enable() } else { manager.disable() };
            if result.is_ok() {
              let _ = autostart_menu.set_text(if next { "关闭开机自启" } else { "开启开机自启" });
              let _ = app.emit("autostart-changed", next);
            }
          },
          "pin" => if let Some(window) = app.get_webview_window("main") {
            let state = app.state::<DesktopState>();
            if let Ok(mut pinned) = state.always_on_top.lock() { *pinned = !*pinned; let _ = window.set_always_on_top(*pinned); };
          },
          "quit" => {
            if let Some(window) = app.get_webview_window("main") { let _ = window.hide(); }
            if let Some(window) = app.get_webview_window("pet") { let _ = window.hide(); PET_VISIBLE.store(false, Ordering::Release); }
            let app_handle = app.clone();
            std::thread::spawn(move || {
              stop_backend(&app_handle);
              app_handle.exit(0);
            });
          },
          _ => {}
        })
        .on_tray_icon_event(|tray, event| {
          if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
            let _ = tray.app_handle().emit_to("pet", "pet-reset", ());
            if let Some(pet) = tray.app_handle().get_webview_window("pet") { let _ = pet.hide(); PET_VISIBLE.store(false, Ordering::Release); }
            if let Some(window) = tray.app_handle().get_webview_window("main") { let _ = window.show(); let _ = tray.app_handle().emit_to("main", "main-sync", ()); let _ = window.set_focus(); }
          }
        })
        .build(app)?;
      Ok(())
    })
    .on_window_event(|window, event| {
      if window.label() == "main" {
        if let WindowEvent::Focused(focused) = event {
          write_window_diagnostic("native_focus", &format!("focused={focused}"));
        }
      }
      if let WindowEvent::CloseRequested { api, .. } = event {
        api.prevent_close();
        let _ = window.hide();
        if window.label() == "main" {
          if let Some(pet) = window.app_handle().get_webview_window("pet") {
            let _ = window.app_handle().emit_to("pet", "pet-reset", ());
            let _ = pet.show();
            PET_VISIBLE.store(true, Ordering::Release);
            let _ = pet.set_always_on_top(true);
          }
        } else if window.label() == "pet" {
          PET_VISIBLE.store(false, Ordering::Release);
        }
      }
    })
    .build(tauri::generate_context!())
    .expect("无法启动 Yu's AI");

  application.run(|app, event| {
    if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
      stop_backend(app);
    }
  });
}
