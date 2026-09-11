use std::{fs::OpenOptions, io::Write, sync::Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use tauri::{
  AppHandle,
  menu::{Menu, MenuItem},
  tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
  Emitter, LogicalPosition, LogicalSize, Manager, RunEvent, State, WebviewWindow, WindowEvent,
};
use tauri_plugin_shell::{process::{CommandChild, CommandEvent}, ShellExt};
use serde::Serialize;

const DATABASE_FILES: [&str; 3] = ["yus_ai.db", "yus_ai.db-wal", "yus_ai.db-shm"];

struct DesktopState {
  backend: Mutex<Option<CommandChild>>,
  always_on_top: Mutex<bool>,
  mini_mode: Mutex<bool>,
}

#[derive(Serialize)]
struct PetPosition {
  x: f64,
  y: f64,
}

fn stop_backend(app: &AppHandle) {
  if let Ok(mut backend) = app.state::<DesktopState>().backend.lock() {
    if let Some(child) = backend.take() {
      let _ = child.kill();
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
  let _ = pet.set_always_on_top(true);
  let _ = pet.set_focus();
  if let Some(main) = app.get_webview_window("main") {
    main.hide().map_err(|error| error.to_string())?;
  }
  Ok(())
}

fn resize_pet_window(window: &WebviewWindow, expanded: bool, pet_scale: f64, current_expanded: bool, current_placement: &str) -> Result<String, String> {
  let dpi_scale = window.scale_factor().map_err(|error| error.to_string())?;
  let old_position = window.outer_position().map_err(|error| error.to_string())?.to_logical::<f64>(dpi_scale);
  let old_size = window.outer_size().map_err(|error| error.to_string())?.to_logical::<f64>(dpi_scale);
  let old_base_size: LogicalSize<f64> = if current_expanded { LogicalSize::new(430.0, 520.0) } else { LogicalSize::new(250.0, 320.0) };
  let old_factor = old_size.width / old_base_size.width;
  let old_align_left = current_placement.ends_with("left");
  let old_below = current_expanded && current_placement.starts_with("below");
  let old_pet_x = if old_align_left { 21.0 } else { old_base_size.width - 191.0 };
  let old_pet_y = if current_expanded { if old_below { 12.0 } else { 286.0 } } else { 80.0 };
  let pet_left = old_position.x + old_pet_x * old_factor;
  let pet_top = old_position.y + old_pet_y * old_factor;
  let pet_right = pet_left + 170.0 * old_factor;

  let factor = pet_scale.clamp(0.7, 1.25);
  let base_size: LogicalSize<f64> = if expanded { LogicalSize::new(430.0, 520.0) } else { LogicalSize::new(250.0, 320.0) };
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
    let below = expanded && pet_top - 286.0 * factor < monitor_position.y;
    placement = format!("{}-{}", if below { "below" } else { "above" }, if align_left { "left" } else { "right" });
    let new_pet_x = if align_left { 21.0 } else { base_size.width - 191.0 };
    let new_pet_y = if expanded { if below { 12.0 } else { 286.0 } } else { 80.0 };
    new_position = LogicalPosition::new(pet_left - new_pet_x * factor, pet_top - new_pet_y * factor);
    new_position.x = new_position.x.clamp(monitor_position.x, monitor_right - new_size.width);
    new_position.y = new_position.y.clamp(monitor_position.y, monitor_bottom - new_size.height);
  }
  window.set_size(new_size).map_err(|error| error.to_string())?;
  window.set_position(new_position).map_err(|error| error.to_string())?;
  Ok(placement)
}

#[tauri::command]
fn set_pet_layout(window: WebviewWindow, expanded: bool, scale: f64, current_expanded: bool, current_placement: String) -> Result<String, String> {
  resize_pet_window(&window, expanded, scale, current_expanded, &current_placement)
}

#[tauri::command]
fn start_pet_drag(window: WebviewWindow) -> Result<(), String> {
  window.start_dragging().map_err(|error| error.to_string())
}

#[tauri::command]
fn get_pet_position(window: WebviewWindow, expanded: bool, scale: f64, placement: String) -> Result<PetPosition, String> {
  let dpi_scale = window.scale_factor().map_err(|error| error.to_string())?;
  let position = window.outer_position().map_err(|error| error.to_string())?.to_logical::<f64>(dpi_scale);
  let factor = scale.clamp(0.7, 1.25);
  let width = if expanded { 430.0 } else { 250.0 };
  let pet_x = if placement.ends_with("left") { 21.0 } else { width - 191.0 };
  let pet_y = if expanded { if placement.starts_with("below") { 12.0 } else { 286.0 } } else { 80.0 };
  Ok(PetPosition { x: position.x + pet_x * factor, y: position.y + pet_y * factor })
}

#[tauri::command]
fn set_pet_position(window: WebviewWindow, x: f64, y: f64, scale: f64) -> Result<String, String> {
  let factor = scale.clamp(0.7, 1.25);
  let align_left = x - 59.0 * factor < 0.0;
  let pet_x = if align_left { 21.0 } else { 59.0 };
  window.set_position(LogicalPosition::new(x - pet_x * factor, y - 80.0 * factor)).map_err(|error| error.to_string())?;
  Ok(if align_left { "above-left" } else { "above-right" }.to_string())
}

#[tauri::command]
fn hide_pet_window(window: WebviewWindow) -> Result<(), String> {
  window.hide().map_err(|error| error.to_string())
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
fn show_main_window(app: AppHandle) -> Result<(), String> {
  let _ = app.emit_to("pet", "pet-reset", ());
  if let Some(pet) = app.get_webview_window("pet") {
    let _ = pet.hide();
  }
  let main = app.get_webview_window("main").ok_or("找不到主窗口")?;
  main.show().map_err(|error| error.to_string())?;
  main.set_focus().map_err(|error| error.to_string())?;
  Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  let application = tauri::Builder::default()
    .plugin(tauri_plugin_shell::init())
    .plugin(tauri_plugin_log::Builder::default().level(log::LevelFilter::Info).build())
    .manage(DesktopState {
      backend: Mutex::new(None),
      always_on_top: Mutex::new(false),
      mini_mode: Mutex::new(false),
    })
    .invoke_handler(tauri::generate_handler![set_always_on_top, set_mini_mode, enter_pet_mode, show_main_window, set_pet_layout, start_pet_drag, get_pet_position, set_pet_position, hide_pet_window, export_character_card])
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
      let (mut output, child) = app.shell().sidecar("yus-ai-backend")?
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
      tauri::async_runtime::spawn(async move {
        while let Some(event) = output.recv().await {
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
      });

      let show = MenuItem::with_id(app, "show", "显示主界面", true, None::<&str>)?;
      let pet_toggle = MenuItem::with_id(app, "pet_toggle", "显示/隐藏桌宠", true, None::<&str>)?;
      let pet_size_up = MenuItem::with_id(app, "pet_size_up", "桌宠放大", true, None::<&str>)?;
      let pet_size_down = MenuItem::with_id(app, "pet_size_down", "桌宠缩小", true, None::<&str>)?;
      let pet_opacity_up = MenuItem::with_id(app, "pet_opacity_up", "桌宠更清晰", true, None::<&str>)?;
      let pet_opacity_down = MenuItem::with_id(app, "pet_opacity_down", "桌宠更透明", true, None::<&str>)?;
      let pin = MenuItem::with_id(app, "pin", "切换窗口置顶", true, None::<&str>)?;
      let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
      let menu = Menu::with_items(app, &[&show, &pet_toggle, &pet_size_up, &pet_size_down, &pet_opacity_up, &pet_opacity_down, &pin, &quit])?;
      TrayIconBuilder::new()
        .icon(app.default_window_icon().unwrap().clone())
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
          "show" => {
            let _ = app.emit_to("pet", "pet-reset", ());
            if let Some(pet) = app.get_webview_window("pet") {
              let _ = pet.hide();
            }
            if let Some(window) = app.get_webview_window("main") { let _ = window.show(); let _ = window.set_focus(); }
          },
          "pet_toggle" => if let Some(pet) = app.get_webview_window("pet") {
            if pet.is_visible().unwrap_or(false) { let _ = pet.hide(); }
            else { let _ = pet.show(); let _ = pet.set_focus(); }
          },
          "pet_size_up" => { let _ = app.emit_to("pet", "pet-control", "size-up"); },
          "pet_size_down" => { let _ = app.emit_to("pet", "pet-control", "size-down"); },
          "pet_opacity_up" => { let _ = app.emit_to("pet", "pet-control", "opacity-up"); },
          "pet_opacity_down" => { let _ = app.emit_to("pet", "pet-control", "opacity-down"); },
          "pin" => if let Some(window) = app.get_webview_window("main") {
            let state = app.state::<DesktopState>();
            if let Ok(mut pinned) = state.always_on_top.lock() { *pinned = !*pinned; let _ = window.set_always_on_top(*pinned); };
          },
          "quit" => {
            stop_backend(app);
            app.exit(0);
          },
          _ => {}
        })
        .on_tray_icon_event(|tray, event| {
          if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
            let _ = tray.app_handle().emit_to("pet", "pet-reset", ());
            if let Some(pet) = tray.app_handle().get_webview_window("pet") { let _ = pet.hide(); }
            if let Some(window) = tray.app_handle().get_webview_window("main") { let _ = window.show(); let _ = window.set_focus(); }
          }
        })
        .build(app)?;
      Ok(())
    })
    .on_window_event(|window, event| {
      if let WindowEvent::CloseRequested { api, .. } = event {
        api.prevent_close();
        let _ = window.hide();
        if window.label() == "main" {
          if let Some(pet) = window.app_handle().get_webview_window("pet") {
            let _ = window.app_handle().emit_to("pet", "pet-reset", ());
            let _ = pet.show();
            let _ = pet.set_always_on_top(true);
          }
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
