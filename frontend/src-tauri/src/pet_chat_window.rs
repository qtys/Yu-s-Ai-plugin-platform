use std::sync::{atomic::{AtomicBool, Ordering}, Mutex};
use serde::Serialize;
use tauri::{AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, WebviewWindow};
static OPEN: AtomicBool = AtomicBool::new(false);
static REQUEST: Mutex<(f64, f64, f64)> = Mutex::new((430.0, 520.0, 1.0));

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Layout {
  pub width: f64, pub height: f64, pub pet_x: f64, pub pet_y: f64,
  pub input_x: f64, pub input_y: f64, pub available: f64,
  pub bubble_x: f64,
  #[serde(skip_serializing_if = "Option::is_none")]
  pub bubble_top: Option<f64>,
  pub bubble_width_limit: f64,
}
fn calculate(app: &AppHandle, width: f64, height: f64, scale: f64) -> Result<(LogicalPosition<f64>, Layout), String> {
  let pet = app.get_webview_window("pet").ok_or("找不到桌宠窗口")?;
  let dpi = pet.scale_factor().map_err(|e| e.to_string())?;
  let position = pet.outer_position().map_err(|e| e.to_string())?.to_logical::<f64>(dpi);
  let factor = scale.clamp(0.7, 1.25);
  let left = if super::PET_ALIGN_LEFT.load(Ordering::Relaxed) { 36.0 } else { 74.0 };
  let center = position.x + (left + 70.0) * factor;
  let top = position.y + 100.0 * factor;
  let monitor = pet.current_monitor().map_err(|e| e.to_string())?.ok_or("无法读取屏幕边界")?;
  let origin = monitor.position().to_logical::<f64>(monitor.scale_factor());
  let size = monitor.size().to_logical::<f64>(monitor.scale_factor());
  Ok(plan_layout(center, top, origin, size, width, height, factor))
}

fn plan_layout(center: f64, top: f64, origin: LogicalPosition<f64>, size: LogicalSize<f64>, width: f64, height: f64, factor: f64) -> (LogicalPosition<f64>, Layout) {
  let needed_bubbles = (height - 236.0).max(0.0);
  let side = (top - origin.y) / factor - 12.0 < needed_bubbles;
  let width = if side { 720.0 } else { width.clamp(430.0, 720.0) }.min(size.width / factor);
  let height = height.clamp(320.0, 2000.0).min(size.height / factor);
  let x = (center - width * factor / 2.0).clamp(origin.x, (origin.x + size.width - width * factor).max(origin.x));
  let y = (top - (height - 214.0) * factor).clamp(origin.y, (origin.y + size.height - height * factor).max(origin.y));
  let pet_x = (center - x) / factor;
  let pet_y = (top - y) / factor;
  let left_room = (pet_x - 82.0).max(0.0);
  let right_room = (width - pet_x - 82.0).max(0.0);
  let bubble_width_limit = if side { left_room.max(right_room).min(600.0) } else { (2.0 * pet_x.min(width - pet_x) - 24.0).max(1.0) };
  let bubble_x = if !side { pet_x } else if left_room >= right_room { pet_x - 82.0 - bubble_width_limit / 2.0 } else { pet_x + 82.0 + bubble_width_limit / 2.0 };
  (LogicalPosition::new(x, y), Layout { width, height, pet_x, pet_y,
    input_x: (pet_x - 100.0).clamp(0.0, (width - 200.0).max(0.0)),
    input_y: (pet_y + 156.0).min(height - 58.0).max(0.0),
    available: if side { height - 24.0 } else { (pet_y - 12.0).max(0.0) },
    bubble_x, bubble_top: if side { Some(12.0) } else { None }, bubble_width_limit })
}

