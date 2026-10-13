//! Signed, frontend-only updates. Bundled assets remain the recovery baseline.
use std::{collections::BTreeMap, fs, io::{Read, Write}, path::{Path, PathBuf}, sync::{Mutex, OnceLock}};
use base64::{engine::general_purpose::STANDARD, Engine};
use ed25519_dalek::{Signature, VerifyingKey};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

const MANIFEST_URL: &str = "https://github.com/qtys/Yu-s-Ai-plugin-platform/releases/latest/download/ui-update.json";
const MAX_BYTES: u64 = 40_000_000;
static OPERATION: Mutex<()> = Mutex::new(());
static BOOT: OnceLock<Option<Installed>> = OnceLock::new();
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope { payload: String, signature: String }
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Manifest { schema: u32, host_version: String, revision: u64, files: BTreeMap<String,String>, archive_size: u64, archive_sha256: String, url: String }
#[derive(Default, Deserialize, Serialize)]
struct Selection { current: Option<u64>, previous: Option<u64>, pending: bool, attempts: u32, highest: u64 }
struct Installed { directory: PathBuf, manifest: Manifest }
type Result<T> = std::result::Result<T,String>;
fn root() -> Result<PathBuf> {
  let exe = std::env::current_exe().map_err(|e| e.to_string())?;
  Ok(exe.parent().ok_or("Missing install directory")?.join("data").join("ui-updates"))
}
fn hash(bytes: &[u8]) -> String { format!("{:x}", Sha256::digest(bytes)) }
fn safe_name(name: &str) -> bool {
  !name.is_empty() && name != "ui-update.json" && name.len() < 240 && !name.starts_with('/') && !name.contains(['\\', ':', '\0']) &&
    name.split('/').all(|part| !part.is_empty() && part != "." && part != "..") &&
    matches!(Path::new(name).extension().and_then(|v| v.to_str()), Some("html"|"js"|"css"|"json"|"md"|"png"|"webp"|"jpg"|"jpeg"|"gif"|"svg"|"ico"|"woff"|"woff2"|"ttf"))
}
fn verify(envelope: &[u8]) -> Result<Manifest> {
  if envelope.len() > 1_000_000 { return Err("更新清单过大".into()); }
  let env: Envelope = serde_json::from_slice(envelope).map_err(|_| "更新清单格式无效")?;
  let payload = STANDARD.decode(env.payload).map_err(|_| "清单编码无效")?;
  let signature = Signature::from_slice(&STANDARD.decode(env.signature).map_err(|_| "签名编码无效")?).map_err(|_| "签名无效")?;
  let public: [u8;32] = STANDARD.decode(include_str!("../ui-update-public-key.txt").trim()).map_err(|_| "发布公钥无效")?.try_into().map_err(|_| "发布公钥长度无效")?;
  VerifyingKey::from_bytes(&public).map_err(|_| "发布公钥无效")?.verify_strict(&payload, &signature).map_err(|_| "更新签名校验失败")?;
  let manifest: Manifest = serde_json::from_slice(&payload).map_err(|_| "签名清单格式无效")?;
  if manifest.schema != 1 || manifest.host_version != env!("CARGO_PKG_VERSION") { return Err("此界面更新与当前程序不兼容，请使用完整安装包".into()); }
  if manifest.revision == 0 || manifest.files.is_empty() || manifest.files.len() > 1000 || manifest.archive_size > MAX_BYTES || manifest.archive_sha256.len() != 64 ||
    !manifest.files.iter().all(|(name, digest)| safe_name(name) && digest.len() == 64 && digest.bytes().all(|c| c.is_ascii_hexdigit())) ||
    !["index.html","pet.html","pet-chat.html"].iter().all(|name| manifest.files.contains_key(*name)) { return Err("界面更新内容无效".into()); }
  let url = reqwest::Url::parse(&manifest.url).map_err(|_| "下载地址无效")?;
  if url.scheme() != "https" || url.host_str() != Some("github.com") || !url.path().starts_with("/qtys/Yu-s-Ai-plugin-platform/releases/download/") || !url.path().ends_with("/ui-update.zip") || !url.username().is_empty() || url.password().is_some() || url.query().is_some() || url.fragment().is_some() { return Err("下载地址不属于本项目".into()); }
  Ok(manifest)
}
fn selection(root: &Path) -> Selection { fs::read(root.join("active.json")).ok().and_then(|data| serde_json::from_slice(&data).ok()).unwrap_or_default() }
fn save(root: &Path, value: &Selection) -> Result<()> {
  fs::create_dir_all(root).map_err(|e| e.to_string())?;
  let staging = root.join("active.tmp");
  let bytes = serde_json::to_vec(value).map_err(|e| e.to_string())?;
  let mut file = fs::File::create(&staging).map_err(|e| e.to_string())?;
  file.write_all(&bytes).and_then(|_| file.sync_all()).map_err(|e| e.to_string())?;
  drop(file);
  // Windows rename cannot replace an existing target. Atomic replacement uses MoveFileExW.
  #[cfg(windows)] {
    use std::os::windows::ffi::OsStrExt;
    #[link(name="kernel32")] extern "system" { fn MoveFileExW(a: *const u16,b: *const u16,flags: u32) -> i32; }
    let from: Vec<u16> = staging.as_os_str().encode_wide().chain(Some(0)).collect();
    let to: Vec<u16> = root.join("active.json").as_os_str().encode_wide().chain(Some(0)).collect();
    if unsafe { MoveFileExW(from.as_ptr(),to.as_ptr(),9) } == 0 { return Err(std::io::Error::last_os_error().to_string()); }
  }
  #[cfg(not(windows))] fs::rename(staging, root.join("active.json")).map_err(|e| e.to_string())?;
  Ok(())
}
fn load(root: &Path, revision: u64) -> Result<Installed> {
  let directory = root.join("versions").join(revision.to_string());
  let manifest = verify(&fs::read(directory.join("ui-update.json")).map_err(|e| e.to_string())?)?;
  if manifest.revision != revision { return Err("更新目录与版本不一致".into()); }
  let canonical = directory.canonicalize().map_err(|e| e.to_string())?;
  let mut total = 0usize;
  for (name, expected) in &manifest.files {
    let path = directory.join(name).canonicalize().map_err(|e| e.to_string())?;
    if !path.starts_with(&canonical) { return Err("更新路径越界".into()); }
    let data = fs::read(path).map_err(|e| e.to_string())?;
    total += data.len();
    if total as u64 > MAX_BYTES || hash(&data) != *expected { return Err("界面文件校验失败".into()); }
  }
  Ok(Installed { directory, manifest })
}
fn boot() -> Option<Installed> {
  let root = root().ok()?;
  boot_at(&root)
}
fn boot_at(root: &Path) -> Option<Installed> {
  let mut state = selection(&root);
  if state.pending && state.attempts > 0 {
    state.current = state.previous.take(); state.pending = false; state.attempts = 0;
    let _ = save(&root, &state);
    log::warn!("ui_update_startup_rollback");
  } else if state.pending { state.attempts = 1; if save(&root, &state).is_err() { return None; } }
  let revision = state.current?;
  match load(&root, revision) {
    Ok(assets) => Some(assets),
    Err(error) => {
      log::warn!("ui_update_rejected: {error}");
      state.current = state.previous.take(); state.pending = false; state.attempts = 0;
      let fallback = state.current.and_then(|previous| load(&root, previous).ok());
      let _ = save(&root, &state); fallback
    }
  }
}
pub fn response(app: &tauri::AppHandle, path: &str) -> tauri::http::Response<Vec<u8>> {
  let name = if path == "/" { "index.html" } else { path.trim_start_matches('/') };
  let current = BOOT.get_or_init(boot);
  if let Some(current) = current {
    if safe_name(name) {
      if let Some(expected) = current.manifest.files.get(name) {
        if let Ok(data) = fs::read(current.directory.join(name)) {
          if hash(&data) == *expected { return tauri::http::Response::builder().header("Content-Type",mime_guess::from_path(name).first_or_octet_stream().as_ref()).header("Cache-Control","no-store").body(data).unwrap(); }
        }
      }
    }
    return tauri::http::Response::builder().status(404).body(Vec::new()).unwrap();
  }
  if let Some(asset) = app.asset_resolver().get(name.to_string()) {
    let mut response = tauri::http::Response::builder().header("Content-Type",asset.mime_type);
    if let Some(csp) = asset.csp_header { response = response.header("Content-Security-Policy",csp); }
    return response.body(asset.bytes).unwrap();
  }
  tauri::http::Response::builder().status(404).body(Vec::new()).unwrap()
}
fn client() -> Result<reqwest::blocking::Client> {
  reqwest::blocking::Client::builder().timeout(std::time::Duration::from_secs(120)).user_agent("Yus-AI-UI-Updater")
    .redirect(reqwest::redirect::Policy::custom(|attempt| {
      if attempt.previous().len() < 5 && attempt.url().scheme() == "https" && matches!(attempt.url().host_str(),Some("github.com"|"release-assets.githubusercontent.com"|"objects.githubusercontent.com")) { attempt.follow() } else { attempt.stop() }
    })).build().map_err(|e| e.to_string())
}
fn fetch(url: &str, limit: u64) -> Result<Vec<u8>> {
  let response = client()?.get(url).send().map_err(|_| "连接 GitHub 失败，请稍后重试")?;
  if response.status().as_u16() == 404 { return Err("当前发布暂无界面更新包".into()); }
  let response = response.error_for_status().map_err(|_| "GitHub 下载失败")?;
  let mut bytes = Vec::new(); response.take(limit + 1).read_to_end(&mut bytes).map_err(|_| "下载中断，请重试")?;
  if bytes.len() as u64 > limit { return Err("下载内容超过限制".into()); }
  Ok(bytes)
}
#[tauri::command]
pub async fn check_ui_update() -> Result<serde_json::Value> {
  if cfg!(debug_assertions) { return Err("开发测试版使用自动刷新，不执行 GitHub 热更新".into()); }
  tauri::async_runtime::spawn_blocking(|| {
    let _guard = OPERATION.try_lock().map_err(|_| "更新操作正在进行，请稍候")?;
    let manifest = verify(&fetch(MANIFEST_URL,1_000_000)?)?;
    let root = root()?; let state = selection(&root);
    Ok(serde_json::json!({"revision":manifest.revision,"available":manifest.revision > state.highest,"bytes":manifest.archive_size,"host_version":manifest.host_version}))
  }).await.map_err(|e| e.to_string())?
}
#[tauri::command]
pub async fn stage_ui_update() -> Result<u64> {
  if cfg!(debug_assertions) { return Err("开发测试版使用自动刷新，不执行 GitHub 热更新".into()); }
  tauri::async_runtime::spawn_blocking(|| {
    let _guard = OPERATION.try_lock().map_err(|_| "更新操作正在进行，请稍候")?;
    let envelope = fetch(MANIFEST_URL,1_000_000)?; let manifest = verify(&envelope)?;
    let current = selection(&root()?);
    if current.pending || manifest.revision <= current.highest { return Err("没有可应用的新版界面，请先重启确认现有更新".into()); }
    let archive = fetch(&manifest.url,MAX_BYTES)?;
    stage_bytes(&root()?, &envelope, &archive)
  }).await.map_err(|e| e.to_string())?
}
fn stage_bytes(root: &Path, envelope: &[u8], archive: &[u8]) -> Result<u64> {
    let manifest = verify(envelope)?;
    let mut state = selection(root);
    if state.pending || manifest.revision <= state.highest { return Err("没有可应用的新版界面，请先重启确认现有更新".into()); }
    if archive.len() as u64 != manifest.archive_size || hash(&archive) != manifest.archive_sha256 { return Err("下载包校验失败".into()); }
    let mut zip = zip::ZipArchive::new(std::io::Cursor::new(archive)).map_err(|_| "更新包损坏")?;
    if zip.len() != manifest.files.len() { return Err("更新包文件列表不一致".into()); }
    fs::create_dir_all(root.join("versions")).map_err(|e| e.to_string())?;
    let staging = root.join(format!("stage-{}",std::process::id()));
    if staging.exists() { return Err("存在未完成的更新目录，请检查安装目录 data/ui-updates".into()); }
    fs::create_dir(&staging).map_err(|e| e.to_string())?;
    let result = (|| {
      let mut seen = std::collections::BTreeSet::new(); let mut total = 0u64;
      for index in 0..zip.len() {
        let mut entry = zip.by_index(index).map_err(|_| "更新条目损坏")?;
        let name = entry.name().to_string();
        let expected = manifest.files.get(&name).ok_or("更新包含未签名的文件")?;
        if !safe_name(&name) || !seen.insert(name.clone()) || entry.size() > MAX_BYTES || entry.unix_mode().map(|mode| mode & 0o170000 == 0o120000).unwrap_or(false) { return Err("更新条目不安全".into()); }
        let mut data = Vec::new(); entry.by_ref().take(MAX_BYTES+1).read_to_end(&mut data).map_err(|_| "更新解压失败")?;
        total += data.len() as u64;
        if total > MAX_BYTES || hash(&data) != *expected { return Err("更新条目校验失败".into()); }
        let path = staging.join(name); fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?; fs::write(path,data).map_err(|e| e.to_string())?;
      }
      fs::write(staging.join("ui-update.json"),envelope).map_err(|e| e.to_string())?;
      let destination = root.join("versions").join(manifest.revision.to_string());
      if destination.exists() {
        let installed = load(root, manifest.revision)?;
        if installed.manifest.archive_sha256 != manifest.archive_sha256 { return Err("更新版本目录与签名包不一致".into()); }
      } else { fs::rename(&staging,destination).map_err(|e| e.to_string())?; }
      load(root, manifest.revision)?; // Validate actual extracted files before activation.
      state.previous = state.current; state.current = Some(manifest.revision); state.pending = true; state.attempts = 0; state.highest = manifest.revision;
      save(&root,&state)?; Ok(manifest.revision)
    })();
    // Staging is a fixed direct child created by this operation, never a user path.
    if staging.is_dir() {
      if let (Ok(resolved), Ok(parent)) = (staging.canonicalize(), root.canonicalize()) {
        if resolved.starts_with(&parent) && resolved != parent { let _ = fs::remove_dir_all(&staging); }
      }
    }
    result
}
#[tauri::command]
pub fn confirm_ui_update(window: tauri::WebviewWindow) -> Result<()> {
  if window.label() != "main" { return Err("Only main window may confirm startup".into()); }
  let _guard = OPERATION.try_lock().map_err(|_| "更新操作正在进行，请稍候")?;
  let Some(Some(loaded)) = BOOT.get() else { return Ok(()); };
  let root = root()?; let mut state = selection(&root);
  if state.current == Some(loaded.manifest.revision) { state.pending = false; state.attempts = 0; save(&root,&state)?; }
  Ok(())
}
#[tauri::command]
pub fn rollback_ui_update() -> Result<()> {
  if cfg!(debug_assertions) { return Err("开发测试版不切换签名界面包".into()); }
  let _guard = OPERATION.try_lock().map_err(|_| "更新操作正在进行，请稍候")?;
  let root = root()?; let mut state = selection(&root);
  state.current = state.previous.take(); state.pending = false; state.attempts = 0; save(&root,&state)
}

