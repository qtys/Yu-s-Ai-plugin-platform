use std::{fs::OpenOptions, io::Write, sync::Mutex};

use tauri::{
  AppHandle,
  menu::{Menu, MenuItem},
  tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
  LogicalSize, Manager, RunEvent, State, WebviewWindow, WindowEvent,
};
use tauri_plugin_shell::{process::{CommandChild, CommandEvent}, ShellExt};

struct DesktopState {
  backend: Mutex<Option<CommandChild>>,
  always_on_top: Mutex<bool>,
  mini_mode: Mutex<bool>,
}

fn stop_backend(app: &AppHandle) {
  if let Ok(mut backend) = app.state::<DesktopState>().backend.lock() {
    if let Some(child) = backend.take() {
      let _ = child.kill();
    }
  }
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
    .invoke_handler(tauri::generate_handler![set_always_on_top, set_mini_mode])
    .setup(|app| {
      let data_dir = app.path().app_data_dir()?;
      std::fs::create_dir_all(&data_dir)?;
      let fallback_logs = data_dir.join("logs");
      let install_logs = std::env::current_exe()?.parent().unwrap_or(&data_dir).join("logs");
      let log_dir = if std::fs::create_dir_all(&install_logs).is_ok() { install_logs } else {
        std::fs::create_dir_all(&fallback_logs)?;
        fallback_logs
      };
      let sidecar_log = log_dir.join("sidecar.log");
      let (mut output, child) = app.shell().sidecar("yus-ai-backend")?
        .env("YUS_AI_DATA_DIR", &data_dir)
        .env("YUS_AI_LOG_DIR", &log_dir)
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

      let show = MenuItem::with_id(app, "show", "显示 Yu's AI", true, None::<&str>)?;
      let pin = MenuItem::with_id(app, "pin", "切换窗口置顶", true, None::<&str>)?;
      let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
      let menu = Menu::with_items(app, &[&show, &pin, &quit])?;
      TrayIconBuilder::new()
        .icon(app.default_window_icon().unwrap().clone())
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
          "show" => if let Some(window) = app.get_webview_window("main") { let _ = window.show(); let _ = window.set_focus(); },
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
            if let Some(window) = tray.app_handle().get_webview_window("main") { let _ = window.show(); let _ = window.set_focus(); }
          }
        })
        .build(app)?;
      Ok(())
    })
    .on_window_event(|window, event| {
      if let WindowEvent::CloseRequested { api, .. } = event { api.prevent_close(); let _ = window.hide(); }
    })
    .build(tauri::generate_context!())
    .expect("无法启动 Yu's AI");

  application.run(|app, event| {
    if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
      stop_backend(app);
    }
  });
}