#[cfg(test)]
mod tests {
  use super::*;
  #[test]
  fn ordinary_chat_keeps_the_pet_screen_anchor() {
    let (pos, layout) = plan_layout(900.0, 600.0, LogicalPosition::new(0.0, 0.0), LogicalSize::new(1920.0,1080.0), 430.0,520.0,1.0);
    assert_eq!(pos.x + layout.pet_x, 900.0);
    assert_eq!(pos.y + layout.pet_y, 600.0);
    assert!(layout.bubble_top.is_none());
    assert_eq!(layout.input_y, layout.pet_y + 156.0);
  }
  #[test]
  fn top_edge_moves_only_bubbles_to_the_side() {
    let (pos, layout) = plan_layout(1500.0, 100.0, LogicalPosition::new(0.0,0.0), LogicalSize::new(1920.0,1080.0), 430.0,700.0,1.0);
    assert_eq!(pos.x + layout.pet_x, 1500.0);
    assert_eq!(pos.y + layout.pet_y, 100.0);
    assert_eq!(layout.bubble_top, Some(12.0));
    assert!(layout.bubble_x + layout.bubble_width_limit / 2.0 <= layout.pet_x - 82.0);
    assert!(pos.x >= 0.0 && pos.x + layout.width <= 1920.0);
  }
  #[test]
  fn negative_monitor_and_scaled_pet_remain_in_bounds() {
    let (pos, layout) = plan_layout(-1600.0,400.0,LogicalPosition::new(-1920.0,0.0),LogicalSize::new(1920.0,1080.0),720.0,800.0,0.86);
    assert!((pos.x + layout.pet_x * 0.86 + 1600.0).abs() < 0.001);
    assert!((pos.y + layout.pet_y * 0.86 - 400.0).abs() < 0.001);
    assert!(pos.x >= -1920.0 && pos.x + layout.width * 0.86 <= 0.0);
  }
}
#[tauri::command]
pub async fn fit_pet_chat_window(window: WebviewWindow, app: AppHandle, width: f64, height: f64, scale: f64) -> Result<Layout, String> {
  if window.label() != "pet" { return Err("仅桌宠可定位聊天窗口".into()); }
  let (position, layout) = calculate(&app, width, height, scale).map_err(|e| format!("calculate: {e}"))?;
  let chat = if let Some(chat) = app.get_webview_window("pet-chat") { chat } else {
    let config = app.config().app.windows.iter().find(|config| config.label == "pet-chat").ok_or("找不到聊天窗口配置")?;
    tauri::WebviewWindowBuilder::from_config(&app, config).map_err(|e| e.to_string())?.build().map_err(|e| e.to_string())?
  };
  let native = chat.as_ref().window();
  super::window_bounds::apply_window(&native, position, LogicalSize::new(layout.width * scale, layout.height * scale)).map_err(|e| format!("chat_bounds: {e}"))?;
  let _ = chat;
  *REQUEST.lock().map_err(|e| e.to_string())? = (width, height, scale);
  OPEN.store(true, Ordering::Release);
  super::write_window_diagnostic("pet_chat_companion_layout", &format!("x={:.1} y={:.1} width={:.1} height={:.1} pet_host_unchanged=true", position.x, position.y, layout.width, layout.height));
  Ok(layout)
}
#[tauri::command]
pub async fn hide_pet_chat_window(app: AppHandle) {
  OPEN.store(false, Ordering::Release);
  if let Some(chat) = app.get_webview_window("pet-chat") { let _ = chat.hide(); let _ = chat.set_focusable(false); }
}
#[tauri::command]
pub async fn show_pet_chat_window(window: WebviewWindow) -> Result<(), String> {
  if window.label() != "pet-chat" { return Err("仅聊天窗口可确认绘制完成".into()); }
  if OPEN.load(Ordering::Acquire) && super::PET_VISIBLE.load(Ordering::Acquire) { window.show().map_err(|e| e.to_string())?; }
  Ok(())
}
#[tauri::command]
pub async fn set_pet_chat_regions(window: WebviewWindow, regions: Vec<[f64; 4]>, scale: f64) -> Result<(), String> {
  if window.label() != "pet-chat" { return Err("仅聊天窗口可设置点击区域".into()); }
  #[cfg(target_os = "windows")]
  {
    use std::ffi::c_void;
    #[link(name = "gdi32")]
    extern "system" {
      fn CreateRectRgn(left: i32, top: i32, right: i32, bottom: i32) -> *mut c_void;
      fn CreateRoundRectRgn(left: i32, top: i32, right: i32, bottom: i32, width: i32, height: i32) -> *mut c_void;
      fn CombineRgn(destination: *mut c_void, first: *mut c_void, second: *mut c_void, mode: i32) -> i32;
      fn DeleteObject(object: *mut c_void) -> i32;
    }
    #[link(name = "user32")]
    extern "system" { fn SetWindowRgn(hwnd: *mut c_void, region: *mut c_void, redraw: i32) -> i32; }
    let factor = window.scale_factor().map_err(|e| e.to_string())? * scale.clamp(0.7, 1.25);
    let hwnd = window.hwnd().map_err(|e| e.to_string())?;
    unsafe {
      let combined = CreateRectRgn(0, 0, 0, 0);
      if combined.is_null() { return Err("无法创建聊天点击区域".into()); }
      for rect in regions.into_iter().take(5) {
        if rect.iter().any(|v| !v.is_finite()) || rect[2] <= 0.0 || rect[3] <= 0.0 { continue; }
        let region = CreateRoundRectRgn((rect[0] * factor).floor() as i32, (rect[1] * factor).floor() as i32,
          ((rect[0] + rect[2]) * factor).ceil() as i32, ((rect[1] + rect[3]) * factor).ceil() as i32, (18.0 * factor) as i32, (18.0 * factor) as i32);
        if region.is_null() || CombineRgn(combined, combined, region, 2) == 0 {
          if !region.is_null() { DeleteObject(region); }
          DeleteObject(combined); return Err("无法合并聊天点击区域".into());
        }
        DeleteObject(region);
      }
      if SetWindowRgn(hwnd.0 as *mut c_void, combined, 1) == 0 {
        DeleteObject(combined); return Err(std::io::Error::last_os_error().to_string());
      }
    }
  }
  #[cfg(not(target_os = "windows"))]
  let _ = (regions, scale);
  Ok(())
}
pub fn start_following(app: AppHandle) {
  std::thread::spawn(move || {
    let mut previous = None;
    loop {
      std::thread::sleep(std::time::Duration::from_millis(32));
      if !OPEN.load(Ordering::Acquire) { previous = None; continue; }
      if !super::PET_VISIBLE.load(Ordering::Acquire) {
        if let Some(chat) = app.get_webview_window("pet-chat") { let _ = chat.hide(); }
        previous = None; continue;
      }
      let Some(pet) = app.get_webview_window("pet") else { break; };
      let Ok(position) = pet.outer_position() else { continue; };
      let Ok(size) = pet.outer_size() else { continue; };
      let key = (position.x, position.y, size.width, size.height);
      if previous == Some(key) { continue; }
      previous = Some(key);
      let Ok(request) = REQUEST.lock().map(|request| *request) else { continue; };
      if let Ok((position, layout)) = calculate(&app, request.0, request.1, request.2) {
        if let Some(chat) = app.get_webview_window("pet-chat") {
          let _ = super::window_bounds::apply(&chat, position, LogicalSize::new(layout.width * request.2, layout.height * request.2));
          let _ = app.emit_to("pet", "pet-chat-followed", layout);
        }
      }
    }
  });
}
