import { useEffect, useRef, useState } from "react";
import MessageContent, { type MessageDisplayMode } from "./MessageContent";

const API = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? "http://localhost:8001/api" : "http://localhost:8000/api");
type Character = { id: number; name: string };
type Conversation = { id: number; character_id: number; title: string };
type Message = { id?: number; role: "user" | "assistant"; content: string; pending?: boolean };
type Plugin = { id: string; name: string; description: string; enabled: boolean; supported: boolean; permissions: string[] };
type Tab = "chat" | "conversations" | "plugins";

function deviceId(): string {
  const saved = localStorage.getItem("yus_mobile_device_id");
  if (saved && /^[A-Za-z0-9_-]{8,64}$/.test(saved)) return saved;
  const id = `android_${crypto.randomUUID().replaceAll("-", "")}`;
  localStorage.setItem("yus_mobile_device_id", id);
  return id;
}

const DEVICE_ID = deviceId();
const PERMISSION_LABELS: Record<string, string> = {
  local_storage: "本地存储", network_download: "网络下载", local_time: "时间信息",
  location_optional: "手动设置的地区", model_api: "模型 API", conversation_write: "写入对话", network_optional: "可选网络访问",
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `请求失败 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

function jsonBody(value: unknown): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) };
}

export default function MobileApp() {
  const [tab, setTab] = useState<Tab>("chat");
  const [characters, setCharacters] = useState<Character[]>([]);
  const [characterId, setCharacterId] = useState<number | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const [displayMode, setDisplayMode] = useState<MessageDisplayMode>(() => {
    const value = localStorage.getItem("yus_mobile_display_mode");
    return value === "plain" || value === "raw" ? value : "markdown";
  });
  const bottomRef = useRef<HTMLDivElement>(null);
  const activeConversation = conversations.find((item) => item.id === conversationId);
  const selectedCharacter = characters.find((item) => item.id === characterId);
  const displayEnabled = plugins.find((item) => item.id === "message_display")?.enabled ?? true;

  useEffect(() => {
    let live = true;
    Promise.all([
      request<Character[]>("/characters"),
      request<Plugin[]>(`/plugins?platform=android&device_id=${DEVICE_ID}`),
    ]).then(([allCharacters, allPlugins]) => {
      if (!live) return;
      setCharacters(allCharacters);
      setPlugins(allPlugins);
      setConnected(true);
      const saved = Number(localStorage.getItem("yus_mobile_character_id"));
      setCharacterId(allCharacters.find((item) => item.id === saved)?.id ?? allCharacters[0]?.id ?? null);
    }).catch((cause: Error) => { if (live) { setConnected(false); setError(cause.message); } });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    if (!characterId) { setConversations([]); setConversationId(null); return; }
    localStorage.setItem("yus_mobile_character_id", String(characterId));
    let live = true;
    request<Conversation[]>(`/conversations?character_id=${characterId}`).then((items) => {
      if (!live) return;
      setConversations(items);
      setConversationId((current) => items.some((item) => item.id === current) ? current : items[0]?.id ?? null);
    }).catch((cause: Error) => { if (live) setError(cause.message); });
    return () => { live = false; };
  }, [characterId]);

  useEffect(() => {
    if (!conversationId) { setMessages([]); return; }
    let live = true;
    request<Message[]>(`/conversations/${conversationId}/messages`).then((items) => {
      if (live) setMessages(items);
    }).catch((cause: Error) => { if (live) setError(cause.message); });
    return () => { live = false; };
  }, [conversationId]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "auto" }); }, [messages]);

  async function createConversation(): Promise<number | null> {
    if (!characterId) return null;
    const item = await request<Conversation>("/conversations", jsonBody({ character_id: characterId }));
    setConversations((current) => [item, ...current]);
    setConversationId(item.id);
    setTab("chat");
    return item.id;
  }

  async function send() {
    const content = draft.trim();
    if (!content || !characterId || busy) return;
    setError("");
    setBusy(true);
    let activeId = conversationId;
    try {
      const id = activeId ?? await createConversation();
      if (!id) return;
      activeId = id;
      setDraft("");
      setMessages((current) => [...current, { role: "user", content, pending: true }, { role: "assistant", content: "", pending: true }]);
      const response = await fetch(`${API}/conversations/${id}/chat`, {
        ...jsonBody({ content, character_id: characterId, client_device_id: DEVICE_ID }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(typeof body.detail === "string" ? body.detail : `发送失败 (${response.status})`);
      }
      if (!response.body) throw new Error("当前浏览器不支持流式回复");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let revisionStarted = false;
      const applyLine = (line: string) => {
        if (!line.trim()) return;
        const event = JSON.parse(line);
        if (event.error) throw new Error(event.error);
        const startsRevision = typeof event.revision_token === "string" && !revisionStarted;
        if (typeof event.revision_token === "string") revisionStarted = true;
        setMessages((current) => current.map((item, index) => {
          if (index !== current.length - 1 || item.role !== "assistant" || !item.pending) return item;
          let next = item.content;
          if (typeof event.revision_token === "string") {
            next = (startsRevision ? "" : next) + event.revision_token;
          }
          if (typeof event.replace === "string") next = event.replace;
          if (typeof event.token === "string") next += event.token;
          return { ...item, content: next };
        }));
      };
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) applyLine(line);
      }
      buffer += decoder.decode();
      if (buffer.trim()) applyLine(buffer);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      if (activeId) {
        const [latest, recent] = await Promise.all([
          request<Message[]>(`/conversations/${activeId}/messages`).catch(() => null),
          request<Conversation[]>(`/conversations?character_id=${characterId}`).catch(() => null),
        ]);
        if (latest) setMessages(latest);
        if (recent) setConversations(recent);
      }
      setBusy(false);
    }
  }

  async function togglePlugin(plugin: Plugin) {
    if (!plugin.supported) return;
    try {
      const updated = await request<{ id: string; enabled: boolean }>(`/plugins/${plugin.id}/state`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !plugin.enabled, platform: "android", device_id: DEVICE_ID }),
      });
      setPlugins((current) => current.map((item) => item.id === plugin.id ? { ...item, enabled: updated.enabled } : item));
    } catch (cause) { setError((cause as Error).message); }
  }

  function changeDisplayMode(mode: MessageDisplayMode) {
    localStorage.setItem("yus_mobile_display_mode", mode);
    setDisplayMode(mode);
  }

  return <div className="mobile-app">
    <header className="mobile-header">
      <div><span className="mobile-brand">Yu's AI</span><small>手机端原型 · 仅限电脑本机预览</small></div>
      <span className={`mobile-status ${connected ? "online" : ""}`}>{connected ? "开发服务已连接" : "等待开发服务"}</span>
    </header>
    <main className="mobile-main">
      {tab === "chat" && <section className="mobile-chat">
        <div className="mobile-toolbar">
          <label>角色 <select aria-label="选择角色" value={characterId ?? ""} onChange={(event) => setCharacterId(Number(event.target.value))}>
            {!characterId && <option value="">未创建角色</option>}
            {characters.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select></label>
          <button className="mobile-new" disabled={!characterId || busy} onClick={() => createConversation().catch((cause: Error) => setError(cause.message))}>＋ 新对话</button>
        </div>
        <div className="mobile-conversation-title">{activeConversation?.title ?? (selectedCharacter ? `和 ${selectedCharacter.name} 聊聊` : "请先在电脑端创建角色")}</div>
        <div className="mobile-messages" aria-live="polite">
          {!messages.length && <div className="mobile-empty"><span>✦</span><h2>从这里继续对话</h2><p>选择角色，输入第一句话。同一后端的聊天记录会在这里显示。</p></div>}
          {messages.map((item, index) => <article key={item.id ?? `pending-${index}`} className={`mobile-message ${item.role}`}>
            <span className="mobile-speaker">{item.role === "user" ? "你" : selectedCharacter?.name ?? "AI"}</span>
            <div className="mobile-bubble">{item.content ? <MessageContent content={item.content} mode={item.role === "assistant" && displayEnabled ? displayMode : "raw"} /> : "正在回复…"}</div>
          </article>)}
          <div ref={bottomRef} />
        </div>
        <div className="mobile-compose"><textarea aria-label="输入消息" placeholder="给角色发消息…" value={draft} onChange={(event) => setDraft(event.target.value)} disabled={!characterId || busy} rows={2} /><button onClick={send} disabled={!draft.trim() || !characterId || busy}>{busy ? "回复中" : "发送"}</button></div>
      </section>}
      {tab === "conversations" && <section className="mobile-list-panel"><h1>最近对话</h1><p>选中一条对话，即可接着聊。</p>{conversations.length ? conversations.map((item) => <button key={item.id} className={`mobile-conversation ${item.id === conversationId ? "selected" : ""}`} onClick={() => { setConversationId(item.id); setTab("chat"); }}>{item.title}<span>›</span></button>) : <div className="mobile-placeholder">这个角色还没有对话。</div>}</section>}
      {tab === "plugins" && <section className="mobile-list-panel"><h1>手机端插件</h1><p>开关仅作用于这台设备，不改变电脑端配置。</p>{plugins.map((plugin) => <div key={plugin.id} className={`mobile-plugin ${plugin.supported ? "" : "unsupported"}`}><div><strong>{plugin.name}</strong><p>{plugin.description}</p><small>{plugin.supported ? plugin.permissions.length ? `权限：${plugin.permissions.map((permission) => PERMISSION_LABELS[permission] ?? permission).join("、")}` : "无需额外权限" : "桌面专用 · 待移动端适配"}</small></div><button className={plugin.enabled && plugin.supported ? "active" : ""} role="switch" aria-checked={plugin.supported && plugin.enabled} aria-label={`${plugin.name}开关`} disabled={!plugin.supported} onClick={() => togglePlugin(plugin)}>{!plugin.supported ? "待适配" : plugin.enabled ? "已开启" : "已关闭"}</button>{plugin.id === "message_display" && plugin.supported && plugin.enabled && <label className="mobile-display-mode">显示方式 <select value={displayMode} onChange={(event) => changeDisplayMode(event.target.value as MessageDisplayMode)}><option value="markdown">渲染 Markdown</option><option value="plain">过滤 Markdown</option><option value="raw">原始文本</option></select></label>}</div>)}</section>}
    </main>
    {error && <div className="mobile-error" role="alert">{error}<button aria-label="关闭错误提示" onClick={() => setError("")}>×</button></div>}
    <nav className="mobile-nav" aria-label="主导航"><button className={tab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>◉<span>对话</span></button><button className={tab === "conversations" ? "active" : ""} onClick={() => setTab("conversations")}>☷<span>最近</span></button><button className={tab === "plugins" ? "active" : ""} onClick={() => setTab("plugins")}>◇<span>插件</span></button></nav>
  </div>;
}
