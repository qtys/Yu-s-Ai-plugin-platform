use tauri::{LogicalPosition, LogicalSize, WebviewWindow};

/// Commit position and size together: never expose the resized window at its old origin.
pub fn apply(window: &WebviewWindow, position: LogicalPosition<f64>, size: LogicalSize<f64>) -> Result<(), String> {
  apply_window(&window.as_ref().window(), position, size)
}

pub fn apply_window(window: &tauri::Window, position: LogicalPosition<f64>, size: LogicalSize<f64>) -> Result<(), String> {
  #[cfg(target_os = "windows")]
  {
    use std::ffi::c_void;
    #[link(name = "user32")]
    extern "system" {
      fn SetWindowPos(hwnd: *mut c_void, after: *mut c_void, x: i32, y: i32, cx: i32, cy: i32, flags: u32) -> i32;
    }
    let dpi = window.scale_factor().map_err(|error| format!("{} scale_factor: {error}", window.label()))?;
    let position = position.to_physical::<i32>(dpi);
    let size = size.to_physical::<u32>(dpi);
    let hwnd = window.hwnd().map_err(|error| format!("{} hwnd: {error}", window.label()))?;
    // SWP_NOZORDER | SWP_NOACTIVATE: preserve topmost state and keyboard focus.
    let success = unsafe { SetWindowPos(hwnd.0 as *mut c_void, std::ptr::null_mut(), position.x, position.y, size.width as i32, size.height as i32, 0x0004 | 0x0010) };
    if success == 0 { return Err(std::io::Error::last_os_error().to_string()); }
  }
  #[cfg(not(target_os = "windows"))]
  {
    window.set_size(size).map_err(|error| error.to_string())?;
    window.set_position(position).map_err(|error| error.to_string())?;
  }
  Ok(())
}
