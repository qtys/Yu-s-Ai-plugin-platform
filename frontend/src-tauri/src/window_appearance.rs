#[cfg(target_os = "windows")]
use std::ffi::c_void;

#[cfg(target_os = "windows")]
#[link(name = "dwmapi")]
extern "system" {
  fn DwmSetWindowAttribute(hwnd: *mut c_void, attribute: u32, value: *const c_void, size: u32) -> i32;
}

// Windows COLORREF stores red in the low byte, unlike HTML #RRGGBB.
fn colorref(rgb: [u8; 3]) -> u32 {
  u32::from(rgb[0]) | (u32::from(rgb[1]) << 8) | (u32::from(rgb[2]) << 16)
}

pub fn apply(window: &tauri::WebviewWindow, dark: bool, background: [u8; 3], foreground: [u8; 3], border: [u8; 3]) -> Result<(), String> {
  if window.label() != "main" { return Err("仅主窗口支持标题栏配色".into()); }
  window.set_theme(Some(if dark { tauri::Theme::Dark } else { tauri::Theme::Light })).map_err(|error| error.to_string())?;
  #[cfg(target_os = "windows")]
  {
    let hwnd = window.hwnd().map_err(|error| error.to_string())?;
    let immersive_dark = u32::from(dark);
    let caption = colorref(background);
    let text = colorref(foreground);
    let outline = colorref(border);
    // Windows 11 supports explicit colors. Older Windows versions keep the
    // native light/dark fallback when a color attribute is unsupported.
    for (attribute, value) in [(20, immersive_dark), (34, outline), (35, caption), (36, text)] {
      let result = unsafe { DwmSetWindowAttribute(hwnd.0 as *mut c_void, attribute, &value as *const u32 as *const c_void, std::mem::size_of::<u32>() as u32) };
      if result < 0 { log::debug!("Native titlebar attribute {attribute} unavailable: {result}"); }
    }
  }
  #[cfg(not(target_os = "windows"))]
  let _ = (background, foreground, border);
  Ok(())
}

#[cfg(test)]
mod tests {
  #[test]
  fn rgb_to_windows_colorref() {
    assert_eq!(super::colorref([16, 17, 20]), 0x00141110);
    assert_eq!(super::colorref([255, 255, 255]), 0x00ffffff);
    assert_eq!(super::colorref([255, 0, 0]), 255);
  }
}