#[tauri::command]
pub fn ui_update_status() -> Result<serde_json::Value> {
  let _guard = OPERATION.try_lock().map_err(|_| "更新操作正在进行，请稍候")?;
  let state = selection(&root()?);
  let loaded = BOOT.get().and_then(|value| value.as_ref()).map(|value| value.manifest.revision);
  Ok(serde_json::json!({"pending":state.pending,"staged_revision":state.current,"loaded_revision":loaded,"host_version":env!("CARGO_PKG_VERSION"),"development":cfg!(debug_assertions)}))
}

#[cfg(test)] mod tests {
  use super::*;
  #[test] fn reject_traversal_and_executables() {
    for name in ["../x.js","/x.html","C:/x.js","a\\b.js","a/../b.js","run.exe","app.py","a//b.js"] { assert!(!safe_name(name),"{name}"); }
    assert!(safe_name("assets/main-123.js"));
  }
  #[test] fn reject_unsigned_manifest() { assert!(verify(br#"{"payload":"e30=","signature":"AA=="}"#).is_err()); }
  #[test]
  #[ignore = "Requires a signed fixture generated by scripts/build-ui-update.py"]
  fn signed_install_tamper_and_startup_rollback() {
    let fixture = PathBuf::from(std::env::var("YUS_AI_SIGNED_UI_FIXTURE").expect("Set fixture directory"));
    let envelope = fs::read(fixture.join("ui-update.json")).unwrap();
    let archive = fs::read(fixture.join("ui-update.zip")).unwrap();
    let manifest = verify(&envelope).unwrap();
    let directory = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../build").join(format!("ui-update-test-{}-{}",std::process::id(),std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
    fs::create_dir_all(&directory).unwrap();
    let mut corrupt = archive.clone(); corrupt[0] ^= 1;
    assert!(stage_bytes(&directory,&envelope,&corrupt).is_err());
    assert_eq!(stage_bytes(&directory,&envelope,&archive).unwrap(),manifest.revision);
    assert!(selection(&directory).pending);
    assert!(stage_bytes(&directory,&envelope,&archive).is_err());
    assert!(boot_at(&directory).is_some()); // First attempt can load signed assets.
    assert_eq!(selection(&directory).attempts,1);
    assert!(boot_at(&directory).is_none()); // Unconfirmed restart falls back to bundled assets.
    assert!(selection(&directory).current.is_none());
    let mut healthy = selection(&directory);
    healthy.current = Some(manifest.revision); healthy.pending = false; healthy.attempts = 0;
    save(&directory,&healthy).unwrap();
    assert!(boot_at(&directory).is_some()); // Confirmed updates do not roll back.
    assert_eq!(selection(&directory).current,Some(manifest.revision));
    let loaded = load(&directory,manifest.revision).unwrap();
    fs::write(loaded.directory.join("index.html"),b"tampered").unwrap();
    assert!(load(&directory,manifest.revision).is_err());
    fs::remove_dir_all(directory).unwrap();
  }
}
