import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import MessageContent from "./MessageContent";
import type { MessageDisplayMode } from "./MessageContent";
import "./App.css";
import "./Desktop.css";
import "./Themes.css";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api";
type Character = {
  id: number;
  name: string;
  description: string;
  system_prompt: string;
  avatar_data: string; greeting: string; background: string; personality: string;
  speaking_style: string; relationship: string; boundaries: string; example_dialogue: string;
};
const emptyCharacter = { name: "", description: "", system_prompt: "", avatar_data: "", greeting: "", background: "", personality: "", speaking_style: "", relationship: "", boundaries: "", example_dialogue: "" };
type Conversation = { id: number; character_id: number; title: string };
type Message = { id?: number; conversation_id?: number; clientKey?: string; role: "user" | "assistant"; content: string };
type DocumentItem = { id: number; conversation_id: number; filename: string; char_count: number; image_count: number; summary: string; status: "ready" | "analyzing" | "analyzed" | "error"; analysis_mode: "fast" | "deep"; analysis_stage: string; progress_current: number; progress_total: number };
type Settings = {
  base_url: string;
  api_key: string;
  model: string;
  temperature: number;
  max_tokens: number;
  context_message_limit: number;
  memory_limit: number;
  message_display_mode: MessageDisplayMode;
  translation_mirror_url: string;
  vision_model: string;
  document_analysis_mode: "fast" | "deep";
  include_local_time: boolean;
  include_location_context: boolean;
  location_context: string;
};
type Theme = "violet" | "midnight" | "sand" | "paper";
const themes: { id: Theme; name: string; description: string }[] = [
  { id: "violet", name: "暮紫", description: "柔和紫色与深色背景" },
  { id: "midnight", name: "午夜蓝", description: "冷静蓝色与深海层次" },
  { id: "sand", name: "暖砂", description: "温暖琥珀与棕灰色调" },
  { id: "paper", name: "纸墨", description: "明亮纸张与清晰墨色" },
];

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail ?? `请求失败 (${response.status})`);
  }
  return response.json();
}

function documentStatusText(item: DocumentItem) {
  if (item.status === "analyzed") return `可用于对话 · ${item.analysis_mode === "deep" ? "深度" : "快速"}分析${item.image_count ? ` · ${item.image_count} 个视觉切片` : ""}`;
  if (item.status === "error") return "处理失败，点击重试";
  if (item.analysis_stage === "visual" && item.progress_total) return `正在分析第 ${item.progress_current}/${item.progress_total} 页 · ${item.analysis_mode === "deep" ? "深度" : "快速"}模式`;
  if (item.analysis_stage === "text") return "正在整理文档文字…";
  if (item.analysis_stage === "summary") return "正在生成全文总结…";
  return "正在读取文字、页面和图片…";
}

