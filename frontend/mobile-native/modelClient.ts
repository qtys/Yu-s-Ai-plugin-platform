import { Channel, invoke, isTauri } from "@tauri-apps/api/core";

export type ModelMessage = { role: "system" | "user" | "assistant"; content: string };
export type ModelConfig = { baseUrl: string; model: string; apiKey: string; temperature: number; maxTokens: number };
type ChatEvent = { token: string };

export async function streamModelReply(config: ModelConfig, messages: ModelMessage[], onToken: (token: string) => void): Promise<string> {
  if (!config.baseUrl.startsWith("https://")) throw new Error("模型地址必须以 https:// 开头，避免泄露 API Key");
  if (!config.model.trim()) throw new Error("请填写模型名称");
  if (!config.apiKey.trim()) throw new Error("请在模型设置中填写或选择有密钥的模型配置。");
  if (isTauri()) {
    const channel = new Channel<ChatEvent>();
    channel.onmessage = (event) => { if (event.token) onToken(event.token); };
    return invoke<string>("model_chat", {
      baseUrl: config.baseUrl.trim(), apiKey: config.apiKey.trim(), model: config.model.trim(),
      messages, temperature: config.temperature, maxTokens: config.maxTokens, onEvent: channel,
    });
  }
  const endpoint = `${config.baseUrl.replace(/\/+$/, "")}/chat/completions`;
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${config.apiKey.trim()}` },
    body: JSON.stringify({ model: config.model.trim(), messages, stream: true, temperature: config.temperature, max_tokens: config.maxTokens }),
  });
  if (!response.ok) throw new Error(`模型请求失败：HTTP ${response.status}`);
  if (!response.body) throw new Error("当前浏览器不支持流式回复");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let reply = "";
  const parse = (line: string) => {
    if (!line.startsWith("data:")) return;
    const payload = line.slice(5).trim();
    if (!payload || payload === "[DONE]") return;
    const event = JSON.parse(payload);
    const token = event.choices?.[0]?.delta?.content;
    if (typeof token === "string") { reply += token; onToken(token); }
  };
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    lines.forEach(parse);
  }
  buffer += decoder.decode();
  if (buffer.trim()) parse(buffer.trim());
  return reply;
}
