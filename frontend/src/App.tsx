import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { confirm, open, save } from "@tauri-apps/plugin-dialog";
import MessageContent from "./MessageContent";
import type { MessageDisplayMode } from "./MessageContent";
import { isPluginEnabled, permissionLabels } from "./plugins";
import type { PluginId, PluginInfo } from "./plugins";
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
type ChatPhase = "generating" | "reviewing" | "revising";
const chatPhaseLabels: Record<ChatPhase, string> = { generating: "正在生成回复…", reviewing: "正在审核指令…", revising: "正在流式修订回复…" };
type SavedInstruction = { id: number; character_id: number; conversation_id: number | null; content: string; enabled: boolean; source_template_name: string; created_at: string };
type PromptTemplate = { id: number; name: string; category: string; content: string; created_at: string };
type QueuedTemplate = { templateId: number; templateName: string; characterId: number; variables: Record<string, string> };
type ModelProfile = { id: number; name: string; base_url: string; api_key: string; model: string; vision_model: string; active: boolean };
type DocumentItem = { id: number; conversation_id: number; filename: string; char_count: number; image_count: number; summary: string; status: "ready" | "analyzing" | "analyzed" | "error"; analysis_mode: "fast" | "deep"; analysis_stage: string; progress_current: number; progress_total: number };
type Settings = {
  active_model_profile_id: number | null;
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
  vision_model_profile_id: number | null;
  document_analysis_mode: "fast" | "deep";
  include_local_time: boolean;
  include_location_context: boolean;
  location_context: string;
  screen_access_enabled: boolean;
};
type Theme = "paper" | "midnight" | "blossom" | "jade";
type UpdateInfo = { current_version: string; version: string; name: string; notes: string; published_at: string; release_url: string; available: boolean; asset: { name: string; size: number; digest: string } };
type UpdateProgress = { stage: "downloading" | "retrying" | "complete"; percent: number; downloaded?: number; total?: number; resumed?: boolean; attempt?: number; max_attempts?: number; reason?: string; path?: string; sha256?: string; version?: string };
type BackupInfo = { path: string; filename: string; size: number; sha256: string; created_at: string; counts: { characters: number; conversations: number; messages: number; documents: number } };
const themes: { id: Theme; name: string; description: string }[] = [
  { id: "paper", name: "纸墨", description: "暖白纸张 · 高对比墨色" },
  { id: "midnight", name: "深海", description: "深蓝夜色 · 冰蓝高光" },
  { id: "blossom", name: "樱雾", description: "柔粉画布 · 莓红点缀" },
  { id: "jade", name: "青玉", description: "墨绿底色 · 温润金边" },
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

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
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
  const [busy, setBusy] = useState(false),
    [chatPhase, setChatPhase] = useState<ChatPhase | null>(null),
    [error, setError] = useState("");
  const [pinned, setPinned] = useState(false),
    [mini, setMini] = useState(false);
  const [theme, setTheme] = useState<Theme>(
    () => {
      const saved = localStorage.getItem("yus-ai-theme");
      if (themes.some((item) => item.id === saved)) return saved as Theme;
      return ({ violet: "midnight", sand: "paper", seafoam: "jade", ember: "midnight" } as Record<string, Theme>)[saved ?? ""] ?? "paper";
    },
  );
  const [backendReady, setBackendReady] = useState(false);
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [pluginBusy, setPluginBusy] = useState<string | null>(null);
  const [selectedPluginId, setSelectedPluginId] = useState<PluginId | null>(null);
  const [autostartEnabled, setAutostartEnabled] = useState(false);
  const [autostartBusy, setAutostartBusy] = useState(false);
  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);
  const [updateProgress, setUpdateProgress] = useState<UpdateProgress | null>(null);
  const [maintenanceBusy, setMaintenanceBusy] = useState<"update" | "backup" | "restore" | null>(null);
  const [maintenanceStatus, setMaintenanceStatus] = useState("");
  const [panel, setPanel] = useState<"chat" | "characters" | "settings">(
    "chat",
  );
  const [settings, setSettings] = useState<Settings>({
    active_model_profile_id: null,
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
    vision_model_profile_id: null,
    document_analysis_mode: "fast",
    include_local_time: true,
    include_location_context: false,
    location_context: "",
    screen_access_enabled: false,
  });
  const [modelProfiles, setModelProfiles] = useState<ModelProfile[]>([]);
  const [profileNameDraft, setProfileNameDraft] = useState("");
  const [profileBusy, setProfileBusy] = useState(false);
  const [profileNameSaveStatus, setProfileNameSaveStatus] = useState<"saved" | "saving" | "error">("saved");
  const [settingsSaveStatus, setSettingsSaveStatus] = useState<"saved" | "saving" | "error">("saved");
  const [proactiveSaveStatus, setProactiveSaveStatus] = useState<"saved" | "saving" | "error">("saved");
  const [draft, setDraft] = useState(emptyCharacter);
  const [proactivePlugin, setProactivePlugin] = useState({ enabled: false, interval_minutes: 30, randomize_interval: true, random_min_minutes: 15, random_max_minutes: 60, history_weight: 15, care_enabled: true, care_weight: 45, max_tokens: 1024, news_enabled: false, rss_url: "https://www.chinanews.com.cn/rss/scroll-news.xml", screen_context_enabled: false, total_tokens: 0, last_error: "" });
  const [editingCharacter, setEditingCharacter] = useState<number | null>(null);
  const [editingMessage, setEditingMessage] = useState<number | null>(null);
  const [messageDraft, setMessageDraft] = useState("");
  const [instructions, setInstructions] = useState<SavedInstruction[]>([]);
  const [instructionPanelOpen, setInstructionPanelOpen] = useState(false);
  const [instructionTab, setInstructionTab] = useState<"instructions" | "templates">("instructions");
  const [templates, setTemplates] = useState<PromptTemplate[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState<number | null>(null);
  const [templateEditing, setTemplateEditing] = useState(false);
  const [templateDraft, setTemplateDraft] = useState({ name: "", category: "", content: "" });
  const [templateVariables, setTemplateVariables] = useState<Record<string, string>>({});
  const [templateScope, setTemplateScope] = useState<"once" | "conversation" | "character">("once");
  const [queuedTemplate, setQueuedTemplate] = useState<QueuedTemplate | null>(null);
  const [templateBusy, setTemplateBusy] = useState(false);
  const [instructionDraft, setInstructionDraft] = useState("");
  const [instructionScope, setInstructionScope] = useState<"character" | "conversation">("character");
  const [editingInstruction, setEditingInstruction] = useState<number | null>(null);
  const [documentBusy, setDocumentBusy] = useState(false);
  const documentInputRef = useRef<HTMLInputElement | null>(null);
  const templateInputRef = useRef<HTMLInputElement | null>(null);
  const modelSwitcherRef = useRef<HTMLDetailsElement | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const settingsDirtyRef = useRef(false);
  const settingsRevisionRef = useRef(0);
  const settingsSaveQueueRef = useRef<Promise<void>>(Promise.resolve());
  const settingsSaveTimerRef = useRef<number | null>(null);
  const profileNameSaveQueueRef = useRef<Promise<void>>(Promise.resolve());
  const profileNameRevisionRef = useRef(0);
  const proactiveDirtyRef = useRef(false);
  const proactiveRevisionRef = useRef(0);
  const proactiveSaveQueueRef = useRef<Promise<void>>(Promise.resolve());
  const chatGenerationRef = useRef(0);
  const busyRef = useRef(false);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const inputDraftRef = useRef("");
  const inputResizeFrameRef = useRef(0);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const keepMessagesAtBottomRef = useRef(true);
  const forceLatestMessageRef = useRef(true);
  const skipMessageLoadRef = useRef<number | null>(null);
  const selectionReadyRef = useRef(false);
  const desktop = "__TAURI_INTERNALS__" in window;

  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);
  function changeSettings(next: Settings) {
    settingsRevisionRef.current += 1;
    settingsDirtyRef.current = true;
    setSettingsSaveStatus("saving");
    setSettings(next);
  }
  function queueSettingsSave(snapshot: Settings, revision: number): Promise<void> {
    const pending = settingsSaveQueueRef.current.catch(() => undefined).then(async () => {
      await request("/settings", { method: "PUT", body: JSON.stringify(snapshot) });
      if (revision === settingsRevisionRef.current) {
        settingsDirtyRef.current = false;
        setSettingsSaveStatus("saved");
        setError((current) => current.startsWith("自动保存模型设置失败：") ? "" : current);
      }
    });
    settingsSaveQueueRef.current = pending;
    void pending.catch((cause) => {
      if (revision === settingsRevisionRef.current) setSettingsSaveStatus("error");
      setError(`自动保存模型设置失败：${(cause as Error).message}`);
    });
    return pending;
  }
  useEffect(() => {
    if (!settingsDirtyRef.current) return;
    const revision = settingsRevisionRef.current;
    const timer = window.setTimeout(() => {
      settingsSaveTimerRef.current = null;
      void queueSettingsSave(settings, revision);
    }, 650);
    settingsSaveTimerRef.current = timer;
    return () => {
      window.clearTimeout(timer);
      if (settingsSaveTimerRef.current === timer) settingsSaveTimerRef.current = null;
    };
  }, [settings]);
  function changeProactive(next: typeof proactivePlugin) {
    proactiveRevisionRef.current += 1;
    proactiveDirtyRef.current = true;
    setProactiveSaveStatus("saving");
    setProactivePlugin(next);
  }
  useEffect(() => {
    if (!proactiveDirtyRef.current) return;
    const revision = proactiveRevisionRef.current;
    const timer = window.setTimeout(() => {
      const pending = proactiveSaveQueueRef.current.catch(() => undefined).then(async () => {
        await request("/plugins/proactive", { method: "PUT", body: JSON.stringify(proactivePlugin) });
        if (revision === proactiveRevisionRef.current) {
          proactiveDirtyRef.current = false;
          setProactiveSaveStatus("saved");
          setError((current) => current.startsWith("自动保存主动互动设置失败：") ? "" : current);
        }
      });
      proactiveSaveQueueRef.current = pending;
      void pending.catch((cause) => {
        if (revision === proactiveRevisionRef.current) setProactiveSaveStatus("error");
        setError(`自动保存主动互动设置失败：${(cause as Error).message}`);
      });
    }, 650);
    return () => window.clearTimeout(timer);
  }, [proactivePlugin]);
  useEffect(() => {
    if (!selectedPluginId) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelectedPluginId(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [selectedPluginId]);
  useEffect(() => () => window.cancelAnimationFrame(inputResizeFrameRef.current), []);
  function requestLatestMessage() {
    forceLatestMessageRef.current = true;
    keepMessagesAtBottomRef.current = true;
  }
  function scrollMessagesToBottom() {
    const container = messagesRef.current;
    if (container) container.scrollTop = container.scrollHeight;
    else messagesEndRef.current?.scrollIntoView({ block: "end" });
  }
  useLayoutEffect(() => {
    if (panel !== "chat" || (!forceLatestMessageRef.current && !keepMessagesAtBottomRef.current)) return;
    scrollMessagesToBottom();
    const firstFrame = window.requestAnimationFrame(() => {
      scrollMessagesToBottom();
      window.requestAnimationFrame(scrollMessagesToBottom);
    });
    const settleTimer = window.setTimeout(() => {
      scrollMessagesToBottom();
      forceLatestMessageRef.current = false;
    }, 180);
    return () => {
      window.cancelAnimationFrame(firstFrame);
      window.clearTimeout(settleTimer);
    };
  }, [messages, activeConversation, panel]);
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
          setPlugins(await request<PluginInfo[]>("/plugins"));
          const [profilesData, proactiveData] = await Promise.all([
            request<ModelProfile[]>("/model-profiles"),
            request<typeof proactivePlugin>("/plugins/proactive"),
          ]);
          setModelProfiles(profilesData);
          setProfileNameDraft(profilesData.find((item) => item.active)?.name ?? "");
          setProactivePlugin(proactiveData);
          if (characterData[0]) {
            const preferredId = Number(localStorage.getItem("yus-ai-character"));
            const selected = characterData.find((item) => item.id === preferredId) ?? characterData[0];
            setActiveCharacter(selected.id);
            const conversationData = await request<Conversation[]>(
              `/conversations?character_id=${selected.id}`,
            );
            if (!cancelled) {
              setConversations(conversationData);
              const preferredConversation = Number(localStorage.getItem("yus-ai-conversation"));
              const restoredConversation = conversationData.find((item) => item.id === preferredConversation)?.id ?? null;
              if (!restoredConversation) localStorage.removeItem("yus-ai-conversation");
              selectionReadyRef.current = true;
              setActiveConversation(restoredConversation);
            }
          } else {
            selectionReadyRef.current = true;
          }
          if (desktop && !cancelled) {
            try {
              const status = await request<{ version: string }>("/diagnostics/status");
              if (!cancelled) await invoke("confirm_update_startup", { backendVersion: status.version });
            } catch (cause) {
              console.warn("旧更新安装包清理未完成，将在下次启动时重试：", cause);
            }
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
    if (!activeCharacter) return;
    let cancelled = false;
    request<Conversation[]>(`/conversations?character_id=${activeCharacter}`)
      .then((items) => { if (!cancelled) setConversations(items); })
      .catch((e) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
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
          if (!cancelled) {
            requestLatestMessage();
            setMessages(data);
          }
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
    if (!activeCharacter) return;
    let cancelled = false;
    const query = new URLSearchParams({ character_id: String(activeCharacter) });
    if (activeConversation) query.set("conversation_id", String(activeConversation));
    request<SavedInstruction[]>(`/instructions?${query}`)
      .then((items) => { if (!cancelled) setInstructions(items); })
      .catch((cause) => { if (!cancelled) setError((cause as Error).message); });
    return () => { cancelled = true; };
  }, [activeCharacter, activeConversation]);
  useEffect(() => {
    if (!instructionPanelOpen || instructionTab !== "templates") return;
    let cancelled = false;
    request<PromptTemplate[]>("/prompt-templates")
      .then((items) => { if (!cancelled) setTemplates(items); })
      .catch((cause) => { if (!cancelled) setError((cause as Error).message); });
    return () => { cancelled = true; };
  }, [instructionPanelOpen, instructionTab]);
  useEffect(() => {
    localStorage.setItem("yus-ai-theme", theme);
    document.documentElement.dataset.theme = theme;
    return () => {
      delete document.documentElement.dataset.theme;
    };
  }, [theme]);
  useEffect(() => {
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (modelSwitcherRef.current && !modelSwitcherRef.current.contains(event.target as Node)) {
        modelSwitcherRef.current.open = false;
      }
    };
    document.addEventListener("pointerdown", closeOnOutsideClick);
    return () => document.removeEventListener("pointerdown", closeOnOutsideClick);
  }, []);
  useEffect(() => {
    if (activeCharacter)
      localStorage.setItem("yus-ai-character", String(activeCharacter));
  }, [activeCharacter]);
  useEffect(() => {
    if (!selectionReadyRef.current) return;
    if (activeConversation)
      localStorage.setItem("yus-ai-conversation", String(activeConversation));
    else localStorage.removeItem("yus-ai-conversation");
  }, [activeConversation]);
  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    let unlistenPlugins: (() => void) | undefined;
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
        setQueuedTemplate((current) => current?.characterId === characterId ? current : null);
        setConversations(conversationData);
        if (conversationId && conversationData.some((item) => item.id === conversationId)) {
          const messageData = await request<Message[]>(
            `/conversations/${conversationId}/messages`,
          );
          if (!cancelled) {
            skipMessageLoadRef.current = conversationId;
            requestLatestMessage();
            setActiveConversation(conversationId);
            setMessages(messageData);
          }
        } else {
          setActiveConversation(null);
          setMessages([]);
          setDocuments([]);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    }
    if (desktop) {
      void listen("main-sync", () => { void syncPetConversation(); })
        .then((stop) => { if (cancelled) stop(); else unlisten = stop; });
      void listen("open-plugin-manager", () => {
        setPanel("settings");
        window.setTimeout(() => {
          const drawer = document.getElementById("plugin-manager") as HTMLDetailsElement | null;
          if (drawer) drawer.open = true;
          drawer?.scrollIntoView({ behavior: "smooth", block: "start" });
        }, 100);
      }).then((stop) => { if (cancelled) stop(); else unlistenPlugins = stop; });
    } else {
      window.addEventListener("focus", syncPetConversation);
    }
    return () => {
      cancelled = true;
      unlisten?.();
      unlistenPlugins?.();
      if (!desktop) window.removeEventListener("focus", syncPetConversation);
    };
  }, [desktop]);

  function activateCharacter(characterId: number | null) {
    chatGenerationRef.current += 1;
    abortRef.current?.abort();
    setActiveCharacter(characterId);
    setQueuedTemplate(null);
    setActiveConversation(null);
    setMessages([]);
    setDocuments([]);
    setInstructions([]);
    setInstructionPanelOpen(false);
    setInstructionDraft("");
    setEditingInstruction(null);
    setConversations([]);
    skipMessageLoadRef.current = null;
    if (characterId) localStorage.setItem("yus-ai-character", String(characterId));
    else localStorage.removeItem("yus-ai-character");
    localStorage.removeItem("yus-ai-conversation");
  }

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
      if (activeCharacter !== value.id) activateCharacter(value.id);
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
    setCharacters(remaining);
    activateCharacter(remaining[0]?.id ?? null);
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
    try { const data = JSON.parse(await file.text()); const value = await request<Character>("/characters", { method: "POST", body: JSON.stringify({ ...emptyCharacter, ...(data.character ?? data) }) }); setCharacters((items) => [value, ...items]); activateCharacter(value.id); }
    catch (e) { setError(`角色导入失败：${(e as Error).message}`); }
  }
  async function createConversation() {
    if (!activeCharacter) {
      setPanel("characters");
      return;
    }
    const generation = chatGenerationRef.current;
    try {
      const value = await request<Conversation>("/conversations", {
        method: "POST",
        body: JSON.stringify({ character_id: activeCharacter }),
      });
      if (generation !== chatGenerationRef.current) return;
      setConversations((c) => [value, ...c]);
      openConversation(value.id);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function openConversation(conversationId: number | null) {
    if (activeConversation !== conversationId) {
      chatGenerationRef.current += 1;
      abortRef.current?.abort();
      skipMessageLoadRef.current = null;
      setMessages([]);
      setDocuments([]);
      setInstructions([]);
      setEditingMessage(null);
      setEditingInstruction(null);
      setInstructionDraft("");
      setActiveConversation(conversationId);
    }
    setPanel("chat");
  }
  async function deleteConversation(item: Conversation) {
    if (!window.confirm(`确定删除对话“${item.title}”吗？\n其中的消息、附件及由消息生成的自动记忆也会删除；该角色通用指令不受影响。`)) return;
    try {
      await request(`/conversations/${item.id}`, { method: "DELETE" });
      const remaining = conversations.filter((x) => x.id !== item.id);
      setConversations(remaining);
      if (activeConversation === item.id) {
        const next = remaining[0] ?? null;
        openConversation(next?.id ?? null);
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
    activateCharacter(characterId);
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
  async function reloadInstructions() {
    if (!activeCharacter) return;
    const query = new URLSearchParams({ character_id: String(activeCharacter) });
    if (activeConversation) query.set("conversation_id", String(activeConversation));
    setInstructions(await request<SavedInstruction[]>(`/instructions?${query}`));
  }
  function prepareInstructionFromMessage(message: Message) {
    const selected = window.getSelection()?.toString().trim() ?? "";
    setInstructionDraft((selected && message.content.includes(selected) ? selected : message.content).slice(0, 800));
    setInstructionScope(activeConversation ? "conversation" : "character");
    setEditingInstruction(null);
    setInstructionPanelOpen(true);
  }
  async function saveInstruction() {
    const content = instructionDraft.trim();
    if (!activeCharacter || !content) return;
    try {
      if (editingInstruction) {
        const existing = instructions.find((item) => item.id === editingInstruction);
        if (!existing) throw new Error("要编辑的指令已经不存在");
        await request(`/instructions/${editingInstruction}`, { method: "PUT", body: JSON.stringify({ content, enabled: existing.enabled }) });
      } else {
        await request("/instructions", { method: "POST", body: JSON.stringify({
          character_id: activeCharacter,
          conversation_id: instructionScope === "conversation" ? activeConversation : null,
          content,
        }) });
      }
      await reloadInstructions();
      setInstructionDraft("");
      setEditingInstruction(null);
      setError("");
    } catch (cause) { setError((cause as Error).message); }
  }
  async function toggleInstruction(item: SavedInstruction) {
    try {
      await request(`/instructions/${item.id}`, { method: "PUT", body: JSON.stringify({ content: item.content, enabled: !item.enabled }) });
      await reloadInstructions();
    } catch (cause) { setError((cause as Error).message); }
  }
  async function deleteInstruction(item: SavedInstruction) {
    if (!window.confirm("删除这条已保存的对话指令？")) return;
    try {
      await request(`/instructions/${item.id}`, { method: "DELETE" });
      await reloadInstructions();
      if (editingInstruction === item.id) { setEditingInstruction(null); setInstructionDraft(""); }
    } catch (cause) { setError((cause as Error).message); }
  }
  function selectTemplate(item: PromptTemplate) {
    setSelectedTemplateId(item.id);
    setTemplateDraft({ name: item.name, category: item.category, content: item.content });
    setTemplateVariables({});
    setTemplateEditing(false);
  }
  async function importTemplate(file: File) {
    if (!/\.(txt|md)$/i.test(file.name) || file.size > 64 * 1024) { setError("只能导入不超过 64 KB 的 .txt 或 .md 模板"); return; }
    setTemplateBusy(true);
    try {
      const content = (await file.text()).replace(/^\uFEFF/, "").trim();
      if (!content || content.length > 12000) throw new Error("模板内容须为 1–12000 字");
      const created = await request<PromptTemplate>("/prompt-templates", { method: "POST", body: JSON.stringify({ name: file.name.replace(/\.(txt|md)$/i, "").slice(0, 80), category: "导入", content }) });
      setTemplates((items) => [...items, created].sort((a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name)));
      selectTemplate(created);
      setError("");
    } catch (cause) { setError((cause as Error).message); }
    finally { setTemplateBusy(false); if (templateInputRef.current) templateInputRef.current.value = ""; }
  }
  async function saveTemplate() {
    if (templateBusy) return;
    setTemplateBusy(true);
    try {
      const path = selectedTemplateId === null ? "/prompt-templates" : `/prompt-templates/${selectedTemplateId}`;
      const saved = await request<PromptTemplate>(path, { method: selectedTemplateId === null ? "POST" : "PUT", body: JSON.stringify(templateDraft) });
      setTemplates((items) => [...items.filter((item) => item.id !== saved.id), saved].sort((a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name)));
      selectTemplate(saved);
      setError("");
    } catch (cause) { setError((cause as Error).message); }
    finally { setTemplateBusy(false); }
  }
  async function deleteTemplate() {
    const item = templates.find((value) => value.id === selectedTemplateId);
    if (!item || templateBusy || !window.confirm(`删除模板“${item.name}”？已应用的指令会保留，可在指令列表中单独关闭或删除。`)) return;
    setTemplateBusy(true);
    try {
      await request(`/prompt-templates/${item.id}`, { method: "DELETE" });
      setTemplates((items) => items.filter((value) => value.id !== item.id));
      setSelectedTemplateId(null);
      setTemplateDraft({ name: "", category: "", content: "" });
      setTemplateVariables({});
      if (queuedTemplate?.templateId === item.id) setQueuedTemplate(null);
      setError("");
    } catch (cause) { setError((cause as Error).message); }
    finally { setTemplateBusy(false); }
  }
  async function applyTemplate() {
    const item = templates.find((value) => value.id === selectedTemplateId);
    if (!item || !activeCharacter || templateBusy) return;
    const names = [...new Set([...item.content.matchAll(/\{\{([^{}\s]{1,40})\}\}/g)].map((match) => match[1]))];
    if (names.some((name) => !templateVariables[name]?.trim())) { setError("请先填写所有模板变量"); return; }
    if (templateScope === "once") {
      setQueuedTemplate({ templateId: item.id, templateName: item.name, characterId: activeCharacter, variables: Object.fromEntries(names.map((name) => [name, templateVariables[name]])) });
      setInstructionPanelOpen(false);
      setError("");
      return;
    }
    if (templateScope === "conversation" && !activeConversation) { setError("请先创建或打开一段对话"); return; }
    setTemplateBusy(true);
    try {
      await request(`/prompt-templates/${item.id}/apply`, { method: "POST", body: JSON.stringify({
        character_id: activeCharacter,
        conversation_id: templateScope === "conversation" ? activeConversation : null,
        variables: Object.fromEntries(names.map((name) => [name, templateVariables[name]])),
      }) });
      await reloadInstructions();
      setInstructionTab("instructions");
      setError("");
    } catch (cause) { setError((cause as Error).message); }
    finally { setTemplateBusy(false); }
  }
  async function flushSettingsChanges() {
    if (settingsSaveTimerRef.current !== null) window.clearTimeout(settingsSaveTimerRef.current);
    settingsSaveTimerRef.current = null;
    if (settingsDirtyRef.current) await queueSettingsSave(settings, settingsRevisionRef.current);
    else await settingsSaveQueueRef.current;
  }
  async function refreshModelProfiles() {
    const [nextSettings, profiles] = await Promise.all([
      request<Settings>("/settings"),
      request<ModelProfile[]>("/model-profiles"),
    ]);
    setSettings(nextSettings);
    setModelProfiles(profiles);
    setProfileNameDraft(profiles.find((item) => item.active)?.name ?? "");
  }
  const persistProfileName = useCallback(async () => {
    const active = modelProfiles.find((item) => item.active);
    if (!active || profileNameDraft.trim() === active.name) return profileNameSaveQueueRef.current;
    const name = profileNameDraft.trim();
    if (!name) {
      setProfileNameSaveStatus("error");
      throw new Error("模型配置名称不能为空");
    }
    const revision = profileNameRevisionRef.current;
    setProfileNameSaveStatus("saving");
    const pending = profileNameSaveQueueRef.current.catch(() => undefined).then(async () => {
      const updated = await request<ModelProfile>(`/model-profiles/${active.id}/name`, {
        method: "PATCH",
        body: JSON.stringify({ name }),
      });
      setModelProfiles((items) => items.map((item) => item.id === active.id ? updated : item));
      if (revision === profileNameRevisionRef.current) setProfileNameSaveStatus("saved");
    });
    profileNameSaveQueueRef.current = pending;
    void pending.catch((cause) => {
      if (revision === profileNameRevisionRef.current) {
        setProfileNameSaveStatus("error");
        setError(`自动保存配置名称失败：${(cause as Error).message}`);
      }
    });
    return pending;
  }, [modelProfiles, profileNameDraft]);
  useEffect(() => {
    const active = modelProfiles.find((item) => item.active);
    if (!active || profileNameDraft.trim() === active.name || !profileNameDraft.trim()) return;
    const timer = window.setTimeout(() => {
      void persistProfileName();
    }, 650);
    return () => window.clearTimeout(timer);
  }, [profileNameDraft, modelProfiles, persistProfileName]);
  async function switchModelProfile(profileId: number) {
    if (profileBusy || profileId === settings.active_model_profile_id) return;
    setProfileBusy(true);
    try {
      await persistProfileName();
      await flushSettingsChanges();
      await request(`/model-profiles/${profileId}/activate`, { method: "POST" });
      settingsDirtyRef.current = false;
      await refreshModelProfiles();
      setError("");
    } catch (cause) { setError((cause as Error).message); }
    finally { setProfileBusy(false); }
  }
  async function addModelProfile() {
    if (profileBusy) return;
    setProfileBusy(true);
    try {
      await persistProfileName();
      await flushSettingsChanges();
      const created = await request<ModelProfile>("/model-profiles", { method: "POST", body: JSON.stringify({
        name: `新模型 ${modelProfiles.length + 1}`,
        base_url: settings.base_url || "https://api.openai.com/v1",
        api_key: "",
        model: settings.model || "gpt-4o-mini",
        vision_model: "",
      }) });
      await request(`/model-profiles/${created.id}/activate`, { method: "POST" });
      settingsDirtyRef.current = false;
      await refreshModelProfiles();
      setError("");
    } catch (cause) { setError((cause as Error).message); }
    finally { setProfileBusy(false); }
  }
  async function deleteModelProfile() {
    const active = modelProfiles.find((item) => item.active);
    if (!active || profileBusy) return;
    if (modelProfiles.length <= 1) { setError("至少保留一个模型配置"); return; }
    if (!window.confirm(`删除模型配置“${active.name}”？聊天记录不会删除，删除后会自动切换到其他模型。`)) return;
    setProfileBusy(true);
    try {
      await flushSettingsChanges();
      await profileNameSaveQueueRef.current.catch(() => undefined);
      await request(`/model-profiles/${active.id}`, { method: "DELETE" });
      settingsDirtyRef.current = false;
      await refreshModelProfiles();
      setError("");
    } catch (cause) { setError((cause as Error).message); }
    finally { setProfileBusy(false); }
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
  async function togglePlugin(plugin: PluginInfo) {
    if (pluginBusy) return;
    setPluginBusy(plugin.id);
    try {
      await request(`/plugins/${plugin.id}/state`, {
        method: "PUT",
        body: JSON.stringify({ enabled: !plugin.enabled }),
      });
      setPlugins((items) => items.map((item) => item.id === plugin.id ? { ...item, enabled: !plugin.enabled } : item));
      setError("");
    } catch (cause) {
      setError(`切换插件失败：${(cause as Error).message}`);
    } finally {
      setPluginBusy(null);
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
  async function checkForUpdates() {
    if (maintenanceBusy) return;
    setMaintenanceBusy("update");
    setMaintenanceStatus("正在连接 GitHub 检查更新……");
    setUpdateProgress(null);
    try {
      const value = await request<UpdateInfo>("/system/update");
      setUpdateInfo(value);
      setMaintenanceStatus(value.available ? `发现新版本 ${value.version}` : `当前 ${value.current_version} 已是最新版本`);
      setError("");
    } catch (cause) {
      setMaintenanceStatus("");
      setError((cause as Error).message);
    } finally { setMaintenanceBusy(null); }
  }
  async function downloadAndInstallUpdate() {
    if (!desktop || maintenanceBusy) return;
    setMaintenanceBusy("update");
    setMaintenanceStatus("正在准备更新下载……");
    setUpdateProgress(null);
    try {
      const response = await fetch(`${API}/system/update/download`, { method: "POST" });
      if (!response.ok || !response.body) throw new Error(`下载更新失败 (${response.status})`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let completed: UpdateProgress | null = null;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line) continue;
          const event = JSON.parse(line) as UpdateProgress & { error?: string };
          if (event.error) throw new Error(event.error);
          setUpdateProgress(event);
          if (event.stage === "complete") completed = event;
        }
      }
      if (buffer.trim()) {
        const event = JSON.parse(buffer) as UpdateProgress & { error?: string };
        if (event.error) throw new Error(event.error);
        setUpdateProgress(event);
        if (event.stage === "complete") completed = event;
      }
      if (!completed?.path) throw new Error("安装包下载未完成");
      setMaintenanceStatus(`版本 ${completed.version} 已下载并通过 SHA-256 校验`);
      const accepted = await confirm("更新包已经校验完成。现在将退出 Yu's AI 并启动安装程序，数据库和安装目录中的用户数据会保留。是否继续？", { title: "安装更新", kind: "info" });
      if (accepted) await invoke("install_update", { installerPath: completed.path });
    } catch (cause) {
      setError(`应用更新失败：${(cause as Error).message}`);
      setMaintenanceStatus("");
    } finally { setMaintenanceBusy(null); }
  }
  async function createBackup() {
    if (!desktop || maintenanceBusy) return;
    setMaintenanceBusy("backup");
    setMaintenanceStatus("正在创建数据库一致性快照并整理附件……");
    try {
      const preferences: Record<string, string> = {};
      for (let index = 0; index < localStorage.length; index++) {
        const key = localStorage.key(index);
        if (key?.startsWith("yus-ai-")) preferences[key] = localStorage.getItem(key) ?? "";
      }
      const backup = await request<BackupInfo>("/system/backups", { method: "POST", body: JSON.stringify({ preferences }) });
      const destination = await save({ defaultPath: backup.filename, filters: [{ name: "Yu's AI 数据备份", extensions: ["yus-backup"] }] });
      if (destination) {
        const savedPath = await invoke<string>("copy_backup_file", { source: backup.path, destination });
        setMaintenanceStatus(`备份已保存：${savedPath} · ${formatBytes(backup.size)}`);
      } else {
        setMaintenanceStatus(`已在安装目录保留备份：${backup.path}`);
      }
      setError("");
    } catch (cause) {
      setMaintenanceStatus("");
      setError(`数据备份失败：${(cause as Error).message}`);
    } finally { setMaintenanceBusy(null); }
  }
  async function restoreBackup() {
    if (!desktop || maintenanceBusy || busy) return;
    const selected = await open({ multiple: false, directory: false, filters: [{ name: "Yu's AI 数据备份", extensions: ["yus-backup", "zip"] }] });
    if (typeof selected !== "string") return;
    const accepted = await confirm("恢复会用备份中的角色、聊天、记忆、设置和文档替换当前数据。程序会先自动创建一份恢复前快照，然后重新启动。是否继续？", { title: "恢复数据备份", kind: "warning" });
    if (!accepted) return;
    setMaintenanceBusy("restore");
    setMaintenanceStatus("正在校验备份并创建恢复前安全快照……");
    try {
      const result = await request<{ ok: boolean; requires_restart: boolean; safety_backup: string; preferences: Record<string, string> }>("/system/backups/restore", { method: "POST", body: JSON.stringify({ path: selected }) });
      for (const [key, value] of Object.entries(result.preferences ?? {})) {
        if (key.startsWith("yus-ai-") && typeof value === "string") localStorage.setItem(key, value);
      }
      setMaintenanceStatus(`恢复完成，安全快照：${result.safety_backup}`);
      if (result.requires_restart) await invoke("restart_application");
    } catch (cause) {
      setMaintenanceStatus("");
      setError(`恢复备份失败：${(cause as Error).message}`);
    } finally { setMaintenanceBusy(null); }
  }
  async function send(event: FormEvent) {
    event.preventDefault();
    const content = (inputRef.current?.value ?? inputDraftRef.current).trim();
    if (!content || busy) return;
    const oneTimeTemplate = queuedTemplate?.characterId === activeCharacter ? queuedTemplate : null;
    const chatGeneration = chatGenerationRef.current;
    let id = conversations.some((item) => item.id === activeConversation && item.character_id === activeCharacter)
      ? activeConversation
      : null;
    try {
      if (!id) {
        if (!activeCharacter) throw new Error("请先创建一个角色");
        const value = await request<Conversation>("/conversations", {
          method: "POST",
          body: JSON.stringify({ character_id: activeCharacter }),
        });
        if (chatGeneration !== chatGenerationRef.current) return;
        id = value.id;
        skipMessageLoadRef.current = id;
        setActiveConversation(id);
        setConversations((c) => [value, ...c]);
        const initialMessages = await request<Message[]>(`/conversations/${id}/messages`);
        if (chatGeneration !== chatGenerationRef.current) return;
        setMessages(initialMessages);
      }
      if (inputRef.current) {
        inputRef.current.value = "";
        inputRef.current.style.height = "";
      }
      inputDraftRef.current = "";
      setBusy(true);
      setChatPhase("generating");
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
        body: JSON.stringify({ content, character_id: activeCharacter,
          one_time_template_id: oneTimeTemplate?.templateId ?? null,
          one_time_template_variables: oneTimeTemplate?.variables ?? {} }),
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
      let revisionStarted = false;
      while (true) {
        const { value, done } = await reader.read();
        if (chatGeneration !== chatGenerationRef.current) return;
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line) continue;
          const data = JSON.parse(line);
          if (data.error) throw new Error(data.error);
          if (data.phase === "reviewing" || data.phase === "revising") setChatPhase(data.phase);
          if (typeof data.revision_token === "string") {
            const first = !revisionStarted;
            revisionStarted = true;
            setMessages((items) => items.map((message) => message.clientKey === pendingKey ? { ...message, content: (first ? "" : message.content) + data.revision_token } : message));
          }
          if (typeof data.replace === "string")
            setMessages((items) => items.map((message) => message.clientKey === pendingKey ? { ...message, content: data.replace } : message));
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
      if (oneTimeTemplate) setQueuedTemplate((current) => current?.templateId === oneTimeTemplate.templateId ? null : current);
      buffer += decoder.decode();
      if (buffer.trim()) {
        const data = JSON.parse(buffer);
        if (data.error) throw new Error(data.error);
        if (data.phase === "reviewing" || data.phase === "revising") setChatPhase(data.phase);
        if (typeof data.revision_token === "string") {
          const first = !revisionStarted;
          revisionStarted = true;
          setMessages((items) => items.map((message) => message.clientKey === pendingKey ? { ...message, content: (first ? "" : message.content) + data.revision_token } : message));
        }
        if (typeof data.replace === "string") setMessages((items) => items.map((message) => message.clientKey === pendingKey ? { ...message, content: data.replace } : message));
        if (data.token) setMessages((items) => items.map((message) => message.clientKey === pendingKey ? { ...message, content: message.content + data.token } : message));
      }
      const [latestMessages, latestConversations] = await Promise.all([
        request<Message[]>(`/conversations/${id}/messages`),
        request<Conversation[]>(`/conversations?character_id=${activeCharacter}`),
      ]);
      if (chatGeneration !== chatGenerationRef.current) return;
      setMessages(latestMessages);
      setConversations(latestConversations);
    } catch (e) {
      if (chatGeneration === chatGenerationRef.current && (e as Error).name !== "AbortError") {
        setError((e as Error).message);
        if (id) {
          const latestMessages = await request<Message[]>(`/conversations/${id}/messages`).catch(() => []);
          if (chatGeneration === chatGenerationRef.current) setMessages(latestMessages);
        }
      }
    } finally {
      setBusy(false);
      setChatPhase(null);
      busyRef.current = false;
      abortRef.current = null;
    }
  }
  async function uploadDocument(file: File) {
    if (!activeCharacter || documentBusy) return;
    setDocumentBusy(true);
    setError("");
    try {
      let conversationId = conversations.some((item) => item.id === activeConversation && item.character_id === activeCharacter)
        ? activeConversation
        : null;
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
  const selectedTemplate = templates.find((item) => item.id === selectedTemplateId);
  const templateFields = selectedTemplate ? [...new Set([...selectedTemplate.content.matchAll(/\{\{([^{}\s]{1,40})\}\}/g)].map((match) => match[1]))] : [];
  const templatePreview = selectedTemplate?.content.replace(/\{\{([^{}\s]{1,40})\}\}/g, (_match, name: string) => templateVariables[name]?.trim() || `〈待填写：${name}〉`) ?? "";
  const templateReady = templateFields.every((name) => Boolean(templateVariables[name]?.trim()));
  const selectedPlugin = plugins.find((plugin) => plugin.id === selectedPluginId);
  const pluginSettingsStatus = selectedPluginId === "proactive" ? proactiveSaveStatus : settingsSaveStatus;

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
        </div>
        <div className="sidebar-label">最近对话</div>
        <div className="conversation-list">
          <button className="new-chat" onClick={createConversation} disabled={!activeCharacter} aria-label="新建对话" title="新建对话"><span aria-hidden="true">＋</span><span>新建对话</span></button>
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
                  requestLatestMessage();
                  openConversation(x.id);
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
          <button onClick={() => setPanel("settings")}>设置与插件</button>
        </nav>
      </aside>
      <main>
        <header>
          <div className="header-copy">
            <strong>
              {panel === "settings"
                ? "模型设置"
                : panel === "characters"
                  ? "角色管理"
                  : conversation?.title || character?.name || "开始使用"}
            </strong>
            <small>
              {panel === "chat" && character
                ? `正在与 ${character.name} 对话`
                : "Yu’s AI Plugin Platform"}
            </small>
          </div>
          <div className="window-tools">
            {panel === "chat" && modelProfiles.length > 0 && (
              <details className="model-switcher" ref={modelSwitcherRef} onKeyDown={(event) => { if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); } }}>
                <summary aria-label={`当前模型：${modelProfiles.find((item) => item.id === settings.active_model_profile_id)?.name ?? settings.model}，点击切换`} title="切换聊天模型；桌宠与文档分析也会使用所选配置" onClick={(event) => { if (profileBusy || busy) event.preventDefault(); }}>
                  <span className="model-switcher-icon" aria-hidden="true">✦</span>
                  <span className="model-switcher-copy"><small>当前模型</small><strong>{modelProfiles.find((item) => item.id === settings.active_model_profile_id)?.name ?? settings.model}</strong></span>
                  <span className="model-switcher-chevron" aria-hidden="true">⌄</span>
                </summary>
                <div className="model-switcher-menu" role="menu" aria-label="切换聊天模型">
                  <span className="model-switcher-menu-title">选择对话模型</span>
                  {modelProfiles.map((item) => (
                    <button type="button" role="menuitemradio" aria-checked={item.id === settings.active_model_profile_id} className={item.id === settings.active_model_profile_id ? "model-switcher-option selected" : "model-switcher-option"} key={item.id} disabled={profileBusy || busy} onClick={() => { if (modelSwitcherRef.current) modelSwitcherRef.current.open = false; void switchModelProfile(item.id); }} title={`${item.name} · ${item.model}`}>
                      <span className="model-switcher-option-mark" aria-hidden="true">{item.id === settings.active_model_profile_id ? "✓" : ""}</span>
                      <span><strong>{item.name}</strong><small>{item.model}</small></span>
                    </button>
                  ))}
                </div>
              </details>
            )}
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
              eyebrow="外观、插件与模型"
              title="定制你的 AI 空间"
              text="主题与插件设置修改后自动保存。"
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
            {desktop && (
              <div className="maintenance-panel">
                <div className="form-section-title">
                  <strong>更新与数据安全</strong>
                  <small>从 GitHub 校验更新；备份保留角色、聊天、记忆、设置与上传文档</small>
                </div>
                <div className="maintenance-grid">
                  <section>
                    <div className="maintenance-icon">↻</div>
                    <div><strong>应用内更新</strong><small>{updateInfo ? `当前 ${updateInfo.current_version} · 最新 ${updateInfo.version}` : "手动检查，不会在后台自动下载安装"}</small></div>
                    <button type="button" onClick={() => void checkForUpdates()} disabled={maintenanceBusy !== null}>{maintenanceBusy === "update" && !updateProgress ? "检查中…" : "检查更新"}</button>
                    {updateInfo?.available && <button type="button" className="primary" onClick={() => void downloadAndInstallUpdate()} disabled={maintenanceBusy !== null}>{maintenanceBusy === "update" ? "下载中…" : `下载 ${formatBytes(updateInfo.asset.size)}`}</button>}
                  </section>
                  {updateInfo?.available && (
                    <div className="update-release">
                      <strong>{updateInfo.name}</strong>
                      <small>{updateInfo.notes || "此版本没有发布说明。"}</small>
                    </div>
                  )}
                  {updateProgress && (
                    <div className="update-progress">
                      <div><span style={{ width: `${updateProgress.percent}%` }} /></div>
                      <small>{updateProgress.stage === "complete" ? "下载完成并已通过 SHA-256 校验" : updateProgress.stage === "retrying" ? `${updateProgress.reason ?? "连接中断"}，正在重试 ${updateProgress.attempt}/${updateProgress.max_attempts} · 已保留 ${formatBytes(updateProgress.downloaded ?? 0)}` : `${updateProgress.resumed ? "断点续传" : "下载中"} ${updateProgress.percent}% · ${formatBytes(updateProgress.downloaded ?? 0)} / ${formatBytes(updateProgress.total ?? 0)}`}</small>
                    </div>
                  )}
                  <section>
                    <div className="maintenance-icon">▣</div>
                    <div><strong>数据备份与恢复</strong><small>语言包和日志不进入备份，需要时可以重新下载</small></div>
                    <button type="button" onClick={() => void createBackup()} disabled={maintenanceBusy !== null}>{maintenanceBusy === "backup" ? "备份中…" : "创建备份"}</button>
                    <button type="button" className="restore-button" onClick={() => void restoreBackup()} disabled={maintenanceBusy !== null || busy}>{maintenanceBusy === "restore" ? "恢复中…" : "恢复备份"}</button>
                  </section>
                </div>
                {maintenanceStatus && <p className="maintenance-status" role="status">{maintenanceStatus}</p>}
                <small className="maintenance-warning">备份文件包含 API Key 和聊天内容，请存放在可信位置，不要上传到公开网盘或仓库。</small>
              </div>
            )}
            <details className="plugin-manager settings-drawer" id="plugin-manager">
              <summary className="drawer-trigger"><span><strong>插件管理</strong><small>按用途分类管理内置插件</small></span><span className="drawer-chevron" aria-hidden="true">⌄</span></summary>
              <div className="drawer-content">
              {([
                ["对话呈现", ["message_display", "novel_reply"]],
                ["对话增强", ["conversation_environment", "instruction_review"]],
                ["桌宠互动", ["proactive"]],
                ["效率工具", ["translation"]],
              ] as [string, PluginId[]][]).map(([category, ids]) => (
                <div className="plugin-category" key={category}>
                  <h3>{category}</h3>
                  <div className="plugin-manager-grid">
                    {plugins.filter((plugin) => ids.includes(plugin.id)).map((plugin) => (
                      <article className={plugin.enabled ? "plugin-manager-card active" : "plugin-manager-card"} key={plugin.id}>
                        <button type="button" className="plugin-card-open" onClick={() => setSelectedPluginId(plugin.id)} aria-label={`打开${plugin.name}设置`}>
                          <span className="plugin-card-heading"><strong>{plugin.name}</strong><small>内置 · v{plugin.version}</small></span>
                          <span className="plugin-card-description">{plugin.description}</span>
                          <span className="plugin-card-hint">查看设置 <span aria-hidden="true">↗</span></span>
                        </button>
                        <button type="button" role="switch" aria-label={`${plugin.name}开关`} aria-checked={plugin.enabled} disabled={pluginBusy !== null} className={plugin.enabled ? "autostart-switch enabled" : "autostart-switch"} onClick={() => void togglePlugin(plugin)}>
                          <span><i /></span>{pluginBusy === plugin.id ? "正在切换…" : plugin.enabled ? "已启用" : "已停用"}
                        </button>
                      </article>
                    ))}
                    {plugins.length === 0 && <small>正在读取内置插件…</small>}
                  </div>
                </div>
              ))}
              <small>目前仅支持随应用打包的可信内置插件；不会加载外部脚本或第三方安装包。</small>
              </div>
            </details>
            {selectedPlugin && (
              <div className="plugin-modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelectedPluginId(null); }}>
                <section className="plugin-modal" role="dialog" aria-modal="true" aria-label={`${selectedPlugin.name}设置`}>
                  <header className="plugin-modal-header">
                    <div><small>内置插件 · v{selectedPlugin.version}</small><h2>{selectedPlugin.name}</h2><p>{selectedPlugin.description}</p></div>
                    <button type="button" className="plugin-modal-close" onClick={() => setSelectedPluginId(null)} aria-label="关闭插件设置" autoFocus>×</button>
                  </header>
                  <div className="plugin-modal-content">
                    {selectedPlugin.id === "translation" && <>
                      <Field label="语言包镜像地址（可选）"><input type="url" placeholder="例如：https://mirror.example.com/argospm/v1" value={settings.translation_mirror_url} onChange={(e) => changeSettings({ ...settings, translation_mirror_url: e.target.value })} /></Field>
                      <small>留空使用 Argos 官方源；国内镜像需提供相同的 .argosmodel 文件。语言包下载与进度在桌宠翻译功能中操作。</small>
                    </>}
                    {selectedPlugin.id === "message_display" && <>
                      <small>选择 AI 回复的显示方式；三种处理器互斥，只会启用一个。</small>
                      <div className="message-plugin-grid">
                        {([
                          ["markdown", "Markdown 渲染", "显示标题、列表、表格、引用和代码块"],
                          ["plain", "Markdown 过滤", "移除格式标记，仅保留可读纯文本"],
                          ["raw", "原始文本", "完整保留模型返回的所有标记，适合调试"],
                        ] as [MessageDisplayMode, string, string][]).map(([mode, name, description]) => (
                          <button type="button" key={mode} className={settings.message_display_mode === mode ? "message-plugin selected" : "message-plugin"} onClick={() => changeSettings({ ...settings, message_display_mode: mode })}>
                            <span>{settings.message_display_mode === mode ? "●" : "○"}</span><strong>{name}</strong><small>{description}</small>
                          </button>
                        ))}
                      </div>
                    </>}
                    {selectedPlugin.id === "conversation_environment" && <>
                      <Field label="让模型感知当前日期、星期和时间"><input type="checkbox" checked={settings.include_local_time} onChange={(e) => changeSettings({ ...settings, include_local_time: e.target.checked })} /></Field>
                      <Field label="向模型提供位置/地区"><input type="checkbox" checked={settings.include_location_context} onChange={(e) => changeSettings({ ...settings, include_location_context: e.target.checked })} /></Field>
                      {settings.include_location_context && <Field label="位置或地区"><input maxLength={200} value={settings.location_context} onChange={(e) => changeSettings({ ...settings, location_context: e.target.value })} placeholder="例如：中国上海市浦东新区" /></Field>}
                      <small>时间来自本机时钟；位置仅使用你手动填写的地区，不会读取 GPS。关闭插件会停止向聊天模型提供这两项信息，但不会清除已填写的设置。</small>
                    </>}
                    {selectedPlugin.id === "proactive" && <>
                      <Field label="启用主动模型调用"><input type="checkbox" checked={proactivePlugin.enabled} onChange={(e) => changeProactive({ ...proactivePlugin, enabled: e.target.checked })} /></Field>
                      <Field label="主动发言结合当前屏幕"><input type="checkbox" checked={proactivePlugin.screen_context_enabled} disabled={!settings.screen_access_enabled} onChange={(e) => changeProactive({ ...proactivePlugin, screen_context_enabled: e.target.checked })} /></Field>
                      <small>需先在模型设置中允许桌宠读取屏幕；每次可能额外消耗视觉模型 token。</small>
                      <Field label="随机时间主动发言"><input type="checkbox" checked={proactivePlugin.randomize_interval} onChange={(e) => changeProactive({ ...proactivePlugin, randomize_interval: e.target.checked })} /></Field>
                      {proactivePlugin.randomize_interval ? <div className="proactive-random-range">
                        <Field label="最短等待（分钟）"><input type="number" min="1" max="1440" value={proactivePlugin.random_min_minutes} onChange={(e) => changeProactive({ ...proactivePlugin, random_min_minutes: Number(e.target.value) })} /></Field>
                        <Field label="最长等待（分钟）"><input type="number" min="1" max="1440" value={proactivePlugin.random_max_minutes} onChange={(e) => changeProactive({ ...proactivePlugin, random_max_minutes: Number(e.target.value) })} /></Field>
                      </div> : <>
                        <Field label="固定发言间隔（1–1440 分钟）"><input type="number" min="1" max="1440" value={proactivePlugin.interval_minutes} onChange={(e) => changeProactive({ ...proactivePlugin, interval_minutes: Number(e.target.value) })} /></Field>
                        <div className="proactive-frequency-presets">{[1, 5, 15, 30, 60, 120].map((minutes) => <button type="button" key={minutes} className={proactivePlugin.interval_minutes === minutes ? "selected" : ""} onClick={() => changeProactive({ ...proactivePlugin, interval_minutes: minutes })}>{minutes} 分钟</button>)}</div>
                      </>}
                      <small>当前：{proactivePlugin.randomize_interval ? `每次在 ${proactivePlugin.random_min_minutes}–${proactivePlugin.random_max_minutes} 分钟之间重新随机` : `固定每 ${proactivePlugin.interval_minutes} 分钟`}。手动对话时会取消主动发言并重新计时。</small>
                      <Field label={`承接最近聊天的概率（${proactivePlugin.history_weight}%）`}><input type="range" min="0" max="50" step="5" value={proactivePlugin.history_weight} onChange={(e) => changeProactive({ ...proactivePlugin, history_weight: Number(e.target.value) })} /></Field>
                      <small>未抽中时会按人设聊日常、兴趣、轻松话题或可选时事。</small>
                      <Field label="启用时间关心"><input type="checkbox" checked={proactivePlugin.care_enabled} onChange={(e) => changeProactive({ ...proactivePlugin, care_enabled: e.target.checked })} /></Field>
                      {proactivePlugin.care_enabled && <Field label={`关心内容概率（${proactivePlugin.care_weight}%）`}><input type="range" min="0" max="100" step="5" value={proactivePlugin.care_weight} onChange={(e) => changeProactive({ ...proactivePlugin, care_weight: Number(e.target.value) })} /></Field>}
                      <Field label="单次回复 token 上限（64–8192）"><input type="number" min="64" max="8192" value={proactivePlugin.max_tokens} onChange={(e) => changeProactive({ ...proactivePlugin, max_tokens: Number(e.target.value) })} /></Field>
                      <small>建议从 1024 开始；推理模型空回复时可提高至 4096。重试会再次调用模型并可能计费。</small>
                      {proactivePlugin.last_error && <small role="status">最近主动发言失败：{proactivePlugin.last_error}</small>}
                      <Field label="启用时事话题"><input type="checkbox" checked={proactivePlugin.news_enabled} onChange={(e) => changeProactive({ ...proactivePlugin, news_enabled: e.target.checked })} /></Field>
                      <Field label="新闻 RSS（HTTPS）"><input type="url" value={proactivePlugin.rss_url} onChange={(e) => changeProactive({ ...proactivePlugin, rss_url: e.target.value })} /></Field>
                      <small>API 已报告累计 token：{proactivePlugin.total_tokens}（未提供 usage 的服务无法统计）。</small>
                    </>}
                    {selectedPlugin.id === "novel_reply" && <small>此插件目前只有启停开关，没有额外参数。它会把 AI 回复调整为第三人称小说式叙述；当前消息和已保存指令优先。</small>}
                    {selectedPlugin.id === "instruction_review" && <small>默认关闭，聊天优先快速完成。开启后仅对已保存指令及本轮模板做二次审核；明确的字数限制先在本地检查，必要时最多流式修订一次。审核和修订总计最多等待 30 秒，超时则保留原回复。关闭不会停止生成前的指令注入。</small>}
                    <div className="plugin-modal-permissions"><strong>使用权限</strong><span>{selectedPlugin.permissions.length ? selectedPlugin.permissions.map((permission) => permissionLabels[permission] ?? permission).join("、") : "无额外权限"}</span></div>
                  </div>
                  <footer className="plugin-modal-footer"><small role="status">{pluginSettingsStatus === "error" ? "自动保存失败，请检查提示后重试" : pluginSettingsStatus === "saving" ? "正在自动保存…" : "修改后自动保存"}</small><button type="button" onClick={() => setSelectedPluginId(null)}>完成</button></footer>
                </section>
              </div>
            )}
            <details className="model-drawer settings-drawer">
              <summary className="drawer-trigger"><span><strong>模型连接</strong><small>当前：{modelProfiles.find((item) => item.id === settings.active_model_profile_id)?.name ?? settings.model} · {modelProfiles.length} 个配置</small></span><span className="drawer-chevron" aria-hidden="true">⌄</span></summary>
              <form className="drawer-content" onSubmit={(event) => event.preventDefault()}>
                <small>保存多个兼容 OpenAI Chat Completions 的接口；切换后聊天与桌宠使用当前配置。</small>
              <div className="model-profile-actions">
                <select value={settings.active_model_profile_id ?? ""} disabled={profileBusy || busy} onChange={(event) => void switchModelProfile(Number(event.target.value))} aria-label="当前模型配置">
                  {modelProfiles.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.model}</option>)}
                </select>
                <button type="button" disabled={profileBusy || busy || modelProfiles.length >= 20} onClick={() => void addModelProfile()}>＋ 添加模型</button>
                <button type="button" disabled={profileBusy || busy || modelProfiles.length <= 1} onClick={() => void deleteModelProfile()}>删除当前</button>
              </div>
              <Field label="配置名称">
                <input value={profileNameDraft} maxLength={80} onChange={(event) => { profileNameRevisionRef.current += 1; setProfileNameSaveStatus("saving"); setProfileNameDraft(event.target.value); }} onBlur={() => void persistProfileName().catch((cause) => setError((cause as Error).message))} placeholder="例如：DeepSeek V4 Flash" />
              </Field>
              <Field label="API 地址">
                <input
                  value={settings.base_url}
                  onChange={(e) =>
                    changeSettings({ ...settings, base_url: e.target.value })
                  }
                />
              </Field>
              <Field label="API Key">
                <input
                  type="password"
                  value={settings.api_key}
                  onFocus={(event) => { if (settings.api_key === "••••••••") event.currentTarget.select(); }}
                  onChange={(e) =>
                    changeSettings({ ...settings, api_key: e.target.value })
                  }
                  placeholder="sk-..."
                />
              </Field>
              <Field label="模型名称">
                <input
                  value={settings.model}
                  onChange={(e) =>
                    changeSettings({ ...settings, model: e.target.value })
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
                      changeSettings({
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
                      changeSettings({
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
                  <input type="number" min="2" max="200" value={settings.context_message_limit} onChange={(e) => changeSettings({...settings, context_message_limit:Number(e.target.value)})} />
                </Field>
                <Field label="相关长期记忆条数">
                  <input type="number" min="0" max="50" value={settings.memory_limit} onChange={(e) => changeSettings({...settings, memory_limit:Number(e.target.value)})} />
                </Field>
              </div>
              <Field label="视觉模型（文档图片与桌宠看屏幕）">
                <select value={settings.vision_model_profile_id ?? (settings.vision_model ? "legacy" : "")} onChange={(e) => changeSettings({ ...settings, vision_model_profile_id: e.target.value && e.target.value !== "legacy" ? Number(e.target.value) : null, vision_model: e.target.value === "legacy" ? settings.vision_model : "" })}>
                  <option value="">自动跟随当前连接</option>
                  {settings.vision_model && settings.vision_model_profile_id === null && <option value="legacy">原自定义模型：{settings.vision_model}</option>}
                  {modelProfiles.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.model}</option>)}
                </select>
              </Field>
              <small>选择已保存的模型配置时，会使用该配置的 API 地址、密钥和模型名称；需支持 image_url 图片输入。自动模式沿用当前连接，DeepSeek 官方接口优先使用 deepseek-flash。</small>
              <div className="setting-toggle-row">
                <span>允许桌宠按需读取当前屏幕</span>
                <button type="button" role="switch" aria-label="允许桌宠按需读取当前屏幕" aria-checked={settings.screen_access_enabled} className={settings.screen_access_enabled ? "autostart-switch enabled" : "autostart-switch"} onClick={() => changeSettings({ ...settings, screen_access_enabled: !settings.screen_access_enabled })}>
                  <span><i /></span>{settings.screen_access_enabled ? "已启用" : "已停用"}
                </button>
              </div>
              <small>默认关闭。点击桌宠“看屏幕”时抓取鼠标所在显示器的一帧并发给配置的视觉模型；截图不写入本地文件、数据库或日志。请避免在屏幕上显示密码等敏感信息。</small>
              <Field label="文档分析模式">
                <select value={settings.document_analysis_mode} onChange={(e) => changeSettings({ ...settings, document_analysis_mode: e.target.value as "fast" | "deep" })}>
                  <option value="fast">快速分析（整页预览，速度优先）</option>
                  <option value="deep">深度分析（整页＋高清切片，细节优先）</option>
                </select>
              </Field>
              <small className="settings-save-status" role="status">{settingsSaveStatus === "error" || proactiveSaveStatus === "error" || profileNameSaveStatus === "error" ? "自动保存失败，请检查上方提示后修改重试" : settingsSaveStatus === "saving" || proactiveSaveStatus === "saving" || profileNameSaveStatus === "saving" ? "正在自动保存…" : "所有设置已自动保存"}</small>
              </form>
            </details>
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
            <div
              className="messages"
              ref={messagesRef}
              onScroll={(event) => {
                const target = event.currentTarget;
                keepMessagesAtBottomRef.current = target.scrollHeight - target.scrollTop - target.clientHeight < 96;
              }}
              onLoadCapture={() => {
                if (forceLatestMessageRef.current || keepMessagesAtBottomRef.current) scrollMessagesToBottom();
              }}
            >
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
                      {m.id && m.role === "user" && editingMessage !== m.id && (
                        <button className="message-edit" type="button" onClick={() => prepareInstructionFromMessage(m)} disabled={busy}>存为指令</button>
                      )}
                    </div>
                    {editingMessage === m.id ? (
                      <div className="message-editor">
                        <div className="message-editor-header">
                          <strong>编辑这条{m.role === "user" ? "消息" : "回复"}</strong>
                          <small>修改会用于后续对话，但不会自动改写后面的回复</small>
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
                      <MessageContent content={m.content} mode={m.clientKey ? "raw" : m.role === "assistant" && isPluginEnabled(plugins, "message_display") ? settings.message_display_mode : "raw"} />
                    ) : <p><span className="typing">思考中</span></p>}
                  </div>
                </article>
              ))}
              <div ref={messagesEndRef} aria-hidden="true" />
            </div>
            {instructionPanelOpen && (
              <section className="instruction-library" aria-label="对话指令库">
                <div className="instruction-library-head">
                  <div><strong>指令与模板</strong><small>模板使用前可预览；长期生效的内容会显示在指令列表中。</small></div>
                  <button type="button" onClick={() => setInstructionPanelOpen(false)} aria-label="关闭指令库">×</button>
                </div>
                <div className="instruction-tabs">
                  <button type="button" className={instructionTab === "instructions" ? "active" : ""} onClick={() => setInstructionTab("instructions")}>生效的指令（{instructions.filter((item) => item.enabled).length}）</button>
                  <button type="button" className={instructionTab === "templates" ? "active" : ""} onClick={() => setInstructionTab("templates")}>提示词模板</button>
                </div>
                {instructionTab === "instructions" ? <>
                <div className="instruction-list">
                  {instructions.length === 0 && <p>还没有保存指令。可以输入一条，或从你的聊天消息中选择“存为指令”。</p>}
                  {instructions.map((item) => (
                    <div className={item.enabled ? "instruction-item" : "instruction-item disabled"} key={item.id}>
                      <label><input type="checkbox" checked={item.enabled} onChange={() => void toggleInstruction(item)} aria-label={`启用指令：${item.content.slice(0, 20)}`} /><span>{item.conversation_id ? "本段对话" : "该角色所有对话"}{item.source_template_name ? ` · 模板：${item.source_template_name}` : ""}</span></label>
                      <p>{item.content}</p>
                      <div><button type="button" onClick={() => { setEditingInstruction(item.id); setInstructionDraft(item.content); setInstructionScope(item.conversation_id ? "conversation" : "character"); }}>编辑</button><button type="button" onClick={() => void deleteInstruction(item)}>删除</button></div>
                    </div>
                  ))}
                </div>
                <div className="instruction-editor">
                  <textarea value={instructionDraft} maxLength={editingInstruction ? 16000 : 800} onChange={(event) => setInstructionDraft(event.target.value)} placeholder="例如：回答时先给结论，再解释原因；不要忽略我指定的格式。" aria-label="指令内容" />
                  <div>
                    <select value={instructionScope} disabled={editingInstruction !== null} onChange={(event) => setInstructionScope(event.target.value as "character" | "conversation")} aria-label="指令作用范围">
                      <option value="character">该角色所有对话</option>
                      <option value="conversation" disabled={!activeConversation}>仅当前对话</option>
                    </select>
                    {editingInstruction !== null && <button type="button" onClick={() => { setEditingInstruction(null); setInstructionDraft(""); }}>取消编辑</button>}
                    <button type="button" className="primary" disabled={!instructionDraft.trim() || (instructionScope === "conversation" && !activeConversation)} onClick={() => void saveInstruction()}>{editingInstruction !== null ? "保存修改" : "保存指令"}</button>
                  </div>
                </div>
                <small>每个角色最多 30 条；手写指令最多 800 字，模板展开后最多 16000 字。指令会增加模型输入 token；当前消息明确修改旧偏好时，以当前要求为准。</small>
                </> : <div className="template-library">
                  <div className="template-toolbar">
                    <input ref={templateInputRef} type="file" accept=".txt,.md" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) void importTemplate(file); }} />
                    <button type="button" disabled={templateBusy} onClick={() => templateInputRef.current?.click()}>导入 TXT / Markdown</button>
                    <button type="button" disabled={templateBusy} onClick={() => { setSelectedTemplateId(null); setTemplateDraft({ name: "", category: "", content: "" }); setTemplateVariables({}); setTemplateEditing(true); }}>＋ 新建模板</button>
                    <small>仅保存纯文本，不会执行文件中的代码或命令。</small>
                  </div>
                  <div className="template-columns">
                    <div className="template-list">
                      {templates.length === 0 && <p>还没有模板。导入 .txt／.md，或新建一个。</p>}
                      {templates.map((item) => <button type="button" key={item.id} className={selectedTemplateId === item.id ? "active" : ""} onClick={() => selectTemplate(item)}><strong>{item.name}</strong><small>{item.category || "未分类"}</small></button>)}
                    </div>
                    <div className="template-detail">
                      {templateEditing ? <>
                        <input aria-label="模板名称" maxLength={80} placeholder="模板名称" value={templateDraft.name} onChange={(event) => setTemplateDraft({ ...templateDraft, name: event.target.value })} />
                        <input aria-label="模板分类" maxLength={40} placeholder="分类（可选）" value={templateDraft.category} onChange={(event) => setTemplateDraft({ ...templateDraft, category: event.target.value })} />
                        <textarea aria-label="模板内容" maxLength={12000} placeholder="模板内容；可用 {{主题}} 等变量" value={templateDraft.content} onChange={(event) => setTemplateDraft({ ...templateDraft, content: event.target.value })} />
                        <div className="template-actions"><button type="button" onClick={() => { setTemplateEditing(false); if (selectedTemplate) setTemplateDraft({ name: selectedTemplate.name, category: selectedTemplate.category, content: selectedTemplate.content }); }}>取消</button><button type="button" className="primary" disabled={templateBusy || !templateDraft.name.trim() || !templateDraft.content.trim()} onClick={() => void saveTemplate()}>保存模板</button></div>
                      </> : selectedTemplate ? <>
                        <div className="template-heading"><strong>{selectedTemplate.name}</strong><small>{selectedTemplate.category || "未分类"}</small></div>
                        {templateFields.map((name) => <label className="template-variable" key={name}>{name}<input maxLength={1000} value={templateVariables[name] ?? ""} onChange={(event) => setTemplateVariables({ ...templateVariables, [name]: event.target.value })} placeholder={`填写${name}`} /></label>)}
                        <div className="template-preview"><strong>发送给模型的内容预览</strong><pre>{templatePreview}</pre></div>
                        <div className="template-actions"><select aria-label="模板作用范围" value={templateScope} onChange={(event) => setTemplateScope(event.target.value as typeof templateScope)}><option value="once">仅下一次回复</option><option value="conversation" disabled={!activeConversation}>当前对话持续生效</option><option value="character">当前角色所有对话</option></select><button type="button" className="primary" disabled={templateBusy || !activeCharacter || !templateReady || (templateScope === "conversation" && !activeConversation)} onClick={() => void applyTemplate()}>{templateScope === "once" ? "用于下一条消息" : "启用模板"}</button></div>
                        <div className="template-actions secondary"><button type="button" onClick={() => setTemplateEditing(true)}>编辑模板</button><button type="button" onClick={() => void deleteTemplate()}>删除模板</button></div>
                      </> : <p>选择一个模板，填写变量并预览后再使用。</p>}
                    </div>
                  </div>
                </div>}
              </section>
            )}
            <form className="composer" onSubmit={send}>
              <input ref={documentInputRef} type="file" accept=".txt,.md,.markdown,.pdf,.docx" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) void uploadDocument(file); }} />
              {queuedTemplate?.characterId === activeCharacter && <div className="queued-template"><span>下一条消息使用模板：{queuedTemplate.templateName}</span><button type="button" onClick={() => setQueuedTemplate(null)} aria-label="取消仅本次模板">×</button></div>}
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
                <button className="instruction-button" type="button" disabled={!character} onClick={() => setInstructionPanelOpen((open) => !open)} title={`对话指令库 · 已启用 ${instructions.filter((item) => item.enabled).length} 条`} aria-label="打开对话指令库">指</button>
                <textarea
                  ref={inputRef}
                  rows={1}
                  defaultValue={inputDraftRef.current}
                  onInput={(e) => {
                    const field = e.currentTarget;
                    inputDraftRef.current = field.value;
                    window.cancelAnimationFrame(inputResizeFrameRef.current);
                    inputResizeFrameRef.current = window.requestAnimationFrame(() => {
                      field.style.height = "0px";
                      field.style.height = `${Math.min(field.scrollHeight, 144)}px`;
                    });
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
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
              <small className="composer-tip" role={busy ? "status" : undefined}>{busy && chatPhase ? chatPhaseLabels[chatPhase] : "指令库可固定对话要求 · 支持 TXT、Markdown、PDF、DOCX · Enter 发送 · Shift + Enter 换行"}</small>
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