export default function App() {
  const [characters, setCharacters] = useState<Character[]>([]),
    [conversations, setConversations] = useState<Conversation[]>([]),
    [messages, setMessages] = useState<Message[]>([]),
    [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [activeCharacter, setActiveCharacter] = useState<number | null>(null),
    [activeConversation, setActiveConversation] = useState<number | null>(null);
  const [input, setInput] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [pinned, setPinned] = useState(false),
    [mini, setMini] = useState(false);
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem("yus-ai-theme") as Theme) || "violet",
  );
  const [backendReady, setBackendReady] = useState(false);
  const [autostartEnabled, setAutostartEnabled] = useState(false);
  const [autostartBusy, setAutostartBusy] = useState(false);
  const [panel, setPanel] = useState<"chat" | "characters" | "settings">(
    "chat",
  );
  const [settings, setSettings] = useState<Settings>({
    base_url: "https://api.openai.com/v1",
    api_key: "",
    model: "gpt-4o-mini",
    temperature: 0.8,
    max_tokens: 2048,
    context_message_limit: 20,
    memory_limit: 5,
    message_display_mode: "markdown",
    translation_mirror_url: "",
    vision_model: "",
    document_analysis_mode: "fast",
    include_local_time: true,
    include_location_context: false,
    location_context: "",
  });
  const [draft, setDraft] = useState(emptyCharacter);
  const [proactivePlugin, setProactivePlugin] = useState({ enabled: false, interval_minutes: 30, randomize_interval: true, random_min_minutes: 15, random_max_minutes: 60, history_weight: 15, max_tokens: 1024, news_enabled: false, rss_url: "https://www.chinanews.com.cn/rss/scroll-news.xml", total_tokens: 0, last_error: "" });
  const [editingCharacter, setEditingCharacter] = useState<number | null>(null);
  const [editingMessage, setEditingMessage] = useState<number | null>(null);
  const [messageDraft, setMessageDraft] = useState("");
  const [documentBusy, setDocumentBusy] = useState(false);
  const documentInputRef = useRef<HTMLInputElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const skipMessageLoadRef = useRef<number | null>(null);
  const desktop = "__TAURI_INTERNALS__" in window;

  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);
  useEffect(() => {
    if (!desktop) return;
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void invoke<boolean>("get_autostart_status")
      .then((enabled) => { if (!disposed) setAutostartEnabled(enabled); })
      .catch((cause) => { if (!disposed) setError(`读取开机自启状态失败：${String(cause)}`); });
    void listen<boolean>("autostart-changed", (event) => {
      if (!disposed) setAutostartEnabled(event.payload);
    }).then((stop) => { if (disposed) stop(); else unlisten = stop; });
    return () => { disposed = true; unlisten?.(); };
  }, [desktop]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      for (let attempt = 0; attempt < 30; attempt++) {
        try {
          await request("/health");
          if (cancelled) return;
          setBackendReady(true);
          const [characterData, settingsData] = await Promise.all([
            request<Character[]>("/characters"),
            request<Settings>("/settings"),
          ]);
          setCharacters(characterData);
          setSettings(settingsData);
          setProactivePlugin(await request<typeof proactivePlugin>("/plugins/proactive"));
          if (characterData[0]) {
            setActiveCharacter(characterData[0].id);
            const conversationData = await request<Conversation[]>(
              `/conversations?character_id=${characterData[0].id}`,
            );
            if (!cancelled) setConversations(conversationData);
          }
          return;
        } catch {
          await new Promise((resolve) => setTimeout(resolve, 500));
        }
      }
      if (!cancelled)
        setError("本地后端启动失败，请查看安装目录 logs/sidecar.log");
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  useEffect(() => {
    if (activeCharacter)
      request<Conversation[]>(`/conversations?character_id=${activeCharacter}`)
        .then(setConversations)
        .catch((e) => setError(e.message));
  }, [activeCharacter]);
  useEffect(() => {
    if (activeConversation) {
      if (skipMessageLoadRef.current === activeConversation) {
        skipMessageLoadRef.current = null;
        return;
      }
      let cancelled = false;
      request<Message[]>(`/conversations/${activeConversation}/messages`)
        .then((data) => {
          if (!cancelled) setMessages(data);
        })
        .catch((e) => setError(e.message));
      request<DocumentItem[]>(`/conversations/${activeConversation}/documents`)
        .then((data) => { if (!cancelled) setDocuments(data); })
        .catch((e) => setError(e.message));
      return () => {
        cancelled = true;
      };
    }
    setDocuments([]);
  }, [activeConversation]);
  useEffect(() => {
    localStorage.setItem("yus-ai-theme", theme);
    document.documentElement.dataset.theme = theme;
    return () => {
      delete document.documentElement.dataset.theme;
    };
  }, [theme]);
  useEffect(() => {
    if (activeCharacter)
      localStorage.setItem("yus-ai-character", String(activeCharacter));
  }, [activeCharacter]);
  useEffect(() => {
    if (activeConversation)
      localStorage.setItem("yus-ai-conversation", String(activeConversation));
    else localStorage.removeItem("yus-ai-conversation");
  }, [activeConversation]);
  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    async function syncPetConversation() {
      if (busyRef.current) return;
      const characterId = Number(localStorage.getItem("yus-ai-character"));
      const conversationId = Number(localStorage.getItem("yus-ai-conversation"));
      if (!characterId) return;
      try {
        const conversationData = await request<Conversation[]>(
          `/conversations?character_id=${characterId}`,
        );
        if (cancelled) return;
        setActiveCharacter(characterId);
        setConversations(conversationData);
        if (conversationId) {
          const messageData = await request<Message[]>(
            `/conversations/${conversationId}/messages`,
          );
          if (!cancelled) {
            skipMessageLoadRef.current = conversationId;
            setActiveConversation(conversationId);
            setMessages(messageData);
          }
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    }
    if (desktop) {
      void listen("main-sync", () => { void syncPetConversation(); })
        .then((stop) => { if (cancelled) stop(); else unlisten = stop; });
    } else {
      window.addEventListener("focus", syncPetConversation);
    }
    return () => {
      cancelled = true;
      unlisten?.();
      if (!desktop) window.removeEventListener("focus", syncPetConversation);
    };
  }, [desktop]);

  useEffect(() => {
    if (!desktop) return;
    let lastFocusAt = performance.now();
    let expectedTick = performance.now() + 100;
    const report = (event: string, durationMs?: number, details?: string) => {
      void invoke("record_window_diagnostic", {
        event,
        durationMs: durationMs ?? null,
        details: details ?? null,
      }).catch(() => undefined);
    };
    const handleFocus = () => {
      lastFocusAt = performance.now();
      expectedTick = lastFocusAt + 100;
      report("frontend_focus");
    };
    const handleBlur = () => report("frontend_blur");
    window.addEventListener("focus", handleFocus);
    window.addEventListener("blur", handleBlur);
    const timer = window.setInterval(() => {
      const now = performance.now();
      const drift = now - expectedTick;
      expectedTick = now + 100;
      if (drift >= 150 && now - lastFocusAt >= 1000 && document.hasFocus()) {
        report("frontend_event_loop_gap", drift, `visibility=${document.visibilityState}`);
      }
    }, 100);
    let observer: PerformanceObserver | undefined;
    if ("PerformanceObserver" in window) {
      try {
        observer = new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) {
            if (entry.duration >= 50) report("frontend_long_task", entry.duration, `start_ms=${entry.startTime.toFixed(1)}`);
          }
        });
        observer.observe({ entryTypes: ["longtask"] });
      } catch { /* WebView2 may not expose the long-task entry type. */ }
    }
    report("diagnostics_ready");
    return () => {
      window.clearInterval(timer);
      observer?.disconnect();
      window.removeEventListener("focus", handleFocus);
      window.removeEventListener("blur", handleBlur);
    };
  }, [desktop]);

  async function createCharacter(event: FormEvent) {
    event.preventDefault();
    if (!backendReady) {
      setError("本地后端仍在启动，请稍候");
      return;
    }
    try {
      const value = await request<Character>(editingCharacter ? `/characters/${editingCharacter}` : "/characters", {
        method: editingCharacter ? "PUT" : "POST",
        body: JSON.stringify(draft),
      });
      setCharacters((c) => editingCharacter ? c.map((item) => item.id === value.id ? value : item) : [value, ...c]);
      setActiveCharacter(value.id);
      setDraft(emptyCharacter);
      setEditingCharacter(null);
      setPanel("chat");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function editCharacter(item: Character) {
    setEditingCharacter(item.id);
    setDraft({ ...emptyCharacter, ...item });
    setPanel("characters");
  }
  async function deleteCharacter(item: Character) {
    if (!window.confirm(`确定删除角色“${item.name}”吗？该角色的全部对话也会删除。`)) return;
    await request(`/characters/${item.id}`, { method: "DELETE" });
    const remaining = characters.filter((x) => x.id !== item.id);
    setCharacters(remaining); setActiveCharacter(remaining[0]?.id ?? null); setActiveConversation(null); setMessages([]); setConversations([]);
  }
  async function exportCharacter(item: Character) {
    try {
      const { id: _id, ...character } = item;
      const content = JSON.stringify({ format: "yus-ai-character", version: 1, character }, null, 2);
      if (desktop) {
        const path = await invoke<string>("export_character_card", { characterName: item.name, content });
        window.alert(`角色卡已导出到：\n${path}`);
      } else {
        const blob = new Blob([content], { type: "application/json" });
        const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = `${item.name}.yus-character.json`; link.click(); URL.revokeObjectURL(url);
      }
    } catch (e) { setError(`角色导出失败：${String(e)}`); }
  }
  async function importCharacter(file?: File) {
    if (!file) return;
    try { const data = JSON.parse(await file.text()); const value = await request<Character>("/characters", { method: "POST", body: JSON.stringify({ ...emptyCharacter, ...(data.character ?? data) }) }); setCharacters((items) => [value, ...items]); setActiveCharacter(value.id); }
    catch (e) { setError(`角色导入失败：${(e as Error).message}`); }
  }
  async function createConversation() {
    if (!activeCharacter) {
      setPanel("characters");
      return;
    }
    try {
      const value = await request<Conversation>("/conversations", {
        method: "POST",
        body: JSON.stringify({ character_id: activeCharacter }),
      });
      setConversations((c) => [value, ...c]);
      setActiveConversation(value.id);
      setPanel("chat");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function deleteConversation(item: Conversation) {
    if (!window.confirm(`确定删除对话“${item.title}”吗？\n该对话中的消息也会一并删除。`)) return;
    try {
      await request(`/conversations/${item.id}`, { method: "DELETE" });
      const remaining = conversations.filter((x) => x.id !== item.id);
      setConversations(remaining);
      if (activeConversation === item.id) {
        const next = remaining[0] ?? null;
        setActiveConversation(next?.id ?? null);
        if (!next) setMessages([]);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function selectCharacter(characterId: number) {
    setPanel("chat");
    if (activeCharacter === characterId) {
      try {
        setConversations(await request<Conversation[]>(`/conversations?character_id=${characterId}`));
      } catch (e) { setError((e as Error).message); }
      return;
    }
    setActiveCharacter(characterId);
    setActiveConversation(null);
    setMessages([]);
    setConversations([]);
  }
  async function renameConversation(item: Conversation) {
    const title = window.prompt("输入新的对话名称", item.title)?.trim();
    if (!title || title === item.title) return;
    try {
      await request(`/conversations/${item.id}/title`, { method: "PUT", body: JSON.stringify({ title }) });
      setConversations((items) => items.map((value) => value.id === item.id ? { ...value, title } : value));
    } catch (e) { setError((e as Error).message); }
  }
  function beginMessageEdit(message: Message) {
    if (!message.id || busy) return;
    setEditingMessage(message.id);
    setMessageDraft(message.content);
  }
  async function saveMessageEdit(message: Message) {
    const content = messageDraft.trim();
    if (!message.id || !content) return;
    try {
      const updated = await request<Message>(`/messages/${message.id}`, {
        method: "PUT",
        body: JSON.stringify({ content }),
      });
      setMessages((items) => items.map((item) => item.id === updated.id ? updated : item));
      setEditingMessage(null);
      setMessageDraft("");
      setError("");
    } catch (e) { setError((e as Error).message); }
  }
  async function saveSettings(event: FormEvent) {
    event.preventDefault();
    try {
      await request("/settings", {
        method: "PUT",
        body: JSON.stringify(settings),
      });
      await request("/plugins/proactive", { method: "PUT", body: JSON.stringify(proactivePlugin) });
      setPanel("chat");
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function togglePin() {
    try {
      const value = !pinned;
      await invoke("set_always_on_top", { enabled: value });
      setPinned(value);
    } catch (e) {
      setError(String(e));
    }
  }
  async function toggleMini() {
    try {
      await invoke("enter_pet_mode");
      setMini(false);
    } catch (e) {
      setError(String(e));
    }
  }
  async function toggleAutostart() {
    if (!desktop || autostartBusy) return;
    setAutostartBusy(true);
    try {
      const enabled = await invoke<boolean>("set_autostart", { enabled: !autostartEnabled });
      setAutostartEnabled(enabled);
      setError("");
    } catch (cause) { setError(`修改开机自启失败：${String(cause)}`); }
    finally { setAutostartBusy(false); }
  }
  async function send(event: FormEvent) {
    event.preventDefault();
    const content = input.trim();
    if (!content || busy) return;
    let id = activeConversation;
    try {
      if (!id) {
        if (!activeCharacter) throw new Error("请先创建一个角色");
        const value = await request<Conversation>("/conversations", {
          method: "POST",
          body: JSON.stringify({ character_id: activeCharacter }),
        });
        id = value.id;
        skipMessageLoadRef.current = id;
        setActiveConversation(id);
        setConversations((c) => [value, ...c]);
        setMessages(await request<Message[]>(`/conversations/${id}/messages`));
      }
      setInput("");
      if (inputRef.current) inputRef.current.style.height = "auto";
      setBusy(true);
      busyRef.current = true;
      setError("");
      const pendingKey = `assistant-${Date.now()}-${Math.random()}`;
      setMessages((c) => [
        ...c,
        { role: "user", content },
        { role: "assistant", content: "", clientKey: pendingKey },
      ]);
      const controller = new AbortController();
      abortRef.current = controller;
      const response = await fetch(`${API}/conversations/${id}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail ?? "发送失败");
      }
      if (!response.body) throw new Error("浏览器不支持流式响应");
      const reader = response.body.getReader(),
        decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line) continue;
          const data = JSON.parse(line);
          if (data.error) throw new Error(data.error);
          if (data.token)
            setMessages((c) =>
              c.map((m) =>
                m.clientKey === pendingKey
                  ? { ...m, content: m.content + data.token }
                  : m,
              ),
            );
        }
      }
      buffer += decoder.decode();
      if (buffer.trim()) {
        const data = JSON.parse(buffer);
        if (data.error) throw new Error(data.error);
        if (data.token) setMessages((items) => items.map((message) => message.clientKey === pendingKey ? { ...message, content: message.content + data.token } : message));
      }
      setMessages(await request<Message[]>(`/conversations/${id}/messages`));
      setConversations(
        await request(`/conversations?character_id=${activeCharacter}`),
      );
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
      if (id) setMessages(await request<Message[]>(`/conversations/${id}/messages`).catch(() => []));
    } finally {
      setBusy(false);
      busyRef.current = false;
      abortRef.current = null;
    }
  }
  async function uploadDocument(file: File) {
    if (!activeCharacter || documentBusy) return;
    setDocumentBusy(true);
    setError("");
    try {
      let conversationId = activeConversation;
      if (!conversationId) {
        const created = await request<Conversation>("/conversations", { method: "POST", body: JSON.stringify({ character_id: activeCharacter, title: `阅读 ${file.name}` }) });
        conversationId = created.id;
        skipMessageLoadRef.current = created.id;
        setActiveConversation(created.id);
        setConversations((items) => [created, ...items]);
      }
      const contentBase64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
        reader.onerror = () => reject(new Error("读取文件失败"));
        reader.readAsDataURL(file);
      });
      const uploaded = await request<DocumentItem>(`/conversations/${conversationId}/documents`, { method: "POST", body: JSON.stringify({ filename: file.name, content_base64: contentBase64 }) });
      setDocuments((items) => [uploaded, ...items]);
      await analyzeDocument(uploaded, conversationId);
    } catch (cause) { setError((cause as Error).message); }
    finally {
      setDocumentBusy(false);
      if (documentInputRef.current) documentInputRef.current.value = "";
    }
  }
  async function analyzeDocument(item: DocumentItem, conversationId = activeConversation) {
    if (!conversationId) return;
    setDocuments((items) => items.map((value) => value.id === item.id ? { ...value, status: "analyzing", analysis_mode: settings.document_analysis_mode, analysis_stage: "preparing", progress_current: 0, progress_total: 0 } : value));
    const poller = window.setInterval(() => {
      void request<DocumentItem[]>(`/conversations/${conversationId}/documents`)
        .then(setDocuments)
        .catch(() => undefined);
    }, 800);
    try {
      await request(`/documents/${item.id}/analyze`, { method: "POST" });
    } finally {
      window.clearInterval(poller);
      setDocuments(await request<DocumentItem[]>(`/conversations/${conversationId}/documents`));
    }
  }
  async function deleteDocument(item: DocumentItem) {
    try {
      await request(`/documents/${item.id}`, { method: "DELETE" });
      setDocuments((items) => items.filter((value) => value.id !== item.id));
    } catch (cause) { setError((cause as Error).message); }
  }
  const character = characters.find((x) => x.id === activeCharacter),
    conversation = conversations.find((x) => x.id === activeConversation);

  return (
    <div className={mini ? "shell mini" : "shell"} data-theme={theme}>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">雨</span>
          <div>
            <strong>Yu's AI</strong>
            <small>私人智能空间</small>
          </div>
        </div>
        <button className="new-chat" onClick={createConversation}>
          ＋ 新建对话
        </button>
        <div className="sidebar-label">角色</div>
        <div className="character-list">
          {characters.map((x) => (
            <button
              key={x.id}
              className={
                activeCharacter === x.id
                  ? "selected character-row"
                  : "character-row"
              }
              onClick={() => void selectCharacter(x.id)}
            >
              <span className="avatar">{x.avatar_data ? <img src={x.avatar_data} alt="" /> : x.name[0]}</span>
              <span>
                <strong>{x.name}</strong>
                <small>{x.description || "私人角色"}</small>
              </span>
            </button>
          ))}
          {!characters.length && (
            <button
              className="empty-action"
              onClick={() => setPanel("characters")}
            >
              创建第一个角色
            </button>
          )}
        </div>
        <div className="sidebar-label">最近对话</div>
        <div className="conversation-list">
          {conversations.map((x) => (
            <div
              key={x.id}
              className={
                activeConversation === x.id
                  ? "conversation-row selected"
                  : "conversation-row"
              }
            >
              <button
                className="conversation-open"
                title={x.title}
                onClick={() => {
                  setActiveConversation(x.id);
                  setPanel("chat");
                }}
              >
                {x.title}
              </button>
              <button
                className="conversation-rename"
                title={`重命名对话：${x.title}`}
                onClick={() => void renameConversation(x)}
              >✎</button>
              <button
                className="conversation-delete"
                title={`删除对话：${x.title}`}
                aria-label={`删除对话：${x.title}`}
                onClick={() => deleteConversation(x)}
              >
                ×
              </button>
            </div>
          ))}
        </div>
        <nav>
          <button onClick={() => setPanel("characters")}>角色管理</button>
          <button onClick={() => setPanel("settings")}>模型设置</button>
        </nav>
      </aside>
      <main>
        <header>
          <div className="header-copy">
            <strong>
              {panel === "settings"
                ? "模型设置"
                : panel === "characters"
                  ? "创建角色"
                  : conversation?.title || character?.name || "开始使用"}
            </strong>
            <small>
              {panel === "chat" && character
                ? `正在与 ${character.name} 对话`
                : "Yu’s AI Plugin Platform"}
            </small>
          </div>
          <div className="window-tools">
            {desktop && (
              <>
                <button
                  className={pinned ? "active" : ""}
                  onClick={togglePin}
                  title="切换始终置顶"
                >
                  <span>◆</span> 置顶
                </button>
                <button
                  onClick={toggleMini}
                  title="进入桌面宠物模式"
                >
                  桌宠
                </button>
              </>
            )}
            <span className={backendReady ? "status" : "status starting"}>
              <i /> {backendReady ? "本地服务已连接" : "正在启动服务"}
            </span>
          </div>
        </header>
        {error && (
          <div className="error" onClick={() => setError("")}>
            {error}
            <span>×</span>
          </div>
        )}
        {panel === "settings" && (
          <section className="form-page">
            <Heading
              eyebrow="外观与模型"
              title="定制你的 AI 空间"
              text="主题会立即生效并自动保存在本机。"
            />
            <div className="theme-panel">
              <div className="form-section-title">
                <strong>界面主题</strong>
                <small>选择最适合你的阅读氛围</small>
              </div>
              <div className="theme-grid">
                {themes.map((item) => (
                  <button
                    key={item.id}
                    className={theme === item.id ? "theme-card selected" : "theme-card"}
                    data-preview={item.id}
                    onClick={() => setTheme(item.id)}
                  >
                    <span className="theme-preview">
                      <i />
                      <i />
                      <i />
                    </span>
                    <strong>{item.name}</strong>
                    <small>{item.description}</small>
                  </button>
                ))}
              </div>
            </div>
            {desktop && (
              <div className="device-settings">
                <div className="form-section-title">
                  <strong>系统启动</strong>
                  <small>登录 Windows 后自动启动，并恢复桌宠上次所在位置</small>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={autostartEnabled}
                  className={autostartEnabled ? "autostart-switch enabled" : "autostart-switch"}
                  onClick={() => void toggleAutostart()}
                  disabled={autostartBusy}
                >
                  <span><i /></span>{autostartBusy ? "正在设置…" : autostartEnabled ? "已开启" : "已关闭"}
                </button>
              </div>
            )}
            <form onSubmit={saveSettings}>
              <div className="form-section-title">
                <strong>模型连接</strong>
                <small>支持 OpenAI Chat Completions 格式的服务</small>
              </div>
              <Field label="API 地址">
                <input
                  value={settings.base_url}
                  onChange={(e) =>
                    setSettings({ ...settings, base_url: e.target.value })
                  }
                />
              </Field>
              <Field label="API Key">
                <input
                  type="password"
                  value={settings.api_key}
                  onChange={(e) =>
                    setSettings({ ...settings, api_key: e.target.value })
                  }
                  placeholder="sk-..."
                />
              </Field>
              <Field label="模型名称">
                <input
                  value={settings.model}
                  onChange={(e) =>
                    setSettings({ ...settings, model: e.target.value })
                  }
                />
              </Field>
              <div className="form-grid">
                <Field label="温度">
                  <input
                    type="number"
                    min="0"
                    max="2"
                    step=".1"
                    value={settings.temperature}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        temperature: Number(e.target.value),
                      })
                    }
                  />
                </Field>
                <Field label="单轮最大输出（截断时自动续写，最多 3 次）">
                  <input
                    type="number"
                    value={settings.max_tokens}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        max_tokens: Number(e.target.value),
                      })
                    }
                  />
                </Field>
              </div>
              <div className="form-section-title">
                <strong>上下文与记忆</strong>
                <small>数值越大，角色了解的信息越多，但会增加模型输入量</small>
              </div>
              <div className="form-grid">
                <Field label="最近上下文消息数">
                  <input type="number" min="2" max="200" value={settings.context_message_limit} onChange={(e) => setSettings({...settings, context_message_limit:Number(e.target.value)})} />
                </Field>
                <Field label="相关长期记忆条数">
                  <input type="number" min="0" max="50" value={settings.memory_limit} onChange={(e) => setSettings({...settings, memory_limit:Number(e.target.value)})} />
                </Field>
              </div>
              <div className="form-section-title">
                <strong>对话环境信息</strong>
                <small>按开关把设备本地时间或你填写的地区作为可选上下文传给模型</small>
              </div>
              <Field label="提供当前设备本地时间"><input type="checkbox" checked={settings.include_local_time} onChange={(e) => setSettings({ ...settings, include_local_time: e.target.checked })} /></Field>
              <Field label="向模型提供位置/地区"><input type="checkbox" checked={settings.include_location_context} onChange={(e) => setSettings({ ...settings, include_location_context: e.target.checked })} /></Field>
              {settings.include_location_context && (
                <Field label="位置或地区">
                  <input maxLength={200} value={settings.location_context} onChange={(e) => setSettings({ ...settings, location_context: e.target.value })} placeholder="例如：中国上海市浦东新区" />
                </Field>
              )}
              <small>位置由你手动填写，仅在开启位置开关后随对话发送；不会自动读取 GPS。时间与位置都只是参考信息，模型可以在无关问题中忽略它们。</small>
              <div className="form-section-title">
                <strong>离线翻译下载</strong>
                <small>留空使用 Argos 官方源；国内镜像需提供相同的 .argosmodel 文件</small>
              </div>
              <Field label="语言包镜像地址（可选）">
                <input type="url" placeholder="例如：https://mirror.example.com/argospm/v1" value={settings.translation_mirror_url} onChange={(e) => setSettings({...settings, translation_mirror_url:e.target.value})} />
              </Field>
              <Field label="文档图片模型（留空自动选择）">
                <input
                  value={settings.vision_model}
                  onChange={(e) => setSettings({ ...settings, vision_model: e.target.value })}
                  placeholder="DeepSeek 自动使用 deepseek-flash"
                />
              </Field>
              <small>只用于 PDF 页面和文档图片分析，不改变普通聊天模型。DeepSeek 官方接口留空时自动使用 deepseek-flash。</small>
              <Field label="文档分析模式">
                <select value={settings.document_analysis_mode} onChange={(e) => setSettings({ ...settings, document_analysis_mode: e.target.value as "fast" | "deep" })}>
                  <option value="fast">快速分析（整页预览，速度优先）</option>
                  <option value="deep">深度分析（整页＋高清切片，细节优先）</option>
                </select>
              </Field>
              <div className="form-section-title">
                <strong>消息显示插件</strong>
                <small>选择 AI 回复的显示方式；三种处理器互斥，只会启用一个</small>
              </div>
              <div className="message-plugin-grid">
                {([
                  ["markdown", "Markdown 渲染", "显示标题、列表、表格、引用和代码块"],
                  ["plain", "Markdown 过滤", "移除格式标记，仅保留可读纯文本"],
                  ["raw", "原始文本", "完整保留模型返回的所有标记，适合调试"],
                ] as [MessageDisplayMode, string, string][]).map(([mode, name, description]) => (
                  <button
                    type="button"
                    key={mode}
                    className={settings.message_display_mode === mode ? "message-plugin selected" : "message-plugin"}
                    onClick={() => setSettings({ ...settings, message_display_mode: mode })}
                  >
                    <span>{settings.message_display_mode === mode ? "●" : "○"}</span>
                    <strong>{name}</strong>
                    <small>{description}</small>
                  </button>
                ))}
              </div>
              <div className="form-section-title"><strong>角色主动互动插件</strong><small>启用后使用当前模型 API，按角色卡和本地时间生成问题、笑话或真实新闻话题。会消耗 token；23:00–07:00 不打扰。</small></div>
              <Field label="启用主动模型调用"><input type="checkbox" checked={proactivePlugin.enabled} onChange={(e) => setProactivePlugin({ ...proactivePlugin, enabled: e.target.checked })} /></Field>
              <Field label="随机时间主动发言"><input type="checkbox" checked={proactivePlugin.randomize_interval} onChange={(e) => setProactivePlugin({ ...proactivePlugin, randomize_interval: e.target.checked })} /></Field>
              {proactivePlugin.randomize_interval ? (
                <div className="proactive-random-range">
                  <Field label="最短等待（分钟）"><input type="number" min="1" max="1440" value={proactivePlugin.random_min_minutes} onChange={(e) => setProactivePlugin({ ...proactivePlugin, random_min_minutes: Number(e.target.value) })} /></Field>
                  <Field label="最长等待（分钟）"><input type="number" min="1" max="1440" value={proactivePlugin.random_max_minutes} onChange={(e) => setProactivePlugin({ ...proactivePlugin, random_max_minutes: Number(e.target.value) })} /></Field>
                </div>
              ) : (
                <>
                  <Field label="固定发言间隔（1–1440 分钟）"><input type="number" min="1" max="1440" value={proactivePlugin.interval_minutes} onChange={(e) => setProactivePlugin({ ...proactivePlugin, interval_minutes: Number(e.target.value) })} /></Field>
                  <div className="proactive-frequency-presets">{[1, 5, 15, 30, 60, 120].map((minutes) => <button type="button" key={minutes} className={proactivePlugin.interval_minutes === minutes ? "selected" : ""} onClick={() => setProactivePlugin({ ...proactivePlugin, interval_minutes: minutes })}>{minutes} 分钟</button>)}</div>
                </>
              )}
              <small>当前：{proactivePlugin.randomize_interval ? `每次在 ${proactivePlugin.random_min_minutes}–${proactivePlugin.random_max_minutes} 分钟之间重新随机` : `固定每 ${proactivePlugin.interval_minutes} 分钟`}。用户发送消息时会立即取消正在生成的主动发言，并从手动对话后重新计时。</small>
              <Field label={`承接最近聊天的概率（${proactivePlugin.history_weight}%）`}><input type="range" min="0" max="50" step="5" value={proactivePlugin.history_weight} onChange={(e) => setProactivePlugin({ ...proactivePlugin, history_weight: Number(e.target.value) })} /></Field>
              <small>未抽中时不会把聊天记录发给主动模型，而会按人设随机聊日常、兴趣、轻松话题或可选时事。建议保持 10%–20%，避免每次都像在续聊。</small>
              <Field label="单次回复 token 上限（64–8192，推理及输入也可能计费）"><input type="number" min="64" max="8192" value={proactivePlugin.max_tokens} onChange={(e) => setProactivePlugin({ ...proactivePlugin, max_tokens: Number(e.target.value) })} /></Field>
              <small>建议从 1024 开始；推理模型空回复时可提高至 4096。重试会再次调用模型并可能计费。</small>
              {proactivePlugin.last_error && <small role="status">最近主动发言失败：{proactivePlugin.last_error}</small>}
              <Field label="启用时事话题"><input type="checkbox" checked={proactivePlugin.news_enabled} onChange={(e) => setProactivePlugin({ ...proactivePlugin, news_enabled: e.target.checked })} /></Field>
              <Field label="新闻 RSS（HTTPS）"><input type="url" value={proactivePlugin.rss_url} onChange={(e) => setProactivePlugin({ ...proactivePlugin, rss_url: e.target.value })} /></Field>
              <small>API 已报告累计 token：{proactivePlugin.total_tokens}（未提供 usage 的服务无法统计）。新闻源失败时仅生成问题或笑话。配置随“保存设置”一起保存。</small>
              <button className="primary">保存设置</button>
            </form>
          </section>
        )}
        {panel === "characters" && (
          <section className="form-page">
            <Heading
              eyebrow="角色卡"
              title={editingCharacter ? "编辑角色卡" : "创造一个对话角色"}
              text="结构化设定会自动组合为模型每次对话使用的系统提示词。"
            />
            <div className="character-manager">
              {characters.map((item) => <div className="character-card" key={item.id}><strong>{item.name}</strong><span>{item.description || "暂无简介"}</span><div><button onClick={() => editCharacter(item)}>编辑</button><button onClick={() => void exportCharacter(item)}>导出</button><button className="danger" onClick={() => void deleteCharacter(item)}>删除</button></div></div>)}
              <label className="import-character">导入角色卡<input type="file" accept="application/json,.json" onChange={(e) => void importCharacter(e.target.files?.[0])} /></label>
            </div>
            <form onSubmit={createCharacter}>
              <Field label="角色头像"><input type="file" accept="image/png,image/jpeg,image/webp" onChange={(e) => { const file=e.target.files?.[0]; if (!file) return; const reader=new FileReader(); reader.onload=()=>setDraft({...draft,avatar_data:String(reader.result)}); reader.readAsDataURL(file); }} /></Field>
              <Field label="角色名称">
                <input
                  required
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  placeholder="例如：雨"
                />
              </Field>
              <Field label="开场白"><textarea rows={3} value={draft.greeting} onChange={(e) => setDraft({...draft,greeting:e.target.value})} placeholder="新对话开始时，角色主动说的第一句话" /></Field>
              <Field label="身份背景"><textarea rows={4} value={draft.background} onChange={(e) => setDraft({...draft,background:e.target.value})} /></Field>
              <div className="form-grid"><Field label="性格"><textarea rows={4} value={draft.personality} onChange={(e) => setDraft({...draft,personality:e.target.value})} /></Field><Field label="说话方式"><textarea rows={4} value={draft.speaking_style} onChange={(e) => setDraft({...draft,speaking_style:e.target.value})} /></Field></div>
              <div className="form-grid"><Field label="与用户的关系"><textarea rows={4} value={draft.relationship} onChange={(e) => setDraft({...draft,relationship:e.target.value})} /></Field><Field label="行为边界"><textarea rows={4} value={draft.boundaries} onChange={(e) => setDraft({...draft,boundaries:e.target.value})} /></Field></div>
              <Field label="示例对话"><textarea rows={5} value={draft.example_dialogue} onChange={(e) => setDraft({...draft,example_dialogue:e.target.value})} placeholder={'用户：你好\n角色：……'} /></Field>
              <Field label="一句话介绍">
                <input
                  value={draft.description}
                  onChange={(e) =>
                    setDraft({ ...draft, description: e.target.value })
                  }
                  placeholder="温柔而敏锐的私人助手"
                />
              </Field>
              <Field label="系统提示词">
                <textarea
                  rows={8}
                  value={draft.system_prompt}
                  onChange={(e) =>
                    setDraft({ ...draft, system_prompt: e.target.value })
                  }
                  placeholder="描述性格、背景、说话方式和边界……"
                />
              </Field>
              <div className="form-actions"><button className="primary">{editingCharacter ? "保存角色" : "创建角色"}</button>{editingCharacter && <button type="button" onClick={() => {setEditingCharacter(null);setDraft(emptyCharacter)}}>取消编辑</button>}</div>
            </form>
          </section>
        )}
        {panel === "chat" && (
          <section className="chat-page">
            <div className="messages">
              {!messages.length && (
                <div className="welcome">
                  <div className="welcome-orb">雨</div>
                  <span>你的私人 AI 空间</span>
                  <h1>
                    {character
                      ? `想和 ${character.name} 聊些什么？`
                      : "先创造一个属于你的角色"}
                  </h1>
                  <p>
                    {character?.description ||
                      "配置自己的模型、角色与对话，一切数据保留在本地。"}
                  </p>
                  {!character && (
                    <button
                      className="primary"
                      onClick={() => setPanel("characters")}
                    >
                      创建角色
                    </button>
                  )}
                </div>
              )}
              {messages.map((m, i) => (
                <article key={m.id ?? `pending-${i}`} className={m.role}>
                  <div className="message-avatar">
                    {m.role === "user" ? "你" : character?.name[0] || "AI"}
                  </div>
                  <div className="message-body">
                    <div className="message-heading">
                      <strong>{m.role === "user" ? "你" : character?.name || "助手"}</strong>
                      {m.id && editingMessage !== m.id && (
                        <button className="message-edit" type="button" onClick={() => beginMessageEdit(m)} disabled={busy}>编辑</button>
                      )}
                    </div>
                    {editingMessage === m.id ? (
                      <div className="message-editor">
                        <div className="message-editor-header">
                          <strong>编辑这条{m.role === "user" ? "消息" : "回复"}</strong>
                          <small>修改内容会用于后续对话上下文</small>
                        </div>
                        <textarea value={messageDraft} onChange={(e) => setMessageDraft(e.target.value)} onKeyDown={(e) => {
                          if (e.key === "Escape") { setEditingMessage(null); setMessageDraft(""); }
                          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void saveMessageEdit(m); }
                        }} autoFocus />
                        <div className="message-editor-actions">
                          <small>{messageDraft.length} 字 · Ctrl + Enter 保存 · Esc 取消</small>
                          <span>
                            <button type="button" onClick={() => { setEditingMessage(null); setMessageDraft(""); }}>取消</button>
                            <button className="primary" type="button" onClick={() => void saveMessageEdit(m)} disabled={!messageDraft.trim()}>保存</button>
                          </span>
                        </div>
                      </div>
                    ) : m.content ? (
                      <MessageContent content={m.content} mode={m.clientKey ? "raw" : m.role === "assistant" ? settings.message_display_mode : "raw"} />
                    ) : <p><span className="typing">思考中</span></p>}
                  </div>
                </article>
              ))}
              <div ref={messagesEndRef} aria-hidden="true" />
            </div>
            <form className="composer" onSubmit={send}>
              <input ref={documentInputRef} type="file" accept=".txt,.md,.markdown,.pdf,.docx" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadDocument(file); }} />
              {(documents.length > 0 || documentBusy) && (
                <div className="composer-attachments">
                  {documents.map((item) => (
                    <div className={`attachment-chip ${item.status}`} key={item.id} title={item.summary || `${item.char_count.toLocaleString()} 字`}>
                      <span className="attachment-icon">▤</span>
                      <span><strong>{item.filename}</strong><small>{documentStatusText(item)}</small></span>
                      {item.status === "error" && <button type="button" title="重试" onClick={() => void analyzeDocument(item)}>↻</button>}
                      <button type="button" title="移除附件" onClick={() => void deleteDocument(item)}>×</button>
                    </div>
                  ))}
                  {documentBusy && <div className="attachment-chip loading"><span className="attachment-icon">…</span><span><strong>正在处理文件</strong><small>提取内容并理解全文</small></span></div>}
                </div>
              )}
              <div className="composer-main">
                <button className="attach-button" type="button" disabled={!character || documentBusy || busy} onClick={() => documentInputRef.current?.click()} title="上传文档">📎</button>
                <textarea
                  ref={inputRef}
                  rows={1}
                  value={input}
                  onChange={(e) => {
                    setInput(e.target.value);
                    e.target.style.height = "auto";
                    e.target.style.height = `${Math.min(e.target.scrollHeight, 144)}px`;
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      e.currentTarget.form?.requestSubmit();
                    }
                  }}
                  placeholder={documentBusy ? "正在读取文档…" : documents.length ? "针对附件提问…" : character ? `给 ${character.name} 发消息…` : "请先创建角色"}
                  disabled={!character || busy || documentBusy}
                />
                <button className="send-button" type={busy ? "button" : "submit"} onClick={busy ? () => abortRef.current?.abort() : undefined}>
                  {busy ? "■" : "↑"}
                </button>
              </div>
              <small className="composer-tip">支持 TXT、Markdown、PDF、DOCX · Enter 发送 · Shift + Enter 换行</small>
            </form>
          </section>
        )}
      </main>
    </div>
  );
}

function Heading({
  eyebrow,
  title,
  text,
}: {
  eyebrow: string;
  title: string;
  text: string;
}) {
  return (
    <div className="section-heading">
      <span>{eyebrow}</span>
      <h1>{title}</h1>
      <p>{text}</p>
    </div>
  );
}
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label>
      {label}
      {children}
    </label>
  );
}
