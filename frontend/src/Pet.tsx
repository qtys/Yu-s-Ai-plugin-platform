import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { listen } from "@tauri-apps/api/event";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import MessageContent from "./MessageContent";
import type { MessageDisplayMode } from "./MessageContent";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api";
const WAITING_MESSAGE = "本地服务还没准备好，请稍后再点我。";
const READY_MESSAGE = "点点我，我们来聊天吧。";
type Character = { id: number; name: string };
type Conversation = { id: number; character_id: number; title: string };
type PetState = { position_x: number | null; position_y: number | null };
type DisplaySettings = { message_display_mode: MessageDisplayMode };
type TranslationPackage = { from_code: "zh" | "en"; to_code: "zh" | "en"; name: string; size_mb: number; installed: boolean };
type PetFeature = "chat" | "translation" | "settings";
type PetMood = "idle" | "tap" | "happy" | "confused";
type IdleAction = "none" | "squish" | "wiggle" | "sleepy";

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

export default function Pet() {
  const desktop = "__TAURI_INTERNALS__" in window;
  const [open, setOpen] = useState(false);
  const [translationOpen, setTranslationOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [layoutChanging, setLayoutChanging] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [mood, setMood] = useState<PetMood>("idle");
  const [idleAction, setIdleAction] = useState<IdleAction>("none");
  const [gaze, setGaze] = useState({ x: 0, y: 0 });
  const [proactiveMessage, setProactiveMessage] = useState("");
  const [proactiveEnabled, setProactiveEnabled] = useState(
    () => localStorage.getItem("yus-ai-proactive-enabled") !== "false",
  );
  const [placement, setPlacement] = useState("above-right");
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState("");
  const [reply, setReply] = useState(READY_MESSAGE);
  const [translationPackages, setTranslationPackages] = useState<TranslationPackage[]>([]);
  const [translationSource, setTranslationSource] = useState<"zh" | "en">("zh");
  const [translationInput, setTranslationInput] = useState("");
  const [translationOutput, setTranslationOutput] = useState("");
  const [translationBusy, setTranslationBusy] = useState(false);
  const [continuousTranslation, setContinuousTranslation] = useState(false);
  const [character, setCharacter] = useState<Character | null>(null);
  const [messageDisplayMode, setMessageDisplayMode] = useState<MessageDisplayMode>("markdown");
  const [petSize, setPetSize] = useState(
    () => Number(localStorage.getItem("yus-ai-pet-size")) || 100,
  );
  const [petOpacity, setPetOpacity] = useState(
    () => Number(localStorage.getItem("yus-ai-pet-opacity")) || 100,
  );
  const [dialogFontSize, setDialogFontSize] = useState(
    () => Number(localStorage.getItem("yus-ai-dialog-font-size")) || 100,
  );
  const conversationRef = useRef<number | null>(null);
  const dragStartRef = useRef<{ x: number; y: number } | null>(null);
  const draggingRef = useRef(false);
  const didDragRef = useRef(false);
  const positionRestoredRef = useRef(false);
  const startupShownRef = useRef(false);
  const continuousTranslationRef = useRef(false);
  const translationSequenceRef = useRef(0);
  const menuClickTimerRef = useRef<number | undefined>(undefined);
  const lastFeatureRef = useRef<PetFeature>(
    (localStorage.getItem("yus-ai-last-pet-feature") as PetFeature | null) ?? "chat",
  );
  const expanded = open || translationOpen || settingsOpen;
  const openRef = useRef(expanded);
  const placementRef = useRef(placement);
  const petSizeRef = useRef(petSize);
  const moodTimerRef = useRef<number | undefined>(undefined);

  useEffect(() => { openRef.current = expanded; }, [expanded]);
  useEffect(() => { placementRef.current = placement; }, [placement]);
  useEffect(() => { petSizeRef.current = petSize; }, [petSize]);
  useEffect(() => { continuousTranslationRef.current = continuousTranslation; }, [continuousTranslation]);
  useEffect(() => () => window.clearTimeout(menuClickTimerRef.current), []);
  useEffect(() => { localStorage.setItem("yus-ai-proactive-enabled", String(proactiveEnabled)); }, [proactiveEnabled]);

  useEffect(() => {
    if (expanded || dragging || busy) return;
    const actions: IdleAction[] = ["squish", "wiggle", "sleepy"];
    const timer = window.setInterval(() => {
      const action = actions[Math.floor(Math.random() * actions.length)];
      setIdleAction(action);
      window.setTimeout(() => setIdleAction("none"), action === "sleepy" ? 2400 : 1100);
    }, 11000);
    return () => window.clearInterval(timer);
  }, [expanded, dragging, busy]);

  useEffect(() => {
    if (!proactiveEnabled || expanded) return;
    const greetings = ["要记得喝水呀。", "坐久了吗？起来伸个懒腰吧。", "我一直在这里，需要时点点我。", "今天也要对自己温柔一点。"];
    const showGreeting = () => {
      setProactiveMessage(greetings[Math.floor(Math.random() * greetings.length)]);
      window.setTimeout(() => setProactiveMessage(""), 9000);
    };
    const first = window.setTimeout(showGreeting, 120000);
    const recurring = window.setInterval(showGreeting, 240000);
    return () => { window.clearTimeout(first); window.clearInterval(recurring); };
  }, [proactiveEnabled, expanded]);

  useEffect(() => {
    let disposed = false;
    let retryTimer: number | undefined;
    async function loadContext() {
      try {
        const [characters, petState, displaySettings] = await Promise.all([
          request<Character[]>("/characters"),
          request<PetState>("/pet/state"),
          request<DisplaySettings>("/settings"),
        ]);
        setMessageDisplayMode(displaySettings.message_display_mode);
        if (desktop && !positionRestoredRef.current) {
          if (petState.position_x !== null && petState.position_y !== null) {
            const restoredPlacement = await invoke<string>("set_pet_position", {
              x: petState.position_x,
              y: petState.position_y,
              scale: petSizeRef.current / 100,
            });
            setPlacement(restoredPlacement);
          }
          positionRestoredRef.current = true;
          if (!startupShownRef.current) {
            startupShownRef.current = true;
            await invoke("show_pet_window");
          }
        }
        setReply((value) => value === WAITING_MESSAGE ? READY_MESSAGE : value);
        const preferred = Number(localStorage.getItem("yus-ai-character"));
        const selected = characters.find((item) => item.id === preferred) ?? characters[0] ?? null;
        setCharacter(selected);
        if (!selected) return;
        const conversations = await request<Conversation[]>(`/conversations?character_id=${selected.id}`);
        const preferredConversation = Number(localStorage.getItem("yus-ai-conversation"));
        conversationRef.current = conversations.find((item) => item.id === preferredConversation)?.id ?? conversations[0]?.id ?? null;
      } catch {
        setReply(WAITING_MESSAGE);
        if (!disposed) retryTimer = window.setTimeout(loadContext, 1000);
      }
    }
    void loadContext();
    window.addEventListener("focus", loadContext);
    return () => {
      disposed = true;
      window.clearTimeout(retryTimer);
      window.removeEventListener("focus", loadContext);
    };
  }, []);

  useEffect(() => {
    if (!desktop) return;
    let saveTimer: number | undefined;
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void getCurrentWindow().onMoved(() => {
      if (!positionRestoredRef.current) return;
      window.clearTimeout(saveTimer);
      saveTimer = window.setTimeout(async () => {
        try {
          const position = await invoke<{ x: number; y: number }>("get_pet_position", {
            expanded: openRef.current,
            scale: petSizeRef.current / 100,
            placement: placementRef.current,
          });
          await request("/pet/state", {
            method: "PUT",
            body: JSON.stringify({ position_x: position.x, position_y: position.y }),
          });
        } catch { /* 后端启动或退出期间不阻塞窗口操作 */ }
      }, 300);
    }).then((stop) => { if (disposed) stop(); else unlisten = stop; });
    return () => {
      disposed = true;
      window.clearTimeout(saveTimer);
      unlisten?.();
    };
  }, [desktop]);

  useEffect(() => {
    localStorage.setItem("yus-ai-pet-size", String(petSize));
  }, [petSize]);
  useEffect(() => {
    localStorage.setItem("yus-ai-pet-opacity", String(petOpacity));
  }, [petOpacity]);
  useEffect(() => {
    localStorage.setItem("yus-ai-dialog-font-size", String(dialogFontSize));
  }, [dialogFontSize]);

  useEffect(() => {
    if (!desktop) return;
    let disposed = false;
    let unlisten: (() => void) | undefined;
    let unlistenReset: (() => void) | undefined;
    let unlistenSelection: (() => void) | undefined;
    void listen<string>("pet-control", (event) => {
      if (event.payload === "size-up" || event.payload === "size-down") {
        setPetSize((current) => {
          const next = Math.max(70, Math.min(125, current + (event.payload === "size-up" ? 5 : -5)));
          void invoke<string>("set_pet_layout", {
            expanded: openRef.current,
            scale: next / 100,
            currentExpanded: openRef.current,
            currentPlacement: placementRef.current,
          }).then(setPlacement);
          return next;
        });
      }
      if (event.payload === "opacity-up") setPetOpacity((current) => Math.min(100, current + 10));
      if (event.payload === "opacity-down") setPetOpacity((current) => Math.max(30, current - 10));
    }).then((stop) => { if (disposed) stop(); else unlisten = stop; });
    void listen("pet-reset", () => {
      continuousTranslationRef.current = false;
      setContinuousTranslation(false);
      void invoke("set_continuous_translation", { enabled: false });
      if (openRef.current) {
        void invoke<string>("set_pet_layout", {
          expanded: false,
          scale: petSizeRef.current / 100,
          currentExpanded: true,
          currentPlacement: placementRef.current,
        }).then(setPlacement);
      }
      setOpen(false);
      setTranslationOpen(false);
      setSettingsOpen(false);
      setMenuOpen(false);
    }).then((stop) => { if (disposed) stop(); else unlistenReset = stop; });
    void listen<string>("screen-text-selected", (event) => {
      if (!continuousTranslationRef.current || document.hasFocus()) return;
      const text = event.payload.trim();
      if (!text) return;
      const source: "zh" | "en" = /[\u3400-\u9fff]/.test(text) ? "zh" : "en";
      const target = source === "zh" ? "en" : "zh";
      const sequence = ++translationSequenceRef.current;
      setTranslationSource(source);
      setTranslationInput(text);
      setTranslationBusy(true);
      setTranslationOutput("已捕获选中文本，正在翻译……");
      void request<{ translation: string }>("/translation", {
        method: "POST",
        body: JSON.stringify({ text, source, target }),
      }).then((result) => {
        if (sequence === translationSequenceRef.current) setTranslationOutput(result.translation);
      }).catch((error) => {
        if (sequence === translationSequenceRef.current) setTranslationOutput((error as Error).message);
      }).finally(() => {
        if (sequence === translationSequenceRef.current) setTranslationBusy(false);
      });
    }).then((stop) => { if (disposed) stop(); else unlistenSelection = stop; });
    return () => {
      disposed = true;
      void invoke("set_continuous_translation", { enabled: false });
      unlisten?.(); unlistenReset?.(); unlistenSelection?.();
    };
  }, [desktop]);

  async function beginLayoutChange() {
    if (!desktop) return;
    setLayoutChanging(true);
    await new Promise((resolve) => window.setTimeout(resolve, 85));
  }

  function finishLayoutChange() {
    if (!desktop) return;
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => setLayoutChanging(false)));
  }

  function rememberFeature(feature: PetFeature) {
    lastFeatureRef.current = feature;
    localStorage.setItem("yus-ai-last-pet-feature", feature);
  }

  function showMood(nextMood: PetMood, duration = 900) {
    window.clearTimeout(moodTimerRef.current);
    setMood(nextMood);
    moodTimerRef.current = window.setTimeout(() => setMood("idle"), duration);
  }

  async function toggleBubble() {
    const nextOpen = !open;
    if (nextOpen) rememberFeature("chat");
    await beginLayoutChange();
    if (desktop) {
      const nextPlacement = await invoke<string>("set_pet_layout", {
        expanded: nextOpen,
        scale: petSize / 100,
        currentExpanded: expanded,
        currentPlacement: placement,
      });
      setPlacement(nextPlacement);
    }
    setOpen(nextOpen);
    setTranslationOpen(false);
    setSettingsOpen(false);
    setMenuOpen(false);
    finishLayoutChange();
  }

  async function toggleTranslation() {
    const nextOpen = !translationOpen;
    if (nextOpen) rememberFeature("translation");
    if (!nextOpen && continuousTranslationRef.current) {
      continuousTranslationRef.current = false;
      setContinuousTranslation(false);
      if (desktop) await invoke("set_continuous_translation", { enabled: false });
    }
    await beginLayoutChange();
    if (desktop) {
      const nextPlacement = await invoke<string>("set_pet_layout", {
        expanded: nextOpen,
        scale: petSize / 100,
        currentExpanded: expanded,
        currentPlacement: placement,
      });
      setPlacement(nextPlacement);
    }
    setTranslationOpen(nextOpen);
    setOpen(false);
    setSettingsOpen(false);
    setMenuOpen(false);
    finishLayoutChange();
    if (nextOpen) {
      try { setTranslationPackages(await request<TranslationPackage[]>("/translation/packages")); }
      catch (error) { setTranslationOutput((error as Error).message); }
    }
  }

  async function toggleSettings() {
    const nextOpen = !settingsOpen;
    if (nextOpen) rememberFeature("settings");
    await beginLayoutChange();
    if (desktop) {
      const nextPlacement = await invoke<string>("set_pet_layout", {
        expanded: nextOpen,
        scale: petSize / 100,
        currentExpanded: expanded,
        currentPlacement: placement,
      });
      setPlacement(nextPlacement);
    }
    setSettingsOpen(nextOpen);
    setOpen(false);
    setTranslationOpen(false);
    setMenuOpen(false);
    finishLayoutChange();
  }

  function beginDrag(event: React.PointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) return;
    dragStartRef.current = { x: event.screenX, y: event.screenY };
    draggingRef.current = false;
    didDragRef.current = false;
    showMood("tap", 500);
  }

  async function continueDrag(event: React.PointerEvent<HTMLButtonElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    setGaze({
      x: Math.max(-1, Math.min(1, (event.clientX - bounds.left) / bounds.width * 2 - 1)),
      y: Math.max(-1, Math.min(1, (event.clientY - bounds.top) / bounds.height * 2 - 1)),
    });
    const start = dragStartRef.current;
    if (!desktop || !start || draggingRef.current) return;
    if (Math.hypot(event.screenX - start.x, event.screenY - start.y) < 6) return;
    draggingRef.current = true;
    didDragRef.current = true;
    setDragging(true);
    try {
      await invoke("start_pet_drag");
      const snappedPlacement = await invoke<string>("snap_pet_to_edge", {
        threshold: 42,
        expanded,
        currentPlacement: placement,
      });
      setPlacement(snappedPlacement);
      showMood("happy", 900);
    } finally {
      dragStartRef.current = null;
      setDragging(false);
      window.setTimeout(() => { draggingRef.current = false; }, 0);
    }
  }

  function finishDrag() {
    dragStartRef.current = null;
  }

  function stopLooking() {
    setGaze({ x: 0, y: 0 });
  }

  function toggleMenu() {
    if (didDragRef.current) {
      didDragRef.current = false;
      return;
    }
    if (!expanded) {
      window.clearTimeout(menuClickTimerRef.current);
      menuClickTimerRef.current = window.setTimeout(() => setMenuOpen((value) => !value), 220);
    }
  }

  function openLastFeature() {
    if (expanded || didDragRef.current) return;
    window.clearTimeout(menuClickTimerRef.current);
    setMenuOpen(false);
    if (lastFeatureRef.current === "translation") void toggleTranslation();
    else if (lastFeatureRef.current === "settings") void toggleSettings();
    else void toggleBubble();
  }

  function updatePetSize(value: number) {
    setPetSize(value);
    if (desktop)
      void invoke<string>("set_pet_layout", {
        expanded,
        scale: value / 100,
        currentExpanded: expanded,
        currentPlacement: placement,
      }).then(setPlacement);
  }

  async function returnToMain() {
    continuousTranslationRef.current = false;
    setContinuousTranslation(false);
    if (desktop) await invoke("set_continuous_translation", { enabled: false });
    if (desktop)
      await invoke("set_pet_layout", { expanded: false, scale: petSize / 100, currentExpanded: expanded, currentPlacement: placement });
    setOpen(false);
    setTranslationOpen(false);
    setSettingsOpen(false);
    await invoke("show_main_window");
  }

  async function hidePet() {
    continuousTranslationRef.current = false;
    setContinuousTranslation(false);
    if (desktop) await invoke("set_continuous_translation", { enabled: false });
    setOpen(false);
    setTranslationOpen(false);
    setSettingsOpen(false);
    setMenuOpen(false);
    if (desktop) await invoke("hide_pet_window");
  }

  async function downloadTranslationPackage() {
    const source = translationSource;
    const target = source === "zh" ? "en" : "zh";
    setTranslationBusy(true);
    setTranslationOutput(`正在下载 ${source === "zh" ? "中英" : "英中"}离线语言包……`);
    try {
      await request(`/translation/packages/${source}/${target}`, { method: "POST" });
      setTranslationPackages((items) => items.map((item) =>
        item.from_code === source && item.to_code === target ? { ...item, installed: true } : item,
      ));
      const refreshed = await request<TranslationPackage[]>(`/translation/packages?refresh=${Date.now()}`);
      setTranslationPackages(refreshed);
      setTranslationOutput("语言包安装完成，现在可以离线翻译了。");
    } catch (error) {
      setTranslationOutput((error as Error).message);
    } finally { setTranslationBusy(false); }
  }

  async function toggleContinuousTranslation() {
    if (!desktop) {
      setTranslationOutput("连续翻译仅在 Windows 桌面版中可用。");
      return;
    }
    const enabled = !continuousTranslationRef.current;
    try {
      await invoke("set_continuous_translation", { enabled });
      continuousTranslationRef.current = enabled;
      setContinuousTranslation(enabled);
      setTranslationOutput(enabled
        ? "连续翻译已开启：在其他窗口中用鼠标选中文本，译文会自动显示在这里。"
        : "连续翻译已关闭。");
    } catch (error) { setTranslationOutput(`无法切换连续翻译：${String(error)}`); }
  }

  async function runTranslation(event: FormEvent) {
    event.preventDefault();
    const text = translationInput.trim();
    if (!text || translationBusy) return;
    const target = translationSource === "zh" ? "en" : "zh";
    setTranslationBusy(true);
    setTranslationOutput("正在翻译……");
    try {
      const result = await request<{ translation: string }>("/translation", {
        method: "POST",
        body: JSON.stringify({ text, source: translationSource, target }),
      });
      setTranslationOutput(result.translation);
      showMood("happy", 1200);
    } catch (error) { setTranslationOutput((error as Error).message); showMood("confused", 1400); }
    finally { setTranslationBusy(false); }
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    const content = input.trim();
    if (!content || busy) return;
    if (!character) {
      setReply("请先在主界面创建一个角色。");
      return;
    }
    setInput("");
    setBusy(true);
    setReply("正在想……");
    try {
      let id = conversationRef.current;
      if (!id) {
        const conversation = await request<Conversation>("/conversations", {
          method: "POST",
          body: JSON.stringify({ character_id: character.id }),
        });
        id = conversation.id;
        conversationRef.current = id;
        localStorage.setItem("yus-ai-conversation", String(id));
      }
      const response = await fetch(`${API}/conversations/${id}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail ?? "发送失败");
      }
      if (!response.body) throw new Error("无法读取模型回复");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let complete = "";
      setReply("");
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
          if (data.token) {
            complete += data.token;
            setReply(complete);
          }
        }
      }
      showMood("happy", 1800);
    } catch (error) {
      setReply((error as Error).message);
      showMood("confused", 1800);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className={`pet-stage ${expanded ? "open" : ""} ${placement}`}>
      <div
        className={`pet-canvas ${expanded ? "open" : ""} ${layoutChanging ? "layout-changing" : ""}`}
        style={{ transform: `scale(${petSize / 100})`, "--dialog-font-scale": dialogFontSize / 100 } as React.CSSProperties}
      >
      {open && (
        <section className="speech-bubble">
          <div className="speech-head">
            <strong>{character?.name ?? "蓝雨"}</strong>
            <div className="speech-actions">
              <button onClick={returnToMain}>展开</button>
              <button className="close-bubble" onClick={() => void toggleBubble()} aria-label="关闭对话框">×</button>
            </div>
          </div>
          <div className={`pet-reply ${busy ? "thinking" : ""}`}>
            <MessageContent content={reply} mode={messageDisplayMode} />
          </div>
          <form onSubmit={send}>
            <input value={input} onChange={(event) => setInput(event.target.value)} placeholder="和我说点什么……" autoFocus />
            <button disabled={busy || !input.trim()} aria-label="发送">↑</button>
          </form>
        </section>
      )}
      {translationOpen && (
        <section className="speech-bubble translation-panel">
          <div className="speech-head">
            <strong>离线翻译</strong>
            <div className="speech-actions">
              <button className={continuousTranslation ? "continuous active" : "continuous"} onClick={() => void toggleContinuousTranslation()}>
                {continuousTranslation ? "● 连续" : "○ 连续"}
              </button>
              <button onClick={() => setTranslationSource((value) => value === "zh" ? "en" : "zh")}>⇄ {translationSource === "zh" ? "中 → 英" : "英 → 中"}</button>
              <button className="close-bubble" onClick={() => void toggleTranslation()} aria-label="关闭翻译">×</button>
            </div>
          </div>
          {(() => {
            const target = translationSource === "zh" ? "en" : "zh";
            const model = translationPackages.find((item) => item.from_code === translationSource && item.to_code === target);
            return model && !model.installed ? (
              <button className="download-model" disabled={translationBusy} onClick={() => void downloadTranslationPackage()}>
                下载 {model.name}（约 {model.size_mb} MB）
              </button>
            ) : null;
          })()}
          <form className="translation-form" onSubmit={runTranslation}>
            <textarea value={translationInput} onChange={(event) => setTranslationInput(event.target.value)} placeholder="输入要翻译的内容……" autoFocus />
            <button disabled={translationBusy || !translationInput.trim()}>翻译</button>
          </form>
          <div className={`translation-result ${translationBusy ? "thinking" : ""}`}>{translationOutput || "译文会显示在这里。"}</div>
        </section>
      )}
      {settingsOpen && (
        <section className="speech-bubble settings-panel">
          <div className="speech-head">
            <strong>桌宠设置</strong>
            <button className="close-bubble" onClick={() => void toggleSettings()} aria-label="关闭设置">×</button>
          </div>
          <div className="pet-controls">
            <label>桌宠大小 <input type="range" min="70" max="125" value={petSize} onChange={(event) => updatePetSize(Number(event.target.value))} /><span>{petSize}%</span></label>
            <label>透明度 <input type="range" min="30" max="100" value={petOpacity} onChange={(event) => setPetOpacity(Number(event.target.value))} /><span>{petOpacity}%</span></label>
            <label>对话文字 <input type="range" min="80" max="160" step="5" value={dialogFontSize} onChange={(event) => setDialogFontSize(Number(event.target.value))} /><span>{dialogFontSize}%</span></label>
            <label className="proactive-toggle">主动冒泡 <input type="checkbox" checked={proactiveEnabled} onChange={(event) => setProactiveEnabled(event.target.checked)} /><span>{proactiveEnabled ? "开启" : "关闭"}</span></label>
          </div>
        </section>
      )}
      {proactiveMessage && !expanded && !menuOpen && (
        <aside className="proactive-bubble" aria-live="polite">
          <button onClick={() => setProactiveMessage("")} aria-label="关闭主动提醒">×</button>
          {proactiveMessage}
        </aside>
      )}
      <button
        className={`pet-character ${busy ? "thinking" : ""} mood-${mood} idle-${idleAction} ${dragging ? "dragging" : ""}`}
        style={{ opacity: petOpacity / 100, "--gaze-x": gaze.x, "--gaze-y": gaze.y } as React.CSSProperties}
        onPointerDown={beginDrag}
        onPointerMove={continueDrag}
        onPointerUp={finishDrag}
        onPointerCancel={finishDrag}
        onPointerLeave={stopLooking}
        onClick={toggleMenu}
        onDoubleClick={() => { if (open) void toggleBubble(); else if (translationOpen) void toggleTranslation(); else if (settingsOpen) void toggleSettings(); else openLastFeature(); }}
        aria-label="蓝色雨滴史莱姆，拖动移动，点击打开功能菜单"
      >
        <img src="/assets/blue-slime-pet.png" alt="蓝色雨滴史莱姆" draggable={false} />
        <span className="pet-ripple" />
        <span className="pet-emote" aria-hidden="true">{busy ? "…" : mood === "happy" ? "♥" : mood === "confused" ? "?" : idleAction === "sleepy" ? "Zzz" : ""}</span>
      </button>
      {menuOpen && !expanded && (
        <nav className="pet-plugin-menu" aria-label="桌宠功能">
          <button className="plugin-orb chat-orb" onClick={() => void toggleBubble()}><span>💬</span>对话</button>
          <button className="plugin-orb translate-orb" onClick={() => void toggleTranslation()}><span>译</span>翻译</button>
          <button className="plugin-orb settings-orb" onClick={() => void toggleSettings()}><span>⚙</span>设置</button>
          <button className="plugin-orb add-orb" disabled title="等待插件接入"><span>＋</span>插件</button>
          <button className="plugin-orb close-orb" onClick={() => void hidePet()}><span>×</span>关闭</button>
        </nav>
      )}
      </div>
    </main>
  );
}
