use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use std::time::Duration;
use std::{collections::HashMap, fs, path::PathBuf};
use tauri::ipc::Channel;
use tauri::{AppHandle, Manager};

#[derive(Deserialize, Serialize)]
struct ModelMessage {
    role: String,
    content: String,
}

#[derive(Clone, Serialize)]
struct ChatEvent {
    token: String,
}

enum SseLine { Ignore, Done, Token(String) }

fn decode_sse_line(line: &[u8]) -> Result<SseLine, String> {
    let line = std::str::from_utf8(line).map_err(|_| "模型返回了无效文本".to_string())?.trim_end_matches('\r');
    let Some(data) = line.strip_prefix("data:") else { return Ok(SseLine::Ignore); };
    let data = data.trim();
    if data == "[DONE]" { return Ok(SseLine::Done); }
    if data.is_empty() { return Ok(SseLine::Ignore); }
    let value: serde_json::Value = serde_json::from_str(data).map_err(|_| "模型返回的数据格式错误".to_string())?;
    if let Some(message) = value.pointer("/error/message").and_then(|item| item.as_str()) {
        return Err(format!("模型返回错误：{}", message.chars().take(180).collect::<String>()));
    }
    if let Some(token) = value.pointer("/choices/0/delta/content").and_then(|item| item.as_str()) {
        if !token.is_empty() { return Ok(SseLine::Token(token.to_string())); }
    }
    Ok(SseLine::Ignore)
}

fn process_sse_line(line: &[u8], reply: &mut String, on_event: &Channel<ChatEvent>) -> Result<bool, String> {
    match decode_sse_line(line)? {
        SseLine::Ignore => Ok(false),
        SseLine::Done => Ok(true),
        SseLine::Token(token) => {
            reply.push_str(&token);
            on_event.send(ChatEvent { token }).map_err(|_| "界面已关闭".to_string())?;
            Ok(false)
        }
    }
}

fn key_file(app: &AppHandle) -> Result<PathBuf, String> {
    let directory = app.path().app_data_dir().map_err(|_| "无法定位应用数据目录".to_string())?;
    fs::create_dir_all(&directory).map_err(|_| "无法创建应用数据目录".to_string())?;
    Ok(directory.join("model_keys.json"))
}

fn validate_profile_id(profile_id: &str) -> Result<(), String> {
    if profile_id.is_empty() || profile_id.len() > 128 || !profile_id.bytes().all(|byte| byte.is_ascii_alphanumeric() || byte == b'-' || byte == b'_') {
        return Err("模型配置标识无效".into());
    }
    Ok(())
}

fn read_model_keys(app: &AppHandle) -> Result<HashMap<String, String>, String> {
    let path = key_file(app)?;
    if !path.exists() { return Ok(HashMap::new()); }
    let content = fs::read(&path).map_err(|_| "无法读取模型密钥".to_string())?;
    serde_json::from_slice(&content).map_err(|_| "模型密钥文件格式错误".to_string())
}

#[tauri::command]
fn load_model_key(app: AppHandle, profile_id: String) -> Result<String, String> {
    validate_profile_id(&profile_id)?;
    Ok(read_model_keys(&app)?.remove(&profile_id).unwrap_or_default())
}

#[tauri::command]
fn save_model_key(app: AppHandle, profile_id: String, api_key: String) -> Result<(), String> {
    validate_profile_id(&profile_id)?;
    if api_key.len() > 8192 { return Err("模型密钥过长".into()); }
    let mut keys = read_model_keys(&app)?;
    if api_key.is_empty() { keys.remove(&profile_id); } else { keys.insert(profile_id, api_key); }
    let content = serde_json::to_vec(&keys).map_err(|_| "无法保存模型密钥".to_string())?;
    fs::write(key_file(&app)?, content).map_err(|_| "无法保存模型密钥".to_string())
}

#[tauri::command]
async fn model_chat(
    base_url: String,
    api_key: String,
    model: String,
    messages: Vec<ModelMessage>,
    temperature: f64,
    max_tokens: u32,
    on_event: Channel<ChatEvent>,
) -> Result<String, String> {
    let mut url = reqwest::Url::parse(base_url.trim()).map_err(|_| "模型地址无效".to_string())?;
    if url.scheme() != "https" || !url.username().is_empty() || url.password().is_some() || url.query().is_some() || url.fragment().is_some() {
        return Err("模型地址必须是没有账号、查询参数的 HTTPS 地址".into());
    }
    if api_key.trim().is_empty() || model.trim().is_empty() || messages.is_empty() {
        return Err("请先填写模型设置，并输入消息".into());
    }
    if messages.len() > 101 || messages.iter().any(|item| !matches!(item.role.as_str(), "system" | "user" | "assistant") || item.content.len() > 100_000) {
        return Err("对话内容超出本版本支持的范围".into());
    }
    let endpoint_path = format!("{}/chat/completions", url.path().trim_end_matches('/'));
    url.set_path(&endpoint_path);
    let client = reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(15))
        .timeout(Duration::from_secs(300))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|_| "无法初始化网络请求".to_string())?;
    let response = client.post(url)
        .bearer_auth(api_key.trim())
        .json(&serde_json::json!({
            "model": model.trim(), "messages": messages, "stream": true,
            "temperature": temperature.clamp(0.0, 2.0), "max_tokens": max_tokens.clamp(128, 16_384)
        }))
        .send().await.map_err(|error| format!("连接模型失败：{}", error.without_url()))?;
    if !response.status().is_success() {
        return Err(format!("模型请求失败：HTTP {}", response.status()));
    }
    let mut chunks = response.bytes_stream();
    let mut pending = Vec::<u8>::new();
    let mut reply = String::new();
    while let Some(chunk) = chunks.next().await {
        pending.extend_from_slice(&chunk.map_err(|_| "模型响应中断".to_string())?);
        if pending.len() > 1_000_000 { return Err("单条模型事件过大".into()); }
        while let Some(end) = pending.iter().position(|byte| *byte == b'\n') {
            let line: Vec<u8> = pending.drain(..=end).collect();
            if process_sse_line(&line[..line.len() - 1], &mut reply, &on_event)? { return Ok(reply); }
        }
    }
    if !pending.is_empty() { process_sse_line(&pending, &mut reply, &on_event)?; }
    if reply.is_empty() { return Err("模型没有返回文字内容".into()); }
    Ok(reply)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![model_chat, load_model_key, save_model_key])
        .run(tauri::generate_context!())
        .expect("Yu's AI mobile failed to start");
}

#[cfg(test)]
mod tests {
    use super::{decode_sse_line, SseLine};

    #[test]
    fn decodes_unicode_token_and_done_marker() {
        let line = br#"data: {"choices":[{"delta":{"content":"\u4f60\u597d"}}]}"#;
        assert!(matches!(decode_sse_line(line).unwrap(), SseLine::Token(token) if token == "你好"));
        assert!(matches!(decode_sse_line(b"data: [DONE]").unwrap(), SseLine::Done));
        assert!(matches!(decode_sse_line(b": keep-alive").unwrap(), SseLine::Ignore));
    }
}
